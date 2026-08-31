import React from 'react'
import StatusBadge from '../components/StatusBadge'

export default function Patterns({ weekData }) {
  const patterns = weekData?.patterns || []

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">Learned Patterns</h1>
        <span className="text-sm text-muted">{patterns.length} patterns</span>
      </div>
      
      {patterns.length === 0 ? (
        <div className="bg-white rounded-2xl border border-divider p-12 text-center flex flex-col items-center shadow-sm">
          <div className="w-16 h-16 bg-card-bg rounded-full flex items-center justify-center mb-4">
            <svg className="w-8 h-8 text-muted" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" d="M13 10V3L4 14h7v7l9-11h-7z" />
            </svg>
          </div>
          <h3 className="text-lg font-semibold text-gray-900 mb-1">No patterns learned yet</h3>
          <p className="text-muted max-w-sm">The reconciliation agent needs more data to identify recurring variances and match failures.</p>
        </div>
      ) : (
        <div className="bg-white rounded-2xl border border-divider overflow-hidden shadow-sm">
          <div className="overflow-x-auto">
            <table className="w-full text-sm text-left">
              <thead className="bg-card-bg text-muted border-b border-divider font-medium">
                <tr>
                  <th className="px-6 py-3">Channel</th>
                  <th className="px-6 py-3">Cause</th>
                  <th className="px-6 py-3">Condition</th>
                  <th className="px-6 py-3 text-right">Times Applied</th>
                  <th className="px-6 py-3">First Seen</th>
                  <th className="px-6 py-3">Last Applied</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-divider">
                {patterns.map((p, idx) => (
                  <tr key={idx} className={idx % 2 === 0 ? 'bg-white' : 'bg-gray-50'}>
                    <td className="px-6 py-3">
                      <StatusBadge variant="blue" className="capitalize">{p.channel}</StatusBadge>
                    </td>
                    <td className="px-6 py-3 text-gray-900 font-medium capitalize">{p.cause}</td>
                    <td className="px-6 py-3 font-mono text-xs text-muted">{p.condition}</td>
                    <td className="px-6 py-3 text-right font-medium">{p.timesApplied}</td>
                    <td className="px-6 py-3 text-muted">Week {p.firstSeenWeek}</td>
                    <td className="px-6 py-3 text-muted">Week {p.lastAppliedWeek}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
