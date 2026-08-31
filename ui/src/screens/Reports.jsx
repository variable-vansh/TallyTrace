import React, { useState, useMemo } from 'react'
import { BarChart, Bar, ResponsiveContainer, PieChart, Pie, Cell, LineChart, Line, XAxis, YAxis, Tooltip } from 'recharts'
import { Send, BarChart2 } from 'lucide-react'

const formatCurrency = (amount) => {
  return new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(amount)
}

const CHANNEL_COLORS = {
  amazon: '#3D4FE0',
  flipkart: '#1FAA59',
  myntra: '#F59E0B',
  offline: '#EF4444',
  website: '#8B5CF6',
}

const SCENARIO_LABELS = {
  stale_commission_rate_drift: 'Stale Commission',
  known_fee_formula_mismatch: 'Fee Mismatch',
  refund_timing_lag: 'Refund Lag',
  settlement_on_hold: 'On Hold',
  tax_timing_mismatch: 'Tax Timing',
  dispute_chargeback_pending: 'Chargeback',
  duplicate_order_row: 'Duplicate Row',
  exact_amount_rounding: 'Rounding',
  partial_refund_ambiguous: 'Partial Refund',
  genuine_novel_anomaly_mystery_credit: 'Novel: Mystery Credit',
  genuine_novel_anomaly_negative_fee: 'Novel: Negative Fee',
  genuine_novel_anomaly_wrong_settlement_batch: 'Novel: Wrong Batch',
  genuine_novel_anomaly_amount_doubling: 'Novel: Amount Doubling',
  genuine_novel_anomaly_phantom_order: 'Novel: Phantom Order',
  genuine_novel_anomaly_zero_amount: 'Novel: Zero Amount',
  genuine_novel_anomaly_future_dated_settlement: 'Novel: Future Dated',
  genuine_novel_anomaly_fee_exceeds_amount: 'Novel: Fee > Amount',
  genuine_novel_anomaly_duplicate_entity_id: 'Novel: Duplicate Entity',
  genuine_novel_anomaly_negative_amount: 'Novel: Negative Amount',
}

const PIE_COLORS = ['#3D4FE0', '#1FAA59', '#F59E0B', '#EF4444', '#8B5CF6', '#06B6D4', '#EC4899', '#84CC16']

export default function Reports({ weekData, allWeeks }) {
  const [tab, setTab] = useState('history')
  const [chatInput, setChatInput] = useState('')
  const [chatMessages, setChatMessages] = useState([])

  // Revenue by channel — real computed data
  const revenueByChannel = useMemo(() => {
    if (!weekData?.transactions) return []
    const agg = {}
    weekData.transactions.forEach(t => {
      agg[t.channel] = (agg[t.channel] || 0) + t.credit
    })
    return Object.keys(agg)
      .map(k => ({ channel: k.charAt(0).toUpperCase() + k.slice(1), revenue: Math.round(agg[k]), raw: k }))
      .sort((a, b) => b.revenue - a.revenue)
  }, [weekData])

  // Exception causes — real computed data
  const exceptionByCause = useMemo(() => {
    if (!weekData?.exceptions) return []
    const agg = {}
    weekData.exceptions.forEach(e => {
      const label = SCENARIO_LABELS[e.scenarioType] || e.scenarioType
      agg[label] = (agg[label] || 0) + 1
    })
    return Object.keys(agg)
      .map(k => ({ name: k, value: agg[k] }))
      .sort((a, b) => b.value - a.value)
  }, [weekData])

  // Fee per week — real across all weeks
  const feeByWeek = useMemo(() => {
    if (!allWeeks) return []
    return allWeeks.map(w => ({
      name: `W${w.week}`,
      fee: Math.round(w.transactions?.reduce((s, t) => s + (t.fee || 0), 0) || 0),
    }))
  }, [allWeeks])

  // Date range for this week
  const dateRange = useMemo(() => {
    const dates = weekData?.transactions?.map(t => t.createdAt).filter(Boolean) || []
    if (!dates.length) return ''
    const sorted = [...dates].sort()
    return `${sorted[0]} → ${sorted[sorted.length - 1]}`
  }, [weekData])

  const handleChatSend = () => {
    if (!chatInput.trim()) return
    setChatMessages(prev => [...prev, { role: 'user', text: chatInput.trim() }])
    setChatInput('')
    // Placeholder: backend integration in Phase 9
    setTimeout(() => {
      setChatMessages(prev => [
        ...prev,
        { role: 'agent', text: 'NL Report Builder integration is coming in Phase 9. The pipeline results above are real and computed from the matched data.' }
      ])
    }, 600)
  }

  return (
    <div className="flex flex-col gap-6 h-full">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">Reports</h1>
        {dateRange && (
          <span className="text-xs text-muted bg-gray-100 px-3 py-1.5 rounded-md font-mono">{dateRange}</span>
        )}
      </div>

      {/* Tabs */}
      <div className="flex items-center gap-2 border-b border-divider pb-2">
        <button
          onClick={() => setTab('history')}
          className={`px-4 py-2 text-sm font-medium rounded-lg transition-colors ${
            tab === 'history' ? 'bg-card-bg text-gray-900' : 'text-muted hover:bg-gray-50'
          }`}
        >
          Summary
        </button>
        <button
          onClick={() => setTab('new')}
          className={`px-4 py-2 text-sm font-medium rounded-lg transition-colors ${
            tab === 'new' ? 'bg-card-bg text-gray-900' : 'text-muted hover:bg-gray-50'
          }`}
        >
          Ask a Question
        </button>
      </div>

      {tab === 'history' && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          {/* Card 1 — Revenue by channel (real data) */}
          <div className="bg-white rounded-xl border border-divider p-4 shadow-sm">
            <h3 className="font-semibold text-gray-900 mb-1">Net Revenue by Channel</h3>
            <p className="text-xs text-muted mb-3">Credit amounts credited to bank this week</p>
            <div className="h-[140px] w-full mb-3">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={revenueByChannel} margin={{ top: 5, right: 5, left: -20, bottom: 0 }}>
                  <XAxis dataKey="channel" tick={{ fontSize: 10 }} axisLine={false} tickLine={false} />
                  <Tooltip formatter={(val) => formatCurrency(val)} cursor={{ fill: '#f3f4f6' }} />
                  <Bar dataKey="revenue" radius={[4, 4, 0, 0]}>
                    {revenueByChannel.map((entry, index) => (
                      <Cell key={`cell-${index}`} fill={CHANNEL_COLORS[entry.raw] || '#3D4FE0'} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
            <div className="text-xs text-muted">{dateRange}</div>
          </div>

          {/* Card 2 — Exception by cause (real data) */}
          <div className="bg-white rounded-xl border border-divider p-4 shadow-sm">
            <h3 className="font-semibold text-gray-900 mb-1">Exceptions by Cause</h3>
            <p className="text-xs text-muted mb-3">Distribution across exception scenario types</p>
            {exceptionByCause.length > 0 ? (
              <div className="h-[140px] w-full mb-3 flex gap-3 items-center">
                <div className="flex-shrink-0" style={{ width: 110, height: 110 }}>
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie
                        data={exceptionByCause}
                        dataKey="value"
                        cx="50%"
                        cy="50%"
                        innerRadius={28}
                        outerRadius={48}
                      >
                        {exceptionByCause.map((entry, index) => (
                          <Cell key={`cell-${index}`} fill={PIE_COLORS[index % PIE_COLORS.length]} />
                        ))}
                      </Pie>
                    </PieChart>
                  </ResponsiveContainer>
                </div>
                <div className="flex flex-col gap-1 min-w-0 flex-1">
                  {exceptionByCause.slice(0, 5).map((e, i) => (
                    <div key={i} className="flex items-center gap-1.5 text-xs">
                      <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: PIE_COLORS[i % PIE_COLORS.length] }} />
                      <span className="text-gray-600 truncate">{e.name}</span>
                      <span className="ml-auto font-semibold text-gray-900">{e.value}</span>
                    </div>
                  ))}
                  {exceptionByCause.length > 5 && (
                    <div className="text-xs text-muted">+{exceptionByCause.length - 5} more</div>
                  )}
                </div>
              </div>
            ) : (
              <div className="h-[140px] flex items-center justify-center text-muted text-sm">No exceptions this week</div>
            )}
            <div className="text-xs text-muted">{dateRange}</div>
          </div>

          {/* Card 3 — Fees across all weeks (real data) */}
          <div className="bg-white rounded-xl border border-divider p-4 shadow-sm">
            <h3 className="font-semibold text-gray-900 mb-1">Platform Fees Trend</h3>
            <p className="text-xs text-muted mb-3">Total fees deducted per week across all batches</p>
            <div className="h-[140px] w-full mb-3">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={feeByWeek} margin={{ top: 5, right: 5, left: -20, bottom: 0 }}>
                  <XAxis dataKey="name" tick={{ fontSize: 11 }} axisLine={false} tickLine={false} />
                  <YAxis hide />
                  <Tooltip formatter={(val) => formatCurrency(val)} cursor={{ fill: '#f3f4f6' }} />
                  <Line type="monotone" dataKey="fee" stroke="#1FAA59" strokeWidth={2.5} dot={{ r: 4, fill: '#1FAA59', stroke: 'white', strokeWidth: 2 }} />
                </LineChart>
              </ResponsiveContainer>
            </div>
            <div className="text-xs text-muted">Weeks 1–5</div>
          </div>
        </div>
      )}

      {tab === 'new' && (
        <div className="flex-1 bg-white rounded-2xl border border-divider overflow-hidden flex flex-col shadow-sm max-h-[600px]">
          {/* Chat history */}
          <div className="flex-1 overflow-y-auto p-6 space-y-4">
            {chatMessages.length === 0 && (
              <div className="flex flex-col items-center justify-center h-full gap-3 text-center py-12">
                <div className="w-12 h-12 bg-primary/10 rounded-full flex items-center justify-center">
                  <BarChart2 size={22} className="text-primary" />
                </div>
                <div>
                  <p className="font-semibold text-gray-900 mb-1">Ask about your reconciliation data</p>
                  <p className="text-sm text-muted max-w-xs">
                    E.g. "Show me net revenue by channel", "Which cause has the most exceptions?", "Fee trend across weeks"
                  </p>
                </div>
              </div>
            )}
            {chatMessages.map((msg, idx) => (
              <div key={idx} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                <div className={`px-4 py-3 rounded-2xl max-w-[80%] text-sm ${
                  msg.role === 'user'
                    ? 'bg-primary text-white rounded-tr-sm'
                    : 'bg-card-bg text-gray-900 border border-divider rounded-tl-sm'
                }`}>
                  {msg.text}
                </div>
              </div>
            ))}
          </div>

          {/* Input bar */}
          <div className="p-4 border-t border-divider bg-gray-50">
            <div className="relative">
              <input
                type="text"
                value={chatInput}
                onChange={(e) => setChatInput(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleChatSend()}
                placeholder="Ask about your data..."
                className="w-full bg-white border border-gray-300 rounded-xl pl-4 pr-12 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary transition-all"
              />
              <button
                onClick={handleChatSend}
                disabled={!chatInput.trim()}
                className="absolute right-2 top-1/2 -translate-y-1/2 w-8 h-8 flex items-center justify-center bg-primary text-white rounded-lg hover:bg-primary-hover transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              >
                <Send size={14} />
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
