import { dateSpan } from '../lib/format'

// The batch the whole app is looking at. Ten of them, so all ten are on screen and
// any one is a single click away — stepping through with arrows is three clicks to
// get from week seven to week ten, and the run is short enough not to need paging.
//
// The dates sit beside the pills rather than inside them: the numbers are what you
// aim at, and the span is what tells you which week that is. Every date on every
// screen below falls inside it.
export default function WeekSelector({ weeks, selectedWeek, onSelectWeek }) {
  if (!weeks.length) return null

  const here = weeks[selectedWeek]

  return (
    <div className="flex items-center gap-3">
      <span className="hidden md:block text-xs text-white/45 tabular-nums">
        {here?.dateRange ? dateSpan(here.dateRange.from, here.dateRange.to) : ''}
      </span>

      <div className="flex items-center gap-1 bg-white/10 rounded-full p-1">
        {weeks.map((week, index) => {
          const selected = index === selectedWeek
          return (
            <button
              key={week.week}
              onClick={() => onSelectWeek(index)}
              aria-current={selected ? 'true' : undefined}
              title={week.dateRange
                ? `Week ${week.week} · ${dateSpan(week.dateRange.from, week.dateRange.to)}`
                : `Week ${week.week}`}
              className={`px-3.5 py-1.5 rounded-full text-xs font-semibold transition-all
                focus:outline-none focus-visible:ring-2 focus-visible:ring-primary
                ${selected
                  ? 'bg-success text-white shadow-sm'
                  : 'text-white/60 hover:text-white hover:bg-white/10'}`}
            >
              {week.week}
            </button>
          )
        })}
      </div>
    </div>
  )
}
