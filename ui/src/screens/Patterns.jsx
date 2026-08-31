import { useMemo, useState } from 'react'
import { ChevronDown, Power, Scissors, TrendingDown } from 'lucide-react'
import StatusBadge from '../components/StatusBadge'
import { humanise, pct, RULE_STATE_VARIANT } from '../lib/format'

const STATE_ORDER = { active: 0, shadow: 1, proposed: 2, retired: 3 }
// A stable empty default: `|| []` builds a new array every render and re-runs the memo.
const NO_RULES = []

const STATE_BLURB = {
  proposed: 'Just induced from a resolution. Never fires.',
  shadow: 'Predicts on each new batch and logs whether it would have been right. The human still sees the exception.',
  active: 'Promoted on its record. Auto-resolves matching rows — subject to the guardrails, which override it.',
  retired: 'Demoted automatically when live precision fell below the floor. Shown, not hidden.',
}

function band(pair, suffix = '%') {
  return pair ? `${pair[0]}${suffix} … ${pair[1]}${suffix}` : null
}

function Conditions({ rule }) {
  const c = rule.conditions
  const rows = [
    ['channel', c.channel],
    ['matcher reason', c.reason_code],
    ['row type', c.transaction_type],
    ['direction', c.direction === 'any' ? null : c.direction],
    ['fee variance band', band(c.variance_band_pct)],
    ['net variance band', band(c.net_variance_band_pct)],
    ['lag window', c.lag_window_days ? `${c.lag_window_days[0]} … ${c.lag_window_days[1]} days` : null],
  ].filter(([, v]) => v)

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-1 text-xs">
      {rows.map(([label, value]) => (
        <div key={label} className="flex justify-between gap-3 border-b border-divider/60 py-1">
          <span className="text-muted">{label}</span>
          <span className="font-mono text-gray-900 text-right">{value}</span>
        </div>
      ))}
    </div>
  )
}

function RuleRow({ rule }) {
  const [open, setOpen] = useState(false)
  const live = rule.precision === null ? null : Number(rule.precision) * 100
  const trueP = rule.true_precision_pct === null ? null : Number(rule.true_precision_pct)
  const drifted = live !== null && trueP !== null && Math.abs(live - trueP) > 0.01

  return (
    <div className={`bg-white rounded-2xl border overflow-hidden ${
      rule.state === 'retired' ? 'border-danger/40' : 'border-divider'
    }`}>
      <button
        onClick={() => setOpen(!open)}
        className="w-full text-left px-5 py-4 hover:bg-card-bg/50 transition-colors"
      >
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2 mb-1.5 flex-wrap">
              <span className="font-mono text-sm font-bold text-gray-900">{rule.rule_id}</span>
              <StatusBadge variant={RULE_STATE_VARIANT[rule.state]}>{rule.state}</StatusBadge>
              <StatusBadge variant="muted">{humanise(rule.cause)}</StatusBadge>
              <StatusBadge variant="blue">{humanise(rule.resolution_class)}</StatusBadge>
              {!rule.enabled && <StatusBadge variant="danger">disabled</StatusBadge>}
            </div>
            <p className="text-sm text-gray-800 leading-snug pr-4">{rule.plain_words}</p>
          </div>
          <div className="flex items-center gap-6 flex-shrink-0 text-right">
            <div>
              <div className="text-[10px] uppercase tracking-widest text-muted">Support</div>
              <div className="text-sm font-bold text-gray-900">{rule.support}</div>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-widest text-muted">Live precision</div>
              <div className={`text-sm font-bold ${
                live === null ? 'text-muted' : live < 75 ? 'text-danger' : 'text-gray-900'
              }`}>
                {live === null ? '—' : pct(live, 2)}
              </div>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-widest text-muted">Last fired</div>
              <div className="text-sm font-bold text-gray-900">
                {rule.last_fired_batch ? `Batch ${rule.last_fired_batch}` : '—'}
              </div>
            </div>
            <ChevronDown size={16} className={`text-muted transition-transform ${open ? 'rotate-180' : ''}`} />
          </div>
        </div>
      </button>

      {open && (
        <div className="px-5 pb-5 border-t border-divider pt-4 flex flex-col gap-4">
          <p className="text-xs text-muted italic">{STATE_BLURB[rule.state]}</p>

          <div>
            <h4 className="text-[10px] uppercase tracking-widest font-bold text-muted mb-2">
              Conditions — evaluated as a predicate, no model involved
            </h4>
            <Conditions rule={rule} />
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div>
              <h4 className="text-[10px] uppercase tracking-widest font-bold text-muted mb-2">
                Record
              </h4>
              <div className="text-xs space-y-1">
                <div className="flex justify-between"><span className="text-muted">Predictions made</span><span className="font-medium">{rule.support}</span></div>
                <div className="flex justify-between"><span className="text-muted">Confirmed by the operator</span><span className="font-medium text-success">{rule.confirmations}</span></div>
                <div className="flex justify-between"><span className="text-muted">Refuted by the operator</span><span className="font-medium text-danger">{rule.refutations}</span></div>
                <div className="flex justify-between"><span className="text-muted">Live precision</span><span className="font-medium">{live === null ? '—' : pct(live, 2)}</span></div>
                <div className="flex justify-between border-t border-divider pt-1 mt-1">
                  <span className="text-muted">True precision vs answer key</span>
                  <span className="font-medium">
                    {trueP === null ? '—' : `${pct(trueP, 2)} over ${rule.scored_auto_resolutions}`}
                  </span>
                </div>
              </div>
              {drifted && (
                <p className="text-[11px] text-danger mt-2 leading-relaxed">
                  Live and true precision disagree. The operator and the rule were fooled by the
                  same rows — that gap is the near-miss in the corpus, and it is why both numbers
                  are shown.
                </p>
              )}
            </div>

            <div>
              <h4 className="text-[10px] uppercase tracking-widest font-bold text-muted mb-2">
                Action when it fires
              </h4>
              <div className="text-xs bg-card-bg rounded-lg p-3 font-mono">
                {rule.action.type}
                {rule.action.field && <> · {rule.action.field}</>}
                {rule.action.value && <> = {rule.action.value}</>}
              </div>
              <div className="flex items-center gap-2 mt-3">
                <button className="flex items-center gap-1.5 text-xs font-semibold text-gray-700 hover:text-gray-900 border border-divider rounded-lg px-3 py-2">
                  <Scissors size={12} /> Narrow the band
                </button>
                <button className="flex items-center gap-1.5 text-xs font-semibold text-danger hover:opacity-80 border border-danger/30 rounded-lg px-3 py-2">
                  <Power size={12} /> {rule.enabled ? 'Disable' : 'Enable'}
                </button>
              </div>
              <p className="text-[10px] text-muted mt-2 leading-relaxed">
                Narrowing may only tighten a band, never widen one — enforced in the rule model,
                not in the form.
              </p>
            </div>
          </div>

          {rule.descended_from && (
            <div>
              <h4 className="text-[10px] uppercase tracking-widest font-bold text-muted mb-2">
                Descended from a human resolution
              </h4>
              <div className="bg-success-light/40 border border-success/20 rounded-lg p-3">
                <p className="text-xs text-gray-800 italic leading-relaxed">
                  “{rule.descended_from.text}”
                </p>
                <p className="text-[11px] text-muted mt-2">
                  {rule.descended_from.operator} · batch {rule.descended_from.batch} ·{' '}
                  {rule.descended_from.at} · {rule.descended_from.resolution_id}
                </p>
              </div>
            </div>
          )}

          <div>
            <h4 className="text-[10px] uppercase tracking-widest font-bold text-muted mb-2">
              Lifecycle — every transition, with its reason
            </h4>
            <ol className="space-y-1.5">
              {rule.transitions.map((t, i) => (
                <li key={i} className="flex items-start gap-2 text-xs">
                  <StatusBadge variant={RULE_STATE_VARIANT[t.to_state]}>{t.to_state}</StatusBadge>
                  <span className="text-muted whitespace-nowrap">batch {t.batch}</span>
                  <span className="text-gray-700">{t.reason}</span>
                </li>
              ))}
            </ol>
          </div>
        </div>
      )}
    </div>
  )
}

export default function Patterns({ data }) {
  const [stateFilter, setStateFilter] = useState('all')
  const rules = data?.rules ?? NO_RULES

  const shown = useMemo(() => {
    const filtered = stateFilter === 'all' ? rules : rules.filter((r) => r.state === stateFilter)
    return [...filtered].sort(
      (a, b) => (STATE_ORDER[a.state] - STATE_ORDER[b.state]) || (b.support - a.support)
    )
  }, [rules, stateFilter])

  const counts = rules.reduce((acc, r) => ({ ...acc, [r.state]: (acc[r.state] || 0) + 1 }), {})
  const retired = rules.filter((r) => r.state === 'retired')

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <h1 className="text-2xl font-bold text-gray-900">Learned Rules</h1>
        <div className="flex items-center gap-1.5 flex-wrap">
          {['all', 'active', 'shadow', 'proposed', 'retired'].map((state) => (
            <button
              key={state}
              onClick={() => setStateFilter(state)}
              className={`px-3 py-1.5 rounded-full text-xs font-semibold transition-colors capitalize ${
                stateFilter === state
                  ? 'bg-gray-900 text-white'
                  : 'bg-white border border-divider text-gray-600 hover:border-gray-300'
              }`}
            >
              {state} {state === 'all' ? rules.length : counts[state] || 0}
            </button>
          ))}
        </div>
      </div>

      {retired.length > 0 && (
        <div className="bg-danger-light/40 border border-danger/25 rounded-2xl p-5">
          <div className="flex items-center gap-2 mb-2">
            <TrendingDown size={16} className="text-danger" />
            <h2 className="text-sm font-bold text-gray-900">
              {retired.length} rule{retired.length === 1 ? '' : 's'} retired themselves
            </h2>
          </div>
          {retired.map((r) => (
            <p key={r.rule_id} className="text-sm text-gray-700 leading-relaxed">
              <span className="font-mono font-semibold">{r.rule_id}</span> —{' '}
              {r.transitions[r.transitions.length - 1]?.reason}. It was induced from a note that
              generalised across every channel; the operator&rsquo;s own later resolutions
              contradicted it, and the lifecycle demoted it without anyone intervening.
            </p>
          ))}
        </div>
      )}

      <div className="flex flex-col gap-3">
        {shown.map((rule) => <RuleRow key={rule.rule_id} rule={rule} />)}
      </div>

      {shown.length === 0 && (
        <div className="bg-white rounded-2xl border border-divider p-12 text-center text-muted">
          No rules in this state.
        </div>
      )}
    </div>
  )
}
