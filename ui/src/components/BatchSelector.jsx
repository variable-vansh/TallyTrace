export default function BatchSelector({ weeks, selectedWeek, onSelectWeek }) {
  return (
    <div className="flex items-center gap-1 bg-white/10 rounded-full p-1">
      {weeks.map((w, idx) => {
        const isSelected = idx === selectedWeek
        return (
          <button
            key={idx}
            onClick={() => onSelectWeek(idx)}
            className={`px-4 py-1.5 rounded-full text-xs font-semibold transition-all focus:outline-none focus-visible:ring-2 focus-visible:ring-primary
              ${isSelected
                ? 'bg-success text-white shadow-sm'
                : 'text-white/60 hover:text-white hover:bg-white/10'
              }`}
          >
            Week {w.week}
          </button>
        )
      })}
    </div>
  )
}
