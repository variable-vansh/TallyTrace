import { useEffect } from 'react'
import { createPortal } from 'react-dom'
import { X } from 'lucide-react'

// A side panel that belongs to the content area, not to the window.
//
// The first version was `fixed inset-0`, which covered the whole viewport: the scrim
// sat over the sidebar and the batch selector, so a panel opened from a table row
// took the entire application hostage to show one row's detail. This portals into
// `#panel-overlay` inside the rounded content panel, which clips it — the drawer is
// visibly a part of the page it came from, and the nav stays usable behind it.
export default function PanelDrawer({ open, title, subtitle, onClose, children, width = 'max-w-xl' }) {
  // Read during render rather than kept in state: the host is a static node in the
  // shell, so an effect would only cause a second render to go and find it.
  const host = typeof document === 'undefined' ? null : document.getElementById('panel-overlay')

  // Escape closes. A panel with only a corner X is a panel people get stuck in.
  useEffect(() => {
    if (!open) return undefined
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open || !host) return null

  return createPortal(
    <div className="absolute inset-0 flex justify-end pointer-events-auto" role="dialog" aria-modal="true">
      <div
        className="absolute inset-0 bg-gray-900/20 backdrop-blur-[1px] animate-[fadeIn_150ms_ease-out]"
        onClick={onClose}
      />
      <aside
        className={`relative w-full ${width} bg-white h-full flex flex-col border-l border-divider
          shadow-[-8px_0_24px_-12px_rgba(0,0,0,0.15)] animate-[slideIn_220ms_cubic-bezier(0.32,0.72,0,1)]`}
      >
        <header className="flex items-start justify-between gap-4 px-6 py-4 border-b border-divider flex-shrink-0">
          <div className="min-w-0">
            <div className="font-semibold text-gray-900 truncate">{title}</div>
            {subtitle && <div className="text-xs text-muted mt-0.5 truncate">{subtitle}</div>}
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
            className="text-muted hover:text-gray-900 hover:bg-card-bg rounded-lg p-1.5 flex-shrink-0 transition-colors"
          >
            <X size={17} />
          </button>
        </header>
        <div className="flex-1 overflow-y-auto">{children}</div>
      </aside>
    </div>,
    host,
  )
}
