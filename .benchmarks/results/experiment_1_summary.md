# Experiment 1 — Dense vs Sparse vs Hybrid

Dataset: querypilot_retrieval_eval_v1
Queries: 10
Top K: 5
Measured repetitions: 10 per query per mode
Warmups: 1 per query per mode
Measured requests: 300

| Metric | Dense | Sparse | Hybrid |
|---|---:|---:|---:|
| Median latency (ms) | 281.577 | 164.923 | 307.546 |
| P95 latency (ms) | 419.017 | 261.785 | 490.608 |
| Recall@5 | 0.9333 | 0.0333 | 0.9333 |
| Precision@5 | 0.24 | 0.02 | 0.24 |
| MRR@5 | 0.7533 | 0.1000 | 0.7333 |

## Findings

- Dense retrieval achieved the highest MRR@5 and matched hybrid Recall@5.
- Sparse retrieval had the lowest latency but substantially lower retrieval quality.
- Hybrid did not improve measured relevance over dense retrieval.
- Hybrid incurred additional latency compared with dense retrieval.

## Decision

Keep hybrid as the current application default until the remaining
retrieval experiments are completed. Do not claim hybrid superiority.

## Limitations

- Evaluation uses 10 labeled queries.
- Results describe this dataset and configuration only.
- No statistical significance or generalization claim is made.
