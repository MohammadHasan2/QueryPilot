import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers":
    "authorization, x-client-info, apikey, content-type, x-querypilot-secret",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

const RRF_K = 60;
const FETCH_COUNT = 10;
const DEFAULT_TOP_K = 8;

type RetrievalMode = "dense" | "sparse" | "hybrid";

type SearchResult = {
  chunk_id: string;
  source_id: string;
  chunk_text: string;
  section_title: string | null;
  source_url: string;
  source_title: string;
  similarity?: number;
  rank?: number;
};

type FusedResult = SearchResult & {
  semantic_rank: number | null;
  keyword_rank: number | null;
  rrf_score: number;
};

type OptimizationCase = {
  case_id: string;
  title: string;
  problem_pattern: string;
  relevant_conditions: unknown;
  suggested_optimization: string;
  expected_tradeoffs: string | null;
  validation_requirements: unknown;
  supporting_source_ids: string[];
  rank: number;
};

function buildCaseSearchTerms(
  sql: string,
  statementType: string,
): string[] {
  const terms: string[] = [];
  const normalized = sql.toLowerCase();

  const add = (term: string) => {
    if (!terms.includes(term)) {
      terms.push(term);
    }
  };

  // Only add signals that are explicitly present in SQL.
  if (/\bwhere\b/i.test(normalized)) {
    add("filter");
  }

  if (/\bjoin\b/i.test(normalized)) {
    add("join");
  }

  if (/\bover\s*\(/i.test(normalized)) {
    add("window");
  }

  if (/^\s*insert\b/i.test(normalized)) {
    add("insert");
  }

  if (/^\s*update\b/i.test(normalized)) {
    add("update");
  }

  if (/^\s*delete\b/i.test(normalized)) {
    add("delete");
  }

  if (/\bon\s+conflict\b/i.test(normalized)) {
    add("conflict");
  }

  if (/\bcreate\s+(unique\s+)?index\b/i.test(normalized)) {
    add("index");
  }

  if (/\bgroup\s+by\b/i.test(normalized)) {
    add("group");
  }

  if (/\border\s+by\b/i.test(normalized)) {
    add("sort");
  }

  if (/\bwith\b/i.test(normalized)) {
    add("cte");
  }

  // Statement category is a trusted parser result.
  const type = statementType.toUpperCase();

  if (type === "INSERT") add("insert");
  if (type === "UPDATE") add("update");
  if (type === "DELETE") add("delete");
  if (type === "MERGE") add("merge");

  return terms;
}

function isCaseRelevant(
  item: OptimizationCase,
  signals: string[],
): boolean {
  const text = `${item.title} ${item.problem_pattern}`.toLowerCase();

  const requiredSignals: Record<string, RegExp> = {
    join: /\bjoin\b/,
    window: /\bwindow\b/,
    insert: /\binsert\b|\bconflict\b/,
    update: /\bupdate\b/,
    delete: /\bdelete\b/,
    merge: /\bmerge\b/,
    cte: /\bcte\b|common table expression/,
  };

  for (const [signal, pattern] of Object.entries(requiredSignals)) {
    if (pattern.test(text) && !signals.includes(signal)) {
      return false;
    }
  }

  return true;
}

Deno.serve(async (request: Request) => {
  if (request.method === "OPTIONS") {
    return new Response("ok", { headers: corsHeaders });
  }

  if (request.method !== "POST") {
    return Response.json(
      { success: false, error: "Method not allowed" },
      { status: 405, headers: corsHeaders },
    );
  }

  const expectedSecret = Deno.env.get("INGESTION_SECRET");
  const suppliedSecret = request.headers.get("x-querypilot-secret");

  if (!expectedSecret || suppliedSecret !== expectedSecret) {
    return Response.json(
      { success: false, error: "Unauthorized" },
      { status: 401, headers: corsHeaders },
    );
  }

  try {
    const body = await request.json();

    const query = String(body.query ?? "").trim();
    const sql = String(body.sql ?? "").trim();
    const statementType = String(body.statement_type ?? "").trim();

    const topK = Math.max(
      1,
      Math.min(Number(body.top_k ?? DEFAULT_TOP_K), 20),
    );

    const mode = String(
      body.retrieval_mode ?? "hybrid",
    ) as RetrievalMode;

    if (!["dense", "sparse", "hybrid"].includes(mode)) {
      return Response.json(
        {
          success: false,
          error: "retrieval_mode must be dense, sparse, or hybrid",
        },
        { status: 400, headers: corsHeaders },
      );
    }

    if (!query || query.length > 2000) {
      return Response.json(
        {
          success: false,
          error: "Query must contain between 1 and 2000 characters",
        },
        { status: 400, headers: corsHeaders },
      );
    }

    if (sql.length > 10000 || statementType.length > 50) {
      return Response.json(
        {
          success: false,
          error: "SQL or statement type exceeds the allowed length",
        },
        { status: 400, headers: corsHeaders },
      );
    }

    const supabaseUrl = Deno.env.get("SUPABASE_URL");
    const serviceRoleKey = Deno.env.get(
      "SUPABASE_SERVICE_ROLE_KEY",
    );

    if (!supabaseUrl || !serviceRoleKey) {
      throw new Error(
        "Supabase environment configuration is missing",
      );
    }

    const supabase = createClient(
      supabaseUrl,
      serviceRoleKey,
    );

    let semanticResults: SearchResult[] = [];
    let keywordResults: SearchResult[] = [];
    let embeddingDimensions: number | null = null;

    // Optimization cases use SQL-only signals.
    let optimizationCases: OptimizationCase[] = [];
    let optimizationCasesStatus = "ok";

    const caseTerms = sql
      ? buildCaseSearchTerms(sql, statementType)
      : [];

    if (caseTerms.length > 0) {
      const responses = await Promise.all(
        caseTerms.map(async (term) => {
          const response = await supabase.rpc(
            "search_optimization_cases",
            {
              search_query: term,
              match_count: Math.min(topK, 5),
            },
          );

          if (response.error) {
            console.error(
              `Optimization case search failed for "${term}":`,
              response.error.message,
            );

            return {
              data: [] as OptimizationCase[],
              failed: true,
            };
          }

          return {
            data: (response.data ?? []) as OptimizationCase[],
            failed: false,
          };
        }),
      );

      const uniqueCases = new Map<string, OptimizationCase>();

      for (const response of responses) {
        for (const item of response.data) {
          const existing = uniqueCases.get(item.case_id);

          if (
            !existing ||
            Number(item.rank ?? 0) > Number(existing.rank ?? 0)
          ) {
            uniqueCases.set(item.case_id, item);
          }
        }
      }

      optimizationCases = Array.from(uniqueCases.values())
        .filter((item) => isCaseRelevant(item, caseTerms))
        .sort(
          (a, b) => Number(b.rank ?? 0) - Number(a.rank ?? 0),
        )
        .slice(0, Math.min(topK, 5));

      if (responses.every((response) => response.failed)) {
        optimizationCasesStatus = "unavailable";
      } else if (responses.some((response) => response.failed)) {
        optimizationCasesStatus = "partial";
      }
    } else {
      optimizationCasesStatus = "no_query_terms";
    }

    // Dense documentation retrieval.
    if (mode === "dense" || mode === "hybrid") {
      const session = new Supabase.ai.Session("gte-small");

      const embedding = await session.run(query, {
        mean_pool: true,
        normalize: true,
      });

      if (
        !Array.isArray(embedding) ||
        embedding.length !== 384
      ) {
        throw new Error(
          "Query embedding must contain exactly 384 dimensions",
        );
      }

      embeddingDimensions = embedding.length;

      const semanticResponse = await supabase.rpc(
        "match_chunks",
        {
          query_embedding: embedding,
          match_count: FETCH_COUNT,
          filter_source_id: null,
        },
      );

      if (semanticResponse.error) {
        throw new Error(
          `Semantic retrieval failed: ${semanticResponse.error.message}`,
        );
      }

      semanticResults = (
        semanticResponse.data ?? []
      ) as SearchResult[];

      semanticResults.sort(
        (a, b) =>
          Number(b.similarity ?? 0) -
          Number(a.similarity ?? 0),
      );
    }

    // Sparse documentation retrieval.
    if (mode === "sparse" || mode === "hybrid") {
      const keywordResponse = await supabase.rpc(
        "search_chunks",
        {
          search_query: query,
          match_count: FETCH_COUNT,
        },
      );

      if (keywordResponse.error) {
        throw new Error(
          `Keyword retrieval failed: ${keywordResponse.error.message}`,
        );
      }

      keywordResults = (
        keywordResponse.data ?? []
      ) as SearchResult[];

      keywordResults.sort(
        (a, b) =>
          Number(b.rank ?? 0) -
          Number(a.rank ?? 0),
      );
    }

    // Reciprocal Rank Fusion.
    let results: FusedResult[] = [];

    if (mode === "hybrid") {
      const fused = new Map<string, FusedResult>();

      semanticResults.forEach((result, index) => {
        const rank = index + 1;
        const existing = fused.get(result.chunk_id);

        fused.set(result.chunk_id, {
          ...result,
          semantic_rank: rank,
          keyword_rank: existing?.keyword_rank ?? null,
          rrf_score:
            (existing?.rrf_score ?? 0) +
            1 / (RRF_K + rank),
        });
      });

      keywordResults.forEach((result, index) => {
        const rank = index + 1;
        const existing = fused.get(result.chunk_id);

        fused.set(result.chunk_id, {
          ...(existing ?? {
            ...result,
            semantic_rank: null,
            keyword_rank: null,
            rrf_score: 0,
          }),
          keyword_rank: rank,
          rrf_score:
            (existing?.rrf_score ?? 0) +
            1 / (RRF_K + rank),
        });
      });

      results = Array.from(fused.values())
        .sort((a, b) => b.rrf_score - a.rrf_score)
        .slice(0, topK);
    } else {
      const selected = mode === "dense"
        ? semanticResults
        : keywordResults;

      results = selected.slice(0, topK).map(
        (result, index) => ({
          ...result,
          semantic_rank: mode === "dense"
            ? index + 1
            : null,
          keyword_rank: mode === "sparse"
            ? index + 1
            : null,
          rrf_score: 0,
        }),
      );
    }

    return Response.json(
      {
        success: true,
        query,
        retrieval_mode: mode,
        embedding_dimensions: embeddingDimensions,
        optimization_case_queries: caseTerms,

        ranking: mode === "hybrid"
          ? {
            algorithm: "Reciprocal Rank Fusion",
            k: RRF_K,
            semantic_weight: 1,
            keyword_weight: 1,
            fetched_per_method: FETCH_COUNT,
          }
          : {
            algorithm: mode === "dense"
              ? "Cosine similarity"
              : "PostgreSQL full-text search",
            fetched_per_method: FETCH_COUNT,
          },

        counts: {
          semantic: semanticResults.length,
          keyword: keywordResults.length,
          unique_candidates: mode === "hybrid"
            ? new Set([
              ...semanticResults.map((item) => item.chunk_id),
              ...keywordResults.map((item) => item.chunk_id),
            ]).size
            : results.length,
          returned: results.length,
          optimization_cases: optimizationCases.length,
        },

        results,
        optimization_cases: optimizationCases,
        optimization_cases_status: optimizationCasesStatus,
      },
      { headers: corsHeaders },
    );
  } catch (error) {
    console.error("Search knowledge failed:", error);

    return Response.json(
      {
        success: false,
        error: error instanceof Error
          ? error.message
          : String(error),
      },
      { status: 500, headers: corsHeaders },
    );
  }
});
