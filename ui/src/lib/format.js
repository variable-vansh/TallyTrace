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

// --------------------------------------------------------------------------- //
// Dates. The corpus is a run of weekly settlement batches, so every date on
// screen is either a day inside a week or the week itself.
// --------------------------------------------------------------------------- //

const MONTH_SHORT = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

// Parsed by hand rather than through `new Date(iso)`, which reads a bare ISO date as
// UTC and renders it a day early anywhere west of Greenwich. A deadline that shows
// the wrong day is the one bug this screen cannot have.
export const parseISO = (iso) => {
  const [y, m, d] = (iso || '').split('-').map(Number)
  return new Date(y, (m || 1) - 1, d || 1)
}

export const isoOf = (date) =>
  `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`

// Monday-first: an Indian working week starts there, and the weekend reads as a block.
export const mondayIndex = (date) => (date.getDay() + 6) % 7

export const startOfWeek = (iso) => {
  const date = parseISO(iso)
  date.setDate(date.getDate() - mondayIndex(date))
  return date
}

export const addDays = (date, days) => {
  const next = new Date(date)
  next.setDate(next.getDate() + days)
  return next
}

// "21–27 Jul 2025", and "28 Jul – 3 Aug 2025" when the week straddles a month. The
// month and the year are said once where saying them twice would add nothing.
export const dateSpan = (fromIso, toIso) => {
  const from = parseISO(fromIso)
  const to = parseISO(toIso)
  const tail = `${MONTH_SHORT[to.getMonth()]} ${to.getFullYear()}`
  if (from.getMonth() === to.getMonth() && from.getFullYear() === to.getFullYear()) {
    return `${from.getDate()}–${to.getDate()} ${tail}`
  }
  const head = `${from.getDate()} ${MONTH_SHORT[from.getMonth()]}`
  return `${head} – ${to.getDate()} ${tail}`
}

export const dayMonth = (iso) => {
  const date = parseISO(iso)
  return `${date.getDate()} ${MONTH_SHORT[date.getMonth()]}`
}
