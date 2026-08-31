import { TrendingDown, TrendingUp, Minus } from 'lucide-react'

export default function StatCard({ label, value, delta, deltaLabel, icon: Icon }) {
  const deltaColor = delta > 0 ? 'text-danger' : delta < 0 ? 'text-success' : 'text-muted'
  const DeltaIcon = delta > 0 ? TrendingUp : delta < 0 ? TrendingDown : Minus

  return (
    <div className="bg-white rounded-2xl border border-divider p-5 flex flex-col gap-1">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-muted uppercase tracking-wide">{label}</span>
        {Icon && <Icon size={16} className="text-muted" />}
      </div>
      <div className="text-2xl font-bold text-gray-900">{value}</div>
      {delta !== undefined && (
        <div className={`flex items-center gap-1 text-xs font-medium ${deltaColor}`}>
          <DeltaIcon size={12} />
          <span>{Math.abs(delta)}{deltaLabel || ''}</span>
        </div>
      )}
    </div>
  )
}
