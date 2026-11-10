import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type, x-ingestion-secret",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

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
  const suppliedSecret = request.headers.get("x-ingestion-secret");

  if (!expectedSecret || suppliedSecret !== expectedSecret) {
    return Response.json(
      { success: false, error: "Unauthorized" },
      { status: 401, headers: corsHeaders },
    );
  }

  try {
    const body = await request.json();
    const query = String(body.query ?? "").trim();

    if (!query) {
      return Response.json(
        { success: false, error: "A non-empty query is required" },
        { status: 400, headers: corsHeaders },
      );
    }

    const supabaseUrl = Deno.env.get("SUPABASE_URL");
    const serviceRoleKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");

    if (!supabaseUrl || !serviceRoleKey) {
      throw new Error("Supabase environment configuration is missing");
    }

    const supabase = createClient(supabaseUrl, serviceRoleKey);

    // Keyword retrieval using PostgreSQL full-text search.
    const { data: keywordResults, error: keywordError } =
      await supabase.rpc("search_chunks", {
        search_query: query,
        match_count: 5,
      });

    if (keywordError) {
      throw new Error(`Keyword retrieval failed: ${keywordError.message}`);
    }

    // Generate the query embedding inside Supabase.
    const session = new Supabase.ai.Session("gte-small");
    const embedding = await session.run(query, {
      mean_pool: true,
      normalize: true,
    });

    if (!Array.isArray(embedding) || embedding.length !== 384) {
      throw new Error(
        `Unexpected embedding dimensions: ${
          Array.isArray(embedding) ? embedding.length : "invalid"
        }`,
      );
    }

    // Semantic retrieval using pgvector.
    const { data: semanticResults, error: semanticError } =
      await supabase.rpc("match_chunks", {
        query_embedding: embedding,
        match_count: 5,
        filter_source_id: null,
      });

    if (semanticError) {
      throw new Error(`Semantic retrieval failed: ${semanticError.message}`);
    }

    return Response.json(
      {
        success: true,
        query,
        embedding_dimensions: embedding.length,
        keyword: {
          count: keywordResults?.length ?? 0,
          results: keywordResults ?? [],
        },
        semantic: {
          count: semanticResults?.length ?? 0,
          results: semanticResults ?? [],
        },
      },
      { headers: corsHeaders },
    );
  } catch (error) {
    return Response.json(
      {
        success: false,
        error: error instanceof Error ? error.message : String(error),
      },
      { status: 500, headers: corsHeaders },
    );
  }
});
