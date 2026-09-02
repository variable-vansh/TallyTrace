import {
  Bar, BarChart, CartesianGrid, Cell, Line, LineChart, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from 'recharts'
import { inr } from '../lib/format'

// One renderer for every registered metric, driven by the metric's own unit and
// grouping. The registry decides what a number means; this only decides what it
// looks like, so a new metric needs no new chart code.
const AXIS = { fill: '#6B7280', fontSize: 12 }
const TOOLTIP = { contentStyle: { borderRadius: '8px', border: '1px solid #E7E7EA', fontSize: 12 } }

const CHANNEL_COLORS = {
  amazon: '#3D4FE0', flipkart: '#1FAA59', myntra: '#F59E0B',
  offline: '#EF4444', website: '#8B5CF6',
}

const format = (unit) => (value) =>
  unit === 'pct' ? `${value}%` : unit === 'inr' ? inr(value, { whole: true }) : value

export default function MetricChart({ result, height = 240 }) {
  if (!result || !result.points?.length) {
    return <p className="text-sm text-muted">Nothing to plot for this window.</p>
  }
  const { unit, group_by: grouping, points } = result
  const data = points.map((p) => ({ name: p.label.replace('batch ', 'W'), value: p.value }))
  const asLine = grouping === 'batch'
  // A horizontal bar per category needs a row's worth of height. `exception_count_by_cause`
  // has nineteen of them, and squeezing nineteen labels into a fixed 170px is how a chart
  // stops being readable at exactly the point it has something to say.
  const plotted = asLine || data.length <= 6 ? height : Math.max(height, data.length * 22 + 30)

  return (
    <div style={{ height: plotted }}>
      <ResponsiveContainer width="100%" height="100%">
        {asLine ? (
          <LineChart data={data} margin={{ top: 6, right: 10, left: -12, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#E7E7EA" vertical={false} />
            <XAxis dataKey="name" axisLine={false} tickLine={false} tick={AXIS} />
            <YAxis axisLine={false} tickLine={false} tick={AXIS}
                   tickFormatter={unit === 'inr' ? (v) => `₹${(v / 1000).toFixed(0)}k` : undefined}
                   unit={unit === 'pct' ? '%' : undefined} />
            <Tooltip {...TOOLTIP} formatter={format(unit)} />
            <Line type="monotone" dataKey="value" stroke="#3D4FE0" strokeWidth={3} isAnimationActive={false}
                  dot={{ r: 3, fill: '#3D4FE0' }}
                  activeDot={{ r: 6, fill: '#3D4FE0', stroke: 'white', strokeWidth: 2 }} />
          </LineChart>
        ) : (
          <BarChart data={data} layout={data.length > 6 ? 'vertical' : 'horizontal'}
                    margin={{ top: 6, right: 16, left: data.length > 6 ? 60 : -12, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#E7E7EA" vertical={false} />
            {data.length > 6 ? (
              <>
                <XAxis type="number" hide />
                <YAxis type="category" dataKey="name" axisLine={false} tickLine={false}
                       tick={{ ...AXIS, fontSize: 10 }} width={160} />
              </>
            ) : (
              <>
                <XAxis dataKey="name" axisLine={false} tickLine={false}
                       tick={{ ...AXIS, textTransform: 'capitalize' }} />
                <YAxis axisLine={false} tickLine={false} tick={AXIS}
                       tickFormatter={unit === 'inr' ? (v) => `₹${(v / 1000).toFixed(0)}k` : undefined}
                       unit={unit === 'pct' ? '%' : undefined} />
              </>
            )}
            <Tooltip {...TOOLTIP} formatter={format(unit)} />
            <Bar dataKey="value" isAnimationActive={false} radius={data.length > 6 ? [0, 4, 4, 0] : [4, 4, 0, 0]}>
              {data.map((entry) => (
                <Cell key={entry.name} fill={CHANNEL_COLORS[entry.name] || '#3D4FE0'} />
              ))}
            </Bar>
          </BarChart>
        )}
      </ResponsiveContainer>
    </div>
  )
}
