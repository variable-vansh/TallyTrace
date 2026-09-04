import { useMemo, useState } from 'react'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { addDays, inr, isoOf, mondayIndex, parseISO } from '../lib/format'

// The claims queue's real subject is a clock, so the screen shows one.
//
// A claim is money somebody else owes that stops being recoverable on a date: Amazon's
// SAFE-T window is 30 days from the event, a TCS discrepancy has to be raised before
// the 10th of the following month. The list beside this is sorted by what is next; the
// month is here to answer the other question, which is which *week* is bad — the thing
// a person schedules around.
//
// A day is coloured by the soonest deadline it carries, never by how many it carries:
// one expired claim is worse than four with a month left. Nothing else is drawn. No
// cell borders, no chips, no per-day totals — at this size they are texture, and the
// detail belongs in the list, one click away.

const DAY_LETTERS = ['M', 'T', 'W', 'T', 'F', 'S', 'S']
const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December']

function urgencyOf(claims, today) {
  if (!claims.length) return null
  const open = claims.filter((c) => c.status !== 'recovered')
  if (!open.length) return 'recovered'
  if (open.some((c) => c.deadline?.on && c.deadline.on < today)) return 'past'
  const soonest = Math.min(...open.map((c) => c.daysRemaining ?? 999))
  return soonest <= 7 ? 'urgent' : soonest <= 21 ? 'soon' : 'later'
}

const FILL = {
  past: 'bg-danger-light text-danger',
  urgent: 'bg-amber-light text-amber-700',
  soon: 'bg-primary/10 text-primary',
  later: 'bg-card-bg text-gray-700',
  recovered: 'bg-success-light text-success',
}

const LEGEND = [
  ['past', 'Closed'],
  ['urgent', '7 days'],
  ['soon', '3 weeks'],
  ['later', 'Later'],
  ['recovered', 'Recovered'],
]

export default function DeadlineCalendar({ claims, today, selectedDay, onSelectDay }) {
  const withDates = useMemo(() => claims.filter((c) => c.deadline?.on), [claims])

  const byDay = useMemo(() => {
    const index = new Map()
    for (const claim of withDates) {
      const list = index.get(claim.deadline.on) || []
      list.push(claim)
      index.set(claim.deadline.on, list)
    }
    return index
  }, [withDates])

  // Open on the month holding the next thing due, not on today's real-world date,
  // which has nothing to do with this corpus.
  const [cursor, setCursor] = useState(() => {
    const dates = withDates.map((c) => c.deadline.on).sort()
    const landing = dates.find((on) => on >= today) ?? dates[dates.length - 1] ?? today
    const at = parseISO(landing)
    return { year: at.getFullYear(), month: at.getMonth() }
  })

  const cells = useMemo(() => {
    const first = new Date(cursor.year, cursor.month, 1)
    const days = new Date(cursor.year, cursor.month + 1, 0).getDate()
    return [
      ...Array.from({ length: mondayIndex(first) }, () => null),
      ...Array.from({ length: days }, (_, i) => addDays(first, i)),
    ]
  }, [cursor])

  const step = (delta) => setCursor((c) => {
    const next = new Date(c.year, c.month + delta, 1)
    return { year: next.getFullYear(), month: next.getMonth() }
  })

  const thisMonth = cells
    .filter(Boolean)
    .flatMap((date) => byDay.get(isoOf(date)) || [])
  const unclocked = claims.length - withDates.length

  return (
    <div className="bg-white rounded-2xl border border-divider p-5">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-sm font-semibold text-gray-900">
          {MONTHS[cursor.month]} {cursor.year}
        </h2>
        <div className="flex items-center gap-0.5">
          <button onClick={() => step(-1)} aria-label="Previous month"
                  className="p-1.5 rounded-lg text-muted hover:bg-card-bg hover:text-gray-900 transition-colors">
            <ChevronLeft size={15} />
          </button>
          <button onClick={() => step(1)} aria-label="Next month"
                  className="p-1.5 rounded-lg text-muted hover:bg-card-bg hover:text-gray-900 transition-colors">
            <ChevronRight size={15} />
          </button>
        </div>
      </div>

      <div className="grid grid-cols-7 gap-1 text-center">
        {DAY_LETTERS.map((letter, i) => (
          <div key={i} className="text-[10px] font-medium text-muted pb-1.5">{letter}</div>
        ))}

        {cells.map((date, i) => {
          if (!date) return <div key={`pad-${i}`} />
          const key = isoOf(date)
          const here = byDay.get(key) || []
          const tone = urgencyOf(here, today)
          const isToday = key === today
          const isSelected = key === selectedDay

          return (
            <button
              key={key}
              disabled={!here.length}
              onClick={() => onSelectDay(isSelected ? null : key)}
              title={here.length
                ? `${here.length} claim${here.length === 1 ? '' : 's'} · ${inr(here.reduce((s, c) => s + Number(c.amount ?? 0), 0), { whole: true })}`
                : undefined}
              className={`aspect-square rounded-lg flex items-center justify-center text-xs tabular-nums
                transition-colors
                ${tone ? `${FILL[tone]} font-semibold hover:brightness-[0.97] cursor-pointer` : 'text-gray-400'}
                ${isSelected ? 'ring-2 ring-gray-900 ring-offset-1' : ''}
                ${isToday && !isSelected ? 'ring-1 ring-gray-900/30' : ''}`}
            >
              {date.getDate()}
            </button>
          )
        })}
      </div>

      <p className="mt-4 pt-3 border-t border-divider text-[11px] text-muted">
        {thisMonth.length === 0
          ? 'Nothing falls due this month.'
          : `${thisMonth.length} due in ${MONTHS[cursor.month]} · ${inr(thisMonth.reduce((s, c) => s + Number(c.amount ?? 0), 0), { whole: true })}`}
        {unclocked > 0 && ` · ${unclocked} with no filing window, never expire`}
      </p>

      <div className="mt-2.5 flex flex-wrap gap-x-2.5 gap-y-1">
        {LEGEND.map(([tone, label]) => (
          <span key={tone} className="flex items-center gap-1 text-[10px] text-muted">
            <span className={`h-2 w-2 rounded-[3px] ${FILL[tone]}`} />
            {label}
          </span>
        ))}
      </div>
    </div>
  )
}
