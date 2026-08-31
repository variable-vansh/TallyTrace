import { useEffect, useState } from 'react'
import './index.css'
import BatchSelector from './components/BatchSelector'
import Sidebar from './components/Sidebar'
import Dashboard from './screens/Dashboard'
import ReviewQueue from './screens/ReviewQueue'
import Transactions from './screens/Transactions'
import Patterns from './screens/Patterns'
import Reports from './screens/Reports'

// Everything on screen comes from one scored run: `make score && make ui-data`.
// One source so the dashboard, the queue and the rules page cannot disagree with
// each other or with the number the harness printed.
const DATA_URL = `${import.meta.env.BASE_URL}tallytrace.json`

const SCREENS = {
  dashboard: Dashboard,
  review: ReviewQueue,
  transactions: Transactions,
  patterns: Patterns,
  reports: Reports,
}

function Placeholder({ title, body }) {
  return (
    <div className="flex flex-col items-center justify-center h-[60vh] text-center">
      <p className="text-gray-900 font-medium">{title}</p>
      <p className="text-gray-500 text-sm mt-1 max-w-md">{body}</p>
    </div>
  )
}

export default function App() {
  const [activeScreen, setActiveScreen] = useState('dashboard')
  const [selectedWeek, setSelectedWeek] = useState(0) // 0-indexed
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    fetch(DATA_URL)
      .then((response) => {
        if (!response.ok) throw new Error(`${response.status} ${response.statusText}`)
        return response.json()
      })
      .then((payload) => { if (!cancelled) setData(payload) })
      .catch((err) => { if (!cancelled) setError(err.message) })
    return () => { cancelled = true }
  }, [])

  const weeks = data?.weeks || []
  const currentWeek = weeks[selectedWeek]
  const ActiveComponent = SCREENS[activeScreen]

  return (
    <div className="h-screen flex flex-col bg-nav text-white overflow-hidden">
      {/* Top Nav */}
      <header className="h-16 bg-nav flex items-center justify-between px-6 flex-shrink-0 z-20 relative">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2">
            <svg width="28" height="28" viewBox="0 0 28 28" fill="none">
              <rect width="28" height="28" rx="6" fill="#3D4FE0"/>
              <path d="M7 14h14M14 7v14M9 9l10 10M19 9L9 19" stroke="white" strokeWidth="1.5" strokeLinecap="round" opacity="0.3"/>
              <path d="M8 10l4 8 4-5 4 5" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
            <span className="text-white font-semibold text-lg tracking-tight">TallyTrace</span>
          </div>
          <span className="text-white/50 text-xs ml-1 hidden sm:inline">Reconciliation Agent</span>
        </div>

        <BatchSelector
          weeks={weeks}
          selectedWeek={selectedWeek}
          onSelectWeek={setSelectedWeek}
        />
      </header>

      <div className="flex flex-1 overflow-hidden relative z-10">
        <Sidebar activeScreen={activeScreen} onNavigate={setActiveScreen} />

        {/* Content Area - Dark margin on right and bottom, rounded corners */}
        <main className="flex-1 flex overflow-hidden pr-6 pb-6 pt-0">
          <div className="flex-1 overflow-y-auto bg-[#f9fafb] rounded-[24px] text-gray-900 relative shadow-2xl">
            <div className="p-8 max-w-[1400px] min-h-full">
              {error ? (
                <Placeholder
                  title="Could not load the run"
                  body={`${error}. Run \`make score && make ui-data\` to produce ui/public/tallytrace.json.`}
                />
              ) : !data ? (
                <Placeholder title="Loading the scored run…" body="Reading tallytrace.json." />
              ) : currentWeek ? (
                <ActiveComponent
                  weekData={currentWeek}
                  allWeeks={weeks}
                  selectedWeek={selectedWeek}
                  data={data}
                />
              ) : (
                <Placeholder
                  title="No batches in this run"
                  body="Run `make demo` to generate the corpus and score it."
                />
              )}
            </div>
          </div>
        </main>
      </div>
    </div>
  )
}
