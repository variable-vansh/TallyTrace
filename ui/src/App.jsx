import { useEffect, useState } from 'react'
import './index.css'
import WeekSelector from './components/WeekSelector'
import Sidebar from './components/Sidebar'
import Dashboard from './screens/Dashboard'
import ReviewQueue from './screens/ReviewQueue'
import Transactions from './screens/Transactions'
import Claims from './screens/Claims'
import Patterns from './screens/Patterns'
import Ask from './screens/Ask'
import Reports from './screens/Reports'

// Everything on screen comes from one scored run: `make score && make ui-data`.
// One source so the dashboard, the queue and the rules page cannot disagree with
// each other or with the number the harness printed.
const DATA_URL = `${import.meta.env.BASE_URL}tallytrace.json`

const SCREENS = {
  dashboard: Dashboard,
  review: ReviewQueue,
  transactions: Transactions,
  claims: Claims,
  patterns: Patterns,
  ask: Ask,
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

// The screen lives in the URL hash so a screen can be linked to — #claims opens the
// claims queue directly. There is no router and no server; this is one line of state
// kept in sync with `location.hash`.
const screenFromHash = () => {
  // `#claims` and `#ask?q=...` both name a screen; the query half belongs to the screen.
  const name = window.location.hash.replace('#', '').split('?')[0]
  return name in SCREENS ? name : 'dashboard'
}

// `#review?week=6` opens a screen on a named batch. The week is app-level state rather
// than a screen's own, but it belongs in the link for the same reason the screen does:
// every claim made about this run is made about a particular batch, and "the review
// queue in batch six" should be one URL rather than a click someone has to be told to
// make. Returns a 0-indexed week, or null for "wherever the app would have opened".
const weekFromHash = () => {
  const query = window.location.hash.split('?')[1]
  if (!query) return null
  const asked = Number(new URLSearchParams(query).get('week'))
  return Number.isInteger(asked) && asked > 0 ? asked - 1 : null
}

export default function App() {
  const [activeScreen, setActiveScreen] = useState(screenFromHash)
  // null means "the latest batch", resolved below once the run has loaded. The default
  // is the last week rather than the first because that is the week whose books are
  // being closed; batch 1 is the state before anything has been learned, and opening
  // there shows the tool at its least useful and least representative.
  const [selectedWeek, setSelectedWeek] = useState(weekFromHash) // 0-indexed, or null
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    const onHashChange = () => {
      setActiveScreen(screenFromHash())
      // A hash naming no week leaves the current one alone: navigating from the queue
      // to the claims list is not a request to change which week you are reading.
      const asked = weekFromHash()
      if (asked !== null) setSelectedWeek(asked)
    }
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  const navigate = (screen) => {
    // Assigning the bare screen name drops any query a deep link carried, which is what
    // should happen: navigating away ends that question.
    window.location.hash = screen
    setActiveScreen(screen)
  }

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
  // Clamped rather than trusted: `#dashboard?week=99` should open the last batch, not
  // an empty screen, and the run decides how many batches there are.
  const weekIndex = Math.min(selectedWeek ?? weeks.length - 1, weeks.length - 1)
  const currentWeek = weeks[weekIndex]
  const ActiveComponent = SCREENS[activeScreen]

  return (
    <div className="h-screen flex flex-col bg-nav text-white overflow-hidden">
      {/* Top Nav: who this is on the left, which week you are looking at on the
          right. Every date on every screen below sits inside that week, so the
          week is named once, up here, rather than repeated on each screen. */}
      <header className="h-16 bg-nav flex items-center justify-between px-6 flex-shrink-0 z-20 relative">
        <button
          onClick={() => navigate('dashboard')}
          className="flex items-baseline gap-2 rounded-lg px-1 py-1 -ml-1 hover:bg-white/5 transition-colors"
        >
          <span className="text-[17px] font-bold tracking-tight text-white">TallyTrace</span>
        </button>

        <WeekSelector
          weeks={weeks}
          selectedWeek={weekIndex}
          onSelectWeek={setSelectedWeek}
        />
      </header>

      <div className="flex flex-1 overflow-hidden relative z-10">
        <Sidebar activeScreen={activeScreen} onNavigate={navigate} />

        {/* Content Area - Dark margin on right and bottom, rounded corners */}
        {/* The content panel is its own positioning context and clips to its own
            rounded corners. Drawers portal into #panel-overlay below rather than
            covering the viewport, so a side panel opened from a table stays inside
            the panel it was opened from and the nav stays reachable behind it. */}
        <main className="flex-1 flex overflow-hidden pr-6 pb-6 pt-0">
          <div className="flex-1 relative bg-[#f9fafb] rounded-[24px] text-gray-900 shadow-2xl overflow-hidden">
            <div id="panel-overlay" className="absolute inset-0 z-40 pointer-events-none empty:hidden" />
            <div className="absolute inset-0 overflow-y-auto">
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
                  selectedWeek={weekIndex}
                  onSelectWeek={setSelectedWeek}
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
          </div>
        </main>
      </div>
    </div>
  )
}
