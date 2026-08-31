import React, { useState, useMemo } from 'react'
import { CheckCircle, FileText, ArrowUpDown, ChevronDown, Shield, AlertTriangle, Send } from 'lucide-react'
import StatusBadge from '../components/StatusBadge'

const formatINR = (amount) => {
  return new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR' }).format(amount)
}

const getConfidenceVariant = (score) => {
  if (score > 0.85) return 'success'
  if (score > 0.7) return 'amber'
  return 'danger'
}

const CHANNELS = ['all', 'amazon', 'flipkart', 'myntra', 'offline', 'website']
const BUCKET_FILTERS = [
  { id: 'all', label: 'All Exceptions' },
  { id: 'pending', label: 'Pending Review' },
  { id: 'resolved', label: 'Resolved' },
  { id: 'auto_resolved', label: 'Auto-Resolved' },
]
const SORTS = [
  { id: 'amount-desc', label: 'Amount (High → Low)' },
  { id: 'amount-asc', label: 'Amount (Low → High)' },
  { id: 'confidence-desc', label: 'Confidence (High)' },
  { id: 'confidence-asc', label: 'Confidence (Low)' },
]

function FilterSelect({ value, options, onChange }) {
  return (
    <div className="relative">
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="appearance-none bg-white border border-divider rounded-lg pl-3 pr-8 py-2 text-xs font-medium text-gray-700 focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary cursor-pointer hover:border-gray-300 transition-colors"
      >
        {options.map(opt => (
          <option key={typeof opt === 'string' ? opt : opt.id} value={typeof opt === 'string' ? opt : opt.id}>
            {typeof opt === 'string' ? (opt === 'all' ? 'All Channels' : opt.charAt(0).toUpperCase() + opt.slice(1)) : opt.label}
          </option>
        ))}
      </select>
      <ChevronDown size={12} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted pointer-events-none" />
    </div>
  )
}

function DisputeDraftCard({ exc }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div className="mt-3 border border-amber/30 bg-amber-light/30 rounded-lg overflow-hidden">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between px-4 py-2.5 hover:bg-amber-light/50 transition-colors"
      >
        <div className="flex items-center gap-2">
          <FileText size={14} className="text-amber" />
          <span className="text-xs font-semibold text-amber-700">Dispute Draft Available</span>
        </div>
        <ChevronDown size={14} className={`text-amber transition-transform ${expanded ? 'rotate-180' : ''}`} />
      </button>

      {expanded && (
        <div className="px-4 pb-4 pt-1 border-t border-amber/20">
          <div className="bg-white rounded-lg border border-divider p-4 space-y-3">
            <div className="flex items-center justify-between">
              <h4 className="text-xs font-bold text-gray-900 uppercase tracking-wide">Dispute Claim Draft</h4>
              <StatusBadge variant="amber">Pending Submission</StatusBadge>
            </div>

            <div className="grid grid-cols-2 gap-3 text-xs">
              <div>
                <span className="text-muted block mb-0.5">Order Reference</span>
                <span className="font-mono font-medium text-gray-900">{exc.orderId}</span>
              </div>
              <div>
                <span className="text-muted block mb-0.5">Dispute ID</span>
                <span className="font-mono font-medium text-gray-900">{exc.disputeId || 'N/A'}</span>
              </div>
              <div>
                <span className="text-muted block mb-0.5">Expected Amount</span>
                <span className="font-medium text-gray-900">{formatINR(exc.amount)}</span>
              </div>
              <div>
                <span className="text-muted block mb-0.5">Amount Received</span>
                <span className="font-medium text-danger">{formatINR(exc.credit)}</span>
              </div>
            </div>

            <div className="text-xs text-gray-700 bg-card-bg rounded-md p-3 leading-relaxed">
              <p>To Whom It May Concern,</p>
              <p className="mt-2">We are writing regarding a discrepancy identified on order <strong>{exc.orderId}</strong> on the <strong>{exc.channel}</strong> platform. Our records indicate an expected settlement of {formatINR(exc.amount)}, however the actual amount credited was {formatINR(exc.credit)}, resulting in a shortfall of <strong>{formatINR(Math.abs(exc.amount - exc.credit))}</strong>.</p>
              <p className="mt-2">We request an investigation and resolution at the earliest.</p>
            </div>

            <div className="flex items-center gap-2 pt-1">
              <button className="flex items-center gap-1.5 bg-primary hover:bg-primary-hover text-white px-4 py-2 rounded-lg text-xs font-semibold transition-colors">
                <Send size={12} />
                Submit Claim
              </button>
              <button className="text-xs text-muted hover:text-gray-700 px-3 py-2 font-medium">
                Edit Draft
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default function ReviewQueue({ weekData }) {
  const [channelFilter, setChannelFilter] = useState('all')
  const [statusFilter, setStatusFilter] = useState('all')
  const [sortBy, setSortBy] = useState('amount-desc')

  const exceptions = weekData?.exceptions || []

  const filtered = useMemo(() => {
    let result = [...exceptions]

    if (channelFilter !== 'all') result = result.filter(e => e.channel === channelFilter)
    if (statusFilter !== 'all') result = result.filter(e => e.status === statusFilter)

    result.sort((a, b) => {
      switch (sortBy) {
        case 'amount-asc': return a.amount - b.amount
        case 'confidence-desc': return b.confidence - a.confidence
        case 'confidence-asc': return a.confidence - b.confidence
        case 'amount-desc':
        default: return b.amount - a.amount
      }
    })

    return result
  }, [exceptions, channelFilter, statusFilter, sortBy])

  const pendingCount = exceptions.filter(e => e.status === 'pending').length
  const resolvedCount = exceptions.filter(e => e.status === 'resolved' || e.status === 'auto_resolved').length

  return (
    <div className="flex flex-col gap-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold text-gray-900">Review Queue</h1>
          <div className="flex items-center gap-1.5">
            {pendingCount > 0 && (
              <StatusBadge variant="danger">{pendingCount} pending</StatusBadge>
            )}
            <StatusBadge variant="success">{resolvedCount} resolved</StatusBadge>
          </div>
        </div>
      </div>

      {/* Filters bar */}
      <div className="flex items-center gap-3 flex-wrap">
        <FilterSelect value={statusFilter} options={BUCKET_FILTERS} onChange={setStatusFilter} />
        <FilterSelect value={channelFilter} options={CHANNELS} onChange={setChannelFilter} />
        <div className="h-5 w-px bg-divider" />
        <FilterSelect value={sortBy} options={SORTS} onChange={setSortBy} />

        {(channelFilter !== 'all' || statusFilter !== 'all') && (
          <button
            onClick={() => { setChannelFilter('all'); setStatusFilter('all') }}
            className="text-xs text-primary hover:text-primary-hover font-medium"
          >
            Clear filters
          </button>
        )}

        <span className="text-xs text-muted ml-auto">{filtered.length} results</span>
      </div>

      {/* Bulk fix callout */}
      {weekData.bulkFixes && weekData.bulkFixes.length > 0 && (
        <div className="flex flex-col gap-3">
          {weekData.bulkFixes.map((bulkFix, idx) => (
            <div key={idx} className="bg-primary/5 border border-primary/20 rounded-xl overflow-hidden flex relative">
              <div className="w-1.5 bg-primary absolute left-0 top-0 bottom-0" />
              <div className="p-5 flex-1 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 ml-1.5">
                <div>
                  <div className="flex items-center gap-2 mb-1">
                    <Shield size={16} className="text-primary" />
                    <StatusBadge variant="blue" className="uppercase tracking-wider">
                      {bulkFix.channel}
                    </StatusBadge>
                    <span className="font-semibold text-gray-900 text-sm">Bulk Fix Available</span>
                  </div>
                  <p className="text-gray-700 text-sm">
                    {bulkFix.description}
                  </p>
                </div>
                <button className="bg-primary hover:bg-primary-hover text-white px-5 py-2.5 rounded-lg font-medium text-sm transition-colors whitespace-nowrap shadow-sm">
                  Resolve All ({bulkFix.affectedCount})
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Exception cards */}
      <div className="flex flex-col gap-4">
        {filtered.map((exc, idx) => {
          const isResolved = exc.status === 'resolved' || exc.status === 'auto_resolved'
          const confidenceVariant = getConfidenceVariant(exc.confidence)

          return (
            <div key={idx} className={`rounded-xl border bg-white overflow-hidden transition-colors ${
              isResolved ? 'border-divider' : 'border-amber/40 shadow-sm'
            }`}>
              {/* Card header */}
              <div className="flex items-center justify-between px-5 py-3.5 bg-card-bg/50 border-b border-divider">
                <div className="flex items-center gap-3">
                  <span className="font-mono text-sm font-semibold text-gray-900">{exc.orderId}</span>
                  <StatusBadge variant="muted" className="capitalize">{exc.channel}</StatusBadge>
                  {exc.bucket === 'A' && <StatusBadge variant="blue">Bucket A</StatusBadge>}
                  {exc.isNovel && <StatusBadge variant="amber">Novel</StatusBadge>}
                </div>
                <div className="flex items-center gap-3">
                  <span className="text-lg font-bold text-gray-900">{formatINR(exc.amount)}</span>
                  {isResolved && (
                    <div className="flex items-center gap-1 text-success">
                      <CheckCircle size={16} />
                      <span className="text-xs font-semibold capitalize">{exc.status.replace('_', ' ')}</span>
                    </div>
                  )}
                  {!isResolved && (
                    <div className="flex items-center gap-1 text-amber">
                      <AlertTriangle size={14} />
                      <span className="text-xs font-semibold">Pending</span>
                    </div>
                  )}
                </div>
              </div>

              {/* Card body */}
              <div className="px-5 py-4">
                <div className="flex gap-5">
                  {/* Hypothesis */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-2">
                      <span className="text-[10px] text-muted uppercase tracking-widest font-bold">AI Hypothesis</span>
                      <StatusBadge variant={confidenceVariant}>
                        {(exc.confidence * 100).toFixed(0)}% confidence
                      </StatusBadge>
                    </div>
                    <p className="text-sm text-gray-700 leading-relaxed">
                      {exc.hypothesis}
                    </p>
                  </div>

                  {/* Resolution panel */}
                  <div className="w-72 flex-shrink-0">
                    {isResolved ? (
                      <div className="bg-success-light/50 border border-success/20 rounded-lg p-4">
                        <div className="flex items-center gap-2 mb-2">
                          <CheckCircle size={14} className="text-success" />
                          <span className="text-xs font-bold text-success uppercase tracking-wide">
                            {exc.status === 'auto_resolved' ? 'Auto-Resolved' : 'Resolution'}
                          </span>
                        </div>
                        {exc.resolutionReason ? (
                          <p className="text-xs text-gray-700 leading-relaxed">{exc.resolutionReason}</p>
                        ) : exc.status === 'auto_resolved' ? (
                          <p className="text-xs text-muted italic leading-relaxed">
                            Matched a learned pattern — reason will be written back by the pattern store in Phase 5.
                          </p>
                        ) : (
                          <p className="text-xs text-muted italic leading-relaxed">
                            Resolution reason will be captured on human review in Phase 5.
                          </p>
                        )}
                      </div>
                    ) : (
                      <div className="bg-card-bg border border-divider rounded-lg p-4">
                        <span className="text-[10px] text-muted uppercase tracking-widest font-bold block mb-2">Resolve</span>
                        <div className="flex flex-col gap-2">
                          <textarea
                            placeholder="Describe how this was resolved..."
                            rows={2}
                            className="text-sm px-3 py-2 border border-divider rounded-lg w-full focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary bg-white resize-none"
                          />
                          <button className="bg-gray-900 hover:bg-gray-800 text-white px-3 py-2 rounded-lg text-xs font-semibold transition-colors w-full">
                            Mark Resolved
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                </div>

                {/* Dispute draft section — shown for resolved exceptions with a dispute ID */}
                {isResolved && exc.disputeId && (
                  <DisputeDraftCard exc={exc} />
                )}
              </div>
            </div>
          )
        })}
      </div>

      {filtered.length === 0 && (
        <div className="text-center py-16 text-muted">
          <AlertTriangle size={32} className="mx-auto mb-3 text-muted/50" />
          <p className="text-sm">No exceptions match your current filters.</p>
        </div>
      )}
    </div>
  )
}
