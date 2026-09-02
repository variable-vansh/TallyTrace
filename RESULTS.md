# RESULTS

Verbatim output of `make score` over the ten shipped batches.
Regenerated on every run; nothing here is typed by hand.

```
==============================================================================
TALLYTRACE — SCORE REPORT
==============================================================================

THROUGHPUT
------------------------------------------------------------------------------
batch   records   settle   seconds     rec/s    tokens     ₹/txn
    1       244       59     0.017     14086     17127      0.16
    2       184       75     0.018     10102     20939      0.16
    3       195       87     0.024      8080     27078      0.18
    4       225      102     0.026      8717     34007      0.21
    5       240      114     0.031      7720     39869      0.22
    6       269      128     0.025     10647     35580      0.16
    7       301      141     0.028     10630     40671      0.18
    8       283      155     0.032      8751     46199      0.19
    9       245      168     0.034      7145     51980      0.20
   10       223      181     0.038      5845     66345      0.24
------------------------------------------------------------------------------
  all      2409     1210     0.275      8762    379795
       model claude-opus-5, rates from config/pricing.yaml.
       TOKEN COUNTS ARE ESTIMATED. Some cached answers were recorded from a
       transcript rather than metered by the API, so their token counts are
       derived from character length (config/pricing.yaml: estimated_chars_per_token).
       Recording zero instead would report a model-backed pipeline as free,
       which is a more misleading number than an approximate one.
       Cache hits are billed at the cache-read rate rather than as free: the first
       run paid for the answer, and a cost that only counts cold runs is not a cost.

ACCURACY — BUCKETS AND RATES, AS A PERCENTAGE OF BATCH TOTAL
------------------------------------------------------------------------------
batch  settle  match  var  unmat  quar  auto-match   review  auto  net review  new  aged  carried
    1      59     48   10      1     0      81.36%   18.64%     0      18.64%   23     0      118
    2      75     58   14      2     1      77.33%   22.67%     0      22.67%   31     0      145
    3      87     65   16      6     0      74.71%   25.29%     6      18.39%   38     0      165
    4     102     69   23      9     1      67.65%   32.35%     7      25.49%   58     0      188
    5     114     80   26      8     0      70.18%   29.82%     9      21.93%   67     0      188
    6     128     93   26      8     1      72.66%   27.34%    19      12.50%   67     2      197
    7     141     99   31     11     0      70.21%   29.79%    21      14.89%   77     2      223
    8     155    107   37     10     1      69.03%   30.97%    24      15.48%   88     2      202
    9     168    113   40     14     1      67.26%   32.74%    25      17.86%  100     3      114
   10     181    105   43     33     0      58.01%   41.99%    35      22.65%  117     3        0
------------------------------------------------------------------------------
       batch 1 auto-match 81.36%, review 18.64%  ->  batch 10 auto-match 58.01%, review 41.99%
       review rate is a measurement, not a target. Nothing here is tuned to move it.
       'review' is what the matcher alone leaves; 'net review' is what is left after
       learned rules auto-resolve. Two columns, so a decline that came from widening
       a tolerance cannot be mistaken for one that came from learning.
       'new' is findings raised this batch across all three tables; 'aged' is the same problems still
       open from earlier batches; 'carried' is orders inside their window, which are not exceptions.

CAUSE-LEVEL CONFUSION — WHICH BUCKET DID EACH INJECTED TROUBLE LAND IN
------------------------------------------------------------------------------
cause                           class                rows  caught    rate
commission_rate_stale           internal_fix          127     127 100.00%
     127  variance/fee_variance_outside_tolerance
settlement_lag_crossing_batch   internal_fix           56      48  85.71%
      48  variance/settlement_outside_date_window
       8  matched/order_matched_clean
rto_reversal_later_cycle        internal_fix           51      51 100.00%
      51  unmatched/late_row_for_already_settled_order
refund_timing_lag               internal_fix           43      43 100.00%
      43  unmatched/late_row_for_already_settled_order
rounding_variance               internal_fix           40       0   0.00%
      40  matched/order_matched_clean
promo_cofunding_deduction       counterparty_claim     10      10 100.00%
      10  variance/fee_variance_outside_tolerance
weight_dispute_hold             counterparty_claim      8       8 100.00%
       8  variance/payment_withheld_on_hold
chargeback_deduction            counterparty_claim      6       6 100.00%
       6  variance/net_variance_outside_tolerance
missing_settlement_row          counterparty_claim      6       6 100.00%
       3  unmatched/settlement_overdue_beyond_window
       3  variance/fee_variance_outside_tolerance
short_payment_unexplained       counterparty_claim      6       6 100.00%
       4  variance/net_variance_outside_tolerance
       2  variance/fee_variance_outside_tolerance
fee_mismatch_other              internal_fix            4       4 100.00%
       4  variance/fee_variance_outside_tolerance
tcs_timing_mismatch             tax_review              4       4 100.00%
       2  unmatched/late_row_for_already_settled_order
       2  variance/net_variance_outside_tolerance
bank_credit_unmatched           investigate             3       3 100.00%
       3  unmatched/bank_credit_without_settlement_group
duplicate_settlement_row        internal_fix            3       3 100.00%
       3  unmatched/not_funded_by_bank_credit
commission_slab_change          internal_fix            2       2 100.00%
       2  variance/fee_variance_outside_tolerance
tds_timing_mismatch             tax_review              2       2 100.00%
       1  unmatched/late_row_for_already_settled_order
       1  variance/net_variance_outside_tolerance
------------------------------------------------------------------------------
       323 of 371 injected rows surfaced, ₹464246.13 of true impact in the corpus.
       The answer key records no claim about what *should* be catchable. This table is the finding.

SILENT CLEARS — INJECTED TROUBLES THE MATCHER CALLED CLEAN
------------------------------------------------------------------------------
cause                             rows   largest Δ   tightest headroom
rounding_variance                   40       ₹0.98      ₹0.16 of ₹1.00
settlement_lag_crossing_batch        8       ₹0.00      ₹1.03 of ₹1.03
------------------------------------------------------------------------------
       48 of 371 injected rows (12.94%).
       'tightest headroom' is the smallest gap between a cleared row's deviation
       and the band that permitted it. That is the number that says a band is too wide.

AUTO-RESOLUTION
------------------------------------------------------------------------------
       146 attempted, precision 98.63%

LEARNING LOOP — WHAT A RULE CLOSED, AND WHETHER IT WAS RIGHT
------------------------------------------------------------------------------
batch  queue  auto  held   esc  precision  ₹ auto-resolved    ₹ escalated  learn  prom  ret  cards  touch  touch %
    1     13     0     0    13          —            ₹0.00      ₹33776.99      6     0    0      0     13   22.03%
    2     17     0     0    17          —            ₹0.00      ₹11893.62      4     2    0      2     17   22.67%
    3     22     6     4    16    100.00%          ₹585.10      ₹17674.33      5     0    1      3     14   16.09%
    4     35     7     4    28    100.00%          ₹663.96      ₹65010.84      5     0    0      7     26   25.49%
    5     41     9     5    32     88.89%          ₹821.59      ₹41170.78      3     3    0      7     29   25.44%
    6     41    19     9    22    100.00%          ₹977.28      ₹43724.91      2     1    0      7     18   14.06%
    7     46    21    16    25    100.00%         ₹1146.23      ₹43374.46      3     1    0      7     15   10.64%
    8     51    24    19    27     95.83%         ₹1388.81      ₹42916.64      0     2    0     10     15    9.68%
    9     60    25    26    35    100.00%         ₹1280.55      ₹70234.52      3     0    0      8     16    9.52%
   10     74    35    33    39    100.00%         ₹1339.90     ₹120624.39      0     0    0      8     11    6.08%
------------------------------------------------------------------------------
       overall auto-resolution precision 98.63% over 146 scored resolutions.

       Two review series, and they say different things. Both are printed because
       reporting only the flattering one is the failure this harness exists to catch.
       net review rate (rows a human still owns) : 18.64%  ->  22.65%
       human touchpoints (decisions to make)     : 22.03%  ->  6.08%

       'held' is a case a rule matched and a guardrail refused to automate. Those
       rows still belong to a human, and they are collapsed into one card rather
       than N exceptions — which is why the two series diverge.

ABSTENTION — THE CAUSES HELD OUT OF THE CORPUS UNTIL LATE
------------------------------------------------------------------------------
cause                          first seen  cases then  auto then  auto ever  abstention
chargeback_deduction              batch 9           3          0          0     100.00%
promo_cofunding_deduction         batch 7           3          0          0     100.00%
------------------------------------------------------------------------------
       Correct abstention is refusing to automate a cause the system has never
       been taught. It is measured here, not asserted.
       every held-out cause was correctly left to a human on first sight: True

RULES — EVERY ONE, INCLUDING THE RETIRED ONE
------------------------------------------------------------------------------
id    state      born  support    +   -  live prec     true prec  last fired  cause
R-01  shadow        1        2    2   0    100.00%             —           —  duplicate_settlement_row
R-02  shadow        1        2    2   0    100.00%             —           —  bank_credit_unmatched
R-03  active        1        6    4   0    100.00%             —           —  weight_dispute_hold
R-04  shadow        1        0    0   0          —             —           —  duplicate_settlement_row
R-05  active        1       84   26   0    100.00%   97.44% (78)    batch 10  commission_rate_stale
R-06  active        1       40   19   0    100.00%             —           —  refund_timing_lag
R-07  retired       2        5    2   3     40.00%             —           —  rto_reversal_later_cycle
R-08  shadow        2        0    0   0          —             —           —  fee_mismatch_other
R-09  shadow        2        0    0   0          —             —           —  short_payment_unexplained
R-10  shadow        2        0    0   0          —             —           —  fee_mismatch_other
R-11  active        3       40   16   0    100.00%             —           —  refund_timing_lag
R-12  shadow        3        0    0   0          —             —           —  tcs_timing_mismatch
R-13  active        3       37   14   0    100.00%  100.00% (29)    batch 10  commission_rate_stale
R-14  shadow        3        1    1   0    100.00%             —           —  commission_slab_change
R-15  shadow        3        0    0   0          —             —           —  rounding_variance
R-16  active        4       44   19   0    100.00%  100.00% (39)    batch 10  settlement_lag_crossing_batch
R-17  active        4       44   16   0    100.00%             —           —  rto_reversal_later_cycle
R-18  shadow        4        1    1   0    100.00%             —           —  refund_timing_lag
R-19  shadow        4        0    0   0          —             —           —  short_payment_unexplained
R-20  shadow        4        0    0   0          —             —           —  tds_timing_mismatch
R-21  active        5       20    8   0    100.00%             —           —  missing_settlement_row
R-22  shadow        5        0    0   0          —             —           —  missing_settlement_row
R-23  shadow        5        0    0   0          —             —           —  short_payment_unexplained
R-24  shadow        6        0    0   0          —             —           —  refund_timing_lag
R-25  shadow        6        0    0   0          —             —           —  fee_mismatch_other
R-26  shadow        7        0    0   0          —             —           —  tcs_timing_mismatch
R-27  active        7        3    3   0    100.00%             —           —  promo_cofunding_deduction
R-28  shadow        7        0    0   0          —             —           —  fee_mismatch_other
R-29  shadow        9        0    0   0          —             —           —  missing_settlement_row
R-30  shadow        9        3    2   0    100.00%             —           —  chargeback_deduction
R-31  shadow        9        2    2   0    100.00%             —           —  promo_cofunding_deduction
------------------------------------------------------------------------------
       Retired, and why — this is evidence the lifecycle works, not a defect:
       R-07  A deduction arriving within three weeks of an order settling is the return coming back through, and nets off against the original sale.
              retired in batch 3: live precision 40.00% over 5 judged observations is below the 75% floor
       'live prec' is what the operator's own resolutions said. 'true prec' is what
       the answer key says about the rows the rule closed unattended. Where they
       differ, the operator and the rule were fooled by the same row.

CLAIMS QUEUE — OPENED, RECOVERED, EXPIRED, AND THE MONEY ON EACH
------------------------------------------------------------------------------
batch  opened  draft  filed  recov  exp      ₹ opened    ₹ recovered    ₹ expired  open       ₹ open
    1       2      2      2      0    0      ₹5866.21          ₹0.00        ₹0.00     2     ₹5866.21
    2       2      2      2      0    0       ₹717.48          ₹0.00        ₹0.00     4     ₹6583.69
    3       1      0      1      0    0        ₹19.51          ₹0.00        ₹0.00     5     ₹6603.20
    4       3      3      2      1    0      ₹6741.44        ₹287.97        ₹0.00     7    ₹13056.67
    5       9      9      6      0    0     ₹16562.80          ₹0.00        ₹0.00    16    ₹29619.47
    6       6      6      2      5    3     ₹13028.37      ₹11769.13     ₹5885.72    14    ₹24992.99
    7       8      7      6      6    1      ₹8933.37      ₹13028.37      ₹429.51    15    ₹20468.48
    8       9      9      7      4    0     ₹12564.46       ₹8363.13        ₹0.00    20    ₹24669.81
    9      12     12      6      3    3     ₹21829.21       ₹4123.63     ₹6741.44    26    ₹35633.95
   10       5      5      4      7    4     ₹14948.45      ₹10869.35     ₹4460.10    20    ₹35252.95
------------------------------------------------------------------------------
       57 claims opened. 26 recovered (₹48441.58), 11 expired (₹17516.77), 20 still open.
       recovery rate on settled claims: 70.27%. Open claims are not counted as either;
       a claim inside its window is not yet a result.

       queue as it stands: ₹35,252.95 open across 20 claims · 3 expiring in 9 days
       Sorted by expiry, never by creation date. A claims list ordered by when it
       was raised buries the one that stops being recoverable on Thursday.

CLAIM RECOVERY — THE PLANTED REIMBURSEMENTS, ONE ROW EACH
------------------------------------------------------------------------------
order         credit       claimed in  paid in          ₹  outcome
ord_000025    st_001109       batch 1  batch 4    ₹775.36  no claim was ever opened on this order
ord_000081    st_001120       batch 2  batch 4    ₹287.97  recovered
ord_000193    st_001130       batch 3  batch 6   ₹1200.29  recovered
ord_000376    st_001152       batch 5  batch 9    ₹360.05  recovered
ord_000491    st_001164       batch 6  batch 8   ₹3091.06  no claim was ever opened on this order
------------------------------------------------------------------------------
       3 of 5 planted pairs auto-closed against the credit that paid them.
       The misses are not link failures. In both, the reimbursement arrived while the order was
       still inside its settlement window, so the matcher never raised it and no claim was ever
       opened to close. A claim the system had no cause to open is not a claim it failed to recover,
       and it is reported as a miss anyway because excluding it would be marking its own homework.

CLAIM ATTRIBUTION — DID THE ANSWER KEY AGREE THESE WERE CLAIMS
------------------------------------------------------------------------------
cause claimed                    claims  confirmed  precision  self-closed misses
chargeback_deduction                  6          6    100.00%                   0
missing_settlement_row               27          4     14.81%                  14
promo_cofunding_deduction            10         10    100.00%                   0
short_payment_unexplained             4          4    100.00%                   0
tcs_timing_mismatch                   2          2    100.00%                   0
weight_dispute_hold                   8          8    100.00%                   0
------------------------------------------------------------------------------
       34 of 57 claims (59.65%) are confirmed by the answer key.
       14 of the 23 that are not closed themselves when the money arrived,
       with no operator ever filing them.

       Read the missing_settlement_row row and do not look away from it. The queue
       opens a claim whenever a payout is past its settlement window, and most of
       those turn out to be settlements that were merely late. That is a deliberate
       bias and the auto-close is what pays for it: chasing a late payout costs a
       claim that closes itself, and not chasing a genuinely missing one costs the
       whole payout once the filing window shuts. The bias is only affordable
       because the recovery match exists, which is why both numbers are printed
       side by side.

REPORTING — WHAT THE REGISTRY ANSWERED, AND WHAT IT WOULD NOT
------------------------------------------------------------------------------
       10 registered metrics. 8 of 11 questions mapped; 3 were clarified or refused.
       No SQL is generated anywhere in this system. Enterprise text-to-SQL execution accuracy
       runs roughly 21-39% on realistic schemas, and its failures are silent: a valid query
       returns a plausible wrong number. A closed registry can only pick the wrong id out of
       ten, and the restatement puts that choice in front of a human first.

       [mapped  ] How much did we actually get paid by each channel?
                  -> Net revenue settled — money that actually reached the bank after every platform deduction — totalled per channel across all ten weeks.
       [mapped  ] Is Myntra taking a bigger cut than it used to?
                  -> Myntra's effective take rate — commission, GST on commission, TCS and TDS as a percentage of gross order value — plotted week by week.
       [mapped  ] What share of gross are the platforms keeping across the board?
                  -> The effective take rate — every deduction as a percentage of gross order value — for each channel across the whole corpus.
       [mapped  ] Which causes are generating the most exceptions?
                  -> A count of exceptions by cause across all ten weeks, largest first.
       [mapped  ] Is the manual review rate actually coming down?
                  -> The manual review rate — settlement rows still needing a human after learned rules fire, as a percentage of the batch — plotted per week.
       [mapped  ] How much money are we still chasing, by platform?
                  -> The rupee value of claims still open, totalled per platform.
       [mapped  ] How much have we lost to claims that expired before we filed them?
                  -> Rupees on claims whose filing window closed with no recovery, shown for the week each one lapsed in.
       [mapped  ] Show me net revenue by channel for the first four weeks only
                  -> Net revenue settled per channel, restricted to batches one through four.
       [clarify ] How are our fees trending?
                  -> Two different metrics answer this and they diverge by several percentage points, so nothing has been computed yet.
                  ?  Do you mean the platform commission on its own, or every deduction including the GST charged on that commission and the tax collected at source?
       [refuse  ] Which of our SKUs are least profitable?
                  -> Nothing has been computed: the registry has no metric that answers this question.
                  ✗  This reconciliation holds orders, settlements and bank credits. It has no product master and no cost of goods, so profitability per SKU cannot be computed here at all — not approximately, and not from an adjacent figure.
       [refuse  ] What will next month's settlement come to?
                  -> Nothing has been computed: the registry measures what happened and does not forecast.
                  ✗  Every metric in the registry is a measurement over settled batches. There is no forecasting metric, and projecting one of these series forward would produce a number with a reconciliation's authority and a guess's accuracy.
------------------------------------------------------------------------------
       A refusal is the feature. The tempting failure on this surface is to answer
       an unanswerable question with a nearby chart, and a nearby chart carries the
       same authority as a correct one.

PINNED METRICS — RECOMPUTED WITH NO MODEL IN THE LOOP
------------------------------------------------------------------------------
       Myntra take rate, week by week  [effective_take_rate by batch]
           pinned by priya.n@demostore.in on 2025-03-16, from: "Is Myntra taking a bigger cut than it used to?"
           batch 1           30.29%
           batch 2           30.49%
           batch 3           29.87%
           batch 4           32.90%
           batch 5           30.43%
           batch 6           29.33%
           batch 7           28.60%
           batch 8           29.40%
           batch 9           28.06%
           batch 10          32.44%

       Effective take rate by channel  [effective_take_rate by channel]
           pinned by priya.n@demostore.in on 2025-03-16, from: "What share of gross are the platforms keeping across the board?"
           amazon            21.66%
           flipkart          18.95%
           myntra            29.92%
           offline            2.34%
           website            2.23%

       Exceptions by cause  [exception_count_by_cause by cause]
           pinned by priya.n@demostore.in on 2025-03-16, from: "Which causes are generating the most exceptions?"
           commission_rate_stale                     129
           refund_timing_lag                          86
           rto_reversal_later_cycle                   54
           settlement_lag_crossing_batch              48
           missing_settlement_row                     28
           promo_cofunding_deduction                  10
           weight_dispute_hold                         8
           chargeback_deduction                        6
           duplicate_settlement_row                    6
           fee_mismatch_other                          5
           short_payment_unexplained                   5
           bank_credit_unmatched                       3
           commission_slab_change                      3
           malformed_missing_order_id                  2
           malformed_unparseable_date                  2
           tcs_timing_mismatch                         2
           malformed_unparseable_amount                1
           rounding_variance                           1
           tds_timing_mismatch                         1

       Manual review rate  [review_rate_trend by batch]
           pinned by priya.n@demostore.in on 2025-03-16, from: "Is the manual review rate actually coming down?"
           batch 1           18.64%
           batch 2           22.67%
           batch 3           18.39%
           batch 4           25.49%
           batch 5           21.93%
           batch 6           12.50%
           batch 7           14.89%
           batch 8           15.48%
           batch 9           17.86%
           batch 10          22.65%

       Open claim value  [open_claim_value by platform]
           pinned by priya.n@demostore.in on 2025-03-16, from: "How much money are we still chasing, by platform?"
           amazon         ₹2,404.15
           flipkart       ₹5,754.15
           myntra         ₹1,686.76
           website       ₹25,407.89

------------------------------------------------------------------------------
       The model was present at the moment of definition and is absent from every
       run afterwards. What is stored in data/pins.json is a metric id and its
       parameters -- never a number -- so these recompute from the reconciled data
       every batch. pipeline/metrics/pins.py:recompute constructs no client, reads
       no cache and renders no prompt; tests/test_pins.py asserts it by breaking
       the client first and recomputing the whole dashboard anyway.

HONESTY
------------------------------------------------------------------------------
       666 open exceptions, ₹498604.90 in question. See EXCEPTIONS.md.
       5 rows quarantined, none dropped:
             2  malformed_missing_order_id
             1  malformed_unparseable_amount
             2  malformed_unparseable_date
       batch 2: 1, batch 4: 1, batch 6: 1, batch 8: 1, batch 9: 1
```
