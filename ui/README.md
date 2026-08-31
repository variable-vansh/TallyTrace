# Tallytrace UI

The two thin surfaces on top of the reconciled data: a review queue and a rules page,
plus the dashboard that plots what the harness measured.

```bash
npm install
npm run dev        # http://localhost:5173
```

## Where the data comes from

Everything on screen is read from `public/tallytrace.json`, which is written by one
scored run:

```bash
cd .. && make score && make ui-data
```

One source, on purpose. The dashboard, the queue, the transactions tables and the rules
page all read the same file, so they cannot disagree with each other or with the numbers
`make score` printed in the terminal. `make demo` runs the whole chain.

The file is not committed — it is derived, and it is 2 MB. If the app says "Could not
load the run", that is what is missing.

## What this build is, and is not

It **renders a completed run**. The ten batches have already been reconciled,
hypothesised, scored and written to disk before the browser sees anything.

There is no server behind it, so the controls — "Accept all", "Not this time", "Narrow
the band", "Disable" — state what they *would* record rather than writing back to
`data/resolutions.json`. The queue says so at the top rather than leaving a viewer to
find out. The logic behind each control is real and is covered by tests in the Python
side (`_apply_card_decisions` under accept, decline and defer; `Rule.narrowed` including
its refusal to widen a band); only the persistence is missing.

## Screens

| screen | what it is for |
|---|---|
| **Dashboard** | All three review series on one chart, auto-resolution precision on its own beside it, and the auto-resolved-versus-escalated rupee split. |
| **Review Queue** | Batch proposal cards first — one card instead of N exceptions, including the ones a guardrail refused to automate — then the exceptions themselves with the model's hypothesis and the operator's own words. |
| **Transactions** | The settlement report, the bank statement and the internal ledger, each row carrying the bucket and reason code the matcher gave it. Click any flagged row for its decision path. |
| **Rules** | Every learned rule: state, conditions, support, live *and* true precision, the full lifecycle history with reasons, and the human resolution it descends from. The retired rule is at the top, in red. |
| **Reports** | Money by week and channel, cause mix, the abstention result, and the quarantine list. |

## Conventions

- **Tailwind v4** via `@tailwindcss/vite`. The palette and the fonts are tokens in
  `src/index.css`; nothing hardcodes a hex outside that file and the chart colour maps.
- **`src/lib/format.js`** holds every shared formatter — rupees, percentages, reason-code
  prose, badge variants — so two screens cannot disagree about what a number looks like.
- **`src/components/DecisionPath.jsx`** renders a provenance record verbatim. It is used
  by both the queue and the transactions drawer; there is one definition of "show me why".
- Money arrives as JSON numbers. That conversion happens once, in
  `tools/build_ui_data.py::money`, and only on the way to a browser — `data/score.json`
  keeps every amount as a string.
