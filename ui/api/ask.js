// The intent-mapping call site, ported to run in the deployed build.
//
// `pipeline/llm/intent.py` is the same job against Anthropic, and it is the one that
// produced the committed fixtures every scored number rests on. This file exists only
// so a *deployed* page can answer a question that is not already in those fixtures.
// It is the same contract in a different language and against a different provider:
//
//   - the model chooses one id from the frozen registry, or it declines;
//   - it never computes, never writes a query, and never sees a row of data;
//   - the schema cannot express a filter, because this deployment only holds
//     whole-corpus results and a schema that cannot promise one cannot break it;
//   - the restatement goes in front of a human before anything is looked up.
//
// The key is read from the environment and never leaves this function. Anything
// prefixed VITE_ is compiled into the browser bundle, so the key must not be.

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
const GROUPINGS = ['channel', 'batch', 'cause', 'platform']
const GROUPINGS_FOR = Object.fromEntries(REGISTRY.map(([id, , g]) => [id, g]))

// Kept deliberately close to the Python system prompt. The one difference is the
// paragraph about filters, which is true of this deployment and not of the CLI.
const SYSTEM = `You map a question about a reconciliation dashboard onto exactly one metric from a fixed registry, or you decline.

You never compute anything, you never write a query, and you never see the data. You choose an id, and a deterministic function that has already run does the rest.

Rules you must follow:
- Choose only from the metric ids in the schema. There is no nearest match and no "closest available" answer.
- Choose a grouping the metric supports. The catalogue below lists them per metric.
- restatement is required on every outcome. Write what is about to be shown in one plain sentence, as it will be put to the operator for confirmation before it runs. Name the metric in words and the grouping.
- This deployment holds whole-corpus results only. You cannot filter to a single channel or to a range of weeks. If a question can only be answered by such a filter, refuse and say that the deployed build computes the whole ten-week corpus.
- Use outcome "clarify" when two metrics could genuinely be meant, or when the question turns on a distinction the registry draws and the asker did not. Ask exactly one question. Do not also pick a metric.
- Use outcome "refuse" when nothing in the registry answers the question. Say plainly what is not available. Do not offer a different chart as a consolation, and do not suggest the answer might be approximated by something adjacent — an approximate answer to a money question is worse than no answer, because nobody checks it.`

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
    outcome: { type: 'string', enum: ['mapped', 'clarify', 'refuse'] },
    metric_id: { type: 'string', enum: METRIC_IDS, nullable: true },
    group_by: { type: 'string', enum: GROUPINGS, nullable: true },
    restatement: { type: 'string' },
    clarifying_question: { type: 'string', nullable: true },
    refusal: { type: 'string', nullable: true },
  },
  required: ['outcome', 'restatement'],
  propertyOrdering: ['outcome', 'metric_id', 'group_by', 'restatement', 'clarifying_question', 'refusal'],
}

/** The outcome must carry the field it exists for. Mirrors `_outcome_carries_its_payload`. */
function validate(intent) {
  const { outcome } = intent
  if (!['mapped', 'clarify', 'refuse'].includes(outcome)) return 'unknown outcome'
  if (!intent.restatement || intent.restatement.length < 15) return 'restatement missing'

  if (outcome === 'mapped') {
    if (!intent.metric_id) return 'mapped without a metric id'
    if (!METRIC_IDS.includes(intent.metric_id)) return 'metric id outside the registry'
    const allowed = GROUPINGS_FOR[intent.metric_id]
    // An unsupported grouping is corrected to the metric's own default rather than
    // refused: the id is the choice that matters, and every metric has exactly one
    // natural grouping to fall back to.
    if (!intent.group_by || !allowed.includes(intent.group_by)) intent.group_by = allowed[0]
    return null
  }

  // A refusal that also names a metric is the failure worth blocking: it reads as an
  // answer on screen. Both declining outcomes must arrive empty-handed.
  if (intent.metric_id) return `${outcome} must not name a metric`
  if (outcome === 'clarify' && !intent.clarifying_question) return 'clarify without a question'
  if (outcome === 'refuse' && !intent.refusal) return 'refuse without a reason'
  return null
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

  let reply
  try {
    const upstream = await fetch(ENDPOINT(MODEL), {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'x-goog-api-key': key },
      body: JSON.stringify({
        system_instruction: { parts: [{ text: SYSTEM }] },
        contents: [{ role: 'user', parts: [{ text: renderUser(question) }] }],
        generationConfig: {
          temperature: 0,
          responseMimeType: 'application/json',
          responseSchema: RESPONSE_SCHEMA,
        },
      }),
    })

    if (!upstream.ok) {
      const detail = await upstream.text()
      console.error('gemini upstream', upstream.status, detail.slice(0, 500))
      return res.status(502).json({ error: `The model service returned ${upstream.status}.` })
    }

    const payload = await upstream.json()
    const text = payload?.candidates?.[0]?.content?.parts?.[0]?.text
    if (!text) return res.status(502).json({ error: 'The model returned no content.' })
    reply = JSON.parse(text)
  } catch (err) {
    console.error('gemini call failed', err)
    return res.status(502).json({ error: 'Could not reach the model service.' })
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
