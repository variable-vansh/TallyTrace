// Arithmetic over the reconciled cube, for questions no metric was registered for.
//
// The ten registered metrics are the questions this run already answered. This is the
// material they were computed from — one row per batch per channel, carrying gross,
// net, fees, taxes, distinct orders and settlement rows — so a question nobody
// anticipated is still arithmetic rather than a refusal.
//
// **The model does not write this arithmetic.** It fills in a plan: which measure, over
// what denominator, grouped how, filtered to which channels and weeks. Every field is
// drawn from a closed vocabulary, validated on the way in, and executed here by code
// that was written once and can be read. There is no query, no generated expression and
// nothing evaluated — the failure mode is "picked the wrong measure", which the
// restatement puts in front of a person before it runs, and not "returned a plausible
// wrong number", which is what generated SQL does.
//
// The cube is pre-aggregated in Python from the same object the registry reads, so a
// figure derived here cannot disagree with one the registry printed. It is aggregates
// rather than raw rows for a specific reason: the UI's ledger view repeats an order in
// every batch that carries it forward, so summing those rows would overstate the books
// by two and a half times.

export const MEASURES = {
  gross: { label: 'gross order value', unit: 'inr', money: true },
  net: { label: 'net settled to the bank', unit: 'inr', money: true },
  fees: { label: 'commission and fulfilment fees', unit: 'inr', money: true },
  taxes: { label: 'tax withheld — GST on fees, TCS and TDS', unit: 'inr', money: true },
  deductions: { label: 'every deduction — fees and tax withheld', unit: 'inr', money: true },
  orders: { label: 'distinct orders settled', unit: 'count', money: false },
  settlement_rows: { label: 'settlement rows', unit: 'count', money: false },
}

export const DENOMINATORS = {
  order: { label: 'per order', measure: 'orders' },
  settlement_row: { label: 'per settlement row', measure: 'settlement_rows' },
  gross: { label: 'as a percentage of gross order value', measure: 'gross' },
}

export const GROUPINGS = ['channel', 'batch', 'none']

/** One cube row's value for a measure, including the two derived ones. */
function measureOf(row, measure) {
  if (measure === 'deductions') return (row.fees || 0) + (row.taxes || 0)
  if (measure === 'settlement_rows') return row.settlementRows || 0
  return row[measure] || 0
}

/** Reject a plan the vocabulary does not contain, rather than coercing it into one. */
export function validatePlan(plan) {
  if (!plan || typeof plan !== 'object') return 'no plan'
  if (!MEASURES[plan.measure]) return `unknown measure "${plan.measure}"`
  if (plan.per != null && !DENOMINATORS[plan.per]) return `unknown denominator "${plan.per}"`
  if (plan.groupBy != null && !GROUPINGS.includes(plan.groupBy)) {
    return `unknown grouping "${plan.groupBy}"`
  }
  // A count divided by itself is 1.00 on every row — arithmetic that is technically
  // valid and answers nothing, so it is refused rather than plotted.
  if (plan.per && DENOMINATORS[plan.per].measure === plan.measure) {
    return `${plan.measure} over itself is not a quantity`
  }
  return null
}

const titleFor = (plan) => {
  const measure = MEASURES[plan.measure]
  const head = measure.label.charAt(0).toUpperCase() + measure.label.slice(1)
  if (!plan.per) return head
  return `${head} ${DENOMINATORS[plan.per].label}`
}

/** The unit of the answer, which is not always the unit of the measure. */
const unitFor = (plan) => {
  if (!plan.per) return MEASURES[plan.measure].unit
  if (plan.per === 'gross') return 'pct'
  // Money over a count is money; a count over a count is a bare ratio.
  return MEASURES[plan.measure].money ? 'inr' : 'ratio'
}

const round = (value, unit) => {
  const dp = unit === 'pct' ? 2 : unit === 'ratio' ? 3 : unit === 'count' ? 0 : 2
  return Number(value.toFixed(dp))
}

/**
 * Run a validated plan over the cube.
 *
 * Returns the same shape the registry's own results use, so the chart, the pin and the
 * confirm step do not need to know which of the two produced it.
 */
export function computePlan(facts, plan) {
  const problem = validatePlan(plan)
  if (problem) return { error: problem }

  const channels = plan.channels?.length ? new Set(plan.channels) : null
  const from = plan.fromBatch ?? -Infinity
  const to = plan.toBatch ?? Infinity

  const rows = (facts || []).filter(
    (row) => (!channels || channels.has(row.channel)) && row.batch >= from && row.batch <= to
  )
  if (!rows.length) return { error: 'no rows in that window' }

  const groupBy = plan.groupBy && plan.groupBy !== 'none' ? plan.groupBy : null
  const denominator = plan.per ? DENOMINATORS[plan.per].measure : null
  const unit = unitFor(plan)

  // Sum numerator and denominator separately, then divide once per group. Averaging
  // per-batch ratios instead would weight a 6-order week the same as a 180-order one.
  const buckets = new Map()
  for (const row of rows) {
    const key = groupBy === 'channel' ? row.channel : groupBy === 'batch' ? row.batch : 'all'
    const bucket = buckets.get(key) || { num: 0, den: 0 }
    bucket.num += measureOf(row, plan.measure)
    if (denominator) bucket.den += measureOf(row, denominator)
    buckets.set(key, bucket)
  }

  const value = ({ num, den }) => {
    if (!denominator) return num
    if (!den) return null                       // nothing to divide by; not zero
    return plan.per === 'gross' ? (num / den) * 100 : num / den
  }

  const keys = [...buckets.keys()].sort((a, b) =>
    groupBy === 'batch' ? a - b : String(a).localeCompare(String(b))
  )

  const points = keys
    .map((key) => {
      const raw = value(buckets.get(key))
      return raw === null ? null : {
        label: groupBy === 'batch' ? `batch ${key}` : String(key),
        value: round(raw, unit),
      }
    })
    .filter(Boolean)

  if (!points.length) return { error: 'nothing to divide by in that window' }

  // The total is recomputed over every row rather than summed from the points, because
  // a total of ratios is not a ratio.
  const whole = [...buckets.values()].reduce(
    (acc, b) => ({ num: acc.num + b.num, den: acc.den + b.den }), { num: 0, den: 0 }
  )
  const totalRaw = value(whole)

  return {
    metric_id: 'computed',
    title: titleFor(plan),
    unit,
    group_by: groupBy || 'none',
    points,
    total: totalRaw === null ? null : round(totalRaw, unit),
    computed: true,
    plan,
  }
}

/** What the window covers, for the sentence shown before anything runs. */
export function describeScope(plan) {
  const bits = []
  if (plan.channels?.length) bits.push(plan.channels.join(', '))
  if (plan.fromBatch || plan.toBatch) {
    const from = plan.fromBatch ?? 1
    const to = plan.toBatch ?? 10
    bits.push(from === to ? `week ${from}` : `weeks ${from} to ${to}`)
  }
  return bits.length ? bits.join(', ') : 'the whole ten-week corpus'
}
