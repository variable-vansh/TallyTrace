const VARIANTS = {
  success: 'bg-success-light text-success',
  amber: 'bg-amber-light text-amber-700',
  danger: 'bg-danger-light text-danger',
  blue: 'bg-blue-50 text-primary',
  muted: 'bg-gray-100 text-muted',
}

export default function StatusBadge({ variant = 'muted', children, className = '' }) {
  return (
    <span className={`inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-semibold ${VARIANTS[variant] || VARIANTS.muted} ${className}`}>
      {children}
    </span>
  )
}
