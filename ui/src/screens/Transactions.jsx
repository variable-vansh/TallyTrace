import { useMemo, useState } from 'react'
import { ChevronDown, Search, X } from 'lucide-react'
import StatusBadge from '../components/StatusBadge'
import DecisionPath from '../components/DecisionPath'
import { inr, humanise } from '../lib/format'

const STATUS_VARIANT = {
  matched: 'success',
  variance: 'amber',
  unmatched: 'danger',
  quarantined: 'muted',
}

const VIEWS = [
  { id: 'settlement', label: 'Settlement report' },
  { id: 'bank', label: 'Bank statement' },
  { id: 'ledger', label: 'Internal ledger' },
]
const CHANNELS = ['all', 'amazon', 'flipkart', 'myntra', 'offline', 'website']
const STATUSES = ['all', 'matched', 'variance', 'unmatched', 'quarantined']

function FilterPill({ label, value, options, onChange }) {
  return (
    <div className="relative">
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="appearance-none bg-white border border-divider rounded-lg pl-3 pr-8 py-2 text-xs font-medium text-gray-700 focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary cursor-pointer hover:border-gray-300 transition-colors"
      >
        {options.map((opt) => (
          <option key={opt} value={opt}>
            {opt === 'all' ? `All ${label}` : opt.charAt(0).toUpperCase() + opt.slice(1)}
          </option>
        ))}
      </select>
      <ChevronDown size={12} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted pointer-events-none" />
    </div>
  )
}

// Clicking any row opens its decision path. That is the whole point of the screen:
// every number on it can be traced back to the check that produced it.
function Drawer({ exc, onClose }) {
  if (!exc) return null
  return (
    <div className="fixed inset-0 z-40 flex justify-end" role="dialog">
      <div className="absolute inset-0 bg-black/30" onClick={onClose} />
      <aside className="relative z-50 w-full max-w-2xl bg-[#f9fafb] h-full overflow-y-auto shadow-2xl">
        <div className="sticky top-0 bg-white border-b border-divider px-6 py-4 flex items-center justify-between">
          <div>
            <div className="font-mono text-sm font-bold text-gray-900">{exc.key}</div>
            <div className="text-xs text-muted">Decision path · batch {exc.batch}</div>
          </div>
          <button onClick={onClose} className="text-muted hover:text-gray-900 p-1">
            <X size={18} />
          </button>
        </div>
        <div className="p-6">
          <DecisionPath exc={exc} />
        </div>
      </aside>
    </div>
  )
}

export default function Transactions({ weekData }) {
  const [view, setView] = useState('settlement')
  const [channelFilter, setChannelFilter] = useState('all')
  const [statusFilter, setStatusFilter] = useState('all')
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState(null)

  // Exceptions are keyed by order, UTR or row id, which is exactly how a row finds
  // the case it belongs to.
  const caseFor = useMemo(() => {
    const index = {}
    for (const exc of weekData.exceptions || []) {
      index[exc.key] = exc
      for (const rowId of exc.settlementRowIds) index[rowId] = exc
    }
    return index
  }, [weekData])

  const rows = useMemo(() => {
    const source = view === 'bank' ? weekData.bank
      : view === 'ledger' ? weekData.ledger
      : weekData.transactions
    let result = [...(source || [])]
    if (view !== 'bank' && channelFilter !== 'all') {
      result = result.filter((r) => r.channel === channelFilter)
    }
    if (statusFilter !== 'all') result = result.filter((r) => r.status === statusFilter)
    if (search) {
      const q = search.toLowerCase()
      result = result.filter((r) =>
        [r.entityId, r.orderId, r.utr, r.channel, r.reason]
          .filter(Boolean).some((v) => String(v).toLowerCase().includes(q))
      )
    }
    return result
  }, [weekData, view, channelFilter, statusFilter, search])

  const open = (key) => { const exc = caseFor[key]; if (exc) setSelected(exc) }
  const clickable = (key) => (caseFor[key] ? 'cursor-pointer hover:bg-primary/5' : '')

  const table = () => {
    if (view === 'bank') {
      return (
        <table className="w-full text-sm text-left">
          <thead className="bg-card-bg text-muted border-b border-divider font-medium">
            <tr>
              <th className="px-5 py-3">UTR</th>
              <th className="px-5 py-3 text-right">Credited</th>
              <th className="px-5 py-3 text-right">Settlement sum</th>
              <th className="px-5 py-3 text-right">Shortfall</th>
              <th className="px-5 py-3 text-right">Rows</th>
              <th className="px-5 py-3">Verdict</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-divider">
            {rows.map((r, i) => (
              <tr key={r.utr} onClick={() => open(r.utr)}
                  className={`${i % 2 ? 'bg-gray-50/50' : 'bg-white'} ${clickable(r.utr)}`}>
                <td className="px-5 py-3 font-mono text-xs text-gray-900">{r.utr}</td>
                <td className="px-5 py-3 text-right font-medium">{inr(r.amount)}</td>
                <td className="px-5 py-3 text-right text-muted">{inr(r.settlementSum)}</td>
                <td className={`px-5 py-3 text-right ${r.shortfall ? 'text-danger font-medium' : 'text-muted'}`}>
                  {inr(r.shortfall)}
                </td>
                <td className="px-5 py-3 text-right text-muted">{r.rowsInGroup ?? '—'}</td>
                <td className="px-5 py-3">
                  <StatusBadge variant={STATUS_VARIANT[r.status]}>{humanise(r.reason)}</StatusBadge>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )
    }

    if (view === 'ledger') {
      return (
        <table className="w-full text-sm text-left">
          <thead className="bg-card-bg text-muted border-b border-divider font-medium">
            <tr>
              <th className="px-5 py-3">Order ID</th>
              <th className="px-5 py-3">Channel</th>
              <th className="px-5 py-3 text-right">Expected fee</th>
              <th className="px-5 py-3 text-right">Charged fee</th>
              <th className="px-5 py-3 text-right">Expected net</th>
              <th className="px-5 py-3 text-right">Settled net</th>
              <th className="px-5 py-3">Verdict</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-divider">
            {rows.map((r, i) => (
              <tr key={r.orderId} onClick={() => open(r.orderId)}
                  className={`${i % 2 ? 'bg-gray-50/50' : 'bg-white'} ${clickable(r.orderId)}`}>
                <td className="px-5 py-3 font-mono text-xs text-gray-900">{r.orderId}</td>
                <td className="px-5 py-3"><StatusBadge variant="blue" className="capitalize">{r.channel}</StatusBadge></td>
                <td className="px-5 py-3 text-right text-muted">{inr(r.expectedFee)}</td>
                <td className="px-5 py-3 text-right font-medium">{inr(r.chargedFee)}</td>
                <td className="px-5 py-3 text-right text-muted">{inr(r.expectedNet)}</td>
                <td className="px-5 py-3 text-right font-medium">{inr(r.settledNet)}</td>
                <td className="px-5 py-3">
                  <StatusBadge variant={STATUS_VARIANT[r.status]}>{humanise(r.reason)}</StatusBadge>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )
    }

    return (
      <table className="w-full text-sm text-left">
        <thead className="bg-card-bg text-muted border-b border-divider font-medium">
          <tr>
            <th className="px-5 py-3">Entity ID</th>
            <th className="px-5 py-3">Order ID</th>
            <th className="px-5 py-3">Channel</th>
            <th className="px-5 py-3 text-right">In question</th>
            <th className="px-5 py-3">Bucket</th>
            <th className="px-5 py-3">Reason code</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-divider">
          {rows.map((r, i) => (
            <tr key={r.entityId} onClick={() => open(r.entityId)}
                className={`${i % 2 ? 'bg-gray-50/50' : 'bg-white'} ${clickable(r.entityId)}`}>
              <td className="px-5 py-3 font-mono text-xs text-gray-900">{r.entityId}</td>
              <td className="px-5 py-3 font-mono text-xs text-muted">{r.orderId || '—'}</td>
              <td className="px-5 py-3">
                {r.channel ? <StatusBadge variant="blue" className="capitalize">{r.channel}</StatusBadge> : '—'}
              </td>
              <td className={`px-5 py-3 text-right ${r.impact ? 'font-medium text-gray-900' : 'text-muted'}`}>
                {r.impact ? inr(r.impact) : '—'}
              </td>
              <td className="px-5 py-3">
                <StatusBadge variant={STATUS_VARIANT[r.status]} className="capitalize">{r.status}</StatusBadge>
              </td>
              <td className="px-5 py-3 font-mono text-[11px] text-muted">{r.reason}</td>
            </tr>
          ))}
        </tbody>
      </table>
    )
  }

  const total = (view === 'bank' ? weekData.bank : view === 'ledger' ? weekData.ledger : weekData.transactions)?.length || 0

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold text-gray-900">Transactions</h1>
          <span className="text-xs text-muted bg-gray-100 px-2 py-1 rounded-md">
            {rows.length} of {total} rows
          </span>
        </div>
        <div className="flex items-center gap-1 bg-white border border-divider rounded-lg p-1">
          {VIEWS.map((v) => (
            <button
              key={v.id}
              onClick={() => setView(v.id)}
              className={`px-3 py-1.5 rounded-md text-xs font-semibold transition-colors ${
                view === v.id ? 'bg-gray-900 text-white' : 'text-gray-600 hover:bg-gray-50'
              }`}
            >
              {v.label}
            </button>
          ))}
        </div>
      </div>

      <div className="flex items-center gap-3 flex-wrap">
        <div className="relative flex-1 max-w-xs">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search id, channel or reason code…"
            className="w-full pl-9 pr-3 py-2 text-xs border border-divider rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary bg-white"
          />
        </div>
        <div className="h-5 w-px bg-divider" />
        {view !== 'bank' && (
          <FilterPill label="channels" value={channelFilter} options={CHANNELS} onChange={setChannelFilter} />
        )}
        <FilterPill label="buckets" value={statusFilter} options={STATUSES} onChange={setStatusFilter} />
        {(channelFilter !== 'all' || statusFilter !== 'all' || search) && (
          <button
            onClick={() => { setChannelFilter('all'); setStatusFilter('all'); setSearch('') }}
            className="text-xs text-primary hover:text-primary-hover font-medium"
          >
            Clear filters
          </button>
        )}
        <span className="text-xs text-muted ml-auto">Click any flagged row for its decision path</span>
      </div>

      <div className="bg-white rounded-2xl border border-divider overflow-hidden shadow-sm">
        <div className="overflow-x-auto">{table()}</div>
      </div>

      <Drawer exc={selected} onClose={() => setSelected(null)} />
    </div>
  )
}
