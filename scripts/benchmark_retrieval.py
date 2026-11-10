
import argparse
import csv
import json
import math
import os
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

DATASET_PATH = ROOT / ".benchmarks" / "retrieval_eval_v1.json"
RESULTS_DIR = ROOT / ".benchmarks" / "results"

AVAILABLE_MODES = ["dense", "sparse", "hybrid"]


def percentile(values, p):
    ordered = sorted(values)
    index = max(0, math.ceil(p * len(ordered)) - 1)
    return ordered[index]


def get_chunk_ids(response):
    results = response.get("results")

    if not isinstance(results, list):
        raise ValueError(
            f"Expected response.results to be a list. "
            f"Response keys: {list(response.keys())}"
        )

    chunk_ids = []

    for item in results:
        chunk_id = item.get("chunk_id") or item.get("id")

        if not chunk_id:
            raise ValueError(
                f"Could not identify chunk ID. Result keys: {list(item.keys())}"
            )

        chunk_ids.append(str(chunk_id))

    return chunk_ids


def calculate_metrics(retrieved, expected, k):
    retrieved_at_k = retrieved[:k]
    expected_set = set(expected)

    relevant_count = sum(
        1 for chunk_id in retrieved_at_k
        if chunk_id in expected_set
    )

    recall = (
        relevant_count / len(expected_set)
        if expected_set else 0.0
    )

    precision = relevant_count / k

    reciprocal_rank = 0.0

    for rank, chunk_id in enumerate(retrieved_at_k, start=1):
        if chunk_id in expected_set:
            reciprocal_rank = 1.0 / rank
            break

    return {
        "recall_at_k": recall,
        "precision_at_k": precision,
        "mrr": reciprocal_rank,
    }


def validate_response(body, mode, query_id):
    if body.get("success") is False:
        raise RuntimeError(
            f"{query_id} ({mode}) returned unsuccessful response: {body}"
        )

    returned_mode = body.get("retrieval_mode")

    if returned_mode != mode:
        raise RuntimeError(
            f"{query_id}: requested mode '{mode}', "
            f"but received '{returned_mode}'"
        )

    return get_chunk_ids(body)


def build_summary(query_id, query_text, repeats, latencies, metrics, top_k):
    return {
        "query_id": query_id,
        "query": query_text,
        "repeats": repeats,
        "latency_median_ms": round(statistics.median(latencies), 3),
        "latency_p95_ms": round(percentile(latencies, 0.95), 3),
        "recall_at_k": round(
            statistics.mean(m["recall_at_k"] for m in metrics), 4
        ),
        "precision_at_k": round(
            statistics.mean(m["precision_at_k"] for m in metrics), 4
        ),
        "mrr": round(
            statistics.mean(m["mrr"] for m in metrics), 4
        ),
    }


def build_aggregate(mode, dataset, summaries, raw_records,
                    all_latencies, warmups, repeats, top_k):
    return {
        "dataset_name": dataset["dataset_name"],
        "retrieval_mode": mode,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "query_count": len(summaries),
        "total_measured_requests": len(raw_records),
        "warmups_per_query": warmups,
        "repeats_per_query": repeats,
        "top_k": top_k,
        "latency_median_ms": round(
            statistics.median(all_latencies), 3
        ),
        "latency_p95_ms": round(
            percentile(all_latencies, 0.95), 3
        ),
        "mean_recall_at_k": round(
            statistics.mean(s["recall_at_k"] for s in summaries), 4
        ),
        "mean_precision_at_k": round(
            statistics.mean(s["precision_at_k"] for s in summaries), 4
        ),
        "mean_mrr": round(
            statistics.mean(s["mrr"] for s in summaries), 4
        ),
    }


def save_results(mode, timestamp, aggregate, summaries, raw_records):
    raw_path = RESULTS_DIR / f"retrieval_{mode}_{timestamp}.json"
    csv_path = RESULTS_DIR / f"retrieval_{mode}_{timestamp}.csv"

    output = {
        "aggregate": aggregate,
        "per_query": summaries,
        "raw_measurements": raw_records,
    }

    raw_path.write_text(
        json.dumps(output, indent=2),
        encoding="utf-8",
    )

    with csv_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=list(summaries[0].keys()),
        )
        writer.writeheader()
        writer.writerows(summaries)

    return raw_path, csv_path


def main():
    parser = argparse.ArgumentParser(
        description="Compare dense, sparse, and hybrid retrieval."
    )

    parser.add_argument("--repeats", type=int, default=10)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument(
        "--modes",
        nargs="+",
        choices=AVAILABLE_MODES,
        default=AVAILABLE_MODES,
        help="Retrieval modes to benchmark. Defaults to all three.",
    )

    args = parser.parse_args()

    if args.repeats < 1 or args.warmups < 0:
        raise ValueError(
            "Repeats must be positive and warmups cannot be negative."
        )

    supabase_url = os.getenv("SUPABASE_URL")
    function_secret = os.getenv("SUPABASE_FUNCTION_SECRET")

    if not supabase_url or not function_secret:
        raise RuntimeError(
            "Missing SUPABASE_URL or SUPABASE_FUNCTION_SECRET in root .env"
        )

    if not DATASET_PATH.exists():
        raise FileNotFoundError(
            f"Evaluation dataset not found: {DATASET_PATH}"
        )

    endpoint = (
        supabase_url.rstrip("/")
        + "/functions/v1/search-knowledge"
    )

    dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8-sig"))

    top_k = dataset["top_k"]
    queries = dataset["queries"]
    modes = list(dict.fromkeys(args.modes))

    if not queries:
        raise ValueError("Evaluation dataset contains no queries.")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    headers = {
        "Content-Type": "application/json",
        "x-querypilot-secret": function_secret,
    }

    results = {
        mode: {
            "summaries": [],
            "raw_records": [],
            "all_latencies": [],
        }
        for mode in modes
    }

    print(f"Dataset: {dataset['dataset_name']}")
    print(f"Queries: {len(queries)}")
    print(f"Top K: {top_k}")
    print(f"Modes: {', '.join(modes)}")
    print(f"Warm-ups per query per mode: {args.warmups}")
    print(f"Measured repetitions per query per mode: {args.repeats}")
    print(
        f"Total measured requests: "
        f"{len(queries) * len(modes) * args.repeats}"
    )
    print("\nStarting benchmark...\n")

    with httpx.Client(timeout=60.0) as client:
        for query_index, item in enumerate(queries, start=1):
            query_id = item["id"]
            query_text = item["query"]
            expected = item["expected_chunk_ids"]

            print(f"[{query_index}/{len(queries)}] {query_id}: {query_text}")

            # Warm up every selected mode for this query.
            for mode in modes:
                payload = {
                    "query": query_text,
                    "top_k": top_k,
                    "retrieval_mode": mode,
                }

                for _ in range(args.warmups):
                    response = client.post(
                        endpoint,
                        headers=headers,
                        json=payload,
                    )
                    response.raise_for_status()
                    validate_response(response.json(), mode, query_id)

            query_data = {
                mode: {
                    "latencies": [],
                    "metrics": [],
                }
                for mode in modes
            }

            # Interleave modes to reduce ordering-related timing bias.
            for repeat in range(1, args.repeats + 1):
                for mode in modes:
                    payload = {
                        "query": query_text,
                        "top_k": top_k,
                        "retrieval_mode": mode,
                    }

                    started = time.perf_counter()

                    response = client.post(
                        endpoint,
                        headers=headers,
                        json=payload,
                    )

                    latency_ms = (
                        time.perf_counter() - started
                    ) * 1000

                    response.raise_for_status()
                    body = response.json()

                    retrieved = validate_response(
                        body, mode, query_id
                    )

                    metrics = calculate_metrics(
                        retrieved, expected, top_k
                    )

                    query_data[mode]["latencies"].append(latency_ms)
                    query_data[mode]["metrics"].append(metrics)

                    results[mode]["all_latencies"].append(latency_ms)

                    results[mode]["raw_records"].append({
                        "query_id": query_id,
                        "repeat": repeat,
                        "retrieval_mode": mode,
                        "latency_ms": round(latency_ms, 3),
                        "retrieved_chunk_ids": retrieved,
                        "expected_chunk_ids": expected,
                        **metrics,
                    })

            for mode in modes:
                mode_data = query_data[mode]

                summary = build_summary(
                    query_id=query_id,
                    query_text=query_text,
                    repeats=args.repeats,
                    latencies=mode_data["latencies"],
                    metrics=mode_data["metrics"],
                    top_k=top_k,
                )

                results[mode]["summaries"].append(summary)

                print(
                    f"  {mode.upper():<7}"
                    f" Median: {summary['latency_median_ms']} ms"
                    f" | Recall@{top_k}: {summary['recall_at_k']}"
                    f" | Precision@{top_k}: {summary['precision_at_k']}"
                    f" | MRR@{top_k}: {summary['mrr']}"
                )

    print("\n========== COMPARISON RESULTS ==========")

    for mode in modes:
        mode_data = results[mode]

        aggregate = build_aggregate(
            mode=mode,
            dataset=dataset,
            summaries=mode_data["summaries"],
            raw_records=mode_data["raw_records"],
            all_latencies=mode_data["all_latencies"],
            warmups=args.warmups,
            repeats=args.repeats,
            top_k=top_k,
        )

        raw_path, csv_path = save_results(
            mode=mode,
            timestamp=timestamp,
            aggregate=aggregate,
            summaries=mode_data["summaries"],
            raw_records=mode_data["raw_records"],
        )

        print(f"\n--- {mode.upper()} ---")
        print(json.dumps(aggregate, indent=2))
        print(f"Raw results: {raw_path}")
        print(f"CSV report:  {csv_path}")


if __name__ == "__main__":
    main()
