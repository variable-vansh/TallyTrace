import { LayoutDashboard, ListChecks, ArrowLeftRight, Brain, FileBarChart } from 'lucide-react'

const NAV_ITEMS = [
  { id: 'dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { id: 'review', label: 'Review Queue', icon: ListChecks },
  { id: 'transactions', label: 'Transactions', icon: ArrowLeftRight },
  { id: 'patterns', label: 'Rules', icon: Brain },
  { id: 'reports', label: 'Reports', icon: FileBarChart },
]

export default function Sidebar({ activeScreen, onNavigate }) {
  return (
    <aside className="w-60 bg-nav flex-shrink-0 flex flex-col pt-0 relative z-20">
      <nav className="flex-1 space-y-1 pl-4 pr-0">
        {NAV_ITEMS.map(({ id, label, icon: Icon }) => {
          const isActive = activeScreen === id
          const isFirst = id === 'dashboard'
          
          return (
            <div key={id} className="relative">
              <button
                onClick={() => onNavigate(id)}
                className={`w-full flex items-center gap-3 px-4 py-3.5 text-sm font-medium focus:outline-none relative z-10 group
                  ${isActive ? 'text-gray-900' : 'text-white/50'}`}
              >
                {/* Hover background (inactive only) */}
                {!isActive && (
                  <div className="absolute inset-0 rounded-[24px] transition-colors duration-200 group-hover:bg-white/5 z-0 mr-4" />
                )}

                {/* Active white background sliding from right to left */}
                <div 
                  className={`absolute inset-y-0 right-0 bg-[#f9fafb] rounded-l-[24px] transition-all duration-300 ease-out z-0
                    ${isActive ? 'w-full' : 'w-0'}`}
                />

                {/* Content */}
                <div className="relative z-10 flex items-center gap-3 w-full">
                  <Icon size={18} strokeWidth={isActive ? 2 : 1.5} className="transition-colors duration-300" />
                  <span className="transition-colors duration-300">{label}</span>
                </div>
              </button>

              {/* Notch curves and connection blocks */}
              {/* Rendered always to allow fading, but opacity transitions so they fade in smoothly as the white background slides in */}
              <div className={`absolute inset-y-0 right-0 z-0 transition-opacity duration-200 pointer-events-none ${isActive ? 'opacity-100 delay-100' : 'opacity-0'}`}>
                {/* Extension block: pushes 24px right to perfectly cover the 24px rounded corners of the main area */}
                <div className="absolute top-0 -right-6 w-6 h-full bg-[#f9fafb]" />
                
                {/* Top Notch - omitted for the first item since its top edge aligns flush with the main container */}
                {!isFirst && (
                  <div className="absolute right-0 -top-6 h-6 w-6 bg-[#f9fafb]">
                    <div className="h-full w-full rounded-br-[24px] bg-nav" />
                  </div>
                )}

                {/* Bottom Notch */}
                <div className="absolute right-0 -bottom-6 h-6 w-6 bg-[#f9fafb]">
                  <div className="h-full w-full rounded-tr-[24px] bg-nav" />
                </div>
              </div>
            </div>
          )
        })}
      </nav>

      <div className="px-6 py-6 mt-auto border-t border-white/10">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-full bg-primary flex items-center justify-center text-white text-xs font-semibold">
            TT
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
