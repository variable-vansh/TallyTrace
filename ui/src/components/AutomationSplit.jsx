import { useMemo, useState } from 'react'
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { Sparkles } from 'lucide-react'
import { pct } from '../lib/format'

// How settlement rows actually got closed, split three ways.
//
// The three slices partition the batch exactly — matched + AI-resolved + manual equals
// every settlement row, with no residual and nothing counted twice — so the bar is a
// real part-to-whole rather than three numbers that happen to be drawn end to end.
// `tests/test_ui_data.py` asserts the partition against data/score.json.
//
// This replaced a three-series line chart of review rates. That chart was about the
// tool's own rate of improvement; this one answers the question a person opens a
// reconciliation to ask — who closed the books this week, the matcher, the model, or me.

const SLICES = [
  {
    key: 'matched',
    label: 'Auto-matched',
    // Deterministic keys and tolerances. No model, no rule, no human.
    fill: '#3D4FE0',
    hint: 'Exact keys and explicit tolerance bands',
  },
  {
    key: 'autoResolved',
    label: 'AI-resolved',
    // Same green as "Applied" on the queue and "Auto-resolved" on the money chart.
    fill: '#1FAA59',
    hint: 'Flagged, then closed by a rule the model induced from an operator’s note',
  },
  {
    key: 'human',
    label: 'Manual review',
    // Amber-700 rather than the amber-500 used on badges: at amber-500 this slice
    // and the green sit at ΔE 6.2 under protanopia, which is inside the floor band.
    // The darker step clears it at 9.7 without changing what the colour means.
    fill: '#B45309',
    hint: 'A person decides. Guardrails escalate value on purpose',
  },
]

function split(week) {
  const s = week.stats
  return {
    week: week.week,
    total: s.totalTransactions,
    matched: s.autoMatched,
    autoResolved: s.autoResolved,
    human: Math.max(s.flaggedForReview - s.autoResolved, 0),
  }
}

const sum = (rows, key) => rows.reduce((total, row) => total + row[key], 0)

export default function AutomationSplit({ allWeeks, selectedWeek, onSelectWeek }) {
  const [scope, setScope] = useState('week')
  const rows = useMemo(() => allWeeks.map(split), [allWeeks])
  const here = rows[selectedWeek] ?? rows[rows.length - 1]

  const shown = scope === 'week' ? here : {
    week: null,
    total: sum(rows, 'total'),
    matched: sum(rows, 'matched'),
    autoResolved: sum(rows, 'autoResolved'),
    human: sum(rows, 'human'),
  }

  const share = (key) => (shown.total ? (shown[key] * 100) / shown.total : 0)
  // The exception queue is what the matcher could not settle on its own. The AI's
  // share of *that* is the honest denominator: measured against every settlement row
  // it would look small, and the rows it never sees were never its to close.
  const queue = shown.autoResolved + shown.human
  const aiShareOfQueue = queue ? (shown.autoResolved * 100) / queue : 0

  // Each week normalised to 100%, so the composition is comparable across weeks whose
  // row counts triple. Absolute counts stay in the tooltip.
  const trend = rows.map((row) => ({
    name: `W${row.week}`,
    week: row.week,
    ...Object.fromEntries(SLICES.map((s) => [s.key, (row[s.key] * 100) / row.total])),
    counts: row,
  }))

  return (
    <div className="bg-white rounded-2xl border border-divider p-6 flex flex-col">
      <div className="flex items-start justify-between gap-3 flex-wrap mb-4">
        <div>
          <h2 className="text-sm font-medium text-muted uppercase tracking-wide">
            Who closed the books
          </h2>
          <p className="text-xs text-muted mt-0.5">
            Every settlement row, split three ways
          </p>
        </div>
        <div className="flex items-center gap-1 bg-card-bg rounded-lg p-0.5">
          {[['week', `Week ${here.week}`], ['all', `All ${rows.length} weeks`]].map(([id, label]) => (
            <button
              key={id}
              onClick={() => setScope(id)}
              className={`px-2.5 py-1 rounded-md text-[11px] font-semibold transition-colors ${
                scope === id ? 'bg-white text-gray-900 shadow-sm' : 'text-muted hover:text-gray-900'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* One bar, three segments, in the order the work moves through the system. */}
      <div className="flex h-3 rounded-full overflow-hidden gap-0.5">
        {SLICES.map((slice) => (
          <div
            key={slice.key}
            title={`${slice.label}: ${shown[slice.key]} of ${shown.total} rows`}
            style={{ width: `${share(slice.key)}%`, backgroundColor: slice.fill }}
          />
        ))}
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mt-5">
        {SLICES.map((slice) => (
          <div key={slice.key} className="flex items-start gap-2">
            <span className="mt-1.5 h-2.5 w-2.5 rounded-sm flex-shrink-0"
                  style={{ backgroundColor: slice.fill }} />
            <div className="min-w-0">
              <div className="text-2xl font-bold text-gray-900 tabular-nums leading-none">
                {pct(share(slice.key), 1)}
              </div>
              <div className="text-xs font-medium text-gray-900 mt-1">
                {slice.label}
                <span className="text-muted font-normal"> · {shown[slice.key].toLocaleString('en-IN')} rows</span>
              </div>
              <div className="text-[11px] text-muted leading-snug mt-0.5">{slice.hint}</div>
            </div>
          </div>
        ))}
      </div>

      {/* The usefulness number. A share of the whole batch understates it and a raw
          count says nothing about scale, so both are on screen, against the queue
          the AI was actually handed. */}
      <div className="mt-5 pt-4 border-t border-divider flex items-baseline gap-3 flex-wrap">
        <Sparkles size={15} className="text-success self-center" />
        <span className="text-lg font-bold text-success tabular-nums">
          {shown.autoResolved.toLocaleString('en-IN')} issues
        </span>
        <span className="text-sm text-gray-700">
          closed by the AI without a human — {pct(aiShareOfQueue, 1)} of the{' '}
          {queue.toLocaleString('en-IN')} the matcher could not settle
          {scope === 'week' ? ` in week ${here.week}` : ' across the corpus'}.
        </span>
      </div>

      <div className="mt-5 pt-4 border-t border-divider">
        <p className="text-xs text-muted mb-2">
          Week by week, each normalised to 100%. Click a week to select it.
        </p>
        <div className="h-40">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={trend} margin={{ top: 4, right: 4, left: -28, bottom: 0 }}
                      barCategoryGap="22%"
                      onClick={(state) => {
                        const week = state?.activePayload?.[0]?.payload?.week
                        if (week) onSelectWeek(week - 1)
                      }}>
              <XAxis dataKey="name" axisLine={false} tickLine={false}
                     tick={{ fill: '#6B7280', fontSize: 11 }} />
              <YAxis axisLine={false} tickLine={false} unit="%" domain={[0, 100]}
                     tick={{ fill: '#6B7280', fontSize: 11 }} />
              <Tooltip
                cursor={{ fill: 'rgba(0,0,0,0.03)' }}
                contentStyle={{ borderRadius: '8px', border: '1px solid #E7E7EA', fontSize: 12 }}
                formatter={(value, name, item) => {
                  const slice = SLICES.find((s) => s.label === name)
                  const rows = slice ? item?.payload?.counts?.[slice.key] : undefined
                  return [
                    rows === undefined ? `${value.toFixed(1)}%` : `${value.toFixed(1)}% · ${rows} rows`,
                    name,
                  ]
                }}
              />
              {SLICES.map((slice, i) => (
                <Bar key={slice.key} dataKey={slice.key} name={slice.label} stackId="a"
                     fill={slice.fill} isAnimationActive={false}
                     radius={i === SLICES.length - 1 ? [3, 3, 0, 0] : 0} />
              ))}
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  )
}
