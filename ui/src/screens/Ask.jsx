import { useEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowDown, ArrowUp, BookOpen, CheckCircle2, CornerDownLeft, HelpCircle,
  MessageSquareText, Pin, PinOff, Search, Slash,
} from 'lucide-react'
import MetricChart from '../components/MetricChart'
import StatusBadge from '../components/StatusBadge'
import { useStored } from '../lib/useStored'
import { computePlan, describeScope } from '../lib/compute'

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

// The screen opens on a worked example rather than on an empty box. What is worth
// seeing here is the *procedure* — what was asked, what the system understood it to
// mean, and what it then computed — and an empty chat shows none of it. The example is
// read out of the committed question log, so it is a real exchange from this run
// rather than a mock-up, and it clears on the first thing you type.
const DEMO_QUESTION = 'How much money are we still chasing, by platform?'

const demoTurns = (reporting) => {
  const asked = reporting.questions || []
  const entry = asked.find((q) => q.question === DEMO_QUESTION && q.result)
    || asked.find((q) => q.outcome === 'mapped' && q.result)
  if (!entry) return []
  return [
    { id: -2, demo: true, from: 'operator', text: entry.question },
    {
      id: -1,
      demo: true,
      from: 'system',
      outcome: 'mapped',
      state: 'computed',
      metricId: entry.metric_id,
      text: entry.restatement,
      result: entry.result,
      pending: entry.result,
    },
  ]
}

const OUTCOME = {
  mapped: { icon: CheckCircle2, tone: 'border-success/40 bg-success-light/40', label: 'mapped' },
  clarify: { icon: HelpCircle, tone: 'border-amber/50 bg-amber-light/50', label: 'needs one answer' },
  refuse: { icon: Slash, tone: 'border-divider bg-gray-50', label: 'refused' },
  unasked: { icon: Search, tone: 'border-divider bg-gray-50', label: 'not in the fixtures' },
  asking: { icon: Search, tone: 'border-divider bg-gray-50', label: 'mapping…' },
}

function Bubble({ from, children }) {
  const mine = from === 'operator'
  return (
    <div className={`flex ${mine ? 'justify-end' : 'justify-start'}`}>
      <div
        className={`max-w-[85%] rounded-2xl px-4 py-3 ${
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

  const asking = turn.state === 'asking'
  const meta = asking ? OUTCOME.asking : OUTCOME[turn.outcome] || OUTCOME.unasked
  const Icon = meta.icon

  return (
    <Bubble from="system">
      <div className="flex items-center gap-2 mb-1.5">
        <Icon size={14} className={`text-muted ${asking ? 'animate-pulse' : ''}`} />
        <StatusBadge variant={
          asking ? 'muted'
            : turn.outcome === 'mapped' ? 'success' : turn.outcome === 'clarify' ? 'amber' : 'muted'
        }>
          {meta.label}
        </StatusBadge>
        {turn.metricId && (
          <span className="font-mono text-[11px] text-muted">{turn.metricId}</span>
        )}
        {/* Said out loud, because it is the one answer on this screen that did not come
            out of the committed fixtures. */}
        {turn.live && (
          <span className="font-mono text-[11px] text-muted">· mapped live by {turn.live}</span>
        )}
      </div>

      <p className="text-sm text-gray-800 leading-relaxed">{turn.text}</p>

      {/* The plan in words, under the restatement, so what is about to run is legible
          before it runs rather than only describable afterwards. */}
      {turn.computedFrom && (
        <p className="font-mono text-[11px] text-muted mt-1.5">{turn.computedFrom}</p>
      )}

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
          <span className="text-[11px] text-muted">nothing computed yet</span>
        </div>
      )}

      {turn.state === 'declined' && (
        <p className="text-xs text-muted mt-2">Nothing was computed. Rephrase, or pick a metric.</p>
      )}

      {/* Offered on `unasked` as well as `clarify`. The refusal used to end in "pick a
          metric from the registry" without showing the registry, which left a new
          question with nowhere to go — the single most common way to conclude this
          screen does not answer anything. */}
      {turn.registry && turn.state !== 'answered' && (
        <div className="mt-3 border-t border-divider pt-3">
          <p className="text-xs text-muted mb-2">
            {turn.outcome === 'clarify'
              ? 'Name the metric you meant — picking from the registry needs no second model call.'
              : 'Everything this run can compute. Picking one answers it now, offline.'}
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
            {/* Two different provenances, said differently. A registered metric is a
                figure this run published; a computed one is arithmetic over the same
                aggregates, run here. Neither had a model anywhere past the mapping. */}
            <span className="text-[11px] text-muted">
              {turn.result.computed
                ? 'Computed here from the reconciled cube — not a registered metric, and no model past the plan above.'
                : 'Computed by the registry — no model past the mapping above.'}
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

function PinnedCard({ name, result, meta, onUnpin, onMove, first, last }) {
  return (
    <div className="bg-white rounded-2xl border border-divider p-5">
      <div className="flex items-start justify-between gap-2">
        <h3 className="text-sm font-semibold text-gray-900">{name}</h3>
        <div className="flex items-center gap-0.5 flex-shrink-0">
          {meta.session && <StatusBadge variant="blue">this session</StatusBadge>}
          {/* Buttons rather than drag: a board of five cards is reordered in one
              click per step, and it works from a keyboard. */}
          <button onClick={() => onMove(-1)} disabled={first} aria-label="Move up"
                  className="p-1 rounded text-muted hover:text-gray-900 hover:bg-card-bg disabled:opacity-30 disabled:hover:bg-transparent">
            <ArrowUp size={13} />
          </button>
          <button onClick={() => onMove(1)} disabled={last} aria-label="Move down"
                  className="p-1 rounded text-muted hover:text-gray-900 hover:bg-card-bg disabled:opacity-30 disabled:hover:bg-transparent">
            <ArrowDown size={13} />
          </button>
          <button onClick={onUnpin} aria-label="Unpin"
                  className="p-1 rounded text-muted hover:text-danger hover:bg-card-bg">
            <PinOff size={13} />
          </button>
        </div>
      </div>
      <p className="text-[11px] text-muted mb-2 font-mono">
        {result.metric_id} by {result.group_by}
      </p>
      <MetricChart result={result} height={170} />
      <p className="text-[11px] text-muted mt-2 border-t border-divider pt-2">
        {meta.session
          ? 'Previewed here only — make reporting writes the definition to data/pins.json.'
          : `Pinned by ${meta.pinned_by} on ${meta.pinned_at}.`}
      </p>
    </div>
  )
}

export default function Ask({ data }) {
  const reporting = data.reporting || { questions: [], pins: [], registry: [], results: {} }
  const [turns, setTurns] = useState(() =>
    (linkedQuestion() ? [] : demoTurns(data.reporting || {})))
  const [draft, setDraft] = useState('')
  const [sessionPins, setSessionPins] = useState([])
  const [panel, setPanel] = useState('ask')
  // The board's arrangement is a per-viewer preference, so it lives in the browser.
  // `data/pins.json` is the committed definition of what a pin *is* — an id and its
  // parameters — and a rebuild must not be able to reshuffle somebody's board, nor a
  // reshuffle rewrite the run. Removing a card here hides it; `make reporting` is
  // still what changes the pins themselves.
  const [order, setOrder] = useStored('tallytrace.pinOrder', [])
  const [hidden, setHidden] = useStored('tallytrace.pinsHidden', [])
  const nextId = useRef(0)

  const asked = useMemo(() => {
    const index = {}
    for (const entry of reporting.questions) index[normalise(entry.question)] = entry
    return index
  }, [reporting.questions])

  const keyOf = (result) =>
    result.computed
      ? `computed|${JSON.stringify(result.plan)}`
      : `${result.metric_id}|${result.group_by}`

  const resultFor = (metricId, grouping) =>
    reporting.results[`${metricId}|${grouping}`] || null

  const defaultGrouping = (metricId) =>
    (reporting.registry.find((m) => m.metric_id === metricId) || {}).groupings?.[0]

  const push = (turn) => {
    const id = nextId.current++
    setTurns((prev) => [...prev, { id, ...turn }])
    return id
  }

  const amend = (id, patch) =>
    setTurns((prev) => prev.map((t) => (t.id === id ? { ...t, ...patch } : t)))

  // The offline ending, used whenever there is no live mapper to reach: a deployment
  // with no key, a static build, or a call that failed. It is the honest default —
  // this page replays a completed run — and the registry picker gives the question
  // somewhere to go.
  const offline = (id, lead) =>
    amend(id, {
      state: 'idle',
      text: `${lead} This page replays a completed run, so it answers what has already ` +
        'been asked. Pick a metric below to compute one now, offline.',
      registry: reporting.registry,
    })

  // Ask the deployed mapper. It returns an id and a restatement — never a number.
  // The lookup afterwards is this app reading a result the registry already computed,
  // which is why the confirm step still means what it says.
  const askLive = async (question, id) => {
    let reply
    try {
      const response = await fetch('/api/ask', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ question }),
      })
      if (response.status === 501 || response.status === 404) {
        return offline(id, 'Not in this run’s fixtures, and this build has no live mapper.')
      }
      reply = await response.json()
      if (!response.ok) {
        return offline(id, `Not in this run’s fixtures. ${reply.error || 'The mapper failed.'}`)
      }
    } catch {
      return offline(id, 'Not in this run’s fixtures, and the mapper could not be reached.')
    }

    if (reply.outcome === 'refuse') {
      return amend(id, { outcome: 'refuse', state: 'idle', text: reply.refusal, registry: null })
    }
    if (reply.outcome === 'clarify') {
      return amend(id, {
        outcome: 'clarify',
        state: 'idle',
        text: reply.clarifying_question,
        registry: reporting.registry,
      })
    }

    // No registered metric fits, but the question is arithmetic over the reconciled
    // books. The model chose what to compute; this computes it, here, from the same
    // cube the registry was built from.
    if (reply.outcome === 'computed') {
      const computed = computePlan(data.facts, reply.plan)
      if (computed.error) {
        return offline(id, `That plan could not be run: ${computed.error}.`)
      }
      return amend(id, {
        outcome: 'mapped',
        state: 'awaiting',
        metricId: null,
        text: reply.restatement,
        live: reply.model,
        computedFrom: `${computed.title.toLowerCase()} · ${describeScope(reply.plan)}`,
        result: null,
        pending: computed,
        registry: null,
      })
    }

    const result = resultFor(reply.metric_id, reply.group_by)
    if (!result) {
      // The model named a real metric and a real grouping, and this build does not
      // hold that pair. Say so rather than showing the nearest thing that does exist.
      return offline(
        id,
        `Mapped to ${reply.metric_id} by ${reply.group_by}, which this build has not precomputed.`
      )
    }
    amend(id, {
      outcome: 'mapped',
      state: 'awaiting',
      metricId: reply.metric_id,
      text: reply.restatement,
      live: reply.model,
      result: null,
      pending: result,
      registry: null,
    })
  }

  const submit = (text) => {
    const question = text.trim()
    if (!question) return undefined
    setDraft('')
    // The example is a demonstration, not history. The first real question replaces it.
    setTurns((prev) => prev.filter((turn) => !turn.demo))
    push({ from: 'operator', text: question })

    const entry = asked[normalise(question)]
    if (!entry) {
      // Fixtures first, always: they are free, deterministic, and they are what every
      // scored number rests on. Only a question nobody has asked reaches the model,
      // and only where a deployment has been given a key.
      const id = push({
        from: 'system',
        outcome: 'unasked',
        state: 'asking',
        text: 'Not in this run\u2019s fixtures. Asking the model to map it onto the registry\u2026',
      })
      askLive(question, id)
      return id
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
      prev.some((p) => p.key === keyOf(turn.result))
        ? prev
        : [...prev, { key: keyOf(turn.result), name: turn.result.title, result: turn.result }]
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

  // True only while nothing but the seeded example is on screen.
  const showingDemo = turns.length > 0 && turns.every((turn) => turn.demo)

  const isPinned = (turn) =>
    turn.result && sessionPins.some((p) => p.key === keyOf(turn.result))

  // One list of cards, in the viewer's order, session pins first until they are moved.
  const cards = useMemo(() => {
    const all = [
      ...sessionPins.map((entry) => ({
        id: entry.key, name: entry.name, result: entry.result, meta: { session: true },
      })),
      ...reporting.pins.map((pinned) => ({
        id: pinned.pin_id, name: pinned.name, result: pinned.result, meta: pinned,
      })),
    ].filter((card) => !hidden.includes(card.id))
    const rank = (card) => {
      const at = order.indexOf(card.id)
      return at === -1 ? Number.MAX_SAFE_INTEGER : at
    }
    return [...all].sort((a, b) => rank(a) - rank(b))
  }, [sessionPins, reporting.pins, order, hidden])

  const move = (id, delta) => {
    const ids = cards.map((c) => c.id)
    const at = ids.indexOf(id)
    const to = at + delta
    if (at < 0 || to < 0 || to >= ids.length) return
    ids.splice(to, 0, ids.splice(at, 1)[0])
    setOrder(ids)
  }

  const unpin = (id) => setHidden([...hidden, id])

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <h1 className="text-2xl font-bold text-gray-900">Ask</h1>
        <div className="flex items-center gap-1 bg-white border border-divider rounded-lg p-1">
          {[
            { id: 'ask', label: 'Ask', icon: MessageSquareText, count: null },
            { id: 'pinned', label: 'Pinned', icon: Pin, count: cards.length },
          ].map(({ id, label, icon: Icon, count }) => (
            <button
              key={id}
              onClick={() => setPanel(id)}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-semibold transition-colors ${
                panel === id ? 'bg-gray-900 text-white' : 'text-gray-600 hover:bg-gray-50'
              }`}
            >
              <Icon size={13} /> {label}
              {count !== null && (
                <span className={panel === id ? 'text-white/60' : 'text-muted'}>{count}</span>
              )}
            </button>
          ))}
        </div>
      </div>

      <div className={panel === 'ask' ? '' : 'hidden'}>
        <p className="text-sm text-gray-600">
          Ask anything about the books. The answer is pulled from the reconciled data, and
          you see what it is about to compute before it runs.
        </p>
      </div>

      {/* Two panels, switched rather than side by side. The chat needs the width to be
          readable and the board needs it to be legible, and neither got it when they
          shared a row. */}
      <div className={panel === 'ask' ? 'flex flex-col gap-3' : 'hidden'}>
        <div className="flex flex-col gap-3">
          <div className="bg-[#f2f4f7] rounded-2xl border border-divider p-5 flex flex-col gap-3 min-h-[420px]">
            {showingDemo && (
              <div className="flex items-center justify-between gap-2">
                <span className="text-[11px] text-muted">
                  An example, from a question already put to this run.
                </span>
                <button
                  onClick={() => setTurns([])}
                  className="text-[11px] font-medium text-primary hover:text-primary-hover"
                >
                  Clear
                </button>
              </div>
            )}
            {turns.length === 0 ? (
              <div className="flex-1 flex flex-col items-center justify-center text-center py-10">
                <BookOpen size={26} className="text-muted mb-3" />
                <p className="text-sm font-medium text-gray-900">Talk to the books</p>
                <p className="text-xs text-muted mt-1 max-w-sm">
                  Revenue, take rate, the exception mix, the review rate, the claims register.
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

        </div>
      </div>

      {/* ---- what has been kept ------------------------------------------ */}
      <div className={panel === 'pinned' ? 'flex flex-col gap-4' : 'hidden'}>
        <div className="flex items-baseline justify-between gap-3 flex-wrap">
          <p className="text-xs text-muted max-w-2xl">
            A pin stores a metric id and its parameters, never a number — so these recompute
            from the reconciled data every batch, with no model in the loop.
          </p>
          {hidden.length > 0 && (
            <button
              onClick={() => setHidden([])}
              className="text-xs font-medium text-primary hover:text-primary-hover"
            >
              Restore {hidden.length} unpinned
            </button>
          )}
        </div>

        {cards.length === 0 ? (
          <div className="bg-white rounded-2xl border border-divider p-12 text-center">
            <Pin size={24} className="mx-auto mb-3 text-muted/40" />
            <p className="text-sm text-gray-900 font-medium">Nothing pinned</p>
            <p className="text-xs text-muted mt-1">
              Ask something, accept the restatement, then pin the result.
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 items-start">
            {cards.map((card, i) => (
              <PinnedCard
                key={card.id}
                name={card.name}
                result={card.result}
                meta={card.meta}
                first={i === 0}
                last={i === cards.length - 1}
                onMove={(delta) => move(card.id, delta)}
                onUnpin={() => unpin(card.id)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
