# Exit-Timing Study — does gauge-based exiting beat the fixed 2R target?

**Question:** does exiting on gauge exhaustion beat holding for the fixed 2R target?

## Frozen protocol (written BEFORE the replay ran)

- **Gauge frozen:** weights and scoring identical to `../report.md` v1, including the
  30-min ADX day-type injection used in `../calibrate.py`. No weight changes.
- **Exit threshold: 35** — the 90th percentile of the 532 scored calibration bars
  (distribution: median 20, mean 19.1, std 9.6, max 40; 79 bars at 35, 5 at 40).
  Derived from `calibration_bars.csv` before any replay. One shot, no re-picking.
- **Direction-aware exit (mechanical):** a gauge exit fires only on the first bar
  *after/at entry* whose score ≥ 35 **and** whose gauge prevailing direction matches
  the trade direction (long↔uptrend, short↔downtrend). If the gauge reads the
  opposing move as exhausted, that *confirms* the trade — exiting there would be
  perverse — so those bars do not trigger. Chop / no-signal bars never trigger.
- **Exit price:** close of the trigger bar (mild optimism: the close is observed
  first — documented, applies equally to all gauge exits).
- **Intrabar convention (hybrid):** per bar, stop-touch checked first
  (conservative), then target-touch, then gauge score at the close. If the stop is
  touched inside the trigger bar, the trade counts as a stop (-1R), not a gauge exit.
- **Fallbacks:** gauge-only mode falls back to the logged original outcome if the
  trigger never fires before the original exit bar. Hybrid falls back the same way.
- **Costs:** $2 round trip per trade → cost_r = 2 / risk_usd, same as the lab.
- **Verification gate:** hybrid with threshold=∞ must reproduce the logged net-R
  sums (C v3: +7.907; D: +14.065) before the real run counts.

## Verification gate: PASSED

Hybrid with threshold=∞ reproduces the logged outcomes trade-for-trade:

| Setup | Logged net R | Reproduced | Modes agree |
|-------|-------------|------------|-------------|
| C v3 (11) | +7.907 | +7.907 | yes — 5 target / 2 stop / 4 time→fallback |
| D (66) | +14.065 | +14.065 | yes — 28 target / 36 stop / 2 time→fallback |

(Residual ±0.001R is cost rounding: the CSVs store `risk_usd` rounded to cents.
One real bug was caught and fixed by this gate: time exits execute at the **open**
of the exit bar — the exit bar's range is not tradeable — so time-exit trades are
walked only up to the bar before the exit bar. The original backtests' stop/target
detection is bar-range touch, confirmed by mode agreement on all 77 trades.)

Method note: the bracket-ignorant "gauge-only" mode was dropped as untradeable
after verification. It allowed exits at the close of a bar where the target had
already been touched intrabar (2 such artifacts in D). The **hybrid** mode is the
tradeable implementation — per bar: stop-touch → −1R, target-touch → +2R, else
gauge trigger (score ≥ 35, direction-aligned) → exit at close — and it is the mode
reported below as "gauge exit".

## Results

| Setup | Mode | Net R | Exp/trade | Win rate | n |
|-------|------|-------|-----------|----------|---|
| C v3 | fixed 2R | **+7.907** | +0.719 | 63.6% | 11 |
| C v3 | gauge exit (hybrid) | **+8.177** | +0.743 | 63.6% | 11 |
| D | fixed 2R | **+14.065** | +0.213 | 42.4% | 66 |
| D | gauge exit (hybrid) | **+14.066** | +0.213 | 42.4% | 66 |
| Combined | fixed 2R | **+21.972** | +0.285 | 45.5% | 77 |
| Combined | gauge exit (hybrid) | **+22.243** | +0.289 | 45.5% | 77 |

Delta (gauge − fixed): C v3 **+0.270R**, D **+0.001R**, combined **+0.271R**.

### Decomposition — what did the gauge exits actually do?

| Setup | Gauge exits | Cut winners (R left) | Rescued losers (R saved) | Other |
|-------|------------|----------------------|--------------------------|-------|
| C v3 | 3 / 11 | 0 (0.152R left on table) | 0 (0.422R saved) | 3 time-decay trades |
| D | 0 / 66 tradeable | — | — | — |

C v3 detail (all three exits were driftless `time1555` trades, never bracket trades):
- 2026-07-01 13:00 short: time exit +0.630R → gauge exit +0.485R (−0.145R, trimmed a drifter early)
- 2026-07-15 10:55 short: time exit −0.567R → gauge exit −0.144R (+0.423R saved)
- 2026-08-19 13:10 short: time exit +0.589R → gauge exit +0.581R (−0.008R, ~flat)

Net: +0.270R from trimming dead trades — it neither rescued a stop-out nor cut a
target-winner short. It read "this trade is going nowhere" and was right.

D detail: across 201 trade-life bars, only **2** bars scored ≥ 35 with aligned
direction (vs 1 opposed) — and both came at/after the target touch, so no early
exit was tradeable. The gauge at threshold 35 essentially never speaks during D's
trades. (C v3: 5 aligned ≥ 35 bars in 305 trade-life bars; 3 became exits.)

## Honest caveats (read before acting on this)

1. **In-sample.** The gauge's components were developed on this SPY window and the
   threshold (35) comes from its score distribution. This is a one-shot historical
   replay, not a forward test — treat it as suggestive, not as an edge claim.
2. **C v3 n=11.** The entire +0.270R rests on 3 exits. That is noise scale; a single
   trade flipping sign erases it.
3. **Rare by construction.** Direction-alignment makes the rule a dead-trade
   trimmer, not a general exit upgrade: it fired on 3 of 77 trade-lives, all
   time-decay trades. It has never been observed rescuing a stop or (tradeably)
   cutting a winner.
4. **Exit-at-close is mildly optimistic** (the close is observed, then traded).
   Costs use the lab's $2 RT model.
5. **No tuning was done** — no weight or threshold search. The D null (zero
   tradeable exits in 66 trades) is a clean null result, not a failure to search.

## Verdict

**No — gauge-based exiting does not earn a mechanical forward-paper trial, and
neither frozen spec changes.** On D it does literally nothing (zero tradeable
exits in 66 trades): the fixed 2R target stands unchallenged. On C v3 the +0.270R
comes from trimming 3 driftless time-exits — directionally sensible ("take the
money on dead trades") but far too thin at n=11 to touch the frozen spec.

The gauge keeps the job it was built for: discretionary exhaustion context for
exits and protection — never a fade entry, and now, not a mechanical exit rule
either. Optional pre-registration for C v3's forward loop: *note* the gauge score
on trades that decay toward the time exit; if high-exhaustion dead trades keep
underperforming the hold, that becomes a hypothesis for future out-of-sample data
— not a rule today.
