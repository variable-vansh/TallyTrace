import {
  CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { TrendingDown, TrendingUp, Minus, ShieldCheck, Brain, IndianRupee, Layers, Gavel } from 'lucide-react'
import AutomationSplit from '../components/AutomationSplit'
import StatCard from '../components/StatCard'
import StatusBadge from '../components/StatusBadge'
import { inr, pct } from '../lib/format'

const AXIS = { fill: '#6B7280', fontSize: 12 }
const TOOLTIP = {
  contentStyle: { borderRadius: '8px', border: '1px solid #E7E7EA', fontSize: 12 },
  itemStyle: { fontWeight: 600 },
}

function Figure({ label, value, sub, tone = 'text-gray-900' }) {
  return (
    <div>
      <div className="text-xs text-muted uppercase tracking-wide">{label}</div>
      <div className={`text-2xl font-bold ${tone}`}>{value ?? '—'}</div>
      {sub && <div className="text-xs text-muted">{sub}</div>}
    </div>
  )
}

export default function Dashboard({ weekData, allWeeks, selectedWeek, onSelectWeek, data }) {
  const prevWeek = allWeeks[selectedWeek - 1]
  const stats = weekData.stats
  // The ceiling is a number finance sets, so it is read from the run rather than
  // typed here. A hardcoded ₹500 would be a component quietly disagreeing with config.
  const policy = data.autoResolutionPolicy
  const overrides = policy?.overrides ?? []

  const delta = (key) => (prevWeek ? stats[key] - prevWeek.stats[key] : undefined)

  const reviewDelta = prevWeek
    ? Number((stats.manualReviewRate - prevWeek.stats.manualReviewRate).toFixed(2))
    : 0
  const DeltaIcon = reviewDelta > 0 ? TrendingUp : reviewDelta < 0 ? TrendingDown : Minus
  // A falling review rate is the good direction, so down is green.
  const deltaColor = reviewDelta > 0 ? 'text-danger' : reviewDelta < 0 ? 'text-success' : 'text-muted'

  const trend = allWeeks.map((w, i) => ({
    name: `W${w.week}`,
    precision: data.precisionTrend[i],
  }))

  return (
    <div className="flex flex-col gap-6">
      {/* Hero + the three-way split */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* The merchant's question, not the tool's. A dashboard that leads with its own
            review rate is a dashboard about itself; the seller opened it to find out
            what is wrong with the books. The tool's own numbers still appear — under
            the money, and in full on Report & Settings. */}
        <div className="bg-white rounded-2xl border border-divider p-6 flex flex-col justify-center">
          <h2 className="text-sm font-medium text-muted uppercase tracking-wide mb-2">
            Unexplained this week
          </h2>
          <div className="text-4xl font-bold text-gray-900 mb-1">
            {inr(stats.rupeesEscalated, { whole: true })}
          </div>
          <p className="text-xs text-muted mb-3">
            across {stats.flaggedForReview - stats.autoResolved} rows nobody has accounted for
            yet · {inr(stats.rupeesAutoResolved, { whole: true })} closed automatically
          </p>
          <div className="pt-3 border-t border-divider">
            <div className="flex items-baseline justify-between gap-2">
              <span className="text-xs text-muted">Being chased from platforms</span>
              <span className="text-sm font-semibold text-gray-900">
                {inr(Number(data.totals?.rupees_open ?? 0), { whole: true })}
              </span>
            </div>
            <div className="flex items-baseline justify-between gap-2 mt-1">
              <span className="text-xs text-muted">Recovered so far</span>
              <span className="text-sm font-semibold text-success">
                {inr(Number(data.totals?.rupees_recovered ?? 0), { whole: true })}
              </span>
            </div>
          </div>
          <div className="mt-3 pt-3 border-t border-divider flex items-center gap-2 flex-wrap">
            <StatusBadge variant="muted">{pct(stats.manualReviewRate)} of rows with a human</StatusBadge>
            {prevWeek && (
              <span className={`flex items-center gap-1 text-xs font-medium ${deltaColor}`}>
                <DeltaIcon size={13} />
                {Math.abs(reviewDelta)} pts
              </span>
            )}
          </div>
        </div>

        {/* What replaced the review-rate trend: the split a person actually asks
            about — matcher, model, or me — with the AI's own contribution named
            in issues closed rather than in a rate that only falls. */}
        <div className="lg:col-span-2">
          <AutomationSplit
            allWeeks={allWeeks}
            selectedWeek={selectedWeek}
            onSelectWeek={onSelectWeek}
          />
        </div>
      </div>

      {/* Stat row */}
      <div className="grid grid-cols-1 md:grid-cols-3 lg:grid-cols-6 gap-4">
        <StatCard label="Settlement rows" value={stats.totalTransactions} delta={delta('totalTransactions')} />
        <StatCard label="Auto-matched" value={stats.autoMatched} delta={delta('autoMatched')} icon={ShieldCheck} />
        <StatCard label="Auto-resolved" value={stats.autoResolved} delta={delta('autoResolved')} icon={Brain} />
        <StatCard label="Flagged for review" value={stats.flaggedForReview} delta={delta('flaggedForReview')} />
        <StatCard label="Batch proposals" value={stats.bulkFixOpportunities} delta={delta('bulkFixOpportunities')} icon={Layers} />
        <StatCard label="Claims opened" value={weekData.claims?.opened?.length ?? 0} icon={Gavel} />
      </div>

      {/* Claims: the deadline clock, on the front page rather than three screens in */}
      <div className="bg-white rounded-2xl border border-divider p-6">
        <div className="flex items-baseline justify-between flex-wrap gap-2">
          <h2 className="text-sm font-medium text-muted uppercase tracking-wide">
            Claims queue
          </h2>
          <span className="text-xs text-muted">
            whole register, end of batch {allWeeks.length} · sorted by expiry, not creation date
          </span>
        </div>
        <p className="text-xl font-bold text-gray-900 mt-1">{data.claimsQueue?.header}</p>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mt-4 pt-4 border-t border-divider">
          <Figure label="Opened" value={data.totals?.claims_opened} />
          <Figure label="Recovered" value={data.totals?.claims_recovered}
                  sub={inr(Number(data.totals?.rupees_recovered ?? 0), { whole: true })}
                  tone="text-success" />
          <Figure label="Expired" value={data.totals?.claims_expired}
                  sub={inr(Number(data.totals?.rupees_expired ?? 0), { whole: true })}
                  tone="text-danger" />
          <Figure label="Recovery rate" value={pct(data.totals?.claim_recovery_rate_pct, 2)}
                  sub="of settled claims" />
        </div>
        <p className="text-xs text-muted mt-4 leading-relaxed">
          Amazon&rsquo;s SAFE-T window is 30 days; a TCS discrepancy has to be raised before the
          10th of the following month or the GSTR-8 correction misses its return. Sellers lose
          this money because they discover the loss at reconciliation time, which is already
          late — so the clock starts the moment the reconciliation surfaces it.
        </p>
      </div>

      {/* Precision + money */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="bg-white rounded-2xl border border-divider p-6 lg:col-span-2 h-64 flex flex-col">
          <h2 className="text-sm font-medium text-muted uppercase tracking-wide mb-1">
            Auto-resolution precision, scored against ground truth
          </h2>
          <p className="text-xs text-muted mb-3">
            A declining review rate only means something if this line holds. Overall:{' '}
            <span className="font-semibold text-gray-900">{pct(data.overallPrecision, 2)}</span>
          </p>
          <div className="flex-1">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={trend} margin={{ top: 4, right: 8, left: -20, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#E7E7EA" vertical={false} />
                <XAxis dataKey="name" axisLine={false} tickLine={false} tick={AXIS} />
                <YAxis domain={[80, 100]} axisLine={false} tickLine={false} tick={AXIS} unit="%" />
                <Tooltip {...TOOLTIP} formatter={(v) => [`${v}%`, 'Precision']} />
                <Line
                  type="monotone" dataKey="precision" stroke="#1FAA59" strokeWidth={3}
                  connectNulls dot={{ r: 3, fill: '#1FAA59' }} isAnimationActive={false}
                  activeDot={{ r: 6, fill: '#1FAA59', stroke: 'white', strokeWidth: 2 }}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>

        <div className="bg-white rounded-2xl border border-divider p-6 flex flex-col gap-4">
          <h2 className="text-sm font-medium text-muted uppercase tracking-wide">
            This week, in rupees
          </h2>
          <div>
            <div className="flex items-center gap-1.5 text-xs text-muted mb-0.5">
              <IndianRupee size={12} /> Auto-resolved without a human
            </div>
            <div className="text-2xl font-bold text-success">{inr(stats.rupeesAutoResolved)}</div>
          </div>
          <div>
            <div className="flex items-center gap-1.5 text-xs text-muted mb-0.5">
              <IndianRupee size={12} /> Escalated to a human
            </div>
            <div className="text-2xl font-bold text-gray-900">{inr(stats.rupeesEscalated)}</div>
          </div>
          <div className="text-xs text-muted leading-relaxed border-t border-divider pt-3">
            <p>
              The gap is the guardrails working. Anything above the{' '}
              <span className="font-medium text-gray-900">
                {inr(policy?.defaultCeilingInr, { whole: true })}
              </span>{' '}
              default ceiling is refused automation however confident the rule is — so the
              system automates volume and escalates value.
            </p>
            {overrides.length > 0 && (
              <ul className="mt-2 space-y-1">
                {overrides.map((o) => (
                  <li key={o.scope} className="flex justify-between gap-3">
                    <span className="truncate" title={o.note || undefined}>
                      {o.scope.replace(/(cause|channel)=/g, '')}
                      {o.set_by && <span className="text-gray-400"> · {o.set_by}</span>}
                    </span>
                    <span className="font-medium text-gray-900 whitespace-nowrap">
                      {inr(Number(o.max_variance_inr), { whole: true })}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div className="text-xs text-muted border-t border-divider pt-3">
            <div className="flex justify-between"><span>Rules learned</span><span className="font-medium text-gray-900">{stats.rulesLearned}</span></div>
            <div className="flex justify-between"><span>Promoted to active</span><span className="font-medium text-gray-900">{stats.rulesPromoted}</span></div>
            <div className="flex justify-between"><span>Retired</span><span className="font-medium text-gray-900">{stats.rulesRetired}</span></div>
            <div className="flex justify-between mt-1.5 pt-1.5 border-t border-divider">
              <span>Model spend</span>
              <span className="font-medium text-gray-900">
                {inr(stats.costInr)} · {inr(stats.costPerTransactionInr)}/txn
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
