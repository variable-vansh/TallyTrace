import { useEffect, useMemo, useRef, useState } from 'react'
import {
  BookOpen, CheckCircle2, CornerDownLeft, HelpCircle, Pin, PinOff, Search, Slash,
} from 'lucide-react'
import MetricChart from '../components/MetricChart'
import StatusBadge from '../components/StatusBadge'

// Two things live on this screen, and the split is the product.
//
// LEFT — talk to the books. Describe the metric you want in your own words. The model
// maps it onto one registered metric id and says, in a sentence, what it is about to
// compute. Nothing is computed until that sentence is accepted. A question two metrics
// could answer gets one clarifying question; a question the registry cannot answer gets
// a refusal, and never a plausible adjacent chart.
//
// RIGHT — what has been kept. A pinned metric stores an id and its parameters, never a
// number, and recomputes every batch with no model anywhere in the loop.

const normalise = (text) =>
  text.toLowerCase().replace(/[^a-z0-9 ]/g, ' ').replace(/\s+/g, ' ').trim()

// `#ask?q=<question>` opens this screen and asks it — useful for linking someone straight
// at the refusal, which is the part of this surface worth showing first. `&yes=1` accepts
// the restatement on the linker's behalf, exactly as `--yes` does on `make ask`; the link
// is the operator's own confirmation, not a way around the confirm step.
const linkedQuestion = () => {
  const query = window.location.hash.split('?')[1]
  if (!query) return null
  const params = new URLSearchParams(query)
  const asked = params.get('q')
  return asked ? { question: decodeURIComponent(asked), confirmed: params.get('yes') === '1' } : null
}

const OUTCOME = {
  mapped: { icon: CheckCircle2, tone: 'border-success/40 bg-success-light/40', label: 'mapped' },
  clarify: { icon: HelpCircle, tone: 'border-amber/50 bg-amber-light/50', label: 'needs one answer' },
  refuse: { icon: Slash, tone: 'border-divider bg-gray-50', label: 'refused' },
  unasked: { icon: Search, tone: 'border-divider bg-gray-50', label: 'not in the fixtures' },
}

function Bubble({ from, children }) {
  const mine = from === 'operator'
  return (
    <div className={`flex ${mine ? 'justify-end' : 'justify-start'}`}>
      <div
        className={`max-w-[92%] rounded-2xl px-4 py-3 ${
          mine
            ? 'bg-gray-900 text-white rounded-br-sm'
            : 'bg-white border border-divider text-gray-900 rounded-bl-sm'
        }`}
      >
        {children}
      </div>
    </div>
  )
}

function Turn({ turn, onConfirm, onDecline, onPick, onPin, pinned }) {
  if (turn.from === 'operator') {
    return <Bubble from="operator"><p className="text-sm">{turn.text}</p></Bubble>
  }

  const meta = OUTCOME[turn.outcome] || OUTCOME.unasked
  const Icon = meta.icon

  return (
    <Bubble from="system">
      <div className="flex items-center gap-2 mb-1.5">
        <Icon size={14} className="text-muted" />
        <StatusBadge variant={
          turn.outcome === 'mapped' ? 'success' : turn.outcome === 'clarify' ? 'amber' : 'muted'
        }>
          {meta.label}
        </StatusBadge>
        {turn.metricId && (
          <span className="font-mono text-[11px] text-muted">{turn.metricId}</span>
        )}
      </div>

      <p className="text-sm text-gray-800 leading-relaxed">{turn.text}</p>

      {turn.state === 'awaiting' && (
        <div className="flex items-center gap-2 mt-3">
          <button
            onClick={() => onConfirm(turn.id)}
            className="px-3 py-1.5 rounded-lg bg-gray-900 text-white text-xs font-semibold hover:bg-gray-800"
          >
            Compute this
          </button>
          <button
            onClick={() => onDecline(turn.id)}
            className="px-3 py-1.5 rounded-lg border border-divider text-gray-700 text-xs font-semibold hover:bg-gray-50"
          >
            Not what I meant
          </button>
          <span className="text-[11px] text-muted">nothing has been computed yet</span>
        </div>
      )}

      {turn.state === 'declined' && (
        <p className="text-xs text-muted mt-2">
          Nothing was computed. Rephrase, or pick the metric you meant from the registry.
        </p>
      )}

      {turn.outcome === 'clarify' && turn.state !== 'answered' && (
        <div className="mt-3 border-t border-divider pt-3">
          <p className="text-xs text-muted mb-2">
            Answer it by naming the metric you meant. The model asked the question; the
            registry is the vocabulary, and picking from it needs no second model call.
          </p>
          <RegistryPicker registry={turn.registry} onPick={(id) => onPick(turn.id, id)} />
        </div>
      )}

      {turn.result && (
        <div className="mt-3 border-t border-divider pt-3">
          <div className="flex items-baseline justify-between gap-2 mb-1">
            <span className="text-xs font-semibold text-gray-900 uppercase tracking-wide">
              {turn.result.title}
            </span>
            <span className="text-[11px] text-muted">by {turn.result.group_by}</span>
          </div>
          <MetricChart result={turn.result} height={200} />
          <div className="flex items-center justify-between gap-2 mt-2">
            <span className="text-[11px] text-muted">
              Computed by the registry. No model was involved past the mapping above.
            </span>
            <button
              onClick={() => onPin(turn)}
              disabled={pinned}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold ${
                pinned
                  ? 'text-muted border border-divider cursor-default'
                  : 'bg-primary text-white hover:bg-primary-hover'
              }`}
            >
              {pinned ? <PinOff size={12} /> : <Pin size={12} />}
              {pinned ? 'Pinned' : 'Pin this'}
            </button>
          </div>
        </div>
      )}
    </Bubble>
  )
}

function RegistryPicker({ registry, onPick }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {registry.map((metric) => (
        <button
          key={metric.metric_id}
          onClick={() => onPick(metric.metric_id)}
          title={metric.description}
          className="font-mono text-[11px] border border-divider rounded-lg px-2 py-1 text-gray-700 hover:bg-gray-50 hover:border-primary"
        >
          {metric.metric_id}
        </button>
      ))}
    </div>
  )
}

function PinnedCard({ name, result, meta }) {
  return (
    <div className="bg-white rounded-2xl border border-divider p-5">
      <div className="flex items-start justify-between gap-2">
        <h3 className="text-sm font-semibold text-gray-900">{name}</h3>
        {meta.session && <StatusBadge variant="blue">this session</StatusBadge>}
      </div>
      <p className="text-[11px] text-muted mb-2 font-mono">
        {result.metric_id} by {result.group_by}
      </p>
      <MetricChart result={result} height={170} />
      <p className="text-[11px] text-muted mt-2 leading-relaxed border-t border-divider pt-2">
        {meta.session ? (
          <>
            Previewed here only. <span className="font-mono">make reporting</span> writes the
            definition to <span className="font-mono">data/pins.json</span>; this page renders a
            completed run and has no server to write back to.
          </>
        ) : (
          <>
            Pinned by {meta.pinned_by} on {meta.pinned_at}, from “{meta.source_question}”.
            Stored as an id and its parameters — never a number.
          </>
        )}
      </p>
    </div>
  )
}

export default function Ask({ data }) {
  const reporting = data.reporting || { questions: [], pins: [], registry: [], results: {} }
  const [turns, setTurns] = useState([])
  const [draft, setDraft] = useState('')
  const [sessionPins, setSessionPins] = useState([])
  const nextId = useRef(0)

  const asked = useMemo(() => {
    const index = {}
    for (const entry of reporting.questions) index[normalise(entry.question)] = entry
    return index
  }, [reporting.questions])

  const resultFor = (metricId, grouping) =>
    reporting.results[`${metricId}|${grouping}`] || null

  const defaultGrouping = (metricId) =>
    (reporting.registry.find((m) => m.metric_id === metricId) || {}).groupings?.[0]

  const push = (turn) => {
    const id = nextId.current++
    setTurns((prev) => [...prev, { id, ...turn }])
    return id
  }

  const submit = (text) => {
    const question = text.trim()
    if (!question) return undefined
    setDraft('')
    push({ from: 'operator', text: question })

    const entry = asked[normalise(question)]
    if (!entry) {
      return push({
        from: 'system',
        outcome: 'unasked',
        text:
          'I have not been asked this one. This page renders a completed run and the model ' +
          'responses are read from committed fixtures, so it answers the questions in the ' +
          'log below and nothing else. Pick a metric from the registry, or run ' +
          '`make ask` with an API key to ask something new.',
        registry: reporting.registry,
      })
    }

    if (entry.outcome === 'refuse') {
      return push({ from: 'system', outcome: 'refuse', text: entry.refusal })
    }
    if (entry.outcome === 'clarify') {
      return push({
        from: 'system',
        outcome: 'clarify',
        text: entry.clarifying_question,
        registry: reporting.registry,
      })
    }
    return push({
      from: 'system',
      outcome: 'mapped',
      state: 'awaiting',
      metricId: entry.metric_id,
      text: entry.restatement,
      result: null,
      pending: entry.result || resultFor(entry.metric_id, entry.params?.group_by),
    })
  }

  const confirm = (id) =>
    setTurns((prev) =>
      prev.map((t) => (t.id === id ? { ...t, state: 'computed', result: t.pending } : t))
    )

  const decline = (id) =>
    setTurns((prev) => prev.map((t) => (t.id === id ? { ...t, state: 'declined' } : t)))

  const pick = (id, metricId) => {
    const grouping = defaultGrouping(metricId)
    const result = resultFor(metricId, grouping)
    setTurns((prev) => [
      ...prev.map((t) => (t.id === id ? { ...t, state: 'answered' } : t)),
      {
        id: nextId.current++,
        from: 'system',
        outcome: 'mapped',
        state: 'awaiting',
        metricId,
        text: `${result.title}, grouped by ${grouping}, across the whole corpus.`,
        result: null,
        pending: result,
      },
    ])
  }

  const pin = (turn) =>
    setSessionPins((prev) =>
      prev.some((p) => p.key === `${turn.result.metric_id}|${turn.result.group_by}`)
        ? prev
        : [...prev, {
            key: `${turn.result.metric_id}|${turn.result.group_by}`,
            name: turn.result.title,
            result: turn.result,
          }]
    )

  // Deep link, once, on mount. `submitted` guards against the effect re-running and
  // asking the same question twice.
  const submitted = useRef(false)
  useEffect(() => {
    const linked = linkedQuestion()
    if (linked && !submitted.current) {
      submitted.current = true
      const id = submit(linked.question)
      if (linked.confirmed && id !== undefined) confirm(id)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const isPinned = (turn) =>
    turn.result &&
    sessionPins.some((p) => p.key === `${turn.result.metric_id}|${turn.result.group_by}`)

  return (
    <div className="flex flex-col gap-5">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Ask</h1>
        <p className="text-sm text-gray-700 mt-1">
          Describe the metric you want in your own words. The model maps it onto one of{' '}
          {reporting.registry.length} registered metrics and states what it is about to
          compute; nothing runs until you accept that sentence.
        </p>
        <p className="text-xs text-muted mt-1">
          No SQL is generated anywhere. Enterprise text-to-SQL execution accuracy runs roughly
          21–39% on realistic schemas and its failures are silent — a valid query returns a
          plausible wrong number. A closed registry can only pick the wrong id out of{' '}
          {reporting.registry.length}, and you see that choice before it runs. The limit is the
          feature.
        </p>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-5 gap-6 items-start">
        {/* ---- talk to the books ------------------------------------------- */}
        <div className="xl:col-span-3 flex flex-col gap-3">
          <div className="bg-[#f2f4f7] rounded-2xl border border-divider p-5 flex flex-col gap-3 min-h-[420px]">
            {turns.length === 0 ? (
              <div className="flex-1 flex flex-col items-center justify-center text-center py-10">
                <BookOpen size={26} className="text-muted mb-3" />
                <p className="text-sm font-medium text-gray-900">Talk to the books</p>
                <p className="text-xs text-muted mt-1 max-w-md">
                  Ask about revenue, the take rate, the exception mix, the review rate or the
                  claims register. Two of the questions below are refused and one is answered
                  with a question — those are the interesting ones.
                </p>
              </div>
            ) : (
              turns.map((turn) => (
                <Turn
                  key={turn.id}
                  turn={turn}
                  onConfirm={confirm}
                  onDecline={decline}
                  onPick={pick}
                  onPin={pin}
                  pinned={isPinned(turn)}
                />
              ))
            )}
          </div>

          <form
            onSubmit={(event) => { event.preventDefault(); submit(draft) }}
            className="flex items-center gap-2 bg-white border border-divider rounded-2xl px-4 py-2.5"
          >
            <input
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              placeholder="Describe what you want to see…"
              className="flex-1 text-sm outline-none placeholder:text-muted"
            />
            <button
              type="submit"
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-gray-900 text-white text-xs font-semibold hover:bg-gray-800"
            >
              Ask <CornerDownLeft size={12} />
            </button>
          </form>

          <div>
            <p className="text-xs text-muted mb-1.5">
              Asked before — the operator’s own log, replayed through the mapping on every run:
            </p>
            <div className="flex flex-wrap gap-1.5">
              {reporting.questions.map((entry) => (
                <button
                  key={entry.question}
                  onClick={() => submit(entry.question)}
                  title={
                    entry.outcome === 'mapped'
                      ? 'maps to a registered metric'
                      : entry.outcome === 'clarify'
                        ? 'answered with one clarifying question'
                        : 'refused — nothing in the registry answers it'
                  }
                  className={`text-[11px] rounded-full px-2.5 py-1 border transition-colors hover:bg-white ${
                    entry.outcome === 'mapped'
                      ? 'border-divider text-gray-700'
                      : entry.outcome === 'clarify'
                        ? 'border-amber/60 text-amber-700 bg-amber-light/40'
                        : 'border-danger/40 text-danger bg-danger-light/30 line-through decoration-danger/40'
                  }`}
                >
                  {entry.question}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* ---- what has been kept ------------------------------------------ */}
        <div className="xl:col-span-2 flex flex-col gap-4">
          <div className="flex items-baseline justify-between">
            <h2 className="text-sm font-medium text-muted uppercase tracking-wide">
              Pinned
            </h2>
            <span className="text-xs text-muted">
              {reporting.pins.length + sessionPins.length} kept
            </span>
          </div>
          <p className="text-xs text-muted -mt-2 leading-relaxed">
            The model is present at the moment of definition and absent from every run
            afterwards. A pin stores a metric id and its parameters, so these recompute from
            the reconciled data every batch with nothing in the loop.
          </p>

          {sessionPins.map((entry) => (
            <PinnedCard key={entry.key} name={entry.name} result={entry.result}
                        meta={{ session: true }} />
          ))}
          {reporting.pins.map((pinned) => (
            <PinnedCard key={pinned.pin_id} name={pinned.name} result={pinned.result}
                        meta={pinned} />
          ))}
        </div>
      </div>
    </div>
  )
}
