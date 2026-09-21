# Reversal Gauge — Frozen v1 Specification

Frozen spec. Weights do not change. The only authorized post-freeze
changes are the trend-direction gate (21 → 6 bars, 2026-09-21) and
display wording ("Warming up", "No triggers", "No signal"). Everything
else below is exactly as calibrated.

## 1. Purpose

Measures exhaustion of the **current move** on 5-minute bars. Output is
a 0–100 score plus trend direction. Used as exit/protection context.
Never issues entries; never recommends fading a trend on the score
alone.

## 2. Trend direction (gate: 6 session bars / 30 minutes)

Evaluated once at least 6 five-minute bars of the session exist.
Direction rule (all three):

1. Close relative to EMA21.
2. EMA21 slope versus two bars earlier.
3. Close relative to session VWAP (skipped if VWAP unavailable).

Result: uptrend, downtrend, or chop. Recomputed every bar.

## 3. Exhaustion scoring (gate: 15 session bars)

No exhaustion components are evaluated before 15 session bars exist.
Bars 6–14 display trend direction with **"Warming up"** instead of a
score. Bars with no prevailing trend display **"No signal"** (never a
numeric zero for an unscored bar).

### Scored factors

- **ATR climax / wide-range expansion** — range expansion relative to
  the session's ATR footprint.
- **Relative-volume spike with fading follow-through** — volume surge
  against the session baseline, then follow-through fades.
- **Rejection wick at a known level** — long wick rejecting VWAP,
  prior high/low, opening-range edge, overnight high/low, or a round
  number.
- **Absorption / repeated tests without extension** — price tests a
  level multiple times and fails to extend through it.
- **Location** — distance/position versus VWAP, prior-session high/low,
  opening range, overnight high/low, and round numbers.
- **RSI extremes, gated by session shape and confirmation** —
  session-shape regime: ≥0.6 trend day, ≤0.4 range day, otherwise mixed.
  On trend days, RSI extremes *lower* the score (trend healthy, do not
  fade). On range days, an RSI extreme adds +15 **only** with
  rejection-at-level or divergence confirmation.

A bar that scores a genuine 0 in an intact trend displays
**"No triggers"**: extension alone does not earn exhaustion points.

## 4. Bands

- **<30** — trend healthy.
- **30–60** — caution: do not add; consider tightening protection.
- **>60** — high risk: protect capital; optional confirmation trigger is
  a structural break plus a failed retest.

## 5. Structural reversal (for calibration only)

A close beyond the last counter-trend swing extreme that holds (no
reclaim within 2 bars). Used to score the calibration, not produced as
a live signal.

## 6. Session model

- SPY: RTH session.
- MES: futures day session; VWAP / ATR / RSI / EMA21 / volume reset at
  the day-session open; overnight levels remain available as reference;
  regular session and opening range use exclusive endpoints.

## 7. Calibration record (frozen)

- **SPY** (60 sessions, 532 bars): base 6-bar reversal rate 7.7%.
  <30: 8.3% (n=448). 30–60: 4.8% (n=84). >60: never fired, max 40.
- **MES** (94 sessions, 2,659 scored bars): base rate 14.3%.
  <30: 14.8% (n=2,411). 30–60: 9.3% (n=248). >60: never fired, max 50.
  Mean score 17.8 on reversal bars vs 17.7 otherwise.
- **Exit study** (threshold 35): setup C v3 hybrid +8.177R vs fixed
  +7.907R (+0.270R, 11 trades); setup D +14.066R vs +14.065R (+0.001R,
  66 trades). No mechanical exit rule earned a forward-paper trial.

Conclusion recorded 2026-09-21: the gauge failed as a
structural-reversal predictor on both instruments. It remains
discretionary exhaustion/exit context only.
