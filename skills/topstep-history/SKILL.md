---
name: "topstep_history"
description: "Pull futures candle history (MESU26 etc.) from Josh's TopstepX account. Trigger on 'pull MES history', 'Topstep bars', 'MESU26 candles', or when MES data is needed for the reversal gauge."
---

# Topstep History

## Purpose
Fetch historical futures candles from Josh's TopstepX (ProjectX Gateway) account for research, backtests, and the reversal gauge's MES leg.

## Tooling
CLI: `~/workspace/skills/topstep-history/bin/topstepx.py`

```bash
export TOPSTEPX_USERNAME=... TOPSTEPX_API_KEY=...   # transient, session only — never persist
topstepx.py auth-check
topstepx.py search --text MESU26
topstepx.py bars --symbol MESU26 --timeframe 5m --start 2026-07-01 --end 2026-09-20 --out mesu26_5m.csv
topstepx.py bars --symbol MESU26 --timeframe 5m --days 3 --out recent.csv
```

Timeframes: `1m 5m 15m 1h 1d`. Output CSV columns: `timestamp,open,high,low,close,volume` (timestamps UTC ISO).

## Auth
The Secure Vault cannot hold this credential (TopstepX login needs username + API key together in one request body; the vault deposits one secret). Credentials arrive via `TOPSTEPX_USERNAME` / `TOPSTEPX_API_KEY` environment variables only, supplied by Josh in chat for transient use. **Never** write them to files, memory, logs, URLs, or tool arguments that get recorded. Never ask Josh to re-paste unless the login actually fails.

A 401 or Topstep `success:false` on loginKey is a credential question — ask Josh for a fresh key. Any other endpoint failing with `success:false` is a request-shape question first (enums are integers; check the `success` boolean, never just the HTTP status).

## Operating Rules
1. Authenticated requests go only to `api.topstepx.com`. The CLI enforces this.
2. Rate limits: 50 requests / 30 s on `retrieveBars`, 200 / 60 s elsewhere. The CLI sleeps between paginated calls.
3. `retrieveBars` history depth is limited (~2 months on TopstepX 5-min). Chunk large ranges; the CLI paginates automatically.
4. Contract symbols roll (MESU26 → MESZ26 ...). Use `search` to resolve the current front contract before a pull.
5. `--live` defaults to false (sim contracts). Match the account type to the research question.
6. Do not create scheduled jobs that depend on these credentials without Josh's explicit approval.
