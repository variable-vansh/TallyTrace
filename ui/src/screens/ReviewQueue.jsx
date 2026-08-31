import { useMemo, useState } from 'react'
import {
  AlertTriangle, CheckCircle, ChevronDown, Layers, Shield, Sparkles, User,
} from 'lucide-react'
import StatusBadge from '../components/StatusBadge'
import DecisionPath from '../components/DecisionPath'
import {
  inr, humanise, OUTCOME_LABEL, OUTCOME_VARIANT, confidenceVariant,
} from '../lib/format'

const CHANNELS = ['all', 'amazon', 'flipkart', 'myntra', 'offline', 'website']
const OUTCOMES = [
  { id: 'all', label: 'All exceptions' },
  { id: 'no_rule_matched', label: 'No rule matched' },
  { id: 'shadow_prediction', label: 'Shadow predictions' },
  { id: 'held_by_guardrail', label: 'Held by a guardrail' },
  { id: 'auto_resolved', label: 'Auto-resolved' },
]
// Stable empty defaults: `|| []` builds a new array every render and re-runs the memo.
const NO_EXCEPTIONS = []
const NO_PROPOSALS = []

const SORTS = [
  { id: 'amount-desc', label: 'Amount (high → low)' },
  { id: 'amount-asc', label: 'Amount (low → high)' },
  { id: 'confidence-asc', label: 'Confidence (low first)' },
  { id: 'confidence-desc', label: 'Confidence (high first)' },
]

function FilterSelect({ value, options, onChange }) {
  return (
    <div className="relative">
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="appearance-none bg-white border border-divider rounded-lg pl-3 pr-8 py-2 text-xs font-medium text-gray-700 focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary cursor-pointer hover:border-gray-300 transition-colors"
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
  const [open, setOpen] = useState(false)
  // The card's own decision, held in the browser. This build renders a completed,
  // scored run; a decision here does not write back to data/resolutions.json, so the
  // card says what it *would* record rather than pretending it recorded it.
  const [decision, setDecision] = useState(null)
  const fired = proposal.outcome === 'auto_resolved'
  const tone = decision === 'not_this_time' ? 'border-danger/40 bg-danger-light/30'
    : fired ? 'border-success/30 bg-success-light/30'
    : 'border-amber/40 bg-amber-light/30'

  return (
    <div className={`rounded-xl border ${tone} overflow-hidden`}>
      <div className="p-5 flex flex-col lg:flex-row items-start lg:items-center justify-between gap-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2 mb-1.5 flex-wrap">
            {fired ? <Shield size={15} className="text-success" /> : <AlertTriangle size={15} className="text-amber" />}
            <StatusBadge variant="muted">{proposal.rule_id}</StatusBadge>
            <StatusBadge variant={fired ? 'success' : 'amber'}>
              {fired ? 'Applied' : 'Needs your call'}
            </StatusBadge>
            <StatusBadge variant="blue">{humanise(proposal.cause)}</StatusBadge>
          </div>
          <p className="font-semibold text-gray-900 text-sm leading-snug">{proposal.headline}</p>
          <p className="text-gray-700 text-sm mt-0.5">{proposal.subhead}</p>
          <p className="text-xs text-muted mt-1">
            Learned from {proposal.learned_from.operator}&rsquo;s resolution in batch{' '}
            {proposal.learned_from.batch}.
          </p>
          {proposal.held_because && (
            <p className="text-xs text-amber-700 mt-1.5 font-medium">
              Guardrail: {proposal.held_because}
            </p>
          )}
        </div>
        <div className="flex flex-col items-end gap-1.5 flex-shrink-0">
          <div className="flex items-center gap-2">
            <button
              onClick={() => setDecision('accept_all')}
              className={`px-4 py-2.5 rounded-lg font-medium text-sm transition-colors whitespace-nowrap shadow-sm text-white ${
                fired ? 'bg-success hover:opacity-90' : 'bg-primary hover:bg-primary-hover'
              }`}
            >
              {fired ? `Accepted (${proposal.rows})` : `Accept all (${proposal.rows})`}
            </button>
            <button
              onClick={() => { setDecision('review_individually'); setOpen(true) }}
              className="text-xs text-gray-700 hover:text-gray-900 px-3 py-2 font-medium whitespace-nowrap"
            >
              Review individually
            </button>
            <button
              onClick={() => setDecision('not_this_time')}
              className="text-xs text-muted hover:text-danger px-3 py-2 font-medium whitespace-nowrap"
            >
              Not this time
            </button>
          </div>
          {decision && (
            <p className="text-[11px] text-muted max-w-xs text-right leading-relaxed">
              {decision === 'not_this_time'
                ? `Would record ${proposal.cases} negative observation(s) against ${proposal.rule_id}, lowering its live precision and possibly retiring it.`
                : decision === 'accept_all'
                ? `Would record ${proposal.cases} confirmation(s) for ${proposal.rule_id} and resolve ${proposal.rows} row(s).`
                : 'Deferred. Reviewing individually judges the rule neither way.'}
            </p>
          )}
        </div>
      </div>
      {open && (
        <div className="px-5 pb-4 border-t border-divider/60 pt-3">
          <p className="text-[11px] uppercase tracking-widest font-bold text-muted mb-2">
            {proposal.case_ids.length} exception(s), {proposal.rows} settlement row(s)
          </p>
          <div className="flex flex-wrap gap-1.5">
            {proposal.settlement_row_ids.map((id) => (
              <span key={id} className="font-mono text-[11px] bg-white border border-divider rounded px-1.5 py-0.5">
                {id}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function ExceptionCard({ exc }) {
  const [open, setOpen] = useState(false)
  const resolved = exc.outcome === 'auto_resolved'
  // `trueCause` is the answer key, which the pipeline never sees. It reaches this file
  // through the harness so a scored run can show its own false positives; it is
  // labelled wherever it appears, because a product that could read this would not
  // need any of the rest of the system.
  const wrong = resolved && exc.trueCause && exc.trueCause !== exc.proposedCause

  return (
    <div className={`rounded-xl border bg-white overflow-hidden transition-colors ${
      wrong ? 'border-danger/50' : resolved ? 'border-divider' : 'border-amber/40 shadow-sm'
    }`}>
      <div className="flex items-center justify-between px-5 py-3.5 bg-card-bg/50 border-b border-divider gap-3">
        <div className="flex items-center gap-3 min-w-0 flex-wrap">
          <span className="font-mono text-sm font-semibold text-gray-900">{exc.key}</span>
          {exc.channel && <StatusBadge variant="muted" className="capitalize">{exc.channel}</StatusBadge>}
          <StatusBadge variant={OUTCOME_VARIANT[exc.outcome] || 'muted'}>
            {OUTCOME_LABEL[exc.outcome] || exc.outcome}
          </StatusBadge>
          {exc.ruleId && <StatusBadge variant="blue">{exc.ruleId}</StatusBadge>}
          {wrong && (
            <StatusBadge
              variant="danger"
              title={`The answer key says ${exc.trueCause}. Scoring only — the pipeline never reads it.`}
            >
              False positive (per answer key)
            </StatusBadge>
          )}
        </div>
        <div className="flex items-center gap-3 flex-shrink-0">
          <span className="text-lg font-bold text-gray-900">{inr(exc.impact)}</span>
          {resolved ? (
            <div className="flex items-center gap-1 text-success">
              <CheckCircle size={16} />
              <span className="text-xs font-semibold">Auto-resolved</span>
            </div>
          ) : (
            <div className="flex items-center gap-1 text-amber">
              <AlertTriangle size={14} />
              <span className="text-xs font-semibold">Needs a human</span>
            </div>
          )}
        </div>
      </div>

      <div className="px-5 py-4">
        <div className="flex flex-col lg:flex-row gap-5">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-2 flex-wrap">
              <span className="text-[10px] text-muted uppercase tracking-widest font-bold">
                AI hypothesis
              </span>
              {exc.hypothesis && (
                <>
                  <StatusBadge variant={confidenceVariant(exc.hypothesis.confidence)}>
                    {(exc.hypothesis.confidence * 100).toFixed(0)}% confidence
                  </StatusBadge>
                  <StatusBadge variant="muted">{humanise(exc.hypothesis.cause)}</StatusBadge>
                </>
              )}
            </div>
            {wrong && (
              <p className="text-xs text-danger bg-danger-light/40 border border-danger/25 rounded-lg px-3 py-2 mb-2 leading-relaxed">
                A rule closed this as <strong>{humanise(exc.proposedCause)}</strong> and the
                answer key says <strong>{humanise(exc.trueCause)}</strong>. This is one of
                the two near-misses planted in the dataset: same channel, same variance
                band, different true cause. It is counted as a miss in the precision
                number rather than hidden. The answer key is used for scoring only — the
                pipeline never reads it.
              </p>
            )}
            <p className="text-sm text-gray-700 leading-relaxed">
              {exc.hypothesis
                ? exc.hypothesis.text
                : 'No hypothesis: the row was quarantined before the matcher could read it, and the frozen cause enum has no value for malformed input.'}
            </p>
            <p className="text-xs text-muted mt-2 font-mono">{exc.reason}</p>
          </div>

          <div className="w-full lg:w-80 flex-shrink-0">
            {exc.humanResolution ? (
              <div className="bg-success-light/50 border border-success/20 rounded-lg p-4">
                <div className="flex items-center gap-2 mb-2">
                  <User size={13} className="text-success" />
                  <span className="text-xs font-bold text-success uppercase tracking-wide">
                    Operator resolution
                  </span>
                </div>
                <p className="text-xs text-gray-700 leading-relaxed italic">
                  “{exc.humanResolution.text}”
                </p>
                <p className="text-[11px] text-muted mt-2">
                  {exc.humanResolution.operator} · {exc.humanResolution.at}
                </p>
              </div>
            ) : (
              <div className="bg-card-bg border border-divider rounded-lg p-4">
                <span className="text-[10px] text-muted uppercase tracking-widest font-bold block mb-2">
                  Resolve
                </span>
                <textarea
                  placeholder="Describe how this was resolved, in your own words…"
                  rows={2}
                  className="text-sm px-3 py-2 border border-divider rounded-lg w-full focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary bg-white resize-none"
                />
                <button className="mt-2 bg-gray-900 hover:bg-gray-800 text-white px-3 py-2 rounded-lg text-xs font-semibold transition-colors w-full">
                  Mark resolved
                </button>
                <p className="text-[10px] text-muted mt-2 leading-relaxed">
                  Free text only. The rule is induced from what you write, not from a builder.
                </p>
              </div>
            )}
          </div>
        </div>

        <button
          onClick={() => setOpen(!open)}
          className="mt-3 flex items-center gap-1.5 text-xs font-semibold text-primary hover:text-primary-hover"
        >
          <Sparkles size={13} />
          {open ? 'Hide decision path' : 'Show full decision path'}
          <ChevronDown size={13} className={`transition-transform ${open ? 'rotate-180' : ''}`} />
        </button>
        {open && (
          <div className="mt-3 pt-3 border-t border-divider">
            <DecisionPath exc={exc} />
          </div>
        )}
      </div>
    </div>
  )
}

export default function ReviewQueue({ weekData }) {
  const [channelFilter, setChannelFilter] = useState('all')
  const [outcomeFilter, setOutcomeFilter] = useState('all')
  const [sortBy, setSortBy] = useState('amount-desc')

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
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3 flex-wrap">
          <h1 className="text-2xl font-bold text-gray-900">Review Queue</h1>
          <StatusBadge variant="danger">{pending} need a human</StatusBadge>
          <StatusBadge variant="success">{auto} auto-resolved</StatusBadge>
          <StatusBadge variant="blue">{weekData.stats.touchpoints} decisions</StatusBadge>
        </div>
      </div>

      <p className="text-xs text-muted bg-card-bg border border-divider rounded-lg px-4 py-2.5 leading-relaxed">
        This is a recorded run: the ten batches have already been reconciled, hypothesised
        and scored, and the operator resolutions shown below are the ones in
        <code className="mx-1 font-mono">data/resolutions.json</code> that the rules were
        induced from. The controls say what they would record rather than writing back.
      </p>

      <div className="flex items-center gap-3 flex-wrap">
        <FilterSelect value={outcomeFilter} options={OUTCOMES} onChange={setOutcomeFilter} />
        <FilterSelect value={channelFilter} options={CHANNELS} onChange={setChannelFilter} />
        <div className="h-5 w-px bg-divider" />
        <FilterSelect value={sortBy} options={SORTS} onChange={setSortBy} />
        {(channelFilter !== 'all' || outcomeFilter !== 'all') && (
          <button
            onClick={() => { setChannelFilter('all'); setOutcomeFilter('all') }}
            className="text-xs text-primary hover:text-primary-hover font-medium"
          >
            Clear filters
          </button>
        )}
        <span className="text-xs text-muted ml-auto">{filtered.length} results</span>
      </div>

      {proposals.length > 0 && (
        <div className="flex flex-col gap-3">
          <div className="flex items-center gap-2">
            <Layers size={15} className="text-primary" />
            <h2 className="text-sm font-bold text-gray-900 uppercase tracking-wide">
              Batch proposals — {proposals.length} card{proposals.length === 1 ? '' : 's'} instead of{' '}
              {proposals.reduce((s, p) => s + p.cases, 0)} exceptions
            </h2>
          </div>
          {proposals.map((p) => <ProposalCard key={`${p.rule_id}-${p.outcome}`} proposal={p} />)}
        </div>
      )}

      <div className="flex flex-col gap-4">
        {filtered.map((exc) => <ExceptionCard key={exc.caseId} exc={exc} />)}
      </div>

      {filtered.length === 0 && (
        <div className="text-center py-16 text-muted">
          <AlertTriangle size={32} className="mx-auto mb-3 text-muted/50" />
          <p className="text-sm">No exceptions match your current filters.</p>
        </div>
      )}
    </div>
  )
}
