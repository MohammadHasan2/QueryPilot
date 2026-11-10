import { useState, type FormEvent, type ReactNode } from 'react'
import {
  Activity,
  AlertCircle,
  ArrowUpRight,
  CheckCircle2,
  Clock3,
  Code2,
  Database,
  FileText,
  Gauge,
  LoaderCircle,
  Play,
  Search,
  ShieldCheck,
  Sparkles,
  Terminal,
  Zap,
} from 'lucide-react'

type JsonRecord = Record<string, unknown>

type Finding = {
  issue: string
  severity: 'low' | 'medium' | 'high'
  explanation: string
  recommendation: string
  tradeoffs?: string[]
  validation_steps?: string[]
  evidence_chunk_ids?: string[]
}

type InvestigationReport = {
  summary: string
  findings: Finding[]
  overall_recommendation: string
  limitations: string[]
}

type InvestigationResult = {
  valid: boolean
  statement_type: string
  sql: string
  analysis: {
    plan?: unknown
    database?: string
    analysis_mode?: string
    statement_timeout_ms?: number
  }
  retrieval_status: string
  evidence: JsonRecord[]
  ai_status: string
  report: InvestigationReport | null
  error?: string | null
  error_message?: string | null
}

const SAMPLE_SQL = `SELECT *
FROM lab_status
LIMIT 10;`

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}


function formatStatus(status: string): string {
  return status.replaceAll('_', ' ')
}

function StatusPill({
  children,
  tone = 'neutral',
}: {
  children: ReactNode
  tone?: 'neutral' | 'green' | 'amber' | 'red' | 'blue'
}) {
  const styles = {
    neutral: 'border-white/10 bg-white/5 text-slate-300',
    green: 'border-emerald-400/20 bg-emerald-400/10 text-emerald-300',
    amber: 'border-amber-400/20 bg-amber-400/10 text-amber-300',
    red: 'border-rose-400/20 bg-rose-400/10 text-rose-300',
    blue: 'border-blue-400/20 bg-blue-400/10 text-blue-300',
  }

  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium capitalize ${styles[tone]}`}>
      {children}
    </span>
  )
}

function MetricCard({
  label,
  value,
  detail,
  icon: Icon,
}: {
  label: string
  value: string
  detail: string
  icon: typeof Clock3
}) {
  return (
    <div className="rounded-xl border border-white/[0.08] bg-[#111827] p-4">
      <div className="flex items-center justify-between">
        <span className="text-xs text-slate-400">{label}</span>
        <Icon size={16} className="text-slate-500" />
      </div>
      <div className="mt-3 text-2xl font-semibold tracking-tight text-white">
        {value}
      </div>
      <div className="mt-1 text-xs text-slate-500">{detail}</div>
    </div>
  )
}

function severityTone(severity: Finding['severity']) {
  if (severity === 'high') return 'red'
  if (severity === 'medium') return 'amber'
  return 'blue'
}

function App() {
  const [sql, setSql] = useState(SAMPLE_SQL)
  const [result, setResult] = useState<InvestigationResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function investigate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()

    if (!sql.trim()) {
      setError('Enter a SQL query before starting an investigation.')
      return
    }

    setLoading(true)
    setError(null)
    setResult(null)

    try {
      const response = await fetch('/api/investigate', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ sql }),
      })

      const payload: unknown = await response.json()

      if (!response.ok) {
        const message = isRecord(payload) && typeof payload.detail === 'string'
          ? payload.detail
          : `The API returned HTTP ${response.status}.`
        throw new Error(message)
      }

      if (!isRecord(payload) || !isRecord(payload.analysis)) {
        throw new Error('The API returned an unexpected response.')
      }

      setResult(payload as unknown as InvestigationResult)
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : 'An unexpected error occurred while contacting the API.',
      )
    } finally {
      setLoading(false)
    }
  }
  

  const aiStatus = result?.ai_status ?? ''
  const aiAvailable = Boolean(result?.report)
  const aiTone = aiAvailable
    ? 'green'
    : aiStatus === 'budget_exhausted' || aiStatus === 'rate_limited'
      ? 'amber'
      : 'neutral'

  return (
    <div className="min-h-screen bg-[#090d18] text-slate-200">
      <header className="sticky top-0 z-20 border-b border-white/[0.07] bg-[#090d18]/90 backdrop-blur-xl">
        <div className="mx-auto flex h-16 max-w-[1440px] items-center justify-between px-5 sm:px-8">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-blue-500/15 text-blue-400">
              <Database size={19} />
            </div>
            <div>
              <div className="text-sm font-semibold tracking-wide text-white">
                QueryPilot
              </div>
              <div className="text-[11px] text-slate-500">
                PostgreSQL Performance Investigator
              </div>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <StatusPill tone="green">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
               Workspace
            </StatusPill>
            <div className="hidden items-center gap-2 border-l border-white/10 pl-4 text-xs text-slate-500 sm:flex">
              <ShieldCheck size={15} />
              SQL
            </div>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1440px] px-5 py-8 sm:px-8">
        <section className="mb-8 flex flex-col justify-between gap-4 md:flex-row md:items-end">
          <div>
            <div className="mb-3 flex items-center gap-2 text-xs font-medium uppercase tracking-[0.18em] text-blue-400">
              <Activity size={14} />
              Query intelligence
            </div>
            <h1 className="text-3xl font-semibold tracking-tight text-white sm:text-4xl">
              Investigate your SQL.
            </h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-400">
              Analyze PostgreSQL statements, retrieve relevant technical
              evidence, and get actionable optimization recommendations.
            </p>
          </div>

          <div className="flex items-center gap-2 text-xs text-slate-500">
            <Database size={14} />
            PostgreSQL SQL analysis
          </div>
        </section>

        <div className="grid gap-6 xl:grid-cols-[minmax(0,1.15fr)_minmax(360px,0.85fr)]">
          <section className="overflow-hidden rounded-2xl border border-white/[0.08] bg-[#111827]">
            <div className="flex items-center justify-between border-b border-white/[0.07] px-5 py-4">
              <div className="flex items-center gap-2">
                <Code2 size={17} className="text-blue-400" />
                <h2 className="text-sm font-semibold text-white">SQL workspace</h2>
              </div>
              <StatusPill tone="blue">Static SQL analysis</StatusPill>
            </div>

            <form onSubmit={investigate}>
              <div className="flex items-center justify-between px-5 py-3">
                <div className="flex items-center gap-2 text-xs text-slate-500">
                  <Terminal size={14} />
                  query.sql
                </div>
                <button
                  type="button"
                  onClick={() => setSql(SAMPLE_SQL)}
                  className="text-xs text-slate-400 transition hover:text-white"
                >
                  Reset example
                </button>
              </div>

              <div className="px-5 pb-5">
                <div className="overflow-hidden rounded-xl border border-white/10 bg-[#080e1a]">
                  <div className="flex min-h-[280px]">
                    <div className="select-none border-r border-white/[0.06] px-3 py-4 text-right font-mono text-xs leading-6 text-slate-600">
                      {sql.split('\n').map((_, index) => (
                        <div key={index}>{index + 1}</div>
                      ))}
                    </div>
                    <textarea
                      value={sql}
                      onChange={(event) => setSql(event.target.value)}
                      spellCheck={false}
                      aria-label="SQL query editor"
                      placeholder="Write a read-only SELECT query..."
                      className="min-h-[280px] w-full resize-y bg-transparent p-4 font-mono text-[13px] leading-6 text-emerald-200 outline-none placeholder:text-slate-700"
                    />
                  </div>
                </div>

                <div className="mt-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                  <div className="flex items-center gap-2 text-xs text-slate-500">
                    <ShieldCheck size={14} className="text-emerald-400" />
                    Parsed and analyzed without executing SQL
                  </div>
                  <button
                    type="submit"
                    disabled={loading || !sql.trim()}
                    className="inline-flex items-center justify-center gap-2 rounded-lg bg-blue-500 px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-blue-400 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {loading ? (
                      <>
                        <LoaderCircle size={16} className="animate-spin" />
                        Investigating...
                      </>
                    ) : (
                      <>
                        <Play size={15} />
                        Investigate query
                      </>
                    )}
                  </button>
                </div>
              </div>
            </form>
          </section>

          <section className="rounded-2xl border border-white/[0.08] bg-[#111827]">
            <div className="flex items-center gap-2 border-b border-white/[0.07] px-5 py-4">
              <Gauge size={17} className="text-violet-400" />
              <h2 className="text-sm font-semibold text-white">Investigation pipeline</h2>
            </div>

            <div className="p-5">
              <div className="space-y-5">
                <div className="flex gap-3">
                  <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-emerald-400/10 text-emerald-300">
                    <ShieldCheck size={16} />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-medium text-white">SQL validation</div>
                    <p className="mt-1 text-xs leading-5 text-slate-500">
                      Read-only SELECT validation before execution.
                    </p>
                  </div>
                  <StatusPill tone={result ? 'green' : 'neutral'}>
                    {result ? 'Passed' : 'Ready'}
                  </StatusPill>
                </div>

                <div className="ml-4 h-5 border-l border-dashed border-white/10" />

                <div className="flex gap-3">
                  <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-blue-400/10 text-blue-300">
                    <Database size={16} />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-medium text-white">Static SQL analysis</div>
                    <p className="mt-1 text-xs leading-5 text-slate-500">
                      Statement classification and deterministic optimization rules.
                    </p>
                  </div>
                  <StatusPill tone={result?.analysis ? 'green' : 'neutral'}>
                    {result?.analysis ? 'Complete' : 'Ready'}
                  </StatusPill>
                </div>

                <div className="ml-4 h-5 border-l border-dashed border-white/10" />

                <div className="flex gap-3">
                  <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-amber-400/10 text-amber-300">
                    <Search size={16} />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-medium text-white">Knowledge retrieval</div>
                    <p className="mt-1 text-xs leading-5 text-slate-500">
                      Hybrid search across PostgreSQL documentation and cases.
                    </p>
                  </div>
                  <StatusPill
                    tone={result?.retrieval_status === 'success' || result?.retrieval_status === 'completed'
                      ? 'green'
                      : 'neutral'}
                  >
                    {result ? formatStatus(result.retrieval_status) : 'Ready'}
                  </StatusPill>
                </div>

                <div className="ml-4 h-5 border-l border-dashed border-white/10" />

                <div className="flex gap-3">
                  <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-violet-400/10 text-violet-300">
                    <Sparkles size={16} />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-medium text-white">AI recommendations</div>
                    <p className="mt-1 text-xs leading-5 text-slate-500">
                      Budget-controlled structured recommendations.
                    </p>
                  </div>
                  <StatusPill tone={aiTone}>
                    {result ? formatStatus(aiStatus) : 'Ready'}
                  </StatusPill>
                </div>
              </div>

              <div className="mt-7 rounded-xl border border-blue-400/10 bg-blue-400/[0.05] p-4">
                <div className="flex items-center gap-2 text-xs font-medium text-blue-300">
                  <Zap size={14} />
                  Reliability by design
                </div>
                <p className="mt-2 text-xs leading-5 text-slate-400">
                  SQL analysis is independent of AI availability. If the AI
                  budget is exhausted, execution-plan results remain available.
                </p>
              </div>
            </div>
          </section>
        </div>

        {error && (
          <div className="mt-6 flex items-start gap-3 rounded-xl border border-rose-400/20 bg-rose-400/[0.07] p-4">
            <AlertCircle size={18} className="mt-0.5 shrink-0 text-rose-300" />
            <div>
              <div className="text-sm font-medium text-rose-200">
                Investigation request failed
              </div>
              <p className="mt-1 text-sm text-rose-200/70">{error}</p>
            </div>
          </div>
        )}

        {result && (
          <section className="mt-8 space-y-6">
            <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-center">
              <div>
                <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-[0.16em] text-emerald-400">
                  <CheckCircle2 size={14} />
                  Analysis completed
                </div>
                <h2 className="mt-2 text-xl font-semibold text-white">
                  Investigation results
                </h2>
              </div>
              <StatusPill tone="green">SQL analysis successful</StatusPill>
            </div>

            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
  <MetricCard
    label="SQL statement"
    value={result.statement_type}
    detail="Parsed successfully"
    icon={Database}
  />
  <MetricCard
    label="Execution"
    value="Not performed"
    detail="Static analysis only"
    icon={Activity}
  />
  <MetricCard
    label="Static analysis"
    value="Not generated"
    detail="No SQL execution"
    icon={Gauge}
  />
  <MetricCard
    label="Evidence sources"
    value={String(result.evidence?.length ?? 0)}
    detail={`Retrieval: ${formatStatus(result.retrieval_status)}`}
    icon={FileText}
  />
</div>
<div className="grid gap-6 xl:grid-cols-2">
              <section className="rounded-2xl border border-white/[0.08] bg-[#111827]">
  <div className="flex items-center gap-2 border-b border-white/[0.07] px-5 py-4">
    <Activity size={17} className="text-blue-400" />
    <h3 className="text-sm font-semibold text-white">Static SQL analysis</h3>
  </div>
  <div className="space-y-3 p-5 text-sm text-slate-400">
    <p>QueryPilot analyzes SQL structure and optimization patterns without executing submitted SQL.</p>
    <p>Execution plans, runtime statistics, and database measurements are not collected.</p>
    <p className="text-emerald-400">SQL execution: disabled</p>
  </div>
</section>

              <section className="rounded-2xl border border-white/[0.08] bg-[#111827]">
                <div className="flex items-center justify-between border-b border-white/[0.07] px-5 py-4">
                  <div className="flex items-center gap-2">
                    <Sparkles size={17} className="text-violet-400" />
                    <h3 className="text-sm font-semibold text-white">AI investigation</h3>
                  </div>
                  <StatusPill tone={aiTone}>
                    {formatStatus(aiStatus || 'pending')}
                  </StatusPill>
                </div>

                <div className="p-5">
                  {result.report ? (
                    <div className="space-y-5">
                      <div>
                        <div className="mb-2 text-xs font-medium uppercase tracking-wider text-slate-500">
                          Summary
                        </div>
                        <p className="text-sm leading-6 text-slate-300">
                          {result.report.summary}
                        </p>
                      </div>

                      <div>
                        <div className="mb-3 text-xs font-medium uppercase tracking-wider text-slate-500">
                          Overall recommendation
                        </div>
                        <div className="rounded-xl border border-emerald-400/15 bg-emerald-400/[0.06] p-4 text-sm leading-6 text-emerald-100">
                          {result.report.overall_recommendation}
                        </div>
                      </div>

                      <div>
                        <div className="mb-3 text-xs font-medium uppercase tracking-wider text-slate-500">
                          Findings ({result.report.findings.length})
                        </div>
                        <div className="space-y-3">
                          {result.report.findings.map((finding, index) => (
                            <div
                              key={`${finding.issue}-${index}`}
                              className="rounded-xl border border-white/[0.08] bg-[#0c1423] p-4"
                            >
                              <div className="flex flex-wrap items-center justify-between gap-2">
                                <h4 className="text-sm font-semibold text-white">
                                  {finding.issue}
                                </h4>
                                <StatusPill tone={severityTone(finding.severity)}>
                                  {finding.severity}
                                </StatusPill>
                              </div>
                              <p className="mt-3 text-sm leading-6 text-slate-400">
                                {finding.explanation}
                              </p>
                              <div className="mt-3 border-l-2 border-blue-400/40 pl-3">
                                <div className="text-xs font-medium text-blue-300">
                                  Recommendation
                                </div>
                                <p className="mt-1 text-sm leading-6 text-slate-300">
                                  {finding.recommendation}
                                </p>
                              </div>
                              {finding.tradeoffs && finding.tradeoffs.length > 0 && (
                                <div className="mt-3 text-xs leading-5 text-slate-500">
                                  <strong className="text-slate-400">Trade-offs:</strong>{' '}
                                  {finding.tradeoffs.join(' · ')}
                                </div>
                              )}
                              {finding.validation_steps && finding.validation_steps.length > 0 && (
                                <div className="mt-3">
                                  <div className="mb-1 text-xs font-medium text-slate-400">
                                    Validation steps
                                  </div>
                                  <ul className="list-inside list-disc space-y-1 text-xs leading-5 text-slate-500">
                                    {finding.validation_steps.map((step, stepIndex) => (
                                      <li key={stepIndex}>{step}</li>
                                    ))}
                                  </ul>
                                </div>
                              )}
                            </div>
                          ))}
                        </div>
                      </div>

                      {result.report.limitations.length > 0 && (
                        <div className="text-xs leading-5 text-slate-500">
                          <strong className="text-slate-400">Limitations:</strong>{' '}
                          {result.report.limitations.join(' · ')}
                        </div>
                      )}
                    </div>
                  ) : (
                    <div className="rounded-xl border border-amber-400/15 bg-amber-400/[0.05] p-4">
                      <div className="flex items-center gap-2 text-sm font-medium text-amber-200">
                        <AlertCircle size={16} />
                        AI report not available
                      </div>
                      <p className="mt-2 text-sm leading-6 text-slate-400">
                        {result.error_message ||
                          'Static SQL analysis completed, but an AI report was not generated.'}
                      </p>
                      <p className="mt-3 text-xs leading-5 text-slate-500">
                        Your static SQL analysis remains available. No SQL was executed.
                        No additional AI request was triggered.
                      </p>
                    </div>
                  )}
                </div>
              </section>
            </div>

            <section className="rounded-2xl border border-white/[0.08] bg-[#111827]">
              <div className="flex items-center justify-between border-b border-white/[0.07] px-5 py-4">
                <div className="flex items-center gap-2">
                  <FileText size={17} className="text-amber-300" />
                  <h3 className="text-sm font-semibold text-white">Retrieved evidence</h3>
                </div>
                <StatusPill>{result.evidence?.length ?? 0} results</StatusPill>
              </div>

              <div className="p-5">
                {result.evidence?.length ? (
                  <div className="grid gap-3 md:grid-cols-2">
                    {result.evidence.map((item, index) => {
                      const title = item.source_title ?? item.title ?? `Evidence ${index + 1}`
                      const content = item.content ?? item.chunk_text ?? item.text
                      const url = item.source_url ?? item.url

                      return (
                        <article
                          key={String(item.chunk_id ?? item.id ?? index)}
                          className="rounded-xl border border-white/[0.08] bg-[#0c1423] p-4"
                        >
                          <div className="flex items-start justify-between gap-3">
                            <h4 className="text-sm font-medium text-slate-200">
                              {String(title)}
                            </h4>
                            {typeof url === 'string' && url.startsWith('https://') && (
                              <a
                                href={url}
                                target="_blank"
                                rel="noreferrer"
                                className="shrink-0 text-slate-500 transition hover:text-blue-300"
                                aria-label="Open evidence source"
                              >
                                <ArrowUpRight size={15} />
                              </a>
                            )}
                          </div>
                          {content !== undefined && (
                            <p className="mt-2 line-clamp-5 text-xs leading-5 text-slate-500">
                              {String(content)}
                            </p>
                          )}
                        </article>
                      )
                    })}
                  </div>
                ) : (
                  <p className="text-sm text-slate-500">
                    No evidence was returned. Retrieval status: {formatStatus(result.retrieval_status)}.
                  </p>
                )}
              </div>
            </section>
          </section>
        )}

        {!result && !error && (
          <div className="mt-8 rounded-2xl border border-dashed border-white/10 bg-white/[0.02] px-6 py-10 text-center">
            <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl bg-blue-400/10 text-blue-300">
              <Search size={21} />
            </div>
            <h3 className="mt-4 text-sm font-semibold text-white">
              Ready to investigate
            </h3>
            <p className="mx-auto mt-2 max-w-md text-xs leading-5 text-slate-500">
              Submit a PostgreSQL statement for static analysis,
              retrieve relevant PostgreSQL knowledge, and generate recommendations.
            </p>
          </div>
        )}

        <footer className="mt-10 flex flex-col justify-between gap-2 border-t border-white/[0.07] pt-5 text-[11px] text-slate-600 sm:flex-row">
          <span>QueryPilot · PostgreSQL Performance Investigator</span>
          <span>Static SQL analysis · Supabase knowledge · Budget-controlled AI</span>
        </footer>
      </main>
    </div>
  )
}

export default App


