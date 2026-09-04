import { useMemo, useState } from 'react'
import { AlertTriangle, CheckCircle, ChevronDown, Layers, Shield, User } from 'lucide-react'
import StatusBadge from '../components/StatusBadge'
import DecisionPath from '../components/DecisionPath'
import PanelDrawer from '../components/PanelDrawer'
import { inr, humanise, OUTCOME_LABEL, confidenceVariant } from '../lib/format'

// The queue, rewritten for a person working it rather than for a demo of everything
// the system knows.
//
// The previous version rendered every exception as a full card: two colour washes, a
// coloured border, four badges, the model's paragraph, a resolve box and a decision
// path toggle — forty of those down one page. Every card competed for attention, so
// none of them had it, and the amber and green washes made an ordinary queue look
// like an incident board.
//
// Now: one quiet line per exception with the single sentence that says what it is, a
// thin status rail instead of a fill, and everything else in a side panel on click.
// Nothing was removed — the decision path, the near-miss disclosure and the resolve
// box all live in the panel — but the page no longer says all of it at once.

const CHANNELS = ['all', 'amazon', 'flipkart', 'myntra', 'offline', 'website']
const OUTCOMES = [
  { id: 'all', label: 'All exceptions' },
  { id: 'no_rule_matched', label: 'No rule matched' },
  { id: 'shadow_prediction', label: 'Shadow predictions' },
  { id: 'held_by_guardrail', label: 'Held by a guardrail' },
  { id: 'auto_resolved', label: 'Auto-resolved' },
]
const SORTS = [
  { id: 'amount-desc', label: 'Amount (high → low)' },
  { id: 'amount-asc', label: 'Amount (low → high)' },
  { id: 'confidence-asc', label: 'Confidence (low first)' },
  { id: 'confidence-desc', label: 'Confidence (high first)' },
]

// Stable empty defaults: `|| []` builds a new array every render and re-runs the memo.
const NO_EXCEPTIONS = []
const NO_PROPOSALS = []

// A 2px rail, not a fill. Status is legible at the edge of the row and the row itself
// stays white, so forty of them read as a list rather than as forty warnings.
const RAIL = {
  auto_resolved: 'bg-success',
  held_by_guardrail: 'bg-amber-700',
  shadow_prediction: 'bg-primary/60',
  rules_disagree: 'bg-amber-700',
  no_rule_matched: 'bg-divider',
}

function FilterSelect({ value, options, onChange }) {
  return (
    <div className="relative">
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="appearance-none bg-white border border-divider rounded-lg pl-3 pr-8 py-2 text-xs font-medium text-gray-700 focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary cursor-pointer hover:border-gray-300 transition-colors"
      >
        {options.map((opt) => {
          const id = typeof opt === 'string' ? opt : opt.id
          const label = typeof opt === 'string'
            ? (opt === 'all' ? 'All channels' : opt.charAt(0).toUpperCase() + opt.slice(1))
            : opt.label
          return <option key={id} value={id}>{label}</option>
        })}
      </select>
      <ChevronDown size={12} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted pointer-events-none" />
    </div>
  )
}

// One card instead of N exceptions. A rule that fired shows what it closed; a rule a
// guardrail held shows what it would have closed and why it was not allowed to —
// which is still one decision for a human instead of fourteen.
function ProposalCard({ proposal }) {
  const [expanded, setExpanded] = useState(false)
  // The card's own decision, held in the browser. This build renders a completed,
  // scored run; a decision here does not write back to data/resolutions.json, so the
  // card says what it *would* record rather than pretending it recorded it.
  const [decision, setDecision] = useState(null)
  const fired = proposal.outcome === 'auto_resolved'

  return (
    <div className="rounded-xl border border-divider bg-white overflow-hidden">
      <div className="flex items-start gap-4 p-4">
        <span className={`mt-1 flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-lg
          ${fired ? 'bg-success-light text-success' : 'bg-card-bg text-amber-700'}`}>
          {fired ? <CheckCircle size={15} /> : <Shield size={15} />}
        </span>

        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-gray-900 leading-snug">{proposal.headline}</p>
          <p className="text-sm text-gray-600 mt-0.5">{proposal.subhead}</p>
          <div className="flex items-center gap-1.5 flex-wrap mt-2">
            <StatusBadge variant="muted">{proposal.rule_id}</StatusBadge>
            <StatusBadge variant="muted">{humanise(proposal.cause)}</StatusBadge>
            <span className="text-xs text-muted">
              from {proposal.learned_from.operator}, batch {proposal.learned_from.batch}
            </span>
          </div>
          {proposal.held_because && (
            <p className="text-xs text-gray-600 mt-2 border-l-2 border-amber-700/40 pl-2.5 leading-relaxed">
              {proposal.held_because}
            </p>
          )}
          {decision && (
            <p className="text-xs text-muted mt-2 leading-relaxed">
              {decision === 'not_this_time'
                ? `Would record ${proposal.cases} negative observation(s) against ${proposal.rule_id}, lowering its live precision and possibly retiring it.`
                : decision === 'accept_all'
                ? `Would record ${proposal.cases} confirmation(s) for ${proposal.rule_id} and resolve ${proposal.rows} row(s).`
                : 'Deferred. Reviewing individually judges the rule neither way.'}
            </p>
          )}
        </div>

        <div className="flex flex-col items-end gap-1.5 flex-shrink-0">
          <button
            onClick={() => setDecision('accept_all')}
            className={`px-3.5 py-2 rounded-lg text-xs font-semibold whitespace-nowrap transition-colors
              ${decision === 'accept_all'
                ? 'bg-success text-white'
                : 'bg-gray-900 text-white hover:bg-gray-800'}`}
          >
            {fired ? `Accepted · ${proposal.rows}` : `Accept all · ${proposal.rows}`}
          </button>
          <div className="flex items-center gap-1">
            <button
              onClick={() => { setDecision('review_individually'); setExpanded(true) }}
              className="text-xs text-gray-600 hover:text-gray-900 px-2 py-1 font-medium"
            >
              Review
            </button>
            <button
              onClick={() => setDecision('not_this_time')}
              className="text-xs text-muted hover:text-danger px-2 py-1 font-medium"
            >
              Skip
            </button>
          </div>
        </div>
      </div>

      {expanded && (
        <div className="px-4 pb-4 -mt-1">
          <p className="text-xs text-muted mb-2">
            {proposal.case_ids.length} exception(s) · {proposal.rows} settlement row(s)
          </p>
          <div className="flex flex-wrap gap-1">
            {proposal.settlement_row_ids.map((id) => (
              <span key={id} className="font-mono text-[11px] text-muted bg-card-bg rounded px-1.5 py-0.5">
                {id}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// One line. The sentence is the model's hypothesis where there is one, because that is
// the thing a person reads to decide whether to open it.
function ExceptionRow({ exc, onOpen }) {
  const resolved = exc.outcome === 'auto_resolved'
  // `trueCause` is the answer key, which the pipeline never sees. It reaches this file
  // through the harness so a scored run can show its own false positives; it is
  // labelled wherever it appears.
  const wrong = resolved && exc.trueCause && exc.trueCause !== exc.proposedCause

  return (
    <button
      onClick={() => onOpen(exc)}
      className="group w-full text-left flex items-stretch bg-white border border-divider rounded-lg
        overflow-hidden hover:border-gray-300 hover:shadow-sm transition-all"
    >
      <span className={`w-[3px] flex-shrink-0 ${wrong ? 'bg-danger' : RAIL[exc.outcome] || 'bg-divider'}`} />
      <span className="flex-1 min-w-0 flex items-center gap-3 px-4 py-3">
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-2 flex-wrap">
            <span className="font-mono text-xs font-semibold text-gray-900">{exc.key}</span>
            {exc.channel && <span className="text-xs text-muted capitalize">{exc.channel}</span>}
            {wrong && <StatusBadge variant="danger">False positive</StatusBadge>}
          </span>
          <span className="block text-sm text-gray-600 truncate mt-0.5">
            {exc.hypothesis ? exc.hypothesis.text : humanise(exc.reason)}
          </span>
        </span>
        <span className="text-sm font-semibold text-gray-900 tabular-nums flex-shrink-0">
          {inr(exc.impact)}
        </span>
        <span className="w-32 flex-shrink-0 text-right text-xs text-muted hidden md:block">
          {OUTCOME_LABEL[exc.outcome] || exc.outcome}
        </span>
      </span>
    </button>
  )
}

function ExceptionPanel({ exc }) {
  const resolved = exc.outcome === 'auto_resolved'
  const wrong = resolved && exc.trueCause && exc.trueCause !== exc.proposedCause

  return (
    <div className="p-6 flex flex-col gap-5">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 flex-wrap">
          {exc.channel && <StatusBadge variant="muted" className="capitalize">{exc.channel}</StatusBadge>}
          <StatusBadge variant="muted">{OUTCOME_LABEL[exc.outcome] || exc.outcome}</StatusBadge>
          {exc.hypothesis && (
            <StatusBadge variant={confidenceVariant(exc.hypothesis.confidence)}>
              {(exc.hypothesis.confidence * 100).toFixed(0)}% confidence
            </StatusBadge>
          )}
        </div>
        <span className="text-xl font-bold text-gray-900">{inr(exc.impact)}</span>
      </div>

      {wrong && (
        <p className="text-xs text-gray-700 bg-danger-light/40 border border-danger/20 rounded-lg px-3 py-2.5 leading-relaxed">
          A rule closed this as <strong>{humanise(exc.proposedCause)}</strong> and the answer
          key says <strong>{humanise(exc.trueCause)}</strong>. One of the two near-misses
          planted in the dataset: same channel, same variance band, different true cause.
          It is counted as a miss in the precision number rather than hidden — and the
          pipeline never reads the answer key.
        </p>
      )}

      {exc.humanResolution ? (
        <div className="rounded-lg border border-divider bg-card-bg/60 p-4">
          <div className="flex items-center gap-2 mb-2">
            <User size={13} className="text-muted" />
            <span className="text-[11px] font-semibold text-muted uppercase tracking-widest">
              Operator resolution
            </span>
          </div>
          <p className="text-sm text-gray-700 leading-relaxed italic">“{exc.humanResolution.text}”</p>
          <p className="text-xs text-muted mt-2">
            {exc.humanResolution.operator} · {exc.humanResolution.at}
          </p>
        </div>
      ) : (
        <div className="rounded-lg border border-divider p-4">
          <span className="text-[11px] font-semibold text-muted uppercase tracking-widest block mb-2">
            Resolve
          </span>
          <textarea
            placeholder="Describe how this was resolved, in your own words…"
            rows={3}
            className="text-sm px-3 py-2 border border-divider rounded-lg w-full focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary bg-white resize-none"
          />
          <button className="mt-2 bg-gray-900 hover:bg-gray-800 text-white px-3 py-2 rounded-lg text-xs font-semibold transition-colors w-full">
            Mark resolved
          </button>
          <p className="text-xs text-muted mt-2 leading-relaxed">
            Free text only. The rule is induced from what you write, not from a builder.
          </p>
        </div>
      )}

      <div className="border-t border-divider pt-4">
        <DecisionPath exc={exc} />
      </div>
    </div>
  )
}

export default function ReviewQueue({ weekData }) {
  const [channelFilter, setChannelFilter] = useState('all')
  const [outcomeFilter, setOutcomeFilter] = useState('all')
  const [sortBy, setSortBy] = useState('amount-desc')
  const [selected, setSelected] = useState(null)
  const [showNote, setShowNote] = useState(false)

  const exceptions = weekData.exceptions ?? NO_EXCEPTIONS
  const proposals = weekData.proposals ?? NO_PROPOSALS

  const filtered = useMemo(() => {
    let result = [...exceptions]
    if (channelFilter !== 'all') result = result.filter((e) => e.channel === channelFilter)
    if (outcomeFilter !== 'all') result = result.filter((e) => e.outcome === outcomeFilter)

    const conf = (e) => (e.hypothesis ? e.hypothesis.confidence : -1)
    result.sort((a, b) => {
      switch (sortBy) {
        case 'amount-asc': return a.impact - b.impact
        case 'confidence-desc': return conf(b) - conf(a)
        case 'confidence-asc': return conf(a) - conf(b)
        default: return b.impact - a.impact
      }
    })
    return result
  }, [exceptions, channelFilter, outcomeFilter, sortBy])

  const pending = exceptions.filter((e) => e.outcome !== 'auto_resolved').length
  const auto = exceptions.length - pending

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-baseline justify-between flex-wrap gap-3">
        <h1 className="text-2xl font-bold text-gray-900">Review Queue</h1>
        <div className="flex items-center gap-4 text-sm text-muted">
          <span><span className="font-semibold text-gray-900">{pending}</span> need a human</span>
          <span><span className="font-semibold text-gray-900">{auto}</span> auto-resolved</span>
          <span><span className="font-semibold text-gray-900">{weekData.stats.touchpoints}</span> decisions</span>
          <button
            onClick={() => setShowNote(!showNote)}
            className="text-xs text-primary hover:text-primary-hover font-medium"
          >
            About this run
          </button>
        </div>
      </div>

      {showNote && (
        <p className="text-xs text-muted bg-card-bg border border-divider rounded-lg px-4 py-2.5 leading-relaxed">
          This is a recorded run: the ten batches have already been reconciled,
          hypothesised and scored, and the operator resolutions shown are the ones in
          <code className="mx-1 font-mono">data/resolutions.json</code> that the rules were
          induced from. The controls say what they would record rather than writing back.
        </p>
      )}

      {proposals.length > 0 && (
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-2">
            <Layers size={14} className="text-muted" />
            <h2 className="text-xs font-semibold text-muted uppercase tracking-widest">
              {proposals.length} card{proposals.length === 1 ? '' : 's'} instead of{' '}
              {proposals.reduce((s, p) => s + p.cases, 0)} exceptions
            </h2>
          </div>
          {proposals.map((p) => <ProposalCard key={`${p.rule_id}-${p.outcome}`} proposal={p} />)}
        </div>
      )}

      <div className="flex items-center gap-2 flex-wrap">
        <FilterSelect value={outcomeFilter} options={OUTCOMES} onChange={setOutcomeFilter} />
        <FilterSelect value={channelFilter} options={CHANNELS} onChange={setChannelFilter} />
        <FilterSelect value={sortBy} options={SORTS} onChange={setSortBy} />
        {(channelFilter !== 'all' || outcomeFilter !== 'all') && (
          <button
            onClick={() => { setChannelFilter('all'); setOutcomeFilter('all') }}
            className="text-xs text-primary hover:text-primary-hover font-medium"
          >
            Clear
          </button>
        )}
        <span className="text-xs text-muted ml-auto">
          {filtered.length} exceptions · click one for its decision path
        </span>
      </div>

      <div className="flex flex-col gap-1.5">
        {filtered.map((exc) => (
          <ExceptionRow key={exc.caseId} exc={exc} onOpen={setSelected} />
        ))}
      </div>

      {filtered.length === 0 && (
        <div className="text-center py-16 text-muted">
          <AlertTriangle size={28} className="mx-auto mb-3 text-muted/40" />
          <p className="text-sm">No exceptions match your current filters.</p>
        </div>
      )}

      <PanelDrawer
        open={Boolean(selected)}
        onClose={() => setSelected(null)}
        title={selected?.key}
        subtitle={selected ? `${humanise(selected.reason)} · batch ${selected.batch}` : ''}
      >
        {selected && <ExceptionPanel exc={selected} />}
      </PanelDrawer>
    </div>
  )
}
