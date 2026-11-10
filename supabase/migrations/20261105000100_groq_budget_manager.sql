-- QueryPilot Phase 6: shared, atomic Groq budget management.
-- The application-level TPM ceiling must never exceed 8,000.

create table if not exists public.groq_budget_config (
    singleton boolean primary key default true check (singleton),
    rpm_limit integer not null default 30 check (rpm_limit > 0),
    rpd_limit integer not null default 1200 check (rpd_limit > 0),
    tpm_limit integer not null default 8000
        check (tpm_limit > 0 and tpm_limit <= 8000),
    updated_at timestamptz not null default now()
);

insert into public.groq_budget_config (singleton, rpm_limit, rpd_limit, tpm_limit)
values (true, 30, 1200, 8000)
on conflict (singleton) do nothing;


create table if not exists public.groq_budget_state (
    singleton boolean primary key default true check (singleton),
    minute_start timestamptz not null,
    minute_tokens_reserved integer not null default 0,
    minute_requests integer not null default 0,
    day_start date not null,
    daily_requests integer not null default 0,
    updated_at timestamptz not null default now()
);

insert into public.groq_budget_state (
    singleton, minute_start, day_start
)
values (
    true,
    date_trunc('minute', now() at time zone 'UTC') at time zone 'UTC',
    (now() at time zone 'UTC')::date
)
on conflict (singleton) do nothing;


create table if not exists public.groq_budget_reservations (
    id uuid primary key default gen_random_uuid(),
    investigation_id uuid references public.investigations(id)
        on delete set null,
    model text not null,
    reserved_tokens integer not null check (reserved_tokens > 0),
    actual_tokens integer,
    minute_start timestamptz not null,
    status text not null default 'reserved'
        check (status in (
            'reserved', 'completed', 'rate_limited',
            'failed', 'ai_deferred'
        )),
    provider_status_code integer,
    latency_ms integer,
    created_at timestamptz not null default now(),
    settled_at timestamptz
);

create index if not exists groq_budget_reservations_created_idx
    on public.groq_budget_reservations (created_at);

create index if not exists groq_budget_reservations_status_idx
    on public.groq_budget_reservations (status);


-- Connect the reservation ledger to the existing usage-event table.
alter table public.llm_usage_events
    add column if not exists reservation_id uuid
        references public.groq_budget_reservations(id);

create unique index if not exists llm_usage_events_reservation_uidx
    on public.llm_usage_events (reservation_id)
    where reservation_id is not null;


-- Atomically reserve request and token capacity.
create or replace function public.reserve_groq_budget(
    p_model text,
    p_estimated_tokens integer,
    p_investigation_id uuid default null
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_now timestamptz := clock_timestamp();
    v_minute timestamptz;
    v_day date;
    v_config public.groq_budget_config%rowtype;
    v_state public.groq_budget_state%rowtype;
    v_retry integer;
    v_reservation_id uuid;
begin
    if p_model is null or length(trim(p_model)) = 0 then
        raise exception 'Model is required';
    end if;

    if p_estimated_tokens is null or p_estimated_tokens <= 0 then
        raise exception 'Estimated tokens must be positive';
    end if;

    v_minute :=
        date_trunc('minute', v_now at time zone 'UTC') at time zone 'UTC';
    v_day := (v_now at time zone 'UTC')::date;

    select *
    into v_config
    from public.groq_budget_config
    where singleton = true;

    if not found then
        raise exception 'Groq budget configuration is missing';
    end if;

    if p_estimated_tokens > v_config.tpm_limit then
        return jsonb_build_object(
            'allowed', false,
            'status', 'budget_exhausted',
            'reason', 'request_exceeds_tpm_limit',
            'retry_after_seconds', 60
        );
    end if;

    insert into public.groq_budget_state (
        singleton, minute_start, day_start
    )
    values (true, v_minute, v_day)
    on conflict (singleton) do nothing;

    -- This row lock serializes all reservations across API instances.
    select *
    into v_state
    from public.groq_budget_state
    where singleton = true
    for update;

    if v_state.minute_start <> v_minute then
        v_state.minute_start := v_minute;
        v_state.minute_tokens_reserved := 0;
        v_state.minute_requests := 0;
    end if;

    if v_state.day_start <> v_day then
        v_state.day_start := v_day;
        v_state.daily_requests := 0;
    end if;

    if v_state.daily_requests >= v_config.rpd_limit then
        v_retry := greatest(
            1,
            ceil(extract(epoch from (
                ((v_day + 1)::timestamp at time zone 'UTC') - v_now
            )))::integer
        );

        update public.groq_budget_state
        set minute_start = v_state.minute_start,
            minute_tokens_reserved = v_state.minute_tokens_reserved,
            minute_requests = v_state.minute_requests,
            day_start = v_state.day_start,
            daily_requests = v_state.daily_requests,
            updated_at = v_now
        where singleton = true;

        return jsonb_build_object(
            'allowed', false,
            'status', 'rate_limited',
            'reason', 'daily_request_limit',
            'retry_after_seconds', v_retry
        );
    end if;

    if v_state.minute_requests >= v_config.rpm_limit then
        v_retry := greatest(
            1,
            ceil(extract(epoch from (
                v_minute + interval '1 minute' - v_now
            )))::integer
        );

        update public.groq_budget_state
        set minute_start = v_state.minute_start,
            minute_tokens_reserved = v_state.minute_tokens_reserved,
            minute_requests = v_state.minute_requests,
            day_start = v_state.day_start,
            daily_requests = v_state.daily_requests,
            updated_at = v_now
        where singleton = true;

        return jsonb_build_object(
            'allowed', false,
            'status', 'rate_limited',
            'reason', 'requests_per_minute_limit',
            'retry_after_seconds', v_retry
        );
    end if;

    if v_state.minute_tokens_reserved + p_estimated_tokens
        > v_config.tpm_limit then

        v_retry := greatest(
            1,
            ceil(extract(epoch from (
                v_minute + interval '1 minute' - v_now
            )))::integer
        );

        update public.groq_budget_state
        set minute_start = v_state.minute_start,
            minute_tokens_reserved = v_state.minute_tokens_reserved,
            minute_requests = v_state.minute_requests,
            day_start = v_state.day_start,
            daily_requests = v_state.daily_requests,
            updated_at = v_now
        where singleton = true;

        return jsonb_build_object(
            'allowed', false,
            'status', 'budget_exhausted',
            'reason', 'tokens_per_minute_limit',
            'retry_after_seconds', v_retry
        );
    end if;

    insert into public.groq_budget_reservations (
        investigation_id,
        model,
        reserved_tokens,
        minute_start
    )
    values (
        p_investigation_id,
        p_model,
        p_estimated_tokens,
        v_minute
    )
    returning id into v_reservation_id;

    update public.groq_budget_state
    set minute_start = v_minute,
        minute_tokens_reserved =
            v_state.minute_tokens_reserved + p_estimated_tokens,
        minute_requests = v_state.minute_requests + 1,
        day_start = v_day,
        daily_requests = v_state.daily_requests + 1,
        updated_at = v_now
    where singleton = true;

    return jsonb_build_object(
        'allowed', true,
        'status', 'reserved',
        'reservation_id', v_reservation_id,
        'reserved_tokens', p_estimated_tokens
    );
end;
$$;


-- Reconcile a reservation with actual provider usage.
-- NULL actual_tokens releases the token reservation, but does not undo
-- the request count: the provider attempt still consumed an RPM/RPD slot.
create or replace function public.settle_groq_budget(
    p_reservation_id uuid,
    p_actual_tokens integer,
    p_status text,
    p_provider_status_code integer default null,
    p_latency_ms integer default null
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_reservation public.groq_budget_reservations%rowtype;
    v_now timestamptz := clock_timestamp();
    v_actual integer;
begin
    if p_status is null or p_status not in (
        'completed', 'rate_limited', 'failed', 'ai_deferred'
    ) then
        raise exception 'Invalid settlement status';
    end if;

    if p_actual_tokens is not null and p_actual_tokens < 0 then
        raise exception 'Actual tokens cannot be negative';
    end if;

    select *
    into v_reservation
    from public.groq_budget_reservations
    where id = p_reservation_id
    for update;

    if not found then
        return jsonb_build_object(
            'settled', false,
            'reason', 'reservation_not_found'
        );
    end if;

    if v_reservation.status <> 'reserved' then
        return jsonb_build_object(
            'settled', true,
            'already_settled', true,
            'status', v_reservation.status
        );
    end if;

    v_actual := coalesce(p_actual_tokens, 0);

    update public.groq_budget_reservations
    set actual_tokens = p_actual_tokens,
        status = p_status,
        provider_status_code = p_provider_status_code,
        latency_ms = p_latency_ms,
        settled_at = v_now
    where id = p_reservation_id;

    -- Only adjust the active minute. Older windows have already expired.
    update public.groq_budget_state
    set minute_tokens_reserved = greatest(
            0,
            minute_tokens_reserved
                - v_reservation.reserved_tokens
                + v_actual
        ),
        updated_at = v_now
    where singleton = true
      and minute_start = v_reservation.minute_start;

    insert into public.llm_usage_events (
        investigation_id,
        model,
        estimated_tokens,
        actual_tokens,
        latency_ms,
        status,
        quota_decision,
        provider_status_code,
        reservation_id
    )
    values (
        v_reservation.investigation_id,
        v_reservation.model,
        v_reservation.reserved_tokens,
        p_actual_tokens,
        p_latency_ms,
        p_status,
        p_status,
        p_provider_status_code,
        p_reservation_id
    )
    on conflict (reservation_id) where reservation_id is not null
    do nothing;

    return jsonb_build_object(
        'settled', true,
        'status', p_status,
        'reserved_tokens', v_reservation.reserved_tokens,
        'actual_tokens', p_actual_tokens
    );
end;
$$;


-- Keep all budget state and RPCs server-side.
alter table public.groq_budget_config enable row level security;
alter table public.groq_budget_state enable row level security;
alter table public.groq_budget_reservations enable row level security;

revoke all on public.groq_budget_config
    from public, anon, authenticated;
revoke all on public.groq_budget_state
    from public, anon, authenticated;
revoke all on public.groq_budget_reservations
    from public, anon, authenticated;

revoke all on function public.reserve_groq_budget(text, integer, uuid)
    from public, anon, authenticated;
revoke all on function public.settle_groq_budget(uuid, integer, text, integer, integer)
    from public, anon, authenticated;

grant select, insert, update on public.groq_budget_config to service_role;
grant select, insert, update on public.groq_budget_state to service_role;
grant select, insert, update on public.groq_budget_reservations to service_role;
grant select, insert on public.llm_usage_events to service_role;

grant execute on function public.reserve_groq_budget(text, integer, uuid)
    to service_role;
grant execute on function public.settle_groq_budget(uuid, integer, text, integer, integer)
    to service_role;