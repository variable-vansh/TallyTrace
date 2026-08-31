import { CheckCircle, XCircle, GitBranch, Shield, User, Sparkles } from 'lucide-react'
import StatusBadge from './StatusBadge'
import { inr, humanise, OUTCOME_LABEL, OUTCOME_VARIANT } from '../lib/format'

// The screen that answers "would you trust it". Every step the system took on one
// exception, in the order it took them: what the matcher measured, what the model
// guessed, which rule matched, which guardrails ran, and whose resolution the rule
// descends from. Nothing here is summarised — it is the provenance record rendered.

function Step({ icon: Icon, title, children, tone = 'default' }) {
  const ring = tone === 'hold' ? 'border-amber/40 bg-amber-light/30'
    : tone === 'pass' ? 'border-success/30 bg-success-light/40'
    : 'border-divider bg-white'
  return (
    <div className={`relative pl-9 pb-4 last:pb-0`}>
      <span className="absolute left-0 top-0 w-6 h-6 rounded-full bg-white border border-divider flex items-center justify-center">
        <Icon size={13} className="text-muted" />
      </span>
      <span className="absolute left-3 top-6 bottom-0 w-px bg-divider last:hidden" />
      <div className={`rounded-lg border ${ring} px-4 py-3`}>
        <div className="text-[11px] font-bold uppercase tracking-widest text-muted mb-1.5">{title}</div>
        {children}
      </div>
    </div>
  )
}

export default function DecisionPath({ exc }) {
  const detail = exc.verdicts?.[0]?.detail || {}
  const guardrails = (exc.guardrails || []).map((g, i) => ({
    name: g.split(':')[0],
    outcome: g.split(':')[1],
    detail: exc.guardrailDetail?.[i] || '',
  }))

  return (
    <div className="pt-1">
      <Step icon={GitBranch} title="What the matcher measured">
        <p className="text-sm text-gray-800">
          <span className="font-mono text-xs bg-gray-100 px-1.5 py-0.5 rounded">{exc.reason}</span>
          {' '}on {exc.channel || 'no channel'} — {inr(exc.impact)} in question.
        </p>
        <div className="mt-2 grid grid-cols-2 sm:grid-cols-3 gap-x-4 gap-y-1 text-xs">
          {Object.entries(exc.features || {})
            .filter(([, v]) => v !== null && v !== undefined)
            .map(([k, v]) => (
              <div key={k} className="flex justify-between gap-2 border-b border-divider/60 py-0.5">
                <span className="text-muted">{k.replace(/_/g, ' ')}</span>
                <span className="font-medium text-gray-900 text-right">{String(v)}</span>
              </div>
            ))}
        </div>
        {Object.keys(detail).length > 0 && (
          <details className="mt-2">
            <summary className="text-xs text-primary cursor-pointer font-medium">Raw verdict detail</summary>
            <pre className="mt-1.5 text-[11px] bg-card-bg rounded p-2 overflow-x-auto">{JSON.stringify(detail, null, 1)}</pre>
          </details>
        )}
      </Step>

      {exc.hypothesis && (
        <Step icon={Sparkles} title="Hypothesis (LLM, constrained to the frozen enum)">
          <div className="flex items-center gap-2 mb-1.5">
            <StatusBadge variant="blue">{humanise(exc.hypothesis.cause)}</StatusBadge>
            <span className="text-xs text-muted">{(exc.hypothesis.confidence * 100).toFixed(0)}% confidence</span>
          </div>
          <p className="text-sm text-gray-700 leading-relaxed">{exc.hypothesis.text}</p>
        </Step>
      )}

      <Step
        icon={exc.outcome === 'auto_resolved' ? CheckCircle : XCircle}
        title="Rule matching (deterministic — no model)"
        tone={exc.outcome === 'auto_resolved' ? 'pass' : 'default'}
      >
        <div className="flex items-center gap-2 mb-1.5 flex-wrap">
          <StatusBadge variant={OUTCOME_VARIANT[exc.outcome] || 'muted'}>
            {OUTCOME_LABEL[exc.outcome] || exc.outcome}
          </StatusBadge>
          {exc.ruleId && <StatusBadge variant="muted">{exc.ruleId} · {exc.ruleState}</StatusBadge>}
          {exc.proposedCause && <StatusBadge variant="blue">{humanise(exc.proposedCause)}</StatusBadge>}
        </div>
        <p className="text-sm text-gray-700">{exc.decisionNote}</p>
      </Step>

      {guardrails.length > 0 && (
        <Step
          icon={Shield}
          title="Guardrails — evaluated after the rule matched, and they override it"
          tone={guardrails.some((g) => g.outcome === 'hold') ? 'hold' : 'pass'}
        >
          <ul className="space-y-1.5">
            {guardrails.map((g) => (
              <li key={g.name} className="flex items-start gap-2 text-xs">
                {g.outcome === 'pass'
                  ? <CheckCircle size={13} className="text-success mt-0.5 flex-shrink-0" />
                  : <XCircle size={13} className="text-amber mt-0.5 flex-shrink-0" />}
                <span className="font-mono text-[11px] text-gray-900 w-40 flex-shrink-0">{g.name}</span>
                <span className="text-gray-700">{g.detail}</span>
              </li>
            ))}
          </ul>
        </Step>
      )}

      {exc.sourceResolutionId && (
        <Step icon={User} title="Where the rule came from">
          <p className="text-sm text-gray-700">
            Induced from resolution{' '}
            <span className="font-mono text-xs bg-gray-100 px-1.5 py-0.5 rounded">{exc.sourceResolutionId}</span>
            {' '}written by <span className="font-medium text-gray-900">{exc.sourceOperator}</span>.
          </p>
        </Step>
      )}

      {exc.humanResolution && (
        <Step icon={User} title="What the operator wrote about this case">
          <p className="text-sm text-gray-800 italic leading-relaxed">“{exc.humanResolution.text}”</p>
          <p className="text-xs text-muted mt-1.5">
            {exc.humanResolution.operator} · {exc.humanResolution.at} · {exc.humanResolution.id}
          </p>
        </Step>
      )}
    </div>
  )
}
