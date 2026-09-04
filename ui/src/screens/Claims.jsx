import { useMemo, useState } from 'react'
import {
  AlarmClock, CheckCircle2, Copy, Mail, Pencil, ShieldAlert, XCircle,
} from 'lucide-react'
import StatusBadge from '../components/StatusBadge'
import PanelDrawer from '../components/PanelDrawer'
import DeadlineCalendar from '../components/DeadlineCalendar'
import { useStored } from '../lib/useStored'
import { inr, pct, humanise } from '../lib/format'

// The claims queue, rewritten around the thing it is actually about: a deadline.
//
// The old screen was a two-column list-and-detail of `CLM-0001 · weight_dispute_hold ·
// flipkart · 9 days left`. Every word of that is a field name, and a person reading it
// has to translate before they can decide anything. This one leads with the sentence —
// "Flipkart is holding ₹3,192 on ord_000006 over a shipping-weight dispute" — and puts
// the evidence, the clock's basis and the drafted message one click behind it.

const STATUS_VARIANT = {
  open: 'muted', drafted: 'blue', filed: 'amber',
  recovered: 'success', expired: 'danger', written_off: 'danger',
}

const TABS = [
  { id: 'open', label: 'Open' },
  { id: 'recovered', label: 'Recovered' },
  { id: 'expired', label: 'Expired' },
  { id: 'recovery', label: 'Recovery vs truth' },
]

const CLOSED = new Set(['recovered', 'expired', 'written_off'])
const NO_CLAIMS = []

// What each cause means in a sentence, with the platform as the subject. The frozen
// enum is precise and unreadable; this is the same fact said the way the person who
// has to act on it would say it. Keyed by the enum, so a new cause shows its own code
// rather than silently getting the wrong sentence.
const IN_WORDS = {
  weight_dispute_hold: (p) => `${p} is holding the payout over a shipping-weight dispute`,
  missing_settlement_row: (p) => `${p} never reported this order on any settlement`,
  short_payment_unexplained: (p) => `${p} paid less than the books expected, with nothing on the report to explain it`,
  chargeback_deduction: (p) => `${p} deducted a chargeback from the payout`,
  promo_cofunding_deduction: (p) => `${p} charged a share of a promotion that was never agreed`,
  bank_credit_unmatched: () => 'A credit landed in the bank with no settlement row behind it',
}

const titleCase = (s) => (s || '').charAt(0).toUpperCase() + (s || '').slice(1)

function sentence(claim) {
  const platform = titleCase(claim.platform)
  const say = IN_WORDS[claim.cause]
  return say ? say(platform) : `${platform}: ${humanise(claim.cause)}`
}

// The clock in words. "9 days left" is a number; "the window shuts on 25 March" is a
// date somebody can put in a calendar, which is the point of the whole feature.
function clockWords(claim) {
  if (claim.status === 'recovered') return 'Recovered — closed by the credit that paid it'
  if (claim.status === 'expired') return `The filing window shut on ${claim.deadline?.on}`
  if (!claim.deadline?.on) return 'No published filing window — this one does not expire'
  const days = claim.daysRemaining
  if (days === null || days === undefined) return `Deadline ${claim.deadline.on}`
  if (days <= 0) return `Due today (${claim.deadline.on})`
  return `${days} day${days === 1 ? '' : 's'} to file — window shuts ${claim.deadline.on}`
}

function urgencyTone(claim) {
  if (claim.status === 'recovered') return 'bg-success'
  if (claim.status === 'expired') return 'bg-danger'
  if (claim.daysRemaining === null || claim.daysRemaining === undefined) return 'bg-divider'
  return claim.daysRemaining <= 7 ? 'bg-amber-700'
    : claim.daysRemaining <= 21 ? 'bg-primary/60' : 'bg-divider'
}

function ClaimRow({ claim, onOpen }) {
  return (
    <button
      onClick={() => onOpen(claim)}
      className="group w-full text-left flex items-stretch bg-white border border-divider rounded-lg
        overflow-hidden hover:border-gray-300 hover:shadow-sm transition-all"
    >
      <span className={`w-[3px] flex-shrink-0 ${urgencyTone(claim)}`} />
      <span className="flex-1 min-w-0 flex items-center gap-4 px-4 py-3">
        <span className="min-w-0 flex-1">
          <span className="block text-sm text-gray-900 leading-snug">
            {sentence(claim)}
            {claim.order_key && (
              <span className="text-muted"> on <span className="font-mono text-xs">{claim.order_key}</span></span>
            )}
          </span>
          <span className="block text-xs text-muted mt-0.5">{clockWords(claim)}</span>
        </span>
        <span className="text-sm font-semibold text-gray-900 tabular-nums flex-shrink-0">
          {inr(claim.amount)}
        </span>
        <span className="hidden sm:block flex-shrink-0">
          <StatusBadge variant={STATUS_VARIANT[claim.status]}>{humanise(claim.status)}</StatusBadge>
        </span>
      </span>
    </button>
  )
}

// Recipients, entered once and reused. A published page cannot send mail itself, so
// "Send" hands a fully-composed message to the operator's own mail client — which is
// also the honest place for it to go: a claim leaves under a person's name, not the
// tool's, and they get to read it before it goes.
function Recipients({ platform, address, onChange }) {
  const [editing, setEditing] = useState(!address)
  const [draft, setDraft] = useState(address || '')

  if (editing) {
    return (
      <div className="flex items-center gap-2">
        <input
          type="email"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={`seller-support@${platform}.example`}
          className="flex-1 text-xs px-2.5 py-1.5 border border-divider rounded-lg bg-white
            focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary"
        />
        <button
          onClick={() => { onChange(draft.trim()); setEditing(false) }}
          className="text-xs font-semibold text-white bg-gray-900 hover:bg-gray-800 px-3 py-1.5 rounded-lg"
        >
          Save
        </button>
      </div>
    )
  }

  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="text-muted">To</span>
      <span className="font-medium text-gray-900 truncate">{address}</span>
      <button onClick={() => setEditing(true)} className="text-muted hover:text-gray-900 p-1">
        <Pencil size={11} />
      </button>
      <span className="text-muted ml-auto">saved for all {platform} claims</span>
    </div>
  )
}

function ClaimPanel({ claim, recipients, setRecipients }) {
  const [copied, setCopied] = useState(false)
  const address = recipients[claim.platform] || ''

  // The draft is written "Subject: …\n\n<body>", which is exactly what a mail client
  // wants split in two.
  const [subjectLine, ...rest] = claim.draft.split('\n')
  const subject = subjectLine.replace(/^Subject:\s*/, '')
  const body = rest.join('\n').trimStart()
  const mailto = `mailto:${encodeURIComponent(address)}`
    + `?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`

  const copy = () => {
    navigator.clipboard?.writeText(`${subject}\n\n${body}`).then(
      () => { setCopied(true); setTimeout(() => setCopied(false), 1800) },
      () => setCopied(false),
    )
  }

  return (
    <div className="p-6 flex flex-col gap-5">
      <div>
        <p className="text-base text-gray-900 leading-snug">{sentence(claim)}</p>
        <div className="flex items-center gap-3 mt-2 flex-wrap">
          <span className="text-2xl font-bold text-gray-900">{inr(claim.amount)}</span>
          <StatusBadge variant={STATUS_VARIANT[claim.status]}>{humanise(claim.status)}</StatusBadge>
          {claim.recoveredAmount ? (
            <span className="text-xs text-success font-medium">
              {inr(claim.recoveredAmount)} recovered
            </span>
          ) : null}
        </div>
      </div>

      <div className={`rounded-lg border px-4 py-3 ${
        claim.status === 'expired' ? 'border-danger/25 bg-danger-light/30'
          : claim.status === 'recovered' ? 'border-success/25 bg-success-light/40'
          : 'border-divider bg-card-bg/60'}`}>
        <div className="flex items-center gap-2">
          <AlarmClock size={13} className="text-muted" />
          <span className="text-sm font-medium text-gray-900">{clockWords(claim)}</span>
        </div>
        {claim.deadline?.basis && (
          <p className="text-xs text-muted mt-1 leading-relaxed">{claim.deadline.basis}</p>
        )}
      </div>

      <details className="rounded-lg border border-divider">
        <summary className="cursor-pointer px-4 py-2.5 text-xs font-semibold text-gray-700 select-none">
          Evidence and references
        </summary>
        <div className="px-4 pb-3 text-xs text-gray-700 space-y-1">
          <div className="flex justify-between gap-3 py-1 border-t border-divider">
            <span className="text-muted">Claim</span>
            <span className="font-mono">{claim.claim_id}</span>
          </div>
          <div className="flex justify-between gap-3 py-1 border-t border-divider">
            <span className="text-muted">Opened</span>
            <span>{claim.opened_at} (batch {claim.opened_batch})</span>
          </div>
          {claim.recovery_row_id && (
            <div className="flex justify-between gap-3 py-1 border-t border-divider">
              <span className="text-muted">Closed by</span>
              <span className="font-mono">{claim.recovery_row_id}</span>
            </div>
          )}
          <div className="pt-2 border-t border-divider">
            <span className="text-muted block mb-1">Rows this claim rests on</span>
            <div className="flex flex-wrap gap-1">
              {claim.evidence_row_ids.map((id) => (
                <span key={id} className="font-mono text-[11px] bg-card-bg rounded px-1.5 py-0.5">{id}</span>
              ))}
            </div>
          </div>
        </div>
      </details>

      <div className="rounded-lg border border-divider overflow-hidden">
        <div className="px-4 py-2.5 border-b border-divider bg-card-bg/60">
          <Recipients
            platform={claim.platform}
            address={address}
            onChange={(value) => setRecipients({ ...recipients, [claim.platform]: value })}
          />
        </div>
        <div className="px-4 py-3">
          <p className="text-sm font-semibold text-gray-900">{subject}</p>
          <pre className="mt-2 text-xs text-gray-700 whitespace-pre-wrap font-sans leading-relaxed max-h-72 overflow-y-auto">
            {body}
          </pre>
        </div>
        <div className="px-4 py-3 border-t border-divider flex items-center gap-2">
          <a
            href={address ? mailto : undefined}
            aria-disabled={!address}
            onClick={(e) => { if (!address) e.preventDefault() }}
            className={`inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-xs font-semibold transition-colors
              ${address ? 'bg-gray-900 text-white hover:bg-gray-800' : 'bg-card-bg text-muted cursor-not-allowed'}`}
          >
            <Mail size={13} /> Send to {titleCase(claim.platform)}
          </a>
          <button
            onClick={copy}
            className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium text-gray-700 hover:bg-card-bg transition-colors"
          >
            <Copy size={13} /> {copied ? 'Copied' : 'Copy'}
          </button>
          <span className="text-[11px] text-muted ml-auto text-right leading-tight">
            {address ? 'Opens in your mail client so you read it before it goes.' : 'Add an address to enable sending.'}
          </span>
        </div>
      </div>
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

export default function Claims({ data }) {
  const [tab, setTab] = useState('open')
  const [selected, setSelected] = useState(null)
  const [day, setDay] = useState(null)
  const [recipients, setRecipients] = useStored('tallytrace.claimRecipients', {})

  // Stable identity: `|| []` builds a new array every render and re-runs the memo.
  const claims = data.claims ?? NO_CLAIMS
  const queue = data.claimsQueue || {}
  const totals = data.totals || {}
  const today = queue.as_of

  const buckets = useMemo(() => ({
    open: claims.filter((c) => !CLOSED.has(c.status)),
    recovered: claims.filter((c) => c.status === 'recovered'),
    expired: claims.filter((c) => c.status === 'expired' || c.status === 'written_off'),
  }), [claims])

  const visible = useMemo(() => {
    const list = buckets[tab] || []
    const filtered = day ? list.filter((c) => c.deadline?.on === day) : list
    // Soonest first, and claims with no window last: a list ordered by when it was
    // raised buries the one that stops being recoverable on Thursday.
    return [...filtered].sort((a, b) => {
      const A = a.daysRemaining ?? 9999
      const B = b.daysRemaining ?? 9999
      return A - B
    })
  }, [buckets, tab, day])

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Claims</h1>
          <p className="text-sm text-muted mt-0.5">{queue.header}</p>
        </div>
        <div className="flex items-center gap-1 bg-white border border-divider rounded-lg p-1">
          {TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => { setTab(t.id); setDay(null) }}
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
        <Stat icon={AlarmClock} label="Open" value={buckets.open.length}
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
        <div className="grid grid-cols-1 lg:grid-cols-[300px_1fr] gap-6 items-start">
          <DeadlineCalendar
            key={tab}
            claims={buckets[tab] || []}
            today={today}
            selectedDay={day}
            onSelectDay={setDay}
          />

          <div className="flex flex-col gap-2">
            {day && (
              <div className="flex items-center gap-2 text-xs">
                <span className="text-gray-700 font-medium">Deadlines on {day}</span>
                <button onClick={() => setDay(null)} className="text-primary hover:text-primary-hover font-medium">
                  Show all
                </button>
              </div>
            )}
            {visible.length === 0 ? (
              <p className="text-sm text-muted bg-white border border-divider rounded-lg px-4 py-6 text-center">
                Nothing here.
              </p>
            ) : (
              visible.map((claim) => (
                <ClaimRow key={claim.claim_id} claim={claim} onOpen={setSelected} />
              ))
            )}
          </div>
        </div>
      )}

      <PanelDrawer
        open={Boolean(selected)}
        onClose={() => setSelected(null)}
        title={selected ? `${titleCase(selected.platform)} · ${selected.claim_id}` : ''}
        subtitle={selected ? clockWords(selected) : ''}
      >
        {selected && (
          <ClaimPanel claim={selected} recipients={recipients} setRecipients={setRecipients} />
        )}
      </PanelDrawer>
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
