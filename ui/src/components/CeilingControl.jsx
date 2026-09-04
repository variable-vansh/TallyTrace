import { useMemo, useState } from 'react'
import { AlertTriangle, Check } from 'lucide-react'
import { inr } from '../lib/format'

// The auto-resolution ceiling, set by the business rather than by the model.
//
// Above this many rupees of *error* — not of order value — no rule may close a row
// however confident it is. It is the one number on this page that changes what the
// system does, so it is also the one that has to be argued about with evidence rather
// than adjusted with a slider.
//
// **Why this control shows two precision numbers.** `live` is what the product can
// see: a rule judged against the cause the operator's own words imply. `true` is the
// harness's, judged against an answer key the pipeline never reads. They agree at the
// shipped ceiling and come apart above it, because a rule and an operator can be wrong
// in the same direction and the bigger the row, the more often they are. A control
// that plotted only `live` would recommend raising the ceiling forever.
//
// Every row here is a real scored run of the whole corpus from `make ceilings`, not an
// estimate this page computed. Typing a number that has not been scored says so rather
// than interpolating one.

function Delta({ value, goodWhenUp, suffix = '' }) {
  if (value === 0) return <span className="text-muted">—</span>
  const good = goodWhenUp ? value > 0 : value < 0
  return (
    <span className={good ? 'text-success' : 'text-danger'}>
      {value > 0 ? '+' : ''}{value.toFixed(2)}{suffix}
    </span>
  )
}

export default function CeilingControl({ policy }) {
  const sweep = policy?.scenarios
  const configured = sweep?.configured_ceiling_inr ?? policy?.default?.max_variance_inr
  const [entered, setEntered] = useState(String(Number(configured || 0)))

  const rows = useMemo(
    () => (sweep?.scenarios || []).map((s) => ({ ...s, value: Number(s.ceiling_inr) })),
    [sweep],
  )
  const current = rows.find((r) => r.value === Number(configured))
  const chosen = rows.find((r) => r.value === Number(entered))
  const scoredValues = rows.map((r) => r.value)

  if (!rows.length) {
    return (
      <div className="bg-white rounded-2xl border border-divider p-6">
        <h2 className="text-sm font-medium text-muted uppercase tracking-wide">
          Auto-resolution ceiling
        </h2>
        <p className="text-3xl font-bold text-gray-900 mt-2">
          {inr(Number(policy?.default?.max_variance_inr || 0), { whole: true })}
        </p>
        <p className="text-xs text-muted mt-2 leading-relaxed">
          Set in <span className="font-mono">config/thresholds.yaml</span>. Run{' '}
          <span className="font-mono">make ceilings</span> to score the corpus at a range of
          ceilings and this control will show what each one would have done.
        </p>
      </div>
    )
  }

  return (
    <div className="bg-white rounded-2xl border border-divider p-6 flex flex-col gap-4">
      <div>
        <h2 className="text-sm font-medium text-muted uppercase tracking-wide">
          Auto-resolution ceiling
        </h2>
        <p className="text-xs text-muted mt-1 leading-relaxed max-w-2xl">
          Above this many rupees of <em>error</em> — not of order value — no rule closes a row,
          however confident it is. Type a number to see what it would have done to this corpus.
        </p>
      </div>

      <div className="flex flex-wrap items-end gap-4">
        <label className="block">
          <span className="block text-xs font-medium text-gray-700 mb-1">Ceiling (₹)</span>
          <input
            type="number"
            min="0"
            step="50"
            list="scored-ceilings"
            value={entered}
            onChange={(e) => setEntered(e.target.value)}
            className="w-40 text-lg font-semibold tabular-nums px-3 py-2 border border-divider rounded-lg
              bg-white focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary"
          />
          <datalist id="scored-ceilings">
            {scoredValues.map((v) => <option key={v} value={v} />)}
          </datalist>
        </label>

        <div className="flex flex-wrap gap-1.5">
          {rows.map((row) => (
            <button
              key={row.ceiling_inr}
              onClick={() => setEntered(String(row.value))}
              className={`px-2.5 py-1.5 rounded-lg text-xs font-semibold tabular-nums border transition-colors
                ${row.value === Number(entered)
                  ? 'bg-gray-900 text-white border-gray-900'
                  : 'bg-white border-divider text-gray-600 hover:border-gray-300'}`}
            >
              ₹{row.value}
              {row.value === Number(configured) && (
                <span className={row.value === Number(entered) ? 'text-white/60' : 'text-success'}> ●</span>
              )}
            </button>
          ))}
        </div>
      </div>

      {!chosen ? (
        <p className="text-xs text-gray-700 bg-card-bg border border-divider rounded-lg px-3 py-2.5 leading-relaxed">
          ₹{entered} has not been scored, and this page will not interpolate a number it did not
          measure. Run <span className="font-mono">make whatif ceiling={entered}</span> for the
          full report, or <span className="font-mono">make ceilings --ceilings {entered}</span> to
          add it to this control.
        </p>
      ) : (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {[
              ['Rows closed without a human', chosen.auto_resolutions, current?.auto_resolutions, true, ''],
              ['Closed with the wrong cause', chosen.wrong, current?.wrong, false, ''],
              ['True precision', Number(chosen.true_precision_pct), Number(current?.true_precision_pct), true, '%'],
              ['Last batch left to a human', Number(chosen.final_review_rate_pct), Number(current?.final_review_rate_pct), false, '%'],
            ].map(([label, value, base, goodWhenUp, suffix]) => (
              <div key={label} className="rounded-xl border border-divider p-3">
                <div className="text-[11px] text-muted leading-tight">{label}</div>
                <div className="text-xl font-bold text-gray-900 tabular-nums mt-1">
                  {value}{suffix}
                </div>
                <div className="text-xs mt-0.5">
                  <Delta value={Number(value) - Number(base ?? value)} goodWhenUp={goodWhenUp} suffix={suffix} />
                  <span className="text-muted"> vs in force</span>
                </div>
              </div>
            ))}
          </div>

          {/* The finding this control exists to make visible. */}
          {Number(chosen.precision_gap_pct) > 0.5 ? (
            <div className="flex items-start gap-2.5 rounded-lg border border-amber/40 bg-amber-light/40 px-3 py-2.5">
              <AlertTriangle size={14} className="text-amber-700 mt-0.5 flex-shrink-0" />
              <p className="text-xs text-gray-800 leading-relaxed">
                At ₹{chosen.value} the system would report{' '}
                <strong>{chosen.live_precision_pct}%</strong> precision and actually be{' '}
                <strong>{chosen.true_precision_pct}%</strong> — a{' '}
                {chosen.precision_gap_pct}-point gap, and {chosen.wrong} rows closed with the
                wrong cause instead of {current?.wrong}. The extra rows are ones a rule and an
                operator get wrong in the same direction, so the system cannot see the loss.
                Raising the ceiling on live precision alone raises it forever.
              </p>
            </div>
          ) : (
            <div className="flex items-start gap-2.5 rounded-lg border border-success/25 bg-success-light/40 px-3 py-2.5">
              <Check size={14} className="text-success mt-0.5 flex-shrink-0" />
              <p className="text-xs text-gray-800 leading-relaxed">
                At ₹{chosen.value} the two precision measures agree to within{' '}
                {chosen.precision_gap_pct} points — what the system reports is what it is. This
                is the range where the ceiling can be moved on evidence.
              </p>
            </div>
          )}
        </>
      )}

      <p className="text-xs text-muted leading-relaxed border-t border-divider pt-3">
        This control compares; it does not apply. Every row is a real scored run of all ten
        batches from <span className="font-mono">make ceilings</span>, and the ceiling itself
        lives in <span className="font-mono">config/thresholds.yaml</span> — with per-cause and
        per-channel ceilings under it, most specific first. Changing that file and re-running{' '}
        <span className="font-mono">make score</span> is what makes a number the policy, so the
        committed artifacts always describe the ceiling that produced them.
      </p>
    </div>
  )
}
