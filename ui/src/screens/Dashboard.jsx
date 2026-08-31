import React from 'react';
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts';
import { TrendingDown, TrendingUp, Minus } from 'lucide-react';
import StatCard from '../components/StatCard';

export default function Dashboard({ weekData, allWeeks, selectedWeek, reviewRateTrend }) {
  const prevWeek = allWeeks[selectedWeek + 1];
  
  const getDelta = (key) => {
    if (!prevWeek) return undefined;
    return weekData.stats[key] - prevWeek.stats[key];
  };

  const manualReviewRate = weekData.stats.manualReviewRate;
  const prevManualReviewRate = prevWeek ? prevWeek.stats.manualReviewRate : null;
  const reviewRateDelta = prevManualReviewRate !== null ? Number((manualReviewRate - prevManualReviewRate).toFixed(1)) : 0;
  
  const chartData = (reviewRateTrend || []).map((rate, i) => ({
    name: `Week ${i + 1}`,
    rate: rate
  }));

  const DeltaIcon = reviewRateDelta > 0 ? TrendingUp : reviewRateDelta < 0 ? TrendingDown : Minus;
  
  // Lower review rate is better, so decreasing is success (green), rising is danger (red)
  const deltaColor = reviewRateDelta > 0 ? 'text-danger' : reviewRateDelta < 0 ? 'text-success' : 'text-muted';

  return (
    <div className="flex flex-col gap-6">
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Hero Card */}
        <div className="bg-white rounded-2xl border border-divider p-6 flex flex-col justify-center">
          <h2 className="text-sm font-medium text-muted uppercase tracking-wide mb-2">Manual Review Rate</h2>
          <div className="text-5xl font-bold text-gray-900 mb-4">{manualReviewRate.toFixed(1)}%</div>
          {prevManualReviewRate !== null && (
            <div className={`flex items-center gap-1 font-medium ${deltaColor}`}>
              <DeltaIcon size={20} />
              <span>{Math.abs(reviewRateDelta)}% vs previous week</span>
            </div>
          )}
        </div>

        {/* Chart Card */}
        <div className="bg-white rounded-2xl border border-divider p-6 lg:col-span-2 h-64 flex flex-col">
          <h2 className="text-sm font-medium text-muted uppercase tracking-wide mb-4">Review Rate Trend</h2>
          <div className="flex-1">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={chartData}>
                <defs>
                  <linearGradient id="colorRate" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#3D4FE0" stopOpacity={0.2}/>
                    <stop offset="95%" stopColor="#3D4FE0" stopOpacity={0}/>
                  </linearGradient>
                </defs>
                <XAxis dataKey="name" axisLine={false} tickLine={false} tick={{fill: '#6B7280', fontSize: 12}} />
                <YAxis hide domain={['auto', 'auto']} />
                <Tooltip 
                  contentStyle={{ borderRadius: '8px', border: '1px solid #E7E7EA', boxShadow: '0 4px 6px -1px rgba(0, 0, 0, 0.1)' }}
                  itemStyle={{ color: '#0B0B0C', fontWeight: 600 }}
                  formatter={(value) => [`${value}%`, 'Review Rate']}
                />
                <Area 
                  type="monotone" 
                  dataKey="rate" 
                  stroke="#3D4FE0" 
                  strokeWidth={3}
                  fillOpacity={1} 
                  fill="url(#colorRate)"
                  activeDot={{ r: 6, fill: '#3D4FE0', stroke: 'white', strokeWidth: 2 }}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      {/* Row of 5 stat cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 lg:grid-cols-5 gap-4">
        <StatCard 
          label="Total Transactions" 
          value={weekData.stats.totalTransactions}
          delta={getDelta('totalTransactions')}
        />
        <StatCard 
          label="Auto-Matched" 
          value={weekData.stats.autoMatched}
          delta={getDelta('autoMatched')}
        />
        <StatCard 
          label="Auto-Resolved" 
          value={weekData.stats.autoResolved}
          delta={getDelta('autoResolved')}
        />
        <StatCard 
          label="Flagged for Review" 
          value={weekData.stats.flaggedForReview}
          delta={getDelta('flaggedForReview')}
        />
        <StatCard 
          label="Bulk-Fix Opportunities" 
          value={weekData.stats.bulkFixOpportunities}
          delta={getDelta('bulkFixOpportunities')}
        />
      </div>
    </div>
  );
}
