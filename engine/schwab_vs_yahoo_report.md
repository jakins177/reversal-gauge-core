# Schwab vs Yahoo head-to-head — SPY 2026-09-18 (one RTH session)

**Date:** 2026-09-20. **Question:** does the live Schwab gateway change
the reversal gauge's behavior vs the Yahoo feed?
**Method:** scored every bar of Friday 2026-09-18 (78 five-min bars)
with the FROZEN v1 weights on each feed's own bars; 48 bars scorable
(>=30 bars history). No weights touched.

**Hard limit:** the gateway ignores period/startDate params and always
returns only the latest RTH session (78 bars). A full 60-session
recalibration on Schwab data is impossible through this gateway, so
this is a same-session head-to-head, not a recalibration.

## Feed agreement (78 common timestamps)

| Field | max abs diff | mean abs diff |
|---|---|---|
| Open/High/Low/Close | <= $0.02 | < $0.001 |
| Volume | 8.88M | 361K |

Prices are effectively identical. **Volume is not:** Schwab reports a
median 87.8% of Yahoo's volume (mean 79.0%, min 2.5% on one bar). The
volume-spike input uses a 2x-median *relative* threshold so it mostly
survives, but absolute volume reads differ across feeds.

## Score comparison (48 scored bars)

- Score correlation: **0.832**; mean |diff| 3.12, median 0.00
- Band agreement: **87.5%**; direction agreement: 91.7%
- Max score: **35 on both feeds** (calibration max was 40; >60 band
  still unreachable)
- Mean score: Schwab 16.0 vs Yahoo 14.6
- Band mix — Schwab: 30 healthy / 6 caution / 12 no-signal;
  Yahoo: 24 / 8 / 16

## Where they diverged (9 bars with |diff| >= 10)

- **12:30–12:35:** Yahoo fired the volume input, Schwab didn't
  (Schwab's lower volume print kept the spike under 2x median).
- **13:50–14:10 (4 bars):** Yahoo read chop ("no signal"), Schwab read
  a prevailing direction and scored 20 (absorption+location). Tiny
  price differences near the 21-EMA/VWAP flipped the direction call.
- **14:30–15:00:** volume input fired on one feed but not the other.

## Verdict

**No improvement, no meaningful difference.** The gauge tells the same
story on both feeds: same max score (35), same band structure, same
unreachable >60 band. The divergences are feed noise in the volume
proxy and EMA/VWAP direction flips at the margin — exactly what you'd
expect from two feeds with identical prices and ~12% different volume.
The calibration verdict (fails as a near-term structural-reversal
predictor; useful as exhaustion context) stands unchanged.

The real win from the Schwab connection is **latency** (real-time vs
~15-min delayed), not score quality. Nothing in this session suggests
re-deriving bands or weights.

**Artifacts:** `schwab_vs_yahoo.py`, `schwab_vs_yahoo_bars.csv`
(bar-level log, 48 rows).
