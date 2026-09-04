import { useState } from 'react'
import { CheckCircle, ChevronRight, GitBranch, Shield, Sparkles, User, XCircle } from 'lucide-react'
import StatusBadge from './StatusBadge'
import { inr, humanise, OUTCOME_LABEL, OUTCOME_VARIANT } from '../lib/format'

// The screen that answers "would you trust it" — every step the system took on one
// exception, in the order it took them.
//
// It used to render all of it at once: five cards, a fifteen-cell feature grid and a
// JSON blob, with no way to tell which two lines mattered. The provenance is still all
// here and nothing is summarised away, but each step now leads with one sentence a
// person can read and keeps its evidence behind a disclosure. The default view is the
// story; the detail is one click per step for whoever needs it.

function Row({ label, value }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1 border-b border-divider/60 last:border-0">
      {/* min-w-0 on both halves: a reason code like `settlement_outside_date_window`
          is wider than its column and used to overrun the value beside it. */}
      <span className="text-muted text-[11px] uppercase tracking-wide flex-shrink-0">{label}</span>
      <span className="font-medium text-gray-900 text-right min-w-0 truncate" title={value}>
        {value}
      </span>
    </div>
  )
}

function Step({ icon: Icon, title, summary, tone = 'default', badges, children, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen)
  const accent = tone === 'hold' ? 'text-amber-700' : tone === 'pass' ? 'text-success' : 'text-muted'

  return (
    <div className="relative pl-8 pb-5 last:pb-0">
      <span className="absolute left-0 top-0.5 w-[22px] h-[22px] rounded-full bg-white border border-divider flex items-center justify-center">
        <Icon size={12} className={accent} />
      </span>
      {/* The connector stops short of the next node rather than running under it. */}
      <span className="absolute left-[10px] top-7 bottom-1 w-px bg-divider" />

      <div className="text-[11px] font-semibold uppercase tracking-widest text-muted">{title}</div>
      <p className="text-sm text-gray-800 leading-relaxed mt-1">{summary}</p>

      {badges && <div className="flex items-center gap-1.5 flex-wrap mt-2">{badges}</div>}

      {children && (
        <>
          <button
            onClick={() => setOpen(!open)}
            className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-primary hover:text-primary-hover"
          >
            <ChevronRight size={12} className={`transition-transform ${open ? 'rotate-90' : ''}`} />
            {open ? 'Hide detail' : 'Detail'}
          </button>
          {open && <div className="mt-2">{children}</div>}
        </>
      )}
    </div>
  )
}

// The features a rule may look at, in the order a person would ask about them, with
// the raw keys mapped to words. Anything not named here still shows, under its key.
const FEATURE_LABELS = {
  channel: 'Channel',
  reason: 'Reason code',
  bucket: 'Bucket',
  direction: 'Direction',
  variance_inr: 'Variance ₹',
  fee_variance_pct: 'Fee variance %',
  net_variance_pct: 'Net variance %',
  transaction_type: 'Row type',
  days_late: 'Days late',
  days_after_settlement: 'Days after settlement',
  days_since_order: 'Days since order',
}

const DIRECTION_WORDS = {
  short: 'we were paid less than the books expected',
  over: 'we were paid more than the books expected',
  flat: 'the money is right; something about the timing is not',
}

export default function DecisionPath({ exc }) {
  const detail = exc.verdicts?.[0]?.detail || {}
  const features = exc.features || {}
  const guardrails = (exc.guardrails || []).map((g, i) => ({
    name: g.split(':')[0],
    outcome: g.split(':')[1],
    detail: exc.guardrailDetail?.[i] || '',
  }))
  const held = guardrails.filter((g) => g.outcome === 'hold')
  const resolved = exc.outcome === 'auto_resolved'

  const measured = `${humanise(exc.reason)}${exc.channel ? ` on ${exc.channel}` : ''}. `
    + (exc.impact ? `${inr(exc.impact)} in question` : 'No money in question')
    + (DIRECTION_WORDS[features.direction] ? ` — ${DIRECTION_WORDS[features.direction]}` : '')
    + '.'

  return (
    <div className="pt-1">
      <Step icon={GitBranch} title="What the matcher measured" summary={measured}>
        <div className="rounded-lg border border-divider bg-card-bg/60 px-3 py-2 text-xs">
          {Object.entries(features)
            .filter(([, v]) => v !== null && v !== undefined && v !== '')
            .map(([k, v]) => (
              <Row key={k} label={FEATURE_LABELS[k] || k.replace(/_/g, ' ')} value={String(v)} />
            ))}
          {Object.keys(detail).length > 0 && (
            <details className="mt-2">
              <summary className="text-[11px] text-primary cursor-pointer font-medium">
                Raw verdict detail
              </summary>
              <pre className="mt-1.5 text-[11px] bg-white border border-divider rounded p-2 overflow-x-auto">
                {JSON.stringify(detail, null, 1)}
              </pre>
            </details>
          )}
        </div>
      </Step>

      {exc.hypothesis && (
        <Step
          icon={Sparkles}
          title="What the model guessed"
          summary={exc.hypothesis.text}
          badges={
            <>
              <StatusBadge variant="blue">{humanise(exc.hypothesis.cause)}</StatusBadge>
              <span className="text-xs text-muted">
                {(exc.hypothesis.confidence * 100).toFixed(0)}% confidence
              </span>
            </>
          }
        />
      )}

      <Step
        icon={resolved ? CheckCircle : XCircle}
        title="What the rules did"
        tone={resolved ? 'pass' : 'default'}
        summary={exc.decisionNote}
        badges={
          <>
            <StatusBadge variant={OUTCOME_VARIANT[exc.outcome] || 'muted'}>
              {OUTCOME_LABEL[exc.outcome] || exc.outcome}
            </StatusBadge>
            {exc.ruleId && <StatusBadge variant="muted">{exc.ruleId} · {exc.ruleState}</StatusBadge>}
            {exc.proposedCause && <StatusBadge variant="blue">{humanise(exc.proposedCause)}</StatusBadge>}
          </>
        }
      />

      {guardrails.length > 0 && (
        <Step
          icon={Shield}
          title="Guardrails"
          tone={held.length ? 'hold' : 'pass'}
          summary={
            held.length
              ? `Held. ${held[0].detail}${held.length > 1 ? ` (and ${held.length - 1} more)` : ''}`
              : `All ${guardrails.length} passed, so the rule was allowed to close it.`
          }
        >
          <ul className="space-y-1.5 rounded-lg border border-divider bg-card-bg/60 px-3 py-2">
            {guardrails.map((g) => (
              <li key={g.name} className="flex items-start gap-2 text-xs">
                {g.outcome === 'pass'
                  ? <CheckCircle size={12} className="text-success mt-0.5 flex-shrink-0" />
                  : <XCircle size={12} className="text-amber-700 mt-0.5 flex-shrink-0" />}
                <span className="text-gray-700 min-w-0">{g.detail}</span>
              </li>
            ))}
          </ul>
        </Step>
      )}

      {exc.sourceResolutionId && (
        <Step
          icon={User}
          title="Where the rule came from"
          summary={`A resolution ${exc.sourceOperator} wrote by hand. The rule is what the system generalised from those words.`}
        >
          <div className="rounded-lg border border-divider bg-card-bg/60 px-3 py-2 text-xs">
            <Row label="Resolution" value={exc.sourceResolutionId} />
            <Row label="Operator" value={exc.sourceOperator} />
          </div>
        </Step>
      )}

      {exc.humanResolution && (
        <Step
          icon={User}
          title="What the operator wrote about this case"
          summary={`“${exc.humanResolution.text}”`}
        >
          <div className="rounded-lg border border-divider bg-card-bg/60 px-3 py-2 text-xs">
            <Row label="Operator" value={exc.humanResolution.operator} />
            <Row label="When" value={exc.humanResolution.at} />
            <Row label="Resolution" value={exc.humanResolution.id} />
          </div>
        </Step>
      )}
    </div>
  )
}
