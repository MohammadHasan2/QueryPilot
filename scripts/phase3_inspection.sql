SELECT jsonb_pretty(jsonb_build_object(
    'tables', (
        SELECT jsonb_agg(table_name ORDER BY table_name)
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name IN (
              'knowledge_sources',
              'knowledge_chunks',
              'optimization_cases'
          )
    ),
    'functions', (
        SELECT jsonb_agg(p.proname ORDER BY p.proname)
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public'
          AND p.proname IN ('match_chunks', 'search_chunks')
    ),
    'sources', (
        SELECT jsonb_agg(jsonb_build_object(
            'title', title,
            'url', url,
            'status', ingestion_status
        ))
        FROM public.knowledge_sources
    ),
    'curated_cases', (
        SELECT count(*) FROM public.optimization_cases
    )
)) AS inspection;
