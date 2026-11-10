ALTER TABLE public.knowledge_sources
ADD COLUMN IF NOT EXISTS ingestion_cursor INTEGER NOT NULL DEFAULT 0;

COMMENT ON COLUMN public.knowledge_sources.ingestion_cursor
IS 'Next knowledge chunk index to process during resumable ingestion';
