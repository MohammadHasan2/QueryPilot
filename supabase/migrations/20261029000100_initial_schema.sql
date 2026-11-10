create extension if not exists vector with schema extensions;

create table if not exists public.knowledge_sources (
    id uuid primary key default gen_random_uuid(),
    url text not null unique,
    title text not null,
    publisher text,
    source_type text not null default 'documentation',
    source_version text,
    content_hash text,
    ingestion_status text not null default 'pending'
        check (ingestion_status in ('pending', 'completed', 'failed')),
    last_ingested_at timestamptz,
    created_at timestamptz not null default now()
);

create table if not exists public.knowledge_chunks (
    id uuid primary key default gen_random_uuid(),
    source_id uuid not null references public.knowledge_sources(id)
        on delete cascade,
    chunk_text text not null,
    section_title text,
    location_metadata jsonb not null default '{}'::jsonb,
    embedding extensions.vector(384),
    search_vector tsvector generated always as
        (to_tsvector('english', coalesce(chunk_text, ''))) stored,
    content_hash text not null,
    created_at timestamptz not null default now(),
    unique (source_id, content_hash)
);

create index if not exists knowledge_chunks_search_idx
    on public.knowledge_chunks using gin (search_vector);

create index if not exists knowledge_chunks_source_idx
    on public.knowledge_chunks (source_id);

create table if not exists public.optimization_cases (
    id uuid primary key default gen_random_uuid(),
    title text not null,
    problem_pattern text not null,
    relevant_conditions jsonb not null default '{}'::jsonb,
    suggested_optimization text not null,
    expected_tradeoffs text,
    validation_requirements jsonb not null default '[]'::jsonb,
    supporting_source_ids uuid[] not null default '{}',
    created_at timestamptz not null default now()
);

create table if not exists public.scenarios (
    id text primary key,
    title text not null,
    description text not null,
    metadata jsonb not null default '{}'::jsonb,
    enabled boolean not null default true,
    created_at timestamptz not null default now()
);

create table if not exists public.investigations (
    id uuid primary key default gen_random_uuid(),
    query_text text not null,
    status text not null default 'pending'
        check (status in (
            'pending', 'running', 'completed', 'failed',
            'budget_exhausted', 'rate_limited', 'ai_deferred'
        )),
    plan_summary jsonb,
    evidence jsonb not null default '[]'::jsonb,
    recommendation jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.benchmark_runs (
    id uuid primary key default gen_random_uuid(),
    investigation_id uuid references public.investigations(id)
        on delete set null,
    scenario_id text references public.scenarios(id),
    run_type text not null check (run_type in ('baseline', 'candidate')),
    execution_time_ms double precision,
    p95_execution_time_ms double precision,
    plan_summary jsonb,
    result_equivalent boolean,
    environment_details jsonb not null default '{}'::jsonb,
    status text not null default 'pending',
    error_message text,
    created_at timestamptz not null default now()
);

create table if not exists public.llm_usage_events (
    id uuid primary key default gen_random_uuid(),
    investigation_id uuid references public.investigations(id)
        on delete set null,
    model text not null,
    estimated_tokens integer not null default 0,
    actual_tokens integer,
    latency_ms integer,
    status text not null,
    quota_decision text,
    provider_status_code integer,
    created_at timestamptz not null default now()
);

create table if not exists public.evaluation_runs (
    id uuid primary key default gen_random_uuid(),
    experiment_name text not null,
    dataset_version text not null,
    configuration jsonb not null default '{}'::jsonb,
    metrics jsonb not null default '{}'::jsonb,
    report_reference text,
    started_at timestamptz not null default now(),
    completed_at timestamptz
);

create index if not exists knowledge_chunks_embedding_hnsw_idx
    on public.knowledge_chunks
    using hnsw (embedding extensions.vector_cosine_ops)
    where embedding is not null;

create or replace function public.match_chunks(
    query_embedding extensions.vector(384),
    match_count integer default 5,
    filter_source_id uuid default null
)
returns table (
    chunk_id uuid,
    source_id uuid,
    chunk_text text,
    section_title text,
    source_url text,
    source_title text,
    similarity double precision
)
language sql
stable
set search_path = ''
as $$
    select
        kc.id,
        kc.source_id,
        kc.chunk_text,
        kc.section_title,
        ks.url,
        ks.title,
        (1 - (kc.embedding OPERATOR(extensions.<=>) query_embedding))::double precision
    from public.knowledge_chunks as kc
    join public.knowledge_sources as ks on ks.id = kc.source_id
    where kc.embedding is not null
      and (filter_source_id is null or kc.source_id = filter_source_id)
    order by kc.embedding OPERATOR(extensions.<=>) query_embedding
    limit greatest(1, least(coalesce(match_count, 5), 20));
$$;

create or replace function public.search_chunks(
    search_query text,
    match_count integer default 5
)
returns table (
    chunk_id uuid,
    source_id uuid,
    chunk_text text,
    section_title text,
    source_url text,
    source_title text,
    rank real
)
language sql
stable
set search_path = ''
as $$
    select
        kc.id,
        kc.source_id,
        kc.chunk_text,
        kc.section_title,
        ks.url,
        ks.title,
        ts_rank_cd(
            kc.search_vector,
            websearch_to_tsquery('english', search_query)
        ) as rank
    from public.knowledge_chunks as kc
    join public.knowledge_sources as ks on ks.id = kc.source_id
    where kc.search_vector @@ websearch_to_tsquery('english', search_query)
    order by rank desc
    limit greatest(1, least(coalesce(match_count, 5), 20));
$$;

-- Enable RLS. No browser-role policies are created in this phase:
-- direct anon/authenticated access remains denied by default.
alter table public.knowledge_sources enable row level security;
alter table public.knowledge_chunks enable row level security;
alter table public.optimization_cases enable row level security;
alter table public.scenarios enable row level security;
alter table public.investigations enable row level security;
alter table public.benchmark_runs enable row level security;
alter table public.llm_usage_events enable row level security;
alter table public.evaluation_runs enable row level security;

revoke all on function public.match_chunks(extensions.vector, integer, uuid)
    from public, anon, authenticated;
revoke all on function public.search_chunks(text, integer)
    from public, anon, authenticated;

grant execute on function public.match_chunks(extensions.vector, integer, uuid)
    to service_role;
grant execute on function public.search_chunks(text, integer)
    to service_role;

insert into public.scenarios (id, title, description, metadata)
values (
    'ecommerce-orders',
    'E-commerce Orders',
    'Isolated lab scenario for investigating order lookup and join performance.',
    '{"version": 1, "status": "initial"}'::jsonb
)
on conflict (id) do nothing;

insert into public.knowledge_sources
    (url, title, publisher, source_type, ingestion_status)
values (
    'https://www.postgresql.org/docs/current/using-explain.html',
    'Using EXPLAIN',
    'PostgreSQL Global Development Group',
    'official_documentation',
    'pending'
)
on conflict (url) do nothing;
