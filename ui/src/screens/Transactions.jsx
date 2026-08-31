import React, { useState, useMemo } from 'react'
import { ChevronDown, ArrowUpDown, Search } from 'lucide-react'
import StatusBadge from '../components/StatusBadge'

const formatCurrency = (amount) => {
  return new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR' }).format(amount)
}

const getStatusConfig = (status) => {
  switch(status) {
    case 'matched':
      return { label: 'Matched', variant: 'success', icon: true }
    case 'variance':
      return { label: 'Variance', variant: 'amber', icon: false }
    case 'unmatched':
      return { label: 'Unmatched', variant: 'danger', icon: false }
    default:
      return { label: status, variant: 'muted', icon: false }
  }
}

const VIEWS = [
  { id: 'combined', label: 'Combined View' },
  { id: 'settlement', label: 'Settlement Report' },
  { id: 'bank', label: 'Bank Statement' },
  { id: 'ledger', label: 'Internal Ledger' },
]

const CHANNELS = ['all', 'amazon', 'flipkart', 'myntra', 'offline', 'website']
const STATUSES = ['all', 'matched', 'variance', 'unmatched']
const TYPES = ['all', 'payment', 'refund']
const SORTS = [
  { id: 'date-desc', label: 'Date (Latest)' },
  { id: 'date-asc', label: 'Date (Earliest)' },
  { id: 'amount-desc', label: 'Amount (High → Low)' },
  { id: 'amount-asc', label: 'Amount (Low → High)' },
]

function FilterPill({ label, value, options, onChange }) {
  return (
    <div className="relative">
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="appearance-none bg-white border border-divider rounded-lg pl-3 pr-8 py-2 text-xs font-medium text-gray-700 focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary cursor-pointer hover:border-gray-300 transition-colors"
      >
        {options.map(opt => (
          <option key={typeof opt === 'string' ? opt : opt.id} value={typeof opt === 'string' ? opt : opt.id}>
            {typeof opt === 'string' ? (opt === 'all' ? `All ${label}` : opt.charAt(0).toUpperCase() + opt.slice(1)) : opt.label}
          </option>
        ))}
      </select>
      <ChevronDown size={12} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted pointer-events-none" />
    </div>
  )
}

export default function Transactions({ weekData }) {
  const [activeView, setActiveView] = useState('combined')
  const [viewDropdownOpen, setViewDropdownOpen] = useState(false)
  const [channelFilter, setChannelFilter] = useState('all')
  const [statusFilter, setStatusFilter] = useState('all')
  const [typeFilter, setTypeFilter] = useState('all')
  const [sortBy, setSortBy] = useState('date-desc')
  const [search, setSearch] = useState('')

  const currentViewLabel = VIEWS.find(v => v.id === activeView)?.label || 'Combined View'

  const txns = weekData?.transactions || []

  // Apply filters and sorting
  const filtered = useMemo(() => {
    let result = [...txns]

    if (channelFilter !== 'all') result = result.filter(t => t.channel === channelFilter)
    if (statusFilter !== 'all') result = result.filter(t => t.status === statusFilter)
    if (typeFilter !== 'all') result = result.filter(t => t.type === typeFilter)
    if (search) {
      const q = search.toLowerCase()
      result = result.filter(t => t.orderId.toLowerCase().includes(q) || t.channel.includes(q))
    }

    // Sort
    result.sort((a, b) => {
      switch (sortBy) {
        case 'amount-desc': return b.amount - a.amount
        case 'amount-asc': return a.amount - b.amount
        case 'date-asc': return a.createdAt.localeCompare(b.createdAt)
        case 'date-desc':
        default: return b.createdAt.localeCompare(a.createdAt)
      }
    })

    return result
  }, [txns, channelFilter, statusFilter, typeFilter, sortBy, search])

  // Column configs per view
  const renderTable = () => {
    if (activeView === 'bank') {
      return (
        <table className="w-full text-sm text-left">
          <thead className="bg-card-bg text-muted border-b border-divider font-medium">
            <tr>
              <th className="px-5 py-3">UTR</th>
              <th className="px-5 py-3 text-right">Amount</th>
              <th className="px-5 py-3 text-right">Fees</th>
              <th className="px-5 py-3 text-right">Tax</th>
              <th className="px-5 py-3">Date</th>
              <th className="px-5 py-3">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-divider">
            {/* Bank statement has one row per settlement batch, show unique UTRs */}
            {[...new Set(filtered.map(t => t.settlementUtr))].map((utr, idx) => {
              const group = filtered.filter(t => t.settlementUtr === utr)
              const totalCredit = group.reduce((s, t) => s + t.credit, 0)
              const totalFee = group.reduce((s, t) => s + t.fee, 0)
              const totalTax = group.reduce((s, t) => s + t.tax, 0)
              return (
                <tr key={idx} className={idx % 2 === 0 ? 'bg-white' : 'bg-gray-50/50'}>
                  <td className="px-5 py-3 font-mono text-xs text-gray-900">{utr}</td>
                  <td className="px-5 py-3 text-right font-medium text-gray-900">{formatCurrency(totalCredit)}</td>
                  <td className="px-5 py-3 text-right text-muted">{formatCurrency(totalFee)}</td>
                  <td className="px-5 py-3 text-right text-muted">{formatCurrency(totalTax)}</td>
                  <td className="px-5 py-3 text-muted">{group[0]?.settledAt}</td>
                  <td className="px-5 py-3"><StatusBadge variant="success">Processed</StatusBadge></td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )
    }

    if (activeView === 'ledger') {
      return (
        <table className="w-full text-sm text-left">
          <thead className="bg-card-bg text-muted border-b border-divider font-medium">
            <tr>
              <th className="px-5 py-3">Order ID</th>
              <th className="px-5 py-3">Channel</th>
              <th className="px-5 py-3 text-right">Order Value</th>
              <th className="px-5 py-3 text-right">Expected Fee</th>
              <th className="px-5 py-3 text-right">Expected Net</th>
              <th className="px-5 py-3">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-divider">
            {filtered.map((txn, idx) => {
              const statusConf = getStatusConfig(txn.status)
              return (
                <tr key={idx} className={idx % 2 === 0 ? 'bg-white' : 'bg-gray-50/50'}>
                  <td className="px-5 py-3 font-mono text-xs text-gray-900 truncate max-w-[150px]" title={txn.orderId}>{txn.orderId}</td>
                  <td className="px-5 py-3"><StatusBadge variant="blue" className="capitalize">{txn.channel}</StatusBadge></td>
                  <td className="px-5 py-3 text-right font-medium text-gray-900">{formatCurrency(txn.amount)}</td>
                  <td className="px-5 py-3 text-right text-muted">{txn.expectedFee != null ? formatCurrency(txn.expectedFee) : '—'}</td>
                  <td className="px-5 py-3 text-right text-muted">{txn.expectedNet != null ? formatCurrency(txn.expectedNet) : '—'}</td>
                  <td className="px-5 py-3"><StatusBadge variant={statusConf.variant}>{statusConf.label}</StatusBadge></td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )
    }

    if (activeView === 'settlement') {
      return (
        <table className="w-full text-sm text-left">
          <thead className="bg-card-bg text-muted border-b border-divider font-medium">
            <tr>
              <th className="px-5 py-3">Order ID</th>
              <th className="px-5 py-3">Type</th>
              <th className="px-5 py-3">Channel</th>
              <th className="px-5 py-3 text-right">Amount</th>
              <th className="px-5 py-3 text-right">Fee</th>
              <th className="px-5 py-3 text-right">Tax</th>
              <th className="px-5 py-3 text-right">Credit</th>
              <th className="px-5 py-3">Settlement UTR</th>
              <th className="px-5 py-3">Date</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-divider">
            {filtered.map((txn, idx) => (
              <tr key={idx} className={idx % 2 === 0 ? 'bg-white' : 'bg-gray-50/50'}>
                <td className="px-5 py-3 font-mono text-xs text-gray-900 truncate max-w-[150px]" title={txn.orderId}>{txn.orderId}</td>
                <td className="px-5 py-3 text-gray-700 capitalize">{txn.type}</td>
                <td className="px-5 py-3"><StatusBadge variant="blue" className="capitalize">{txn.channel}</StatusBadge></td>
                <td className="px-5 py-3 text-right font-medium text-gray-900">{formatCurrency(txn.amount)}</td>
                <td className="px-5 py-3 text-right text-muted">{formatCurrency(txn.fee)}</td>
                <td className="px-5 py-3 text-right text-muted">{formatCurrency(txn.tax)}</td>
                <td className="px-5 py-3 text-right font-medium text-gray-900">{formatCurrency(txn.credit)}</td>
                <td className="px-5 py-3 font-mono text-xs text-muted truncate max-w-[130px]" title={txn.settlementUtr}>{txn.settlementUtr}</td>
                <td className="px-5 py-3 text-muted">{txn.createdAt}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )
    }

    // Combined view (default)
    return (
      <table className="w-full text-sm text-left">
        <thead className="bg-card-bg text-muted border-b border-divider font-medium">
          <tr>
            <th className="px-5 py-3">Order ID</th>
            <th className="px-5 py-3">Type</th>
            <th className="px-5 py-3">Channel</th>
            <th className="px-5 py-3 text-right">Amount</th>
            <th className="px-5 py-3 text-right">Fee</th>
            <th className="px-5 py-3">Status</th>
            <th className="px-5 py-3">Date</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-divider">
          {filtered.map((txn, idx) => {
            const statusConf = getStatusConfig(txn.status)
            return (
              <tr key={idx} className={idx % 2 === 0 ? 'bg-white' : 'bg-gray-50/50'}>
                <td className="px-5 py-3 font-mono text-xs text-gray-900 truncate max-w-[150px]" title={txn.orderId}>{txn.orderId}</td>
                <td className="px-5 py-3 text-gray-700 capitalize">{txn.type}</td>
                <td className="px-5 py-3"><StatusBadge variant="blue" className="capitalize">{txn.channel}</StatusBadge></td>
                <td className="px-5 py-3 text-right font-medium text-gray-900">{formatCurrency(txn.amount)}</td>
                <td className="px-5 py-3 text-right text-muted">{formatCurrency(txn.fee)}</td>
                <td className="px-5 py-3">
                  <StatusBadge variant={statusConf.variant}>
                    {statusConf.icon && (
                      <svg className="w-3 h-3 mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M5 13l4 4L19 7" />
                      </svg>
                    )}
                    {statusConf.label}
                  </StatusBadge>
                </td>
                <td className="px-5 py-3 text-muted">{txn.createdAt}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    )
  }

  return (
    <div className="flex flex-col gap-5">
      {/* Header with view switcher */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold text-gray-900">Transactions</h1>
          <span className="text-xs text-muted bg-gray-100 px-2 py-1 rounded-md">{filtered.length} of {txns.length} records</span>
        </div>

        {/* View dropdown */}
        <div className="relative">
          <button
            onClick={() => setViewDropdownOpen(!viewDropdownOpen)}
            className="flex items-center gap-2 bg-white border border-divider rounded-lg px-4 py-2 text-sm font-medium text-gray-700 hover:border-gray-300 transition-colors focus:outline-none focus:ring-2 focus:ring-primary/30"
          >
            <span className="text-muted text-xs">View:</span>
            {currentViewLabel}
            <ChevronDown size={14} className="text-muted" />
          </button>
          {viewDropdownOpen && (
            <>
              <div className="fixed inset-0 z-10" onClick={() => setViewDropdownOpen(false)} />
              <div className="absolute right-0 mt-1 w-52 bg-white border border-divider rounded-xl shadow-lg z-20 py-1 overflow-hidden">
                {VIEWS.map(v => (
                  <button
                    key={v.id}
                    onClick={() => { setActiveView(v.id); setViewDropdownOpen(false) }}
                    className={`w-full text-left px-4 py-2.5 text-sm transition-colors ${
                      activeView === v.id
                        ? 'bg-primary/5 text-primary font-medium'
                        : 'text-gray-700 hover:bg-gray-50'
                    }`}
                  >
                    {v.label}
                  </button>
                ))}
              </div>
            </>
          )}
        </div>
      </div>

      {/* Filters bar */}
      <div className="flex items-center gap-3 flex-wrap">
        <div className="relative flex-1 max-w-xs">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by Order ID..."
            className="w-full pl-9 pr-3 py-2 text-xs border border-divider rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary bg-white"
          />
        </div>

        <div className="h-5 w-px bg-divider" />

        <FilterPill label="Channels" value={channelFilter} options={CHANNELS} onChange={setChannelFilter} />
        <FilterPill label="Statuses" value={statusFilter} options={STATUSES} onChange={setStatusFilter} />
        <FilterPill label="Types" value={typeFilter} options={TYPES} onChange={setTypeFilter} />

        <div className="h-5 w-px bg-divider" />

        <FilterPill label="" value={sortBy} options={SORTS} onChange={setSortBy} />

        {(channelFilter !== 'all' || statusFilter !== 'all' || typeFilter !== 'all' || search) && (
          <button
            onClick={() => { setChannelFilter('all'); setStatusFilter('all'); setTypeFilter('all'); setSearch('') }}
            className="text-xs text-primary hover:text-primary-hover font-medium"
          >
            Clear filters
          </button>
        )}
      </div>

      {/* Table */}
      <div className="bg-white rounded-2xl border border-divider overflow-hidden shadow-sm">
        <div className="overflow-x-auto">
          {renderTable()}
        </div>
      </div>
    </div>
  )
}
