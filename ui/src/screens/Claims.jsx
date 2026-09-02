import { useMemo, useState } from 'react'
import {
  AlarmClock, CheckCircle2, FileText, Hourglass, ShieldAlert, XCircle,
} from 'lucide-react'
import StatusBadge from '../components/StatusBadge'
import { inr, pct, humanise } from '../lib/format'

// Sorted by expiry, never by creation date. A claims list ordered by when it was
// raised buries the one that stops being recoverable on Thursday, which is exactly
// how a seller loses a SAFE-T window they were already looking at.
const STATUS_VARIANT = {
  open: 'muted',
  drafted: 'blue',
  filed: 'amber',
  recovered: 'success',
  expired: 'danger',
  written_off: 'danger',
}

const TABS = [
  { id: 'open', label: 'Open' },
  { id: 'recovered', label: 'Recovered' },
  { id: 'expired', label: 'Expired' },
  { id: 'recovery', label: 'Recovery vs truth' },
]

const CLOSED = new Set(['recovered', 'expired', 'written_off'])

function Clock({ days }) {
  if (days === null || days === undefined) {
    return <span className="text-xs text-muted">no configured window</span>
  }
  const variant = days <= 7 ? 'danger' : days <= 14 ? 'amber' : 'muted'
  return <StatusBadge variant={variant}>{days} days left</StatusBadge>
}

function Row({ claim, selected, onSelect }) {
  return (
    <button
      onClick={() => onSelect(claim.claim_id)}
      className={`w-full text-left border-b border-divider px-5 py-3 hover:bg-gray-50 transition-colors ${
        selected ? 'bg-blue-50/60' : ''
      }`}
    >
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2 min-w-0">
          <span className="font-mono text-xs font-semibold text-gray-900">{claim.claim_id}</span>
          <StatusBadge variant={STATUS_VARIANT[claim.status]}>{humanise(claim.status)}</StatusBadge>
          <span className="text-sm text-gray-800 truncate">{humanise(claim.cause)}</span>
        </div>
        <div className="flex items-center gap-3 flex-shrink-0">
          <span className="text-xs text-muted capitalize">{claim.platform}</span>
          <span className="font-semibold text-gray-900">{inr(claim.amount)}</span>
          {CLOSED.has(claim.status) ? null : <Clock days={claim.daysRemaining} />}
        </div>
      </div>
      <div className="text-xs text-muted mt-1">
        {claim.order_key ? `order ${claim.order_key} · ` : ''}
        opened batch {claim.opened_batch}
        {claim.deadline.on ? ` · deadline ${claim.deadline.on}` : ' · no filing window'}
        {claim.recovery_row_id ? ` · closed by ${claim.recovery_row_id}` : ''}
      </div>
    </button>
  )
}

function Detail({ claim }) {
  if (!claim) {
    return (
      <div className="bg-white rounded-2xl border border-divider p-6 text-sm text-muted">
        Select a claim to see its evidence, its clock and the message drafted for it.
      </div>
    )
  }
  return (
    <div className="bg-white rounded-2xl border border-divider p-6 flex flex-col gap-5">
      <div>
        <div className="flex items-center gap-2 flex-wrap mb-1">
          <span className="font-mono text-sm font-semibold text-gray-900">{claim.claim_id}</span>
          <StatusBadge variant={STATUS_VARIANT[claim.status]}>{humanise(claim.status)}</StatusBadge>
          <StatusBadge variant="muted">{claim.resolution_class}</StatusBadge>
        </div>
        <div className="text-2xl font-bold text-gray-900">{inr(claim.amount)}</div>
        <p className="text-xs text-muted mt-1">
          {humanise(claim.cause)} on <span className="capitalize">{claim.platform}</span>
          {claim.order_key ? `, order ${claim.order_key}` : ''}
        </p>
      </div>

      <div className="border-t border-divider pt-4">
        <div className="flex items-center gap-2 mb-1">
          <AlarmClock size={15} className="text-muted" />
          <span className="text-xs font-semibold text-gray-900 uppercase tracking-wide">
            Filing clock
          </span>
        </div>
        <p className="text-sm text-gray-800">
          {claim.deadline.on ? `Closes ${claim.deadline.on}` : 'No configured filing window'}
        </p>
        <p className="text-xs text-muted mt-0.5">{claim.deadline.basis}</p>
      </div>

      <div className="border-t border-divider pt-4">
        <span className="text-xs font-semibold text-gray-900 uppercase tracking-wide">
          Evidence
        </span>
        <div className="flex flex-wrap gap-1.5 mt-2">
          {claim.evidence.map((row) => (
            <span key={`${row.table}-${row.row_id}`}
                  className="font-mono text-[11px] bg-gray-100 text-gray-700 rounded px-2 py-0.5">
              {row.row_id}
              <span className="text-muted"> · {row.table.replace('_', ' ')}</span>
            </span>
          ))}
        </div>
        <p className="text-xs text-muted mt-2">
          Cause proposed by {claim.cause_source === 'rule' ? 'a learned rule' : 'the model’s hypothesis'}.
        </p>
      </div>

      <div className="border-t border-divider pt-4">
        <span className="text-xs font-semibold text-gray-900 uppercase tracking-wide">
          History
        </span>
        <ol className="mt-2 space-y-1.5">
          {claim.transitions.map((t, i) => (
            <li key={i} className="text-xs text-gray-700">
              <span className="font-medium text-gray-900">batch {t.batch}</span>{' '}
              {t.from_status} → <span className="font-medium">{t.to_status}</span>
              <div className="text-muted">{t.reason}</div>
            </li>
          ))}
        </ol>
      </div>

      {claim.draft && (
        <div className="border-t border-divider pt-4">
          <div className="flex items-center gap-2 mb-2">
            <FileText size={15} className="text-muted" />
            <span className="text-xs font-semibold text-gray-900 uppercase tracking-wide">
              Drafted message
            </span>
          </div>
          <pre className="text-[11px] leading-relaxed text-gray-800 bg-gray-50 border border-divider rounded-lg p-3 overflow-x-auto whitespace-pre-wrap">
{claim.draft}
          </pre>
          <p className="text-xs text-muted mt-2">
            The model wrote the subject, the statement and the request, and was forbidden a
            numeral by its schema. Every figure above was substituted from the matcher’s
            verdicts.
          </p>
        </div>
      )}
    </div>
  )
}

export default function Claims({ data, allWeeks }) {
  const [tab, setTab] = useState('open')
  const [selectedId, setSelectedId] = useState(null)

  const buckets = useMemo(() => {
    const all = data.claims || []
    return {
      all,
      open: all.filter((c) => !CLOSED.has(c.status)),
      recovered: all.filter((c) => c.status === 'recovered'),
      expired: all.filter((c) => c.status === 'expired'),
    }
  }, [data.claims])

  const visible = buckets[tab] || []
  const selected = buckets.all.find((c) => c.claim_id === selectedId) || visible[0]
  const queue = data.claimsQueue || {}
  const totals = data.totals || {}

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Claims</h1>
          <p className="text-sm text-gray-700 mt-1">{queue.header}</p>
          <p className="text-xs text-muted mt-0.5">
            The whole register, as it stands at the end of batch {allWeeks.length}. It does
            not follow the batch selector: a filing window does not reset because you are
            looking at an earlier week.
          </p>
          <p className="text-xs text-muted mt-1">
            Sorted by expiry, not by creation date. Amazon’s SAFE-T window is 30 days and a
            TCS discrepancy has to be raised before the 10th of the following month — sellers
            miss these because they only discover the loss at reconciliation, which is already
            late.
          </p>
        </div>
        <div className="flex items-center gap-1 bg-white border border-divider rounded-lg p-1">
          {TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => { setTab(t.id); setSelectedId(null) }}
              className={`px-3 py-1.5 rounded-md text-xs font-semibold transition-colors ${
                tab === t.id ? 'bg-gray-900 text-white' : 'text-gray-600 hover:bg-gray-50'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <Stat icon={Hourglass} label="Open" value={buckets.open.length}
              sub={inr(Number(totals.rupees_open ?? queue.totalInr ?? 0), { whole: true })} />
        <Stat icon={CheckCircle2} label="Recovered" value={buckets.recovered.length}
              sub={inr(Number(totals.rupees_recovered ?? 0), { whole: true })} tone="text-success" />
        <Stat icon={XCircle} label="Expired" value={buckets.expired.length}
              sub={inr(Number(totals.rupees_expired ?? 0), { whole: true })} tone="text-danger" />
        <Stat icon={ShieldAlert} label="Recovery rate" value={pct(totals.claim_recovery_rate_pct, 2)}
              sub="of settled claims" />
      </div>

      {tab === 'recovery' ? (
        <RecoveryAgainstTruth data={data} />
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 items-start">
          <div className="bg-white rounded-2xl border border-divider overflow-hidden">
            {visible.length === 0 ? (
              <p className="text-sm text-muted p-6">Nothing in this bucket.</p>
            ) : (
              visible.map((claim) => (
                <Row key={claim.claim_id} claim={claim}
                     selected={selected?.claim_id === claim.claim_id}
                     onSelect={setSelectedId} />
              ))
            )}
          </div>
          <Detail claim={selected} />
        </div>
      )}
    </div>
  )
}

function Stat({ icon: Icon, label, value, sub, tone = 'text-gray-900' }) {
  return (
    <div className="bg-white rounded-2xl border border-divider p-5">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-muted uppercase tracking-wide">{label}</span>
        <Icon size={16} className="text-muted" />
      </div>
      <div className={`text-2xl font-bold mt-1 ${tone}`}>{value}</div>
      <div className="text-xs text-muted mt-0.5">{sub}</div>
    </div>
  )
}

function RecoveryAgainstTruth({ data }) {
  const planted = data.plantedRecoveries || []
  const attribution = data.claimAttribution || []
  const caught = planted.filter((p) => p.linked_correctly).length

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 items-start">
      <div className="bg-white rounded-2xl border border-divider p-6">
        <h2 className="text-sm font-medium text-muted uppercase tracking-wide">
          Planted recovery pairs
        </h2>
        <p className="text-xs text-muted mt-0.5 mb-3">
          The generator planted reimbursements in later batches. {caught} of {planted.length}{' '}
          auto-closed against the credit that paid them.
        </p>
        <div className="space-y-2">
          {planted.map((entry) => (
            <div key={entry.row_id}
                 className={`rounded-xl border p-3 ${
                   entry.linked_correctly
                     ? 'border-success/30 bg-success-light/30'
                     : 'border-divider bg-gray-50'
                 }`}>
              <div className="flex items-center justify-between gap-2 flex-wrap">
                <span className="font-mono text-xs font-semibold text-gray-900">
                  {entry.order_id}
                </span>
                <span className="font-semibold text-gray-900">{inr(Number(entry.amount_inr))}</span>
              </div>
              <p className="text-xs text-gray-700 mt-1">
                claimed in batch {entry.claim_batch}, paid in batch {entry.recovery_batch} —{' '}
                <span className={entry.linked_correctly ? 'text-success font-medium' : 'text-muted'}>
                  {entry.outcome}
                </span>
                {entry.claim_id ? ` (${entry.claim_id})` : ''}
              </p>
            </div>
          ))}
        </div>
        <p className="text-xs text-muted mt-4 leading-relaxed">
          The two misses are not link failures. In both, the reimbursement arrived while the
          order was still inside its settlement window, so the matcher never raised it and no
          claim was ever opened to close. They are reported as misses anyway.
        </p>
      </div>

      <div className="bg-white rounded-2xl border border-divider p-6">
        <h2 className="text-sm font-medium text-muted uppercase tracking-wide">
          Did the answer key agree these were claims?
        </h2>
        <p className="text-xs text-muted mt-0.5 mb-3">
          The least flattering table in the build, and it is here on purpose.
        </p>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-muted uppercase tracking-wide border-b border-divider">
              <th className="text-left py-2">Cause claimed</th>
              <th className="text-right py-2">Claims</th>
              <th className="text-right py-2">Confirmed</th>
              <th className="text-right py-2">Self-closed</th>
            </tr>
          </thead>
          <tbody>
            {attribution.map((row) => (
              <tr key={row.cause} className="border-b border-divider last:border-0">
                <td className="py-2 text-gray-800">{humanise(row.cause)}</td>
                <td className="py-2 text-right text-gray-900">{row.claims}</td>
                <td className={`py-2 text-right font-medium ${
                  Number(row.precision_pct) < 50 ? 'text-danger' : 'text-gray-900'
                }`}>
                  {row.precision_pct ? `${row.precision_pct}%` : '—'}
                </td>
                <td className="py-2 text-right text-muted">{row.self_closed_misses}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="text-xs text-muted mt-4 leading-relaxed">
          The queue opens a claim whenever a payout is past its settlement window, and most of
          those turn out to be settlements that were merely late. That bias is deliberate and
          the auto-close is what pays for it: chasing a late payout costs a claim that closes
          itself, and not chasing a genuinely missing one costs the whole payout once the
          window shuts.
        </p>
      </div>
    </div>
  )
}
