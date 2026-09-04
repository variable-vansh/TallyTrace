// Arithmetic over the reconciled run, for questions no metric was registered for.
//
// The ten registered metrics are the questions this run already answered. This is the
// material they were computed from, exposed as three fact tables — the money, the
// exceptions, the claims — so a question nobody anticipated is still arithmetic rather
// than a refusal.
//
// **The model does not write this arithmetic.** It fills in a plan: which source, which
// measure, over what denominator, grouped by which dimension, filtered how. Every field
// is drawn from a closed vocabulary declared below, validated on the way in, and
// executed here by code that was written once and can be read. There is no query, no
// generated expression and nothing evaluated — the failure mode is "picked the wrong
// measure", which the restatement puts in front of a person before it runs, and not
// "returned a plausible wrong number", which is what generated SQL does.
//
// Two of the three tables are read straight off the payload the screens already render,
// so nothing here can drift from what the rest of the UI shows. The money table is
// pre-aggregated in Python from the same object the registry reads: it is aggregates
// rather than raw ledger rows because the ledger view repeats an order in every batch
// that carries it forward — 1,049 orders appear 2,625 times — and summing those rows
// would overstate the books by two and a half times.

const INR = 'inr'
const COUNT = 'count'

/** Nulls are a real answer here — an unmatched bank credit has no channel. */
const orNone = (value, fallback = 'unattributed') =>
  value === null || value === undefined || value === '' ? fallback : String(value)

export const SOURCES = {
  money: {
    label: 'the reconciled books',
    describes: 'orders, what they were worth, what was deducted and what reached the bank',
    rows: (data) => data?.facts || [],
    dims: {
      channel: (row) => row.channel,
      batch: (row) => row.batch,
    },
    measures: {
      gross: { label: 'gross order value', unit: INR, of: (r) => r.gross || 0 },
      net: { label: 'net settled to the bank', unit: INR, of: (r) => r.net || 0 },
      fees: { label: 'commission and fulfilment fees', unit: INR, of: (r) => r.fees || 0 },
      taxes: { label: 'tax withheld — GST on fees, TCS and TDS', unit: INR, of: (r) => r.taxes || 0 },
      deductions: {
        label: 'every deduction — fees and tax withheld',
        unit: INR,
        of: (r) => (r.fees || 0) + (r.taxes || 0),
      },
      orders: { label: 'distinct orders settled', unit: COUNT, of: (r) => r.orders || 0 },
      settlement_rows: { label: 'settlement rows', unit: COUNT, of: (r) => r.settlementRows || 0 },
    },
  },

  exceptions: {
    label: 'exceptions the matcher raised',
    describes:
      'every row the matcher could not settle — what it was, which rule touched it, ' +
      'whether it closed itself or went to a person, and what it was worth',
    rows: (data) => (data?.weeks || []).flatMap((week) => week.exceptions || []),
    dims: {
      channel: (row) => orNone(row.channel),
      batch: (row) => row.batch,
      // What the *system* concluded, not the answer key. `true_cause` is offered
      // separately and named for what it is, so a question about causes is answered
      // with what the product believed unless the asker says otherwise.
      cause: (row) => orNone(row.proposedCause, 'unclassified'),
      true_cause: (row) => orNone(row.trueCause, 'unclassified'),
      reason: (row) => orNone(row.reason),
      bucket: (row) => orNone(row.bucket),
      status: (row) => orNone(row.status),
      outcome: (row) => orNone(row.outcome),
      rule: (row) => orNone(row.ruleId, 'no rule'),
      rule_state: (row) => orNone(row.ruleState, 'no rule'),
    },
    measures: {
      count: { label: 'exceptions raised', unit: COUNT, of: () => 1 },
      impact: { label: 'rupees in question', unit: INR, of: (r) => Math.abs(r.impact || 0) },
    },
  },

  claims: {
    label: 'recovery claims',
    describes: 'money somebody else owes, its filing deadline, and whether it came back',
    rows: (data) => data?.claims || [],
    dims: {
      platform: (row) => orNone(row.platform),
      status: (row) => orNone(row.status),
      cause: (row) => orNone(row.cause),
      batch: (row) => row.opened_batch,
    },
    measures: {
      count: { label: 'claims', unit: COUNT, of: () => 1 },
      amount: { label: 'rupees claimed', unit: INR, of: (r) => r.amount || 0 },
      recovered: { label: 'rupees recovered', unit: INR, of: (r) => r.recoveredAmount || 0 },
    },
  },
}

export const SOURCE_IDS = Object.keys(SOURCES)
/** Every dimension and measure name across every source, for the response schema. */
export const ALL_DIMS = [...new Set(SOURCE_IDS.flatMap((s) => Object.keys(SOURCES[s].dims)))]
export const ALL_MEASURES = [
  ...new Set(SOURCE_IDS.flatMap((s) => Object.keys(SOURCES[s].measures))),
]

/** The vocabulary, rendered for a prompt, so the model is told exactly what exists. */
export function vocabulary() {
  return SOURCE_IDS.map((id) => {
    const source = SOURCES[id]
    const measures = Object.entries(source.measures)
      .map(([key, m]) => `${key} (${m.unit})`)
      .join(', ')
    return [
      `SOURCE "${id}" — ${source.label}: ${source.describes}`,
      `  measures: ${measures}`,
      `  groupBy / filter on: ${Object.keys(source.dims).join(', ')}`,
    ].join('\n')
  }).join('\n\n')
}

/** Reject a plan the vocabulary does not contain, rather than coercing it into one. */
export function validatePlan(plan) {
  if (!plan || typeof plan !== 'object') return 'no plan'

  const source = SOURCES[plan.source]
  if (!source) return `unknown source "${plan.source}"`
  if (!source.measures[plan.measure]) {
    return `"${plan.measure}" is not a measure of ${plan.source}`
  }
  if (plan.per != null && !source.measures[plan.per]) {
    return `"${plan.per}" is not a measure of ${plan.source}`
  }
  if (plan.per && plan.per === plan.measure) {
    return `${plan.measure} over itself is not a quantity`
  }
  if (plan.groupBy && plan.groupBy !== 'none' && !source.dims[plan.groupBy]) {
    return `${plan.source} cannot be grouped by "${plan.groupBy}"`
  }
  for (const filter of plan.filters || []) {
    if (!filter || !source.dims[filter.dim]) {
      return `${plan.source} cannot be filtered by "${filter?.dim}"`
    }
    if (!filter.values?.length) return `no values given for filter "${filter.dim}"`
  }
  if (plan.fromBatch != null && plan.toBatch != null && plan.fromBatch > plan.toBatch) {
    return 'the week range runs backwards'
  }
  if (plan.sort != null && !['asc', 'desc'].includes(plan.sort)) {
    return `unknown sort "${plan.sort}"`
  }
  return null
}

const titleFor = (plan) => {
  const source = SOURCES[plan.source]
  const head = source.measures[plan.measure].label
  const title = plan.per
    ? `${head} per ${source.measures[plan.per].label.replace(/^(distinct|every) /, '')}`
    : head
  return title.charAt(0).toUpperCase() + title.slice(1)
}

/** The unit of the answer, which is not always the unit of the measure. */
const unitFor = (plan) => {
  const { measures } = SOURCES[plan.source]
  const measure = measures[plan.measure].unit
  if (!plan.per) return measure
  const per = measures[plan.per].unit
  // Money over money is a share. Money over a count is an average amount. A count over
  // a count is a bare ratio.
  if (measure === INR && per === INR) return 'pct'
  if (measure === INR) return INR
  return 'ratio'
}

const round = (value, unit) => {
  const dp = unit === 'pct' ? 2 : unit === 'ratio' ? 3 : unit === COUNT ? 0 : 2
  return Number(value.toFixed(dp))
}

/**
 * Run a validated plan.
 *
 * Returns the shape the registry's own results use, so the chart, the pin and the
 * confirm step do not need to know which of the two produced it.
 */
export function computePlan(data, plan) {
  const problem = validatePlan(plan)
  if (problem) return { error: problem }

  const source = SOURCES[plan.source]
  const dims = source.dims
  const from = plan.fromBatch ?? -Infinity
  const to = plan.toBatch ?? Infinity

  const matches = (row) => {
    if (dims.batch) {
      const batch = dims.batch(row)
      // A row with no batch (a claim whose opening batch is not recorded) is outside
      // any week range, rather than silently inside every one.
      if (plan.fromBatch != null || plan.toBatch != null) {
        if (batch == null || batch < from || batch > to) return false
      }
    }
    for (const filter of plan.filters || []) {
      const want = new Set(filter.values.map(String))
      if (!want.has(String(dims[filter.dim](row)))) return false
    }
    return true
  }

  const rows = source.rows(data).filter(matches)
  if (!rows.length) return { error: 'nothing matches that' }

  const groupBy = plan.groupBy && plan.groupBy !== 'none' ? plan.groupBy : null
  const measure = source.measures[plan.measure]
  const per = plan.per ? source.measures[plan.per] : null
  const unit = unitFor(plan)

  // Sum numerator and denominator separately, then divide once per group. Averaging
  // per-group ratios instead would weight a 6-row week the same as a 180-row one.
  const buckets = new Map()
  for (const row of rows) {
    const key = groupBy ? String(dims[groupBy](row)) : 'all'
    const bucket = buckets.get(key) || { num: 0, den: 0 }
    bucket.num += measure.of(row)
    if (per) bucket.den += per.of(row)
    buckets.set(key, bucket)
  }

  const value = ({ num, den }) => {
    if (!per) return num
    if (!den) return null                        // nothing to divide by; not zero
    return unit === 'pct' ? (num / den) * 100 : num / den
  }

  const numeric = groupBy === 'batch'
  let points = [...buckets.entries()]
    .map(([label, bucket]) => {
      const raw = value(bucket)
      return raw === null ? null : {
        label: numeric ? `batch ${label}` : label,
        value: round(raw, unit),
        sortKey: numeric ? Number(label) : label,
      }
    })
    .filter(Boolean)

  if (!points.length) return { error: 'nothing to divide by in that window' }

  // A week series is always in week order — sorting it by size would destroy the trend
  // it exists to show. Everything else honours the ordering the question asked for.
  if (numeric || !plan.sort) {
    points.sort((a, b) =>
      numeric ? a.sortKey - b.sortKey : String(a.sortKey).localeCompare(String(b.sortKey))
    )
  } else {
    points.sort((a, b) => (plan.sort === 'asc' ? a.value - b.value : b.value - a.value))
  }
  if (plan.limit > 0) points = points.slice(0, plan.limit)
  points = points.map(({ label, value: v }) => ({ label, value: v }))

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
    rowsConsidered: rows.length,
  }
}

/** What the plan covers, for the line shown under the restatement before it runs. */
export function describeScope(plan) {
  const bits = [`${plan.source}`]
  for (const filter of plan.filters || []) {
    bits.push(`${filter.dim} in ${filter.values.join('/')}`)
  }
  if (plan.fromBatch != null || plan.toBatch != null) {
    const from = plan.fromBatch ?? 1
    const to = plan.toBatch ?? 10
    bits.push(from === to ? `week ${from}` : `weeks ${from}–${to}`)
  } else {
    bits.push('all ten weeks')
  }
  if (plan.sort) bits.push(plan.sort === 'asc' ? 'lowest first' : 'highest first')
  return bits.join(' · ')
}

// --------------------------------------------------------------------------- //
// The sentence
// --------------------------------------------------------------------------- //

const rupees = (n) =>
  `₹${Math.round(n).toLocaleString('en-IN')}`

// Registry results carry totals as strings — every amount in `data/score.json` is a
// string on purpose — so this coerces rather than trusting the type.
const say = (value, unit) => {
  const n = Number(value)
  return unit === 'pct' ? `${n}%`
    : unit === INR ? rupees(n)
      : unit === 'ratio' ? String(n)
        : n.toLocaleString('en-IN')
}

/**
 * The answer in one sentence, derived from the result rather than written about it.
 *
 * A chart is not an answer. "Which channel had the fewest exceptions" wants the words
 * "offline, with one" — reading it off a bar is work the tool should have done.
 *
 * **This is deterministic on purpose, and it is the one place that decision is worth
 * defending.** The obvious build is to hand the computed table back to the model and ask
 * it to phrase the finding, which is what most chat-over-data products do. It is also
 * how a correct computation acquires a wrong sentence: the arithmetic is verified and
 * then a model re-types the number beside it, and nobody re-checks prose. Every figure
 * here is substituted from the result — the same rule the claim drafter follows, where
 * the schema forbids the model a numeral and every rupee is filled in from the matcher's
 * own verdicts.
 */
const unitOfResult = (result) => result.unit

// `refund_timing_lag` is the key the data uses and the chart axis keeps it, because it
// is what the registry prints. A sentence should read like a sentence.
const readable = (label) => String(label).replace(/_/g, ' ')

export function headline(result) {
  if (!result || result.error || !result.points?.length) return null
  const { points, unit, group_by: groupBy, total, plan } = result
  // Registry results arrive without a plan — they were computed in Python, not from a
  // plan — so the subject comes off the title they carry instead. Both kinds get a
  // sentence; only the computed ones can have been sorted by the question.
  const sort = plan?.sort
  const raw = plan
    ? SOURCES[plan.source].measures[plan.measure].label
    : String(result.title || '').toLowerCase()
  // The ₹ sign already says "rupees", so a label that also says it reads twice.
  const subject = raw.replace(/^rupees /, unit === INR ? '' : 'rupees ')

  const Cap = (text) => String(text).charAt(0).toUpperCase() + String(text).slice(1)

  if (groupBy === 'none' || points.length === 1) {
    const only = points[0]
    const where = groupBy === 'none' ? '' : ` for ${readable(only.label)}`
    return `${say(only.value, unit)} ${subject}${where}.`
  }

  // Sorted by the question: the leader is the answer, and the runner-up is the context
  // that says whether it was close.
  const ranked = [...points].sort((a, b) => b.value - a.value)
  const top = ranked[0]
  const bottom = ranked[ranked.length - 1]
  const asked = sort === 'asc' ? bottom : top
  const other = sort === 'asc' ? ranked[ranked.length - 2] : ranked[1]
  const superlative = sort === 'asc' ? 'lowest' : 'highest'

  if (sort) {
    const tail = other
      ? ` Next is ${readable(other.label)} at ${say(other.value, unit)}.`
      : ''
    return `${Cap(readable(asked.label))} is ${superlative}, at ${say(asked.value, unit)}.${tail}`
  }

  // No ordering was asked for, so lead with the shape of the whole thing.
  const spread = `${readable(top.label)} highest at ${say(top.value, unit)}, `
    + `${readable(bottom.label)} lowest at ${say(bottom.value, unit)}`
  // A total of ratios is not a ratio, so it is only quoted where adding up means something.
  const whole = total !== null && unit !== 'pct' && unit !== 'ratio'
    ? `${say(total, unit)} across ${points.length} ${groupBy === 'batch' ? 'weeks' : groupBy + 's'} — `
    : ''
  return `${whole}${spread}.`
}

// --------------------------------------------------------------------------- //
// The fallback
// --------------------------------------------------------------------------- //

/** Hard cap on rows handed back to a model. Enough to reason over, small enough to read. */
export const BROWSE_CAP = 200

/** Fields worth showing per source. The rest is noise in a prompt and costs tokens. */
const BROWSE_FIELDS = {
  money: ['batch', 'channel', 'gross', 'net', 'fees', 'taxes', 'orders', 'settlementRows'],
  exceptions: [
    'batch', 'channel', 'reason', 'bucket', 'status', 'outcome',
    'proposedCause', 'impact', 'ruleId', 'humanResolution',
  ],
  claims: [
    'claim_id', 'platform', 'status', 'cause', 'amount',
    'daysRemaining', 'opened_batch', 'recoveredAmount',
  ],
}

/**
 * Rows for a question the plan grammar cannot express, bounded and projected.
 *
 * The grammar answers aggregations. It cannot answer "what did the bookkeeper say about
 * the Myntra rate", or anything needing two sources at once, and a tool that refuses
 * every such question is a tool people stop asking. So there is a second path: filter
 * with the same validated vocabulary, take at most ``BROWSE_CAP`` rows, and let the
 * model read them.
 *
 * **This path is weaker and the UI says so.** An answer read off rows is the model's
 * reading, not a computed figure — it is labelled differently on screen for that reason,
 * and it is the fallback rather than the default precisely because the guarantee is
 * lower. What it is not is a guess: the rows are real, they are the same rows the
 * screens render, and they are shown alongside the answer.
 */
export function browseRows(data, browse) {
  const problem = validatePlan({ ...browse, measure: firstMeasureOf(browse.source) })
  if (problem) return { error: problem }

  const source = SOURCES[browse.source]
  const dims = source.dims
  const from = browse.fromBatch ?? -Infinity
  const to = browse.toBatch ?? Infinity

  const rows = source.rows(data).filter((row) => {
    if (dims.batch && (browse.fromBatch != null || browse.toBatch != null)) {
      const batch = dims.batch(row)
      if (batch == null || batch < from || batch > to) return false
    }
    for (const filter of browse.filters || []) {
      const want = new Set(filter.values.map(String))
      if (!want.has(String(dims[filter.dim](row)))) return false
    }
    return true
  })

  if (!rows.length) return { error: 'nothing matches that' }

  const fields = BROWSE_FIELDS[browse.source]
  const flatten = (value) =>
    // The operator's resolution arrives as a record; only the sentence they typed is
    // worth sending, and the rest is provenance the model has no use for.
    value && typeof value === 'object' && !Array.isArray(value)
      ? (value.text ?? value.subject ?? JSON.stringify(value).slice(0, 200))
      : value
  const projected = rows.slice(0, BROWSE_CAP).map((row) =>
    Object.fromEntries(
      fields
        .map((f) => [f, flatten(row[f])])
        .filter(([, v]) => v != null && v !== '')
    )
  )

  return { rows: projected, total: rows.length, truncated: rows.length > BROWSE_CAP }
}

const firstMeasureOf = (sourceId) =>
  SOURCES[sourceId] ? Object.keys(SOURCES[sourceId].measures)[0] : undefined
