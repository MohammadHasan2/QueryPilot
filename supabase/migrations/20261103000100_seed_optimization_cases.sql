INSERT INTO public.optimization_cases (
    title,
    problem_pattern,
    relevant_conditions,
    suggested_optimization,
    expected_tradeoffs,
    validation_requirements,
    supporting_source_ids
)
SELECT
    v.title,
    v.problem_pattern,
    v.relevant_conditions,
    v.suggested_optimization,
    v.expected_tradeoffs,
    v.validation_requirements,
    COALESCE(
        (
            SELECT array_agg(ks.id)
            FROM public.knowledge_sources ks
            WHERE ks.url = ANY(v.source_urls)
        ),
        ARRAY[]::uuid[]
    )
FROM (
    VALUES
    (
        'Selective filters with sequential scans',
        'A query with a selective WHERE condition performs a sequential scan on a sufficiently large table.',
        '{
            "signals": [
                "Sequential Scan in the execution plan",
                "A selective filter removes a large proportion of scanned rows"
            ],
            "considerations": [
                "Table size",
                "Filter selectivity",
                "Existing indexes",
                "Write frequency"
            ]
        }'::jsonb,
        'Evaluate a B-tree index whose leading columns match the query filter. For composite indexes, consider equality predicates before range predicates. Confirm that the planner chooses the index when it is beneficial.',
        'Indexes consume storage and add write and maintenance overhead. A sequential scan can still be the correct choice for small tables or queries returning a large proportion of rows.',
        '[
            "Inspect EXPLAIN ANALYZE before and after the proposed change",
            "Compare actual rows, execution time, and buffer activity",
            "Check existing indexes and avoid redundant indexes",
            "Validate representative parameter values"
        ]'::jsonb,
        ARRAY[
            'https://www.postgresql.org/docs/current/using-explain.html',
            'https://www.postgresql.org/docs/current/indexes.html'
        ]::text[]
    ),
    (
        'Stale planner statistics',
        'The estimated row counts in an execution plan differ substantially from actual row counts, potentially leading to poor plan choices.',
        '{
            "signals": [
                "Large difference between estimated rows and actual rows",
                "Unexpected join or scan strategy"
            ],
            "considerations": [
                "Recent bulk inserts, updates, or deletes",
                "Table statistics freshness",
                "Data distribution and skew"
            ]
        }'::jsonb,
        'Run ANALYZE on the affected table to refresh planner statistics. If estimates remain inaccurate for skewed data, evaluate whether higher statistics targets or extended statistics are appropriate.',
        'ANALYZE consumes resources while collecting statistics, and statistics are estimates rather than exact descriptions of every possible parameter value.',
        '[
            "Compare estimated rows with actual rows in EXPLAIN ANALYZE",
            "Inspect when statistics were last updated",
            "Run ANALYZE on the affected table",
            "Re-run representative queries and compare plans"
        ]'::jsonb,
        ARRAY[
            'https://www.postgresql.org/docs/current/sql-analyze.html',
            'https://www.postgresql.org/docs/current/using-explain.html'
        ]::text[]
    ),
    (
        'Non-sargable predicates on indexed columns',
        'A query applies a function or expression to an indexed column in its filter, which may prevent a conventional index from being used efficiently.',
        '{
            "signals": [
                "Function or expression applied to a filtered column",
                "Sequential scan despite an existing index"
            ],
            "considerations": [
                "Predicate semantics",
                "Column data type",
                "Timezone and collation behavior",
                "Existing expression indexes"
            ]
        }'::jsonb,
        'Where semantics allow, rewrite the predicate as an index-friendly condition, such as a range over the original column. Alternatively, evaluate an expression index when the expression is stable and frequently queried.',
        'Rewriting predicates must preserve the original query semantics. Expression indexes require storage and add write-maintenance overhead.',
        '[
            "Inspect the filter expression and existing indexes",
            "Confirm the rewritten predicate returns equivalent results",
            "Compare EXPLAIN ANALYZE plans",
            "Test boundary values and relevant data types"
        ]'::jsonb,
        ARRAY[
            'https://www.postgresql.org/docs/current/indexes.html',
            'https://www.postgresql.org/docs/current/using-explain.html'
        ]::text[]
    )
) AS v(
    title,
    problem_pattern,
    relevant_conditions,
    suggested_optimization,
    expected_tradeoffs,
    validation_requirements,
    source_urls
)
WHERE NOT EXISTS (
    SELECT 1
    FROM public.optimization_cases existing
    WHERE existing.title = v.title
);