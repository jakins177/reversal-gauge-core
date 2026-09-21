---
name: "reversal-gauge"
description: "On-demand reversal-risk gauge (0-100) for SPY, MES, or any ticker on lower timeframes. Trigger on phrases like 'gauge SPY', 'gauge MES', 'reversal gauge AAPL'. Scores trend exhaustion from ATR expansion, volume, rejection wicks, absorption, known levels, and day-type-gated RSI. Risk/exit context only -- never issues entries."
metadata: { "includeInPrompt": true }
---

# Reversal Gauge

## Purpose
Answer "is this trend about to reverse" with a data-grounded 0-100
reversal-risk read on 5-min bars. It scores exhaustion of the
*prevailing* move (reported as uptrend/downtrend): ATR-expansion
climax, volume spike with shrinking follow-through, rejection wicks
at known levels, absorption, location on real levels
(VWAP bands, prior-day high/low, overnight high/low, opening-range
edge, round numbers), and RSI extremes **gated by day-type** --
overbought/oversold only raises risk on range/chop days; on trend
days it lowers the score (trend healthy, do not fade).

## Tooling
Run the helper with the venv python (it carries yfinance; system
python is PEP-668 locked):

```
~/workspace/.venv-mesvwap/bin/python3 ~/workspace/skills/reversal-gauge/bin/gauge.py [TICKER]
```

- `SPY` (default): SPY ETF, RTH session only.
- `MES`: MES futures, globex day/overnight sessions (TopstepX
  real-time when credentials are present, Yahoo `MES=F` ~15 min
  delayed fallback).
- Any other ticker: treated as an RTH equity under its own symbol.

## Auth
Schwab gateway (Josh's own read-only proxy, `custom.schwab-gateway`
credential in the Secure Vault; skill `schwab-gateway/`) supplies
real-time bars for equities -- tried first, Yahoo (~15 min delayed) as
fallback. MES futures use the TopstepX adapter (`adapters.py`,
real-time 5-min history via the `topstep-history` skill CLI). The
Secure Vault cannot hold the TopstepX pair (login needs username +
API key together in one request body; the vault deposits one secret),
so the credentials arrive as `TOPSTEPX_USERNAME` / `TOPSTEPX_API_KEY`
environment variables, supplied by Josh in chat for transient use --
never written to files, memory, or logs. When they are absent the MES
leg falls back to Yahoo `MES=F` (~15 min delayed) automatically.

## Workflow
1. Run the helper with the requested ticker.
2. Report the output to the user as-is, tightened for chat. Keep the
   VERDICT line verbatim.
3. Bands: <30 trend healthy, 30-60 caution (tighten stops, don't add),
   >60 high risk (protect/exit, arm a confirmation trigger -- a
   structural break plus a failed retest). The gauge never issues a
   fade entry.
4. If the helper reports no data, say the market is likely closed or
   the feed failed -- do not invent a read.

## Operating Rules
- Never present Yahoo-fed reads as real-time: always note the ~15 min
  delay. TopstepX/Schwab-gateway reads are real-time; the output names
  its feed.
- Context only, no trade recommendations. The verdict describes
  reversal risk; it is not an entry signal.
- MES prices via the TopstepX front-month contract (roll-aware; the
  CLI resolves the current front month). Yahoo-fallback reads use the
  continuous contract; point diffs approximate front-month P&L but
  roll gaps can distort multi-day reads.
- Candle color alone is not a reversal. The gauge's structural
  definition: a close beyond the last counter-trend swing extreme
  that holds (no reclaim within 2 bars).
- Do not run on a schedule unless the user asks; this is on-demand.
  (A "ping me only when >60" alert is the natural v2.)
- Calibration honesty: on the 60-session SPY window the score never
  exceeded 40 and did not discriminate 6-bar structural reversals --
  treat this as exhaustion context for exits/protection, not as a
  reversal predictor. Details in the engine report.
