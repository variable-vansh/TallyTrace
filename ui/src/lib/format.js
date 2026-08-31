// Shared formatting. One definition each, so two screens cannot disagree about
// what a rupee or a percentage looks like.

export const inr = (amount, opts = {}) =>
  new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    maximumFractionDigits: opts.whole ? 0 : 2,
  }).format(amount ?? 0)

export const pct = (value, places = 1) =>
  value === null || value === undefined ? '—' : `${Number(value).toFixed(places)}%`

// Reason codes and causes are machine-readable on purpose. This is the only place
// they are turned into prose, so the queue, the rules page and the reports all read
// the same way.
export const humanise = (code) =>
  (code || '').replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())

export const RULE_STATE_VARIANT = {
  active: 'success',
  shadow: 'blue',
  proposed: 'muted',
  retired: 'danger',
}

export const OUTCOME_LABEL = {
  auto_resolved: 'Auto-resolved',
  held_by_guardrail: 'Guardrail held',
  shadow_prediction: 'Shadow prediction',
  rules_disagree: 'Rules disagree',
  no_rule_matched: 'No rule matched',
}

export const OUTCOME_VARIANT = {
  auto_resolved: 'success',
  held_by_guardrail: 'amber',
  shadow_prediction: 'blue',
  rules_disagree: 'amber',
  no_rule_matched: 'muted',
}

export const confidenceVariant = (score) =>
  score > 0.75 ? 'success' : score > 0.55 ? 'amber' : 'danger'
