# Reversal Gauge — Scoring Core

The quantitative core behind the Reversal Gauge: a 0–100 trend-exhaustion
scorer for 5-minute bars on SPY, MES, or any ticker. It measures how
exhausted the **current move** is — exit and protection context, never an
entry or fade signal.

This repo holds the frozen scoring engine, the data adapters, the
calibration evidence, and the operator skills. It does **not** include the
dashboard web app (UI + hosted credential plumbing live separately).

## The frozen spec (v1)

- **Trend direction gate: 6 five-minute bars (30 min).** Direction rule:
  close vs EMA21, EMA21 slope vs two bars earlier, close vs session VWAP.
  Uptrend / downtrend / chop.
- **Exhaustion gate: 15 session bars.** Bars 6–14 are "Warming up"
  (trend shown, no exhaustion score yet).
- **Scored factors:** ATR climax / wide-range expansion; relative-volume
  spike with fading follow-through; rejection wick at a known level;
  absorption / repeated tests without extension; location vs VWAP, prior
  highs/lows, opening range, overnight levels, round numbers; RSI
  extremes gated by session shape and confirmation (see `docs/SPEC.md`).
- **Bands:** <30 trend healthy · 30–60 caution (don't add, tighten
  protection) · >60 high risk (protect, wait for structural-break-plus-
  failed-retest confirmation).

Weights are frozen. Full spec in `docs/SPEC.md`.

## Calibration honesty

Tested as a *structural-reversal predictor* (reversal within 6 bars),
the gauge **failed** on both instruments:

| | SPY | MES |
|---|---|---|
| Scored bars | 532 | 2,659 (94 sessions) |
| Base reversal rate | 7.7% | 14.3% |
| Score <30 | 8.3% (n=448) | 14.8% (n=2,411) |
| Score 30–60 | 4.8% (n=84) | 9.3% (n=248) |
| Score >60 | never fired (max 40) | never fired (max 50) |

A mechanical gauge-based exit rule (threshold 35) was also tested and
earned no forward trial: +0.270R over 11 trades on one setup, +0.001R
over 66 on another. Details in `engine/exit_study/report.md`.

Verdict: discretionary exhaustion/exit context only.

## Layout

- `engine/` — `engine.py` (frozen v1 scorer), `adapters.py` (Schwab /
  TopstepX / Yahoo data adapters), calibration scripts and evidence,
  `exit_study/`
- `skills/reversal-gauge/` — operator skill + `bin/gauge.py` CLI
- `skills/topstep-history/` — TopstepX history CLI (`bin/topstepx.py`)
- `docs/SPEC.md` — the complete frozen specification

## Usage

```bash
# score the latest read (needs the venv python carrying yfinance)
~/workspace/.venv-mesvwap/bin/python3 skills/reversal-gauge/bin/gauge.py SPY
~/workspace/.venv-mesvwap/bin/python3 skills/reversal-gauge/bin/gauge.py MES
```

## Credentials

Nothing in this repo authenticates on its own:

- Equities: a read-only Schwab gateway (personal proxy, bearer credential
  held privately).
- MES futures: TopstepX via `TOPSTEPX_USERNAME` / `TOPSTEPX_API_KEY`
  **environment variables only** — transient, never written to files.
  Without them the MES leg falls back to delayed Yahoo `MES=F`.
- Never commit credentials, tokens, or keys to this repo.

## License

MIT — see `LICENSE`.
