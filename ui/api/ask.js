// The intent-mapping call site, ported to run in the deployed build.
//
// `pipeline/llm/intent.py` is the same job against Anthropic, and it is the one that
// produced the committed fixtures every scored number rests on. This file exists only
// so a *deployed* page can answer a question that is not already in those fixtures.
// It is the same contract in a different language and against a different provider:
//
//   - the model chooses a registered metric id, or a plan drawn from a closed
//     vocabulary of sources, measures and dimensions, or it declines;
//   - it never computes, never writes a query, and never sees a row of data —
//     `../src/lib/compute.js` does the arithmetic, and the same module's validator
//     runs here, so what is legal and what is executed cannot disagree;
//   - the restatement goes in front of a human before anything runs.
//
// The key is read from the environment and never leaves this function. Anything
// prefixed VITE_ is compiled into the browser bundle, so the key must not be.

// Imported, not mirrored: the plan vocabulary and its validator are the same module the
// browser evaluates with, so the thing that decides a plan is legal and the thing that
// runs it cannot disagree.
import {
  SOURCE_IDS, ALL_DIMS, ALL_MEASURES, BROWSE_CAP, vocabulary, validatePlan,
} from '../src/lib/compute.js'

const MODEL = process.env.GEMINI_MODEL || 'gemini-2.5-flash'
const ENDPOINT = (model) =>
  `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent`

// The frozen registry, mirrored from `pipeline/metrics/registry.py`. Mirrored rather
// than imported because this runs in Node and that is Python; `tests/test_ui_data.py`
// fails if the two lists ever diverge, so the mirror cannot rot silently.
const REGISTRY = [
  ['net_revenue_by_channel', 'inr', ['channel', 'batch'],
    'Money that actually reached the bank, after every platform deduction.'],
  ['gross_order_value', 'inr', ['channel', 'batch'],
    "What customers paid, from the seller's own ledger, before any deduction."],
  ['effective_take_rate', 'pct', ['channel', 'batch'],
    'Every deduction — commission, GST on commission, TCS and TDS — as a percentage of gross order value. A rising line is the take rate climbing.'],
  ['commission_share_of_gross', 'pct', ['channel', 'batch'],
    'Platform and fulfilment fee alone, excluding tax withholding.'],
  ['exception_count_by_cause', 'count', ['cause'],
    'How many exceptions each cause produced across the window.'],
  ['review_rate_trend', 'pct', ['batch'],
    'Settlement rows still needing a human after learned rules fire, as a percentage of the batch.'],
  ['auto_resolved_rows', 'count', ['batch'],
    'Settlement rows a learned rule closed without a human, per batch.'],
  ['claim_recovery_rate', 'pct', ['platform'],
    'Share of settled claims that recovered, per platform. Claims still inside their filing window are not counted either way.'],
  ['open_claim_value', 'inr', ['platform'],
    'Rupees still being chased, per platform.'],
  ['rupees_expired_unrecovered', 'inr', ['batch'],
    'Money whose filing window closed with no recovery, per batch.'],
]

const METRIC_IDS = REGISTRY.map(([id]) => id)
// Any real measure of the source: `browse` does not aggregate, but the shared validator
// wants one, and this keeps the two paths on a single validation routine.
// The second call, and a deliberately different contract from the first.
//
// The first call chooses what to compute and never sees a row. This one is handed rows
// and asked to read them, which is the weaker guarantee — so it is the fallback rather
// than the default, it is capped, and the UI labels its answers as read rather than
// computed. What keeps it honest is that the rows are real, they are the same rows the
// screens render, and they are shown next to the answer.
const READ_SYSTEM = `You answer a question from a table of rows taken out of a reconciliation of an Indian multi-channel seller's books.

Rules:
- Answer only from the rows given. They are the complete set matching the filter unless the message says the list was capped.
- Quote figures exactly as they appear. Do not add up long columns in your head — if the question needs a total across many rows, say that a computed metric would answer it better rather than risking a wrong sum.
- Money is rupees. A "batch" is one week; there are ten.
- Be brief: two or three sentences. No preamble, no bullet lists, no restating the question.
- If the rows do not contain the answer, say exactly what is missing. Do not guess and do not pad.`

const MEASURE_FOR = { money: 'gross', exceptions: 'count', claims: 'count' }
const GROUPINGS = ['channel', 'batch', 'cause', 'platform']
const GROUPINGS_FOR = Object.fromEntries(REGISTRY.map(([id, , g]) => [id, g]))

// Kept deliberately close to the Python system prompt. The differences are the
// grouping-versus-filter paragraph and the worked refusal, both of which exist because
// of how this went wrong the first time — see the notes on each below.
const SYSTEM = `You answer questions about a reconciliation of an Indian multi-channel apparel seller's books — Amazon, Flipkart, Myntra, their own website and a shop counter, over ten weekly batches.

You never compute anything, you never write a query, and you never see a row of data. You choose *what* to compute from a closed vocabulary, and a deterministic function does the arithmetic.

restatement is required on every outcome: one plain sentence saying what is about to be shown, put to the operator to confirm before anything runs.

You have four outcomes. Two of them are answers, and you should try hard to give one.

1. outcome "mapped" — a registered metric answers this exactly. These are the figures the run already published and the only ones that can be pinned, so prefer them when one genuinely fits. Set metric_id and group_by from the catalogue below.

2. outcome "computed" — no registered metric fits, but the question is arithmetic over the run. Set plan. This is the general case and it covers most questions; reach for it rather than declining.

3. outcome "browse" — the data holds the answer but it is not an aggregation: it needs reading rows, or free text like an operator's note or a claim letter, or two sources at once. Set browse to the source and the filters that narrow it, and the rows will be read for you. Filter as tightly as the question allows; at most ${BROWSE_CAP} rows are read.

4. outcome "clarify" — two readings would give materially different numbers. Ask exactly one question, and set nothing else.

5. outcome "refuse" — the data genuinely does not contain what is being asked about. Set nothing else.

Prefer a plan over a browse whenever the question is a count, a total, an average or a share: a computed figure is verified arithmetic, and a read one is not.

THE PLAN VOCABULARY. Three sources. A plan names one source, one measure from that source, an optional second measure to divide by, and one dimension to group by. Only the measures and dimensions listed under a source may be used with it.

${vocabulary()}

Plan fields:
- source (required), measure (required), groupBy (required — a dimension of that source, or "none" for a single total)
- per (optional): a second measure of the same source to divide by. money gross per orders is average order value; exceptions impact per count is the average rupees per exception; money deductions per gross is a percentage of gross.
- filters (optional): a list of {dim, values} on dimensions of that source. e.g. {dim:"channel", values:["myntra"]} or {dim:"status", values:["pending"]}.
- fromBatch / toBatch (optional): a week range, 1 to 10.
- sort (optional): "desc" for largest first, "asc" for smallest. Use it whenever the question says highest, lowest, worst, best or top. A week-by-week series always stays in week order.
- limit (optional): keep only the first N groups after sorting.

Worked examples:
- "which channel got the lowest conflicts" -> source "exceptions", measure "count", groupBy "channel", sort "asc".
- "highest average order value by channel" -> source "money", measure "gross", per "orders", groupBy "channel", sort "desc".
- "how many orders did we settle each week" -> source "money", measure "orders", groupBy "batch".
- "what is Myntra keeping as a share of gross" -> source "money", measure "deductions", per "gross", groupBy "channel", filters [{dim:"channel", values:["myntra"]}].
- "which cause costs us the most money" -> source "exceptions", measure "impact", groupBy "cause", sort "desc".
- "how much are we still chasing from each platform" -> source "claims", measure "amount", groupBy "platform", filters [{dim:"status", values:["filed","drafted"]}], sort "desc".
- "how many exceptions is a person still working" -> source "exceptions", measure "count", groupBy "none", filters [{dim:"status", values:["pending"]}].
- "what did we actually bank in the first three weeks" -> source "money", measure "net", groupBy "batch", fromBatch 1, toBatch 3.

Worked examples of browse:
- "what did the bookkeeper say about the Myntra rate" -> browse source "exceptions", filters [{dim:"channel", values:["myntra"]}].
- "why did claim CLM-0005 expire" -> browse source "claims", filters [{dim:"status", values:["expired"]}].
- "what kinds of thing is a person still working on" -> browse source "exceptions", filters [{dim:"status", values:["pending"]}].

Notes that prevent wrong answers:
- Grouping is not filtering. "By channel" is groupBy "channel"; "on Myntra" is a filter. A question can do both.
- On exceptions, "cause" is what the system concluded. "true_cause" is the answer key and should only be used if the question explicitly asks what the causes really were.
- The books are ten weekly batches. A "week" and a "batch" are the same thing. There are no dates finer than a week.

WHEN TO REFUSE. Only when the data does not contain the subject at all. If the subject is present but the shape is awkward, use browse rather than declining. What is held: orders and their values, channels, weeks, commission and fulfilment fees, tax withheld (GST on fees, TCS, TDS), money that reached the bank, order and settlement-row counts, every exception with its cause and rupee impact and how it was resolved, the learned rules, and recovery claims with their filing deadlines. What is NOT held: products or SKUs, cost of goods or profit, customers, inventory, shipping carriers, individual dates within a week, employees, and anything about the future — there is no forecast in this system.

Do not refuse merely because no registered metric covers the question; a plan almost certainly does. Do not offer a different chart as a consolation, and never approximate — an approximate answer to a money question is worse than no answer, because nobody checks it.

Write a refusal the way the rest of this system writes one: name what the reconciliation holds, then the specific fact that is missing, then say plainly it cannot be computed here at all. Attribute the limit to the data, never to "the deployed build".

A refusal in the right register, for "which of our SKUs are least profitable?":
"This reconciliation holds orders, settlements and bank credits. It has no product master and no cost of goods, so profitability per SKU cannot be computed here at all — not approximately, and not from an adjacent figure."
`

const catalogue = () =>
  REGISTRY.map(([id, unit, groupings, description]) =>
    `- ${id} [${unit}] (group by: ${groupings.join(', ')})\n    ${description}`
  ).join('\n')

const renderUser = (question) => [
  'The registry of metrics that can be computed:',
  '',
  catalogue(),
  '',
  'Channels in this business: amazon, flipkart, myntra, website, offline.',
  'The corpus is ten weekly batches, numbered one to ten.',
  '',
  'The operator asked:',
  '',
  `    "${question.trim()}"`,
  '',
  'Map it, ask one clarifying question, or refuse.',
].join('\n')

// The OpenAPI subset Gemini accepts. It cannot express "mapped implies metric_id",
// so that rule is enforced in `validate` below — the same check the Python model
// validator makes, for the same reason: a half-answer should never reach the screen.
const RESPONSE_SCHEMA = {
  type: 'object',
  properties: {
    outcome: { type: 'string', enum: ['mapped', 'computed', 'browse', 'clarify', 'refuse'] },
    metric_id: { type: 'string', enum: METRIC_IDS, nullable: true },
    group_by: { type: 'string', enum: GROUPINGS, nullable: true },
    plan: {
      type: 'object',
      nullable: true,
      properties: {
        source: { type: 'string', enum: SOURCE_IDS },
        measure: { type: 'string', enum: ALL_MEASURES },
        per: { type: 'string', enum: ALL_MEASURES, nullable: true },
        groupBy: { type: 'string', enum: [...ALL_DIMS, 'none'] },
        filters: {
          type: 'array',
          nullable: true,
          items: {
            type: 'object',
            properties: {
              dim: { type: 'string', enum: ALL_DIMS },
              values: { type: 'array', items: { type: 'string' } },
            },
            required: ['dim', 'values'],
          },
        },
        fromBatch: { type: 'integer', nullable: true },
        toBatch: { type: 'integer', nullable: true },
        sort: { type: 'string', enum: ['asc', 'desc'], nullable: true },
        limit: { type: 'integer', nullable: true },
      },
      required: ['source', 'measure', 'groupBy'],
    },
    browse: {
      type: 'object',
      nullable: true,
      properties: {
        source: { type: 'string', enum: SOURCE_IDS },
        filters: {
          type: 'array',
          nullable: true,
          items: {
            type: 'object',
            properties: {
              dim: { type: 'string', enum: ALL_DIMS },
              values: { type: 'array', items: { type: 'string' } },
            },
            required: ['dim', 'values'],
          },
        },
        fromBatch: { type: 'integer', nullable: true },
        toBatch: { type: 'integer', nullable: true },
      },
      required: ['source'],
    },
    restatement: { type: 'string' },
    clarifying_question: { type: 'string', nullable: true },
    refusal: { type: 'string', nullable: true },
  },
  required: ['outcome', 'restatement'],
  propertyOrdering: [
    'outcome', 'metric_id', 'group_by', 'plan', 'browse',
    'restatement', 'clarifying_question', 'refusal',
  ],
}

/** The outcome must carry the field it exists for. Mirrors `_outcome_carries_its_payload`. */
function validate(intent) {
  const { outcome } = intent
  if (!['mapped', 'computed', 'browse', 'clarify', 'refuse'].includes(outcome)) {
    return 'unknown outcome'
  }
  if (!intent.restatement || intent.restatement.length < 15) return 'restatement missing'

  if (outcome === 'mapped') {
    if (!intent.metric_id) return 'mapped without a metric id'
    if (!METRIC_IDS.includes(intent.metric_id)) return 'metric id outside the registry'
    const allowed = GROUPINGS_FOR[intent.metric_id]
    // An unsupported grouping is corrected to the metric's own default rather than
    // refused: the id is the choice that matters, and every metric has exactly one
    // natural grouping to fall back to.
    if (!intent.group_by || !allowed.includes(intent.group_by)) intent.group_by = allowed[0]
    intent.plan = null
    return null
  }

  if (outcome === 'computed') {
    if (!intent.plan) return 'computed without a plan'
    // The same validator the browser runs before evaluating, so an illegal plan is
    // caught here rather than becoming an error message where a chart should be.
    const bad = validatePlan(intent.plan)
    if (bad) return bad
    // A computed answer is not a registered metric and must not borrow the name of one.
    intent.metric_id = null
    intent.browse = null
    return null
  }

  if (outcome === 'browse') {
    if (!intent.browse) return 'browse without a selection'
    // Validated through the same vocabulary as a plan, minus the aggregation half.
    const bad = validatePlan({ ...intent.browse, measure: MEASURE_FOR[intent.browse.source] })
    if (bad) return bad
    intent.metric_id = null
    intent.plan = null
    return null
  }

  // A decline that also names a metric or a plan is the failure worth blocking: it
  // reads as an answer on screen. Both declining outcomes must arrive empty-handed.
  if (intent.metric_id) return `${outcome} must not name a metric`
  if (intent.plan) return `${outcome} must not carry a plan`
  if (intent.browse) return `${outcome} must not carry a selection`
  if (outcome === 'clarify' && !intent.clarifying_question) return 'clarify without a question'
  if (outcome === 'refuse' && !intent.refusal) return 'refuse without a reason'
  return null
}

/** One call to the model. Both paths go through it, so retries and errors read alike. */
async function askGemini(key, { system, user, schema }) {
  try {
    const upstream = await fetch(ENDPOINT(MODEL), {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'x-goog-api-key': key },
      body: JSON.stringify({
        system_instruction: { parts: [{ text: system }] },
        contents: [{ role: 'user', parts: [{ text: user }] }],
        generationConfig: {
          temperature: 0,
          ...(schema
            ? { responseMimeType: 'application/json', responseSchema: schema }
            : {}),
        },
      }),
    })
    if (!upstream.ok) {
      const detail = await upstream.text()
      console.error('gemini upstream', upstream.status, detail.slice(0, 500))
      return { error: `The model service returned ${upstream.status}.`, status: 502 }
    }
    const payload = await upstream.json()
    const text = payload?.candidates?.[0]?.content?.parts?.[0]?.text
    if (!text) return { error: 'The model returned no content.', status: 502 }
    return { text }
  } catch (err) {
    console.error('gemini call failed', err)
    return { error: 'Could not reach the model service.', status: 502 }
  }
}

export default async function handler(req, res) {
  if (req.method !== 'POST') {
    res.setHeader('Allow', 'POST')
    return res.status(405).json({ error: 'Use POST.' })
  }

  const key = process.env.GEMINI_API_KEY
  if (!key) {
    // 501, not 500: nothing is broken, the capability is simply not configured. The UI
    // reads this and falls back to the offline fixtures rather than showing an error.
    return res.status(501).json({ error: 'No GEMINI_API_KEY is configured on this deployment.' })
  }

  const question = (req.body?.question || '').toString().trim()
  if (!question) return res.status(400).json({ error: 'Ask a question.' })
  if (question.length > 500) return res.status(400).json({ error: 'That question is too long.' })

  // Mode "read" is the second half of the browse fallback: the browser has already
  // selected and capped the rows through the shared validator, and this only reads them.
  if (req.body?.mode === 'read') {
    const rows = req.body?.rows
    if (!Array.isArray(rows) || !rows.length) {
      return res.status(400).json({ error: 'No rows to read.' })
    }
    if (rows.length > BROWSE_CAP) {
      return res.status(400).json({ error: `Too many rows: ${rows.length} over the ${BROWSE_CAP} cap.` })
    }
    const note = req.body?.truncated
      ? `\n\nThis list was capped at ${rows.length} of ${req.body.total} matching rows.`
      : `\n\nThis is all ${rows.length} matching rows.`
    const answer = await askGemini(key, {
      system: READ_SYSTEM,
      user: `Question: ${question}\n\nRows:\n${JSON.stringify(rows)}${note}`,
    })
    if (answer.error) return res.status(answer.status).json({ error: answer.error })
    return res.status(200).json({ outcome: 'read', answer: answer.text, model: MODEL, source: 'live' })
  }

  const mapped = await askGemini(key, {
    system: SYSTEM,
    user: renderUser(question),
    schema: RESPONSE_SCHEMA,
  })
  if (mapped.error) return res.status(mapped.status).json({ error: mapped.error })

  let reply
  try {
    reply = JSON.parse(mapped.text)
  } catch {
    return res.status(502).json({ error: 'The model returned content that was not JSON.' })
  }

  // No fallback branch, for the same reason the Python client has none: a reply that
  // broke the contract must not be repaired into something plausible.
  const problem = validate(reply)
  if (problem) {
    console.error('schema violation', problem, reply)
    return res.status(502).json({ error: `The model broke the response contract: ${problem}.` })
  }

  return res.status(200).json({ ...reply, model: MODEL, source: 'live' })
}
