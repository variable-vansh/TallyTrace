import { useMemo, useState } from 'react'
import {
  Bar, BarChart, Cell, CartesianGrid, Legend, Pie, PieChart, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from 'recharts'
import { AlertOctagon, Eye, EyeOff, FileWarning, ShieldOff } from 'lucide-react'
import MetricChart from '../components/MetricChart'
import StatusBadge from '../components/StatusBadge'
import CeilingControl from '../components/CeilingControl'
import { useStored } from '../lib/useStored'
import { inr, pct, humanise } from '../lib/format'

const CHANNEL_COLORS = {
  amazon: '#3D4FE0', flipkart: '#1FAA59', myntra: '#F59E0B',
  offline: '#EF4444', website: '#8B5CF6',
}
const PIE_COLORS = ['#3D4FE0', '#1FAA59', '#F59E0B', '#EF4444', '#8B5CF6', '#06B6D4', '#EC4899', '#84CC16']
const AXIS = { fill: '#6B7280', fontSize: 12 }
const TOOLTIP = { contentStyle: { borderRadius: '8px', border: '1px solid #E7E7EA', fontSize: 12 } }

const TABS = [
  { id: 'settings', label: 'Settings' },
  { id: 'utility', label: 'Performance' },
  { id: 'money', label: 'Money' },
  { id: 'causes', label: 'Causes' },
  { id: 'refused', label: 'What it refused' },
]

function Card({ title, subtitle, children, className = '' }) {
  return (
    <div className={`bg-white rounded-2xl border border-divider p-6 ${className}`}>
      <h2 className="text-sm font-medium text-muted uppercase tracking-wide">{title}</h2>
      {subtitle && <p className="text-xs text-muted mt-0.5 mb-3">{subtitle}</p>}
      {children}
    </div>
  )
}

export default function Reports({ weekData, allWeeks, data }) {
  const [tab, setTab] = useState('settings')

  const moneyByWeek = useMemo(
    () => allWeeks.map((w) => ({
      name: `W${w.week}`,
      auto: w.stats.rupeesAutoResolved,
      escalated: w.stats.rupeesEscalated,
    })),
    [allWeeks]
  )

  const impactByChannel = useMemo(() => {
    const agg = {}
    for (const exc of weekData.exceptions || []) {
      if (!exc.channel) continue
      agg[exc.channel] = (agg[exc.channel] || 0) + exc.impact
    }
    return Object.entries(agg)
      .map(([channel, value]) => ({ channel, value: Math.round(value) }))
      .sort((a, b) => b.value - a.value)
  }, [weekData])

  const causeMix = useMemo(() => {
    const agg = {}
    for (const exc of weekData.exceptions || []) {
      const label = humanise(exc.hypothesis?.cause || exc.reason)
      agg[label] = (agg[label] || 0) + 1
    }
    return Object.entries(agg)
      .map(([name, value]) => ({ name, value }))
      .sort((a, b) => b.value - a.value)
  }, [weekData])

  const quarantine = (data.quarantine || []).filter((q) => q.batch === weekData.week)
  const reporting = data.reporting || { pins: [], questions: [], registry: [] }

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Report &amp; Settings</h1>
          <p className="text-xs text-muted mt-0.5">
            Ten batches, one scored run · {data.generatedFrom}
          </p>
        </div>
        <div className="flex items-center gap-1 bg-white border border-divider rounded-lg p-1">
          {TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`px-3 py-1.5 rounded-md text-xs font-semibold transition-colors ${
                tab === t.id ? 'bg-gray-900 text-white' : 'text-gray-600 hover:bg-gray-50'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {tab === 'settings' && <SettingsPanel data={data} />}

      {tab === 'utility' && <UtilityPanel data={data} allWeeks={allWeeks} />}

      {tab === 'money' && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 items-start">
          <Card
            title="Effective take rate, week by week"
            subtitle="Every deduction as a share of gross order value. A rising line is what a silent commission change looks like from outside."
          >
            <MetricChart result={reporting.takeRateByBatch} height={240} />
          </Card>

          <Card
            title="Take rate by channel"
            subtitle="Commission, GST on commission, TCS and TDS, over gross order value"
          >
            <MetricChart result={reporting.takeRateByChannel} height={240} />
            <p className="text-xs text-muted mt-3 leading-relaxed">
              The marketplaces keep a fifth to a third of gross; the own website and the POS
              counter keep about two percent. Commission alone, before the tax withheld on top:{' '}
              {(reporting.commissionShareByChannel?.points || [])
                .map((p) => `${p.label} ${p.value}%`)
                .join(', ')}.
            </p>
          </Card>

          <Card
            title="Auto-resolved vs escalated, per week"
            subtitle="Volume is automated, value is escalated. That is the guardrails, not a limitation."
            className="h-80 flex flex-col"
          >
            <div className="flex-1 min-h-0">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={moneyByWeek} margin={{ top: 4, right: 8, left: 8, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#E7E7EA" vertical={false} />
                  <XAxis dataKey="name" axisLine={false} tickLine={false} tick={AXIS} />
                  <YAxis axisLine={false} tickLine={false} tick={AXIS}
                         tickFormatter={(v) => `₹${(v / 1000).toFixed(0)}k`} />
                  <Tooltip {...TOOLTIP} formatter={(v) => inr(v, { whole: true })} />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                  <Bar dataKey="auto" name="Auto-resolved" fill="#1FAA59" radius={[4, 4, 0, 0]}
                       isAnimationActive={false} />
                  <Bar dataKey="escalated" name="Escalated" fill="#3D4FE0" radius={[4, 4, 0, 0]}
                       isAnimationActive={false} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </Card>

          <Card title="Exception impact by channel"
                subtitle={`Week ${weekData.week} only · ${weekData.dateRange.from} → ${weekData.dateRange.to}`}
                className="h-80 flex flex-col">
            <div className="flex-1 min-h-0">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={impactByChannel} layout="vertical" margin={{ left: 20, right: 12 }}>
                  <XAxis type="number" hide />
                  <YAxis type="category" dataKey="channel" axisLine={false} tickLine={false}
                         tick={{ ...AXIS, textTransform: 'capitalize' }} width={62} />
                  <Tooltip {...TOOLTIP} formatter={(v) => inr(v, { whole: true })} />
                  <Bar dataKey="value" radius={[0, 4, 4, 0]} isAnimationActive={false}>
                    {impactByChannel.map((entry) => (
                      <Cell key={entry.channel} fill={CHANNEL_COLORS[entry.channel] || '#6B7280'} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </Card>
        </div>
      )}

      {tab === 'causes' && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <Card title={`Cause mix — week ${weekData.week}`}
                subtitle="From the model's hypothesis, constrained to the frozen enum"
                className="h-96 flex flex-col">
            <div className="flex-1">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie data={causeMix} dataKey="value" nameKey="name" cx="50%" cy="50%"
                       innerRadius={55} outerRadius={100} paddingAngle={2}
                       isAnimationActive={false}>
                    {causeMix.map((entry, i) => (
                      <Cell key={entry.name} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                    ))}
                  </Pie>
                  <Tooltip {...TOOLTIP} />
                  <Legend wrapperStyle={{ fontSize: 10 }} />
                </PieChart>
              </ResponsiveContainer>
            </div>
          </Card>

          <Card title="Where each exception went" subtitle="Outcome of the deterministic rule pass">
            <div className="space-y-2 mt-2">
              {Object.entries(
                (weekData.exceptions || []).reduce((acc, e) => ({ ...acc, [e.outcome]: (acc[e.outcome] || 0) + 1 }), {})
              )
                .sort((a, b) => b[1] - a[1])
                .map(([outcome, count]) => (
                  <div key={outcome} className="flex items-center justify-between border-b border-divider py-2">
                    <span className="text-sm text-gray-800">{humanise(outcome)}</span>
                    <span className="font-bold text-gray-900">{count}</span>
                  </div>
                ))}
            </div>
            <p className="text-xs text-muted mt-4">
              Auto-resolution precision across the corpus:{' '}
              <span className="font-semibold text-gray-900">{pct(data.overallPrecision, 2)}</span>,
              scored against an answer key the pipeline never reads.
            </p>
          </Card>
        </div>
      )}

      {tab === 'refused' && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 items-start">
          <Card
            title="Correct abstention"
            subtitle="Two causes were held out of the corpus until late. The system had to refuse to automate them on first sight."
          >
            <div className="mt-1 space-y-3">
              {(data.abstention || []).map((entry) => (
                <div key={entry.cause} className={`rounded-xl border p-4 ${
                  entry.correct ? 'border-success/30 bg-success-light/30' : 'border-danger/40 bg-danger-light/30'
                }`}>
                  <div className="flex items-center gap-2 mb-2 flex-wrap">
                    {entry.correct
                      ? <ShieldOff size={15} className="text-success" />
                      : <AlertOctagon size={15} className="text-danger" />}
                    <span className="font-semibold text-gray-900 text-sm">{humanise(entry.cause)}</span>
                    <StatusBadge variant="muted">first seen in batch {entry.first_batch}</StatusBadge>
                    <StatusBadge variant={entry.correct ? 'success' : 'danger'}>
                      {entry.abstention_rate_pct}% abstention
                    </StatusBadge>
                  </div>
                  <p className="text-sm text-gray-700">
                    {entry.cases_on_first_sight} case(s) on first sight,{' '}
                    <strong>{entry.auto_resolved_on_first_sight} auto-resolved</strong>. Across the
                    corpus: {entry.total_cases} cases, {entry.auto_resolved_ever} auto-resolved.
                  </p>
                </div>
              ))}
            </div>
            <p className="text-xs text-muted mt-4 leading-relaxed">
              This is not a special case in the code. A rule that has never been induced cannot
              fire, and the guardrails refuse a counterparty claim however confident a rule is.
            </p>
          </Card>

          <Card
            title={`Quarantined rows — week ${weekData.week}`}
            subtitle="Malformed input is parked with a reason and counted. Never dropped, never silently skipped."
          >
            {quarantine.length === 0 ? (
              <p className="text-sm text-muted">No rows were refused in week {weekData.week}.</p>
            ) : (
              <div className="space-y-2">
                {quarantine.map((q) => (
                  <div key={q.rowId} className="flex items-start gap-3 border border-divider rounded-lg p-3">
                    <FileWarning size={15} className="text-amber mt-0.5 flex-shrink-0" />
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="font-mono text-xs font-semibold text-gray-900">{q.rowId}</span>
                        <StatusBadge variant="amber">{humanise(q.reason)}</StatusBadge>
                        <span className="text-xs text-muted">{q.table}</span>
                      </div>
                      <p className="text-xs text-gray-700 mt-1 font-mono break-words">{q.message}</p>
                    </div>
                  </div>
                ))}
              </div>
            )}
            <p className="text-xs text-muted mt-4">
              {(data.quarantine || []).length} rows quarantined across all ten batches.
            </p>
          </Card>
        </div>
      )}
    </div>
  )
}


// --------------------------------------------------------------------------- //
// Settings — the numbers that change what the system does
// --------------------------------------------------------------------------- //

// The key never leaves this browser and is never sent anywhere by this page: a
// published artifact has no server to send it to. It is here because the *commands*
// need one — `make llm-fixtures` and a non-`--offline` `make ask` call the API — and
// having somewhere to keep it beats keeping it in shell history. Stored in
// localStorage, which is per-browser and readable by anything running on this origin,
// so it is a convenience for a demo machine and not a secret store.
function ApiKeyField() {
  const [key, setKey] = useStored('tallytrace.apiKey', '')
  const [shown, setShown] = useState(false)
  const masked = key ? `${key.slice(0, 7)}${'•'.repeat(Math.max(key.length - 11, 4))}${key.slice(-4)}` : ''

  return (
    <div className="bg-white rounded-2xl border border-divider p-6 flex flex-col gap-3">
      <div>
        <h2 className="text-sm font-medium text-muted uppercase tracking-wide">Anthropic API key</h2>
        <p className="text-xs text-muted mt-1 leading-relaxed">
          Optional. Every number here was produced with <span className="font-mono">--offline</span>,
          which refuses the network even with a key set. A key is only needed to ask the{' '}
          <a href="#ask" className="text-primary hover:text-primary-hover font-medium">Ask</a>{' '}
          screen something that is not already in the fixtures.
        </p>
      </div>
      <div className="flex items-center gap-2">
        <input
          type={shown ? 'text' : 'password'}
          value={shown ? key : (key ? masked : '')}
          onChange={(e) => setKey(e.target.value)}
          placeholder="sk-ant-…"
          spellCheck={false}
          autoComplete="off"
          className="flex-1 font-mono text-xs px-3 py-2 border border-divider rounded-lg bg-white
            focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary"
        />
        <button
          onClick={() => setShown(!shown)}
          aria-label={shown ? 'Hide key' : 'Show key'}
          className="p-2 rounded-lg text-muted hover:text-gray-900 hover:bg-card-bg transition-colors"
        >
          {shown ? <EyeOff size={15} /> : <Eye size={15} />}
        </button>
        {key && (
          <button
            onClick={() => setKey('')}
            className="text-xs font-medium text-muted hover:text-danger px-2 py-2"
          >
            Clear
          </button>
        )}
      </div>
      <p className="text-xs text-muted leading-relaxed border-t border-divider pt-3">
        Kept in this browser only. This page never sends it anywhere — the commands read a key
        from the environment:{' '}
        <span className="font-mono">ANTHROPIC_API_KEY=… make ask q=&quot;…&quot;</span>. Treat
        localStorage as a convenience on a machine you control, not a secret store.
      </p>
      {/* A deployed build can answer a question outside the fixtures, and the key that
          does it is not this one and never reaches the browser. Said here because the
          box above would otherwise imply the page cannot reach a model at all. */}
      <p className="text-xs text-muted leading-relaxed">
        A deployed build may also carry its own server-side key, used only to map a new
        question onto one of the ten registered metrics. That key lives in the deployment
        environment, never in this page, and the mapping is labelled on screen wherever it
        happens. It computes nothing: the numbers still come from this scored run.
      </p>
    </div>
  )
}

function PolicyRow({ label, value, hint }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-2 border-b border-divider last:border-0">
      <div className="min-w-0">
        <div className="text-sm text-gray-900">{label}</div>
        {hint && <div className="text-xs text-muted mt-0.5 leading-relaxed">{hint}</div>}
      </div>
      <div className="text-sm font-medium text-gray-900 text-right flex-shrink-0">{value}</div>
    </div>
  )
}

function SettingsPanel({ data }) {
  const policy = data.autoResolutionPolicy || {}
  const overrides = policy.overrides || []

  return (
    <div className="flex flex-col gap-6">
      <CeilingControl policy={policy} />

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 items-start">
        <div className="bg-white rounded-2xl border border-divider p-6">
          <h2 className="text-sm font-medium text-muted uppercase tracking-wide mb-1">
            The rest of the guardrails
          </h2>
          <p className="text-xs text-muted mb-3 leading-relaxed">
            Confidence is an opinion about a pattern; a threshold is a decision about risk.
            Guardrails run after the rule matches, and the opinion never wins.
          </p>
          <PolicyRow
            label="Never auto-resolved"
            hint="Whatever a rule believes about them."
            value={
              <span className="flex flex-col items-end gap-0.5">
                {(policy.never_auto_resolve_causes || []).map((c) => (
                  <span key={c} className="text-xs">{humanise(c)}</span>
                ))}
              </span>
            }
          />
          <PolicyRow
            label="Always a human, by resolution class"
            hint="Closing a row someone else owes money on is not a resolution — it is a write-off nobody authorised."
            value={
              <span className="flex flex-col items-end gap-0.5">
                {(policy.always_human_classes || []).map((c) => (
                  <span key={c} className="text-xs">{humanise(c)}</span>
                ))}
              </span>
            }
          />
          <PolicyRow
            label="Scoped ceilings"
            hint="Set per cause and per channel in config/thresholds.yaml; most specific wins, a tie goes to the stricter."
            value={overrides.length ? `${overrides.length} set` : 'none'}
          />
          {overrides.map((o) => (
            <PolicyRow
              key={o.scope}
              label={o.scope}
              hint={[o.note, o.set_by && `set by ${o.set_by}`].filter(Boolean).join(' · ')}
              value={inr(Number(o.max_variance_inr), { whole: true })}
            />
          ))}
        </div>

        <ApiKeyField />
      </div>
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Is it worth it? — what the tool cost and what it did
// --------------------------------------------------------------------------- //

function UtilityPanel({ data, allWeeks }) {
  const totals = data.totals || {}
  const spend = allWeeks.reduce((sum, w) => sum + (w.stats.costInr || 0), 0)
  const tokens = allWeeks.reduce((sum, w) => sum + (w.stats.tokens || 0), 0)
  const rows = totals.settlement_rows || 1
  const first = allWeeks[0]?.stats
  const last = allWeeks[allWeeks.length - 1]?.stats

  const figures = [
    ['Records reconciled', totals.records_processed?.toLocaleString('en-IN'), `${rows.toLocaleString('en-IN')} settlement rows`],
    ['Model spend, all ten batches', inr(spend), `${inr(spend / rows)} per settlement row`],
    ['Tokens', tokens.toLocaleString('en-IN'), data.tokensEstimated ? 'estimated from a recorded transcript' : 'metered by the API'],
    ['Auto-resolution precision', pct(totals.auto_resolution_precision_pct, 2), `over ${totals.auto_resolutions_attempted ?? '—'} scored resolutions`],
    ['Decisions a human makes', `${last?.touchpointRate}%`, `from ${first?.touchpointRate}% in week 1`],
    ['Rows a human still owns', `${last?.manualReviewRate}%`, `from ${first?.manualReviewRate}% in week 1`],
    ['Open exceptions', totals.open_exceptions, `${inr(Number(totals.open_exception_impact_inr || 0), { whole: true })} in question`],
    ['Claims recovered', `${totals.claims_recovered} of ${totals.claims_opened}`, `${inr(Number(totals.rupees_recovered || 0), { whole: true })} back`],
  ]

  return (
    <div className="flex flex-col gap-6">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {figures.map(([label, value, sub]) => (
          <div key={label} className="bg-white rounded-2xl border border-divider p-5">
            <div className="text-xs text-muted leading-tight">{label}</div>
            <div className="text-2xl font-bold text-gray-900 mt-1 tabular-nums">{value ?? '—'}</div>
            <div className="text-xs text-muted mt-0.5 leading-snug">{sub}</div>
          </div>
        ))}
      </div>
      <p className="text-xs text-muted leading-relaxed max-w-3xl">
        Both review series are shown because reporting only the flattering one is the failure
        this harness exists to catch. “Rows a human still owns” is the strict reading;
        “decisions a human makes” counts a batch proposal once, however many rows it collapses.
      </p>
    </div>
  )
}
