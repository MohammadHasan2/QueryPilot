
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const SUPABASE_SERVICE_ROLE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const INGESTION_SECRET = Deno.env.get("INGESTION_SECRET")!;

if (!SUPABASE_URL || !SUPABASE_SERVICE_ROLE_KEY || !INGESTION_SECRET) {
  throw new Error("Missing required Supabase environment variables.");
}

const supabase = createClient(
  SUPABASE_URL,
  SUPABASE_SERVICE_ROLE_KEY,
  {
    auth: {
      persistSession: false,
      autoRefreshToken: false,
    },
  },
);

const model = new Supabase.ai.Session("gte-small");

const SOURCES = [
  // Existing sources — preserve their indexes.
  {
    url: "https://www.postgresql.org/docs/current/using-explain.html",
    title: "Using EXPLAIN",
    publisher: "PostgreSQL Documentation",
  },
  {
    url: "https://www.postgresql.org/docs/current/indexes.html",
    title: "PostgreSQL Indexes",
    publisher: "PostgreSQL Documentation",
  },
  {
    url: "https://www.postgresql.org/docs/current/sql-analyze.html",
    title: "PostgreSQL ANALYZE",
    publisher: "PostgreSQL Documentation",
  },

  // DML: data manipulation.
  {
    url: "https://www.postgresql.org/docs/current/sql-insert.html",
    title: "PostgreSQL INSERT",
    publisher: "PostgreSQL Documentation",
  },
  {
    url: "https://www.postgresql.org/docs/current/sql-update.html",
    title: "PostgreSQL UPDATE",
    publisher: "PostgreSQL Documentation",
  },
  {
    url: "https://www.postgresql.org/docs/current/sql-delete.html",
    title: "PostgreSQL DELETE",
    publisher: "PostgreSQL Documentation",
  },
  {
    url: "https://www.postgresql.org/docs/current/sql-merge.html",
    title: "PostgreSQL MERGE",
    publisher: "PostgreSQL Documentation",
  },

  // Index and schema design.
  {
    url: "https://www.postgresql.org/docs/current/sql-createindex.html",
    title: "PostgreSQL CREATE INDEX",
    publisher: "PostgreSQL Documentation",
  },
  {
    url: "https://www.postgresql.org/docs/current/sql-createtable.html",
    title: "PostgreSQL CREATE TABLE",
    publisher: "PostgreSQL Documentation",
  },
  {
    url: "https://www.postgresql.org/docs/current/sql-altertable.html",
    title: "PostgreSQL ALTER TABLE",
    publisher: "PostgreSQL Documentation",
  },
  {
    url: "https://www.postgresql.org/docs/current/ddl-constraints.html",
    title: "PostgreSQL Constraints",
    publisher: "PostgreSQL Documentation",
  },

  // Query design.
  {
    url: "https://www.postgresql.org/docs/current/queries-table-expressions.html",
    title: "PostgreSQL Table Expressions and Joins",
    publisher: "PostgreSQL Documentation",
  },
  {
    url: "https://www.postgresql.org/docs/current/queries-with.html",
    title: "PostgreSQL WITH Queries and CTEs",
    publisher: "PostgreSQL Documentation",
  },
  {
    url: "https://www.postgresql.org/docs/current/functions-window.html",
    title: "PostgreSQL Window Functions",
    publisher: "PostgreSQL Documentation",
  },
  {
    url: "https://www.postgresql.org/docs/current/ddl-partitioning.html",
    title: "PostgreSQL Table Partitioning",
    publisher: "PostgreSQL Documentation",
  },
];

const CHUNK_SIZE = 1400;
const CHUNK_OVERLAP = 150;
const MAX_NEW_EMBEDDINGS_PER_RUN = 2;

type TextChunk = {
  text: string;
  sectionTitle: string;
  index: number;
};

type IngestionStage =
  | "start"
  | "fetch_document"
  | "extract_text"
  | "hash_document"
  | "read_source"
  | "create_source"
  | "update_source"
  | "read_existing_chunks"
  | "hash_chunks"
  | "generate_embedding"
  | "validate_embedding"
  | "upsert_chunk"
  | "update_progress"
  | "remove_stale_chunks"
  | "complete_source";

class IngestionError extends Error {
  stage: IngestionStage;

  constructor(stage: IngestionStage, message: string) {
    super(message);
    this.name = "IngestionError";
    this.stage = stage;
  }
}

async function hashText(value: string): Promise<string> {
  const data = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", data);

  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

function extractText(html: string): string {
  let text = html
    .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, " ")
    .replace(/<style\b[^>]*>[\s\S]*?<\/style>/gi, " ")
    .replace(/<nav\b[^>]*>[\s\S]*?<\/nav>/gi, " ")
    .replace(/<header\b[^>]*>[\s\S]*?<\/header>/gi, " ")
    .replace(/<footer\b[^>]*>[\s\S]*?<\/footer>/gi, " ")
    .replace(/<!--[\s\S]*?-->/g, " ");

  text = text
    .replace(/<h1\b[^>]*>/gi, "\n\n## ")
    .replace(/<h2\b[^>]*>/gi, "\n\n## ")
    .replace(/<h3\b[^>]*>/gi, "\n\n## ")
    .replace(/<\/h[1-3]>/gi, "\n\n")
    .replace(/<\/(p|pre|li|div|section|article|tr)>/gi, "\n\n")
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<[^>]+>/g, " ");

  text = text
    .replace(/&nbsp;/gi, " ")
    .replace(/&amp;/gi, "&")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/&quot;/gi, '"')
    .replace(/&#39;|&apos;/gi, "'")
    .replace(/&#(\d+);/g, (_, code) =>
      String.fromCodePoint(Number(code))
    )
    .replace(/&#x([0-9a-f]+);/gi, (_, code) =>
      String.fromCodePoint(parseInt(code, 16))
    );

  return text
    .replace(/[ \t]+/g, " ")
    .replace(/\n[ \t]+/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function chunkText(content: string): TextChunk[] {
  const paragraphs = content
    .split(/\n{2,}/)
    .map((paragraph) => paragraph.trim())
    .filter(Boolean);

  const chunks: TextChunk[] = [];
  let currentText = "";
  let currentSection = "General";
  let index = 0;

  function pushChunk(value: string, section: string) {
    const cleaned = value.trim();

    if (cleaned.length < 40) return;

    chunks.push({
      text: cleaned,
      sectionTitle: section,
      index: index++,
    });
  }

  for (const paragraph of paragraphs) {
    if (paragraph.startsWith("## ")) {
      if (currentText.trim()) {
        pushChunk(currentText, currentSection);
        currentText = "";
      }

      currentSection = paragraph.replace(/^##\s*/, "").trim();
      continue;
    }

    if (paragraph.length > CHUNK_SIZE) {
      if (currentText.trim()) {
        pushChunk(currentText, currentSection);
        currentText = "";
      }

      let start = 0;

      while (start < paragraph.length) {
        const end = Math.min(start + CHUNK_SIZE, paragraph.length);
        pushChunk(paragraph.slice(start, end), currentSection);

        if (end >= paragraph.length) break;

        start = end - CHUNK_OVERLAP;
      }

      continue;
    }

    const combined = currentText
      ? `${currentText}\n\n${paragraph}`
      : paragraph;

    if (combined.length > CHUNK_SIZE) {
      pushChunk(currentText, currentSection);
      currentText = paragraph;
    } else {
      currentText = combined;
    }
  }

  if (currentText.trim()) {
    pushChunk(currentText, currentSection);
  }

  return chunks;
}

async function ingestSource(sourceIndex: number) {
  let stage: IngestionStage = "start";

  const source = SOURCES[sourceIndex];

  if (!source) {
    throw new IngestionError(
      "start",
      `Invalid source index: ${sourceIndex}`,
    );
  }

  try {
    stage = "fetch_document";

    const response = await fetch(source.url, {
      headers: {
        "User-Agent": "QueryPilot Knowledge Ingestion/1.0",
      },
    });

    if (!response.ok) {
      throw new Error(
        `Document request failed: HTTP ${response.status}`,
      );
    }

    const html = await response.text();

    if (!html || html.length < 200) {
      throw new Error("Downloaded document is empty or too short.");
    }

    stage = "extract_text";

    const extractedText = extractText(html);

    if (extractedText.length < 200) {
      throw new Error(
        `Extracted document is too short: ${extractedText.length} characters.`,
      );
    }

    stage = "hash_document";

    const documentHash = await hashText(extractedText);

    const chunks = chunkText(extractedText);

    if (chunks.length === 0) {
      throw new Error("No usable chunks were produced.");
    }

    stage = "read_source";

    const { data: existingSource, error: readSourceError } =
      await supabase
        .from("knowledge_sources")
        .select(
          "id, content_hash, ingestion_status, ingestion_cursor",
        )
        .eq("url", source.url)
        .maybeSingle();

    if (readSourceError) {
      throw new Error(
        `Could not read source: ${readSourceError.message}`,
      );
    }

    let sourceId: string;
    let cursor = 0;

    if (!existingSource) {
      stage = "create_source";

      const { data: insertedSource, error: insertSourceError } =
        await supabase
          .from("knowledge_sources")
          .insert({
            url: source.url,
            title: source.title,
            publisher: source.publisher,
            source_type: "documentation",
            content_hash: documentHash,
            ingestion_status: "pending",
            ingestion_cursor: 0,
          })
          .select("id")
          .single();

      if (insertSourceError) {
        throw new Error(
          `Could not create source: ${insertSourceError.message}`,
        );
      }

      if (!insertedSource?.id) {
        throw new Error("Source insert returned no source ID.");
      }

      sourceId = insertedSource.id;
    } else {
      sourceId = existingSource.id;

      if (!sourceId) {
        throw new Error("Existing source has no ID.");
      }

      if (
        existingSource.content_hash === documentHash &&
        existingSource.ingestion_status === "completed"
      ) {
        return {
          completed: true,
          alreadyCompleted: true,
          title: source.title,
          sourceId,
          chunkCount: chunks.length,
          insertedThisRun: 0,
        };
      }

      cursor =
        existingSource.content_hash === documentHash
          ? Math.max(0, Number(existingSource.ingestion_cursor) || 0)
          : 0;

      cursor = Math.min(cursor, chunks.length);
    }

    stage = "update_source";

    const { error: updateSourceError } = await supabase
      .from("knowledge_sources")
      .update({
        title: source.title,
        publisher: source.publisher,
        source_type: "documentation",
        content_hash: documentHash,
        ingestion_status: "pending",
        ingestion_cursor: cursor,
      })
      .eq("id", sourceId);

    if (updateSourceError) {
      throw new Error(
        `Could not update source: ${updateSourceError.message}`,
      );
    }

    stage = "read_existing_chunks";

    const { data: existingChunks, error: existingChunksError } =
      await supabase
        .from("knowledge_chunks")
        .select("content_hash")
        .eq("source_id", sourceId);

    if (existingChunksError) {
      throw new Error(
        `Could not read existing chunks: ${existingChunksError.message}`,
      );
    }

    const existingHashes = new Set(
      (existingChunks ?? [])
        .map((item) => item.content_hash)
        .filter((value): value is string => typeof value === "string"),
    );

    stage = "hash_chunks";

    const chunkHashes: string[] = [];

    for (const chunk of chunks) {
      chunkHashes.push(await hashText(chunk.text));
    }

    let insertedThisRun = 0;

    while (cursor < chunks.length) {
      const chunk = chunks[cursor];
      const contentHash = chunkHashes[cursor];

      if (existingHashes.has(contentHash)) {
        cursor++;
        continue;
      }

      stage = "generate_embedding";

      const embeddingResult = await model.run(chunk.text, {
        mean_pool: true,
        normalize: true,
      });

      stage = "validate_embedding";

      const embedding = Array.isArray(embeddingResult)
        ? embeddingResult
        : (embeddingResult as { embedding?: number[] })?.embedding;

      if (!embedding || embedding.length !== 384) {
        throw new Error(
          `Expected 384-dimensional embedding; received ${
            embedding?.length ?? "no embedding"
          }.`,
        );
      }

      if (!embedding.every((value) => Number.isFinite(value))) {
        throw new Error("Embedding contains invalid numeric values.");
      }

      stage = "upsert_chunk";

      const { error: upsertError } = await supabase
        .from("knowledge_chunks")
        .upsert(
          {
            source_id: sourceId,
            chunk_text: chunk.text,
            section_title: chunk.sectionTitle,
            content_hash: contentHash,
            location_metadata: {
              chunk_index: cursor,
              source_url: source.url,
            },
            embedding: Array.from(embedding),
          },
          {
            onConflict: "source_id,content_hash",
            ignoreDuplicates: false,
          },
        );

      if (upsertError) {
        throw new Error(
          `Could not save chunk ${cursor}: ${upsertError.message}`,
        );
      }

      existingHashes.add(contentHash);
      insertedThisRun++;
      cursor++;

      if (insertedThisRun >= MAX_NEW_EMBEDDINGS_PER_RUN) {
        break;
      }
    }

    if (cursor >= chunks.length) {
      stage = "remove_stale_chunks";

      const currentHashes = new Set(chunkHashes);

      const staleHashes = (existingChunks ?? [])
        .map((item) => item.content_hash)
        .filter(
          (hash): hash is string =>
            typeof hash === "string" && !currentHashes.has(hash),
        );

      if (staleHashes.length > 0) {
        const { error: deleteError } = await supabase
          .from("knowledge_chunks")
          .delete()
          .eq("source_id", sourceId)
          .in("content_hash", staleHashes);

        if (deleteError) {
          throw new Error(
            `Could not remove stale chunks: ${deleteError.message}`,
          );
        }
      }

      stage = "complete_source";

      const { error: completeError } = await supabase
        .from("knowledge_sources")
        .update({
          ingestion_status: "completed",
          ingestion_cursor: chunks.length,
          last_ingested_at: new Date().toISOString(),
        })
        .eq("id", sourceId);

      if (completeError) {
        throw new Error(
          `Could not mark source completed: ${completeError.message}`,
        );
      }

      return {
        completed: true,
        alreadyCompleted: false,
        title: source.title,
        sourceId,
        chunkCount: chunks.length,
        insertedThisRun,
        totalExistingChunks: existingHashes.size,
      };
    }

    stage = "update_progress";

    const { error: progressError } = await supabase
      .from("knowledge_sources")
      .update({
        ingestion_status: "pending",
        ingestion_cursor: cursor,
      })
      .eq("id", sourceId);

    if (progressError) {
      throw new Error(
        `Could not save ingestion progress: ${progressError.message}`,
      );
    }

    return {
      completed: false,
      title: source.title,
      sourceId,
      chunkCount: chunks.length,
      insertedThisRun,
      nextCursor: cursor,
      remainingChunks: chunks.length - cursor,
    };
  } catch (error) {
    const message = error instanceof Error
      ? error.message
      : String(error);

    const stack = error instanceof Error ? error.stack : undefined;

    throw new IngestionError(
      stage,
      `${message}${stack ? `\n${stack}` : ""}`,
    );
  }
}

Deno.serve(async (request: Request) => {
  if (request.method !== "POST") {
    return new Response(
      JSON.stringify({
        success: false,
        error: "Method not allowed. Use POST.",
      }),
      {
        status: 405,
        headers: { "Content-Type": "application/json" },
      },
    );
  }

  const suppliedSecret = request.headers.get("x-ingestion-secret");

  if (!suppliedSecret || suppliedSecret !== INGESTION_SECRET) {
    return new Response(
      JSON.stringify({
        success: false,
        error: "Unauthorized.",
      }),
      {
        status: 401,
        headers: { "Content-Type": "application/json" },
      },
    );
  }

  let sourceIndex = 0;

  try {
    const body = await request.json().catch(() => ({}));

    if (body.sourceIndex !== undefined) {
      sourceIndex = Number(body.sourceIndex);
    }

    if (
      !Number.isInteger(sourceIndex) ||
      sourceIndex < 0 ||
      sourceIndex >= SOURCES.length
    ) {
      return new Response(
        JSON.stringify({
          success: false,
          error: `sourceIndex must be between 0 and ${SOURCES.length - 1}.`,
        }),
        {
          status: 400,
          headers: { "Content-Type": "application/json" },
        },
      );
    }

    const result = await ingestSource(sourceIndex);

    return new Response(
      JSON.stringify({
        success: true,
        model: "gte-small",
        dimensions: 384,
        sourceIndex,
        nextSourceIndex:
          result.completed && sourceIndex < SOURCES.length - 1
            ? sourceIndex + 1
            : null,
        result,
      }),
      {
        status: 200,
        headers: { "Content-Type": "application/json" },
      },
    );
  } catch (error) {
    const details = error instanceof Error
      ? {
        name: error.name,
        message: error.message,
        stack: error.stack,
        stage: error instanceof IngestionError
          ? error.stage
          : "request_handler",
      }
      : {
        name: "UnknownError",
        message: String(error),
        stage: "request_handler",
      };

    console.error("Knowledge ingestion failed:", details);

    return new Response(
      JSON.stringify({
        success: false,
        sourceIndex,
        stage: details.stage,
        error: details.message,
        stack: details.stack,
      }),
      {
        status: 500,
        headers: { "Content-Type": "application/json" },
      },
    );
  }
});
