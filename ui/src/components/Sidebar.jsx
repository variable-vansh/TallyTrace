import {
  LayoutDashboard, ListChecks, ArrowLeftRight, Brain, Gavel, MessageSquareText, SlidersHorizontal,
} from 'lucide-react'

// What the merchant works, in the order they work it.
const NAV_ITEMS = [
  { id: 'dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { id: 'review', label: 'Review Queue', icon: ListChecks },
  { id: 'transactions', label: 'Transactions', icon: ArrowLeftRight },
  { id: 'claims', label: 'Claims', icon: Gavel },
  { id: 'patterns', label: 'Rules', icon: Brain },
  { id: 'ask', label: 'Ask', icon: MessageSquareText },
]

// How the tool itself is doing, and how it is configured. Not part of the daily
// job, so it sits at the bottom rather than in the run of work above.
const FOOTER_ITEMS = [
  { id: 'reports', label: 'Report & Settings', icon: SlidersHorizontal },
]

const PANEL = '#f9fafb'
// Slow out, no overshoot. The white is meant to read as the content panel reaching
// into the nav, and a panel that springs reads as a widget instead.
const GLIDE = 'transform 420ms cubic-bezier(0.32, 0.72, 0, 1)'

// The white pill, one per item, sliding out of the content area and back into it.
//
// It travels horizontally: selecting an item pulls the white leftwards out of the
// panel, deselecting pushes it back out to the right, and the two happen at once so
// the eye reads one movement rather than a pill jumping down a list.
//
// Two layers, and the split is the whole trick. **Only white moves.** The pane is a
// plain rounded rectangle with nothing dark in it, so wherever it is mid-flight it
// cannot put a dark edge somewhere a dark edge does not belong. The two notches — the
// white squares with a nav-coloured corner bitten out of them, which are what turn the
// joint into a curve instead of a right angle — are fixed at the sidebar's edge, which
// is the only place their geometry is true. A notch that travels with the pane arrives
// 20px late and paints black across the white; they fade instead.
//
// The clip box runs 24px past the sidebar's right edge, far enough to hold the strip
// that covers the panel's own edge, and clips left and right only: `inset()` with
// negative top and bottom leaves the notches free to reach into the rows above and
// below, while everything the pane overshoots to the right is cut.
function Pill({ active }) {
  const notch = {
    backgroundColor: PANEL,
    opacity: active ? 1 : 0,
    transition: 'opacity 200ms linear',
  }

  return (
    <span
      aria-hidden="true"
      className="absolute inset-y-0 left-0 -right-6 pointer-events-none"
      style={{ clipPath: 'inset(-32px 0px -32px 0px)' }}
    >
      <span
        className="absolute inset-0 rounded-l-[24px] will-change-transform"
        style={{
          backgroundColor: PANEL,
          transform: active ? 'translate3d(0, 0, 0)' : 'translate3d(100%, 0, 0)',
          transition: GLIDE,
        }}
      />
      <span className="absolute right-6 -top-6 h-6 w-6" style={notch}>
        <span className="block h-full w-full rounded-br-[24px] bg-nav" />
      </span>
      <span className="absolute right-6 -bottom-6 h-6 w-6" style={notch}>
        <span className="block h-full w-full rounded-tr-[24px] bg-nav" />
      </span>
    </span>
  )
}

function NavButton({ item, active, onNavigate }) {
  const { id, label, icon: Icon } = item
  return (
    <button
      onClick={() => onNavigate(id)}
      className={`relative w-full flex items-center gap-3 px-4 py-3.5 text-sm font-medium
        focus:outline-none rounded-l-[24px] transition-colors duration-200
        ${active ? 'text-gray-900' : 'text-white/50 hover:text-white/80 hover:bg-white/5'}`}
    >
      <Pill active={active} />
      <Icon size={18} strokeWidth={active ? 2 : 1.5} className="relative z-10" />
      <span className="relative z-10">{label}</span>
    </button>
  )
}

export default function Sidebar({ activeScreen, onNavigate }) {
  return (
    <aside className="w-60 bg-nav flex-shrink-0 flex flex-col relative z-20">
      <div className="relative flex-1 flex flex-col pl-4">
        <nav className="space-y-1 pt-0">
          {NAV_ITEMS.map((item) => (
            <NavButton
              key={item.id}
              item={item}
              active={activeScreen === item.id}
              onNavigate={onNavigate}
            />
          ))}
        </nav>

        <div className="mt-auto space-y-1 pb-2">
          {FOOTER_ITEMS.map((item) => (
            <NavButton
              key={item.id}
              item={item}
              active={activeScreen === item.id}
              onNavigate={onNavigate}
            />
          ))}
        </div>
      </div>

      <div className="px-6 py-5 border-t border-white/10">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-full bg-primary flex items-center justify-center text-white text-xs font-semibold">
            DS
          </div>
          <div className="text-xs">
            <div className="text-white/90 font-medium text-sm">Demo Store</div>
            <div className="text-white/40">test_mode</div>
          </div>
        </div>
      </div>
    </aside>
  )
}
