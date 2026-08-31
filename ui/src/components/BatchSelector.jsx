export default function BatchSelector({ weeks, selectedWeek, onSelectWeek }) {
  if (!weeks.length) return null

  return (
    <div className="flex items-center gap-1 bg-white/10 rounded-full p-1">
      {weeks.map((w, idx) => {
        const isSelected = idx === selectedWeek
        return (
          <button
            key={w.week}
            onClick={() => onSelectWeek(idx)}
            title={w.dateRange ? `${w.dateRange.from} → ${w.dateRange.to}` : undefined}
            className={`px-3.5 py-1.5 rounded-full text-xs font-semibold transition-all focus:outline-none focus-visible:ring-2 focus-visible:ring-primary
              ${isSelected
                ? 'bg-success text-white shadow-sm'
                : 'text-white/60 hover:text-white hover:bg-white/10'
              }`}
          >
            {w.week}
          </button>
        )
      })}
    </div>
  )
}
