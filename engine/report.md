# Reversal-Risk Gauge v1 — Report

**Built:** 2026-09-19. **Status:** on-demand chat tool (`gauge SPY` / `gauge MES` / `gauge <TICKER>`).
**Skill:** `~/workspace/skills/reversal-gauge/` · **Engine:** `hidden_files/reversal_gauge/`
(`adapters.py`, `engine.py`, `calibrate.py`, `calibration_bars.csv`).

## What it is

A ticker-parameterized 0–100 gauge of trend exhaustion on lower
timeframes (5-min primary). It scores reversal risk for the
**prevailing** intraday move and reports which direction that is. It is
**symmetric** (works for uptrends and downtrends) and **never issues an
entry** — the 30–60 band means tighten stops / don't add, the >60 band
means protect/exit and optionally arm a *confirmation* trigger (a
structural break plus a failed retest). Blind fading is explicitly out
of scope: the lab's backtests (G, C, PDH sweep) all died calling the
turn early.

## Frozen scoring spec (v1)

**Prevailing direction** (per session): uptrend if close > 21-EMA,
21-EMA rising (3-bar slope), and close above session VWAP; mirror for
downtrend; else chop (gauge returns "no signal").

**Day-type** (live, no lookahead), three components averaged to [0,1]:
1. `dir_share` — by ~10:30 (session tz), |close−open| as a share of the
   day's range so far;
2. `adx_regime` — 30-min ADX(14) vs its ~20-day median, mapped to [0,1];
3. `vwap_side` — share of session closes on the dominant side of VWAP.
Label: ≥0.60 trend, ≤0.40 range, else mixed.

**Known levels** (tolerance 0.05%): session VWAP ±1/±2 session-ATR
bands, prior-session high/low, overnight high/low (futures only —
SPY's Yahoo pull has no premarket), opening-range edge (first 15 min),
round numbers (SPY $1 grid, MES 25-pt grid).

**The six inputs** (weights a priori, documented, NOT tuned):

| # | Input | Pts | Rule |
|---|-------|-----|------|
| 1 | ATR-expansion climax | +25 | Extreme bar within last 3 bars, range ≥ 2× ATR(14), close in outer third toward the extreme |
| 2 | Volume spike + shrinking follow-through | +15 | Extreme-bar volume ≥ 2× 20-bar median, later bars' volume lower and no new extreme. Adapter-ready hook: `flow_fn` returning `{"delta_z": …}` can substitute real order-flow delta (documented slot, unused in v1 — no retail feed exposes it) |
| 3 | Rejection at a level | +15 | Wick ≥ 40% of bar range at a known level, close back inside (≥25% of range off the extreme) |
| 4 | Absorption | +10 | ≥ 2 tests of the same level in 12 bars, closes fail to extend beyond it |
| 5 | Location | +10 | 20-bar extreme sits on a known level |
| 6 | RSI(14) extreme, **day-type gated** | +15 / −10 / +5 | Range day + (divergence or rejection at level) → +15. **Trend day → −10** (trend healthy, do not fade). Mixed → +5. No usable day-type → 0. Never scores alone |

Score = sum, clamped to [0,100]. Max 90, min −10 before clamping.

**Bands:** <30 trend healthy · 30–60 caution (tighten stops, don't add) ·
>60 high risk (protect/exit, arm confirmation trigger — never a fade entry).

**Structural reversal definition** (shared by the gauge narrative and
calibration): a close beyond the last counter-trend swing extreme
(fractal k=2) that holds — no reclaim within 2 bars. Candle color alone
is not a reversal.

## Session awareness

- **SPY:** RTH 9:30–16:00 ET. No overnight levels (no premarket in feed).
- **MES (`MES=F`):** globex day session 08:30–15:15 CT (VWAP, OR
  08:30–08:45 CT) vs overnight 17:00–08:30 CT; the session the last bar
  falls in is scored, with the other leg's high/low as levels.
- **Any other ticker:** treated as an RTH equity (SPY-style session) —
  the expansion path for "select any ticker".

## Data & adapters

v1: Yahoo Finance (~15 min delayed), no auth. The engine only consumes
the `BarSource` interface (tz-aware OHLCV). `adapters.py` carries
**documented, unimplemented stubs** for the two live upgrades:

- **SchwabAdapter** — real-time SPY bars/volume for account holders.
  Needs Josh to register a developer app (OAuth client ID/secret via
  Secure Vault), then `GET /marketdata/v1/pricehistory`. Does not
  provide true order-flow delta.
- **TopstepXAdapter** — real-time MES via SignalR hub or
  `POST /api/History/retrieveBars`. Needs Josh's API key + secret via
  Secure Vault (never in chat). History capped ~2 months (fine — the
  gauge needs days). Does not expose delta/CVD/footprint aggregates.

Swapping feeds = implementing one adapter class + changing
`default_source()`; the engine does not change.

## Calibration (frozen window, honest)

Walked the cached SPY 5-min window (2026-06-17 → 2026-09-11, 4680 bars,
md5-verified identical across backtest dirs). Every eligible bar scored
with the frozen weights; label = structural reversal within the next 6
bars (bars scored only when 6 future bars exist in the same session).
No weight was adjusted to improve these numbers.

| Band | n | P(reversal within 6 bars) | Share of scored bars |
|------|---|---------------------------|----------------------|
| <30 trend healthy | 448 | **0.083** | 84.2% |
| 30–60 caution | 84 | **0.048** | 15.8% |
| >60 high risk | 0 | n/a — **never fired** | 0% |

- Scored bars: 532 (bars skipped: <60 bars warmup, <6 future bars left in
  session, or chop direction — the gauge only scores with-trend
  exhaustion). 60 sessions.
- Base rate: 0.077 (41/532).
- Mean score | reversal = 17.9 vs | no reversal = 19.2 — **no
  discrimination** (slightly inverse, within noise).
- Score distribution: mean 19.1, std 9.6, **max 40** in 60 sessions.
  The most loaded read observed was
  absorption+location+rejection+rsi_gated = 40.
- P(rev) by day-type: mixed 0.098 (n=264), range 0.053 (n=151),
  trend 0.043 (n=117).

### Calibration verdict (read this before trusting the dial)

Tested against its hardest claim — predicting a *structural* reversal
within 6 bars — **v1 fails**: it does not discriminate, and the >60
"high risk" band is unreachable with the frozen weights (max observed
40; climax/volume almost never co-fire with the rest on 5-min SPY).
The 30–60 band even runs slightly *inverse* to the label at this
horizon.

What this does and doesn't mean:

- It does **not** mean the inputs are meaningless. The live reads are
  sensible exhaustion context (e.g. 2026-09-18 SPY scored 35 CAUTION
  pushing into the opening-range high on 7 tests with no extension and
  a 61% upper-wick rejection — exactly the "don't add / tighten stops"
  situation the band describes).
- It **does** mean the a priori band thresholds don't match the
  empirical score distribution, and "reversal within 30 minutes" is a
  stricter claim than exhaustion warrants — exhaustion often resolves
  into chop, not a clean structural break+hold.
- **v2 fix (not implemented):** re-derive bands from the observed
  distribution (weights stay frozen) — e.g. high-risk ≈ top decile of
  scored bars — and/or test longer horizons (12–24 bars). Do not
  re-tune weights against this window.

## Known limitations

1. **Yahoo delay (~15 min):** on 5-min bars the read is 3 bars stale.
   Fine for regime awareness, not for scalping the turn.
2. **Proxy volume:** no true delta/CVD/footprint; the flow hook is ready
   but empty.
3. **SPY has no overnight levels** in this feed (RTH only).
4. **MES via continuous contract:** roll gaps can distort multi-day
   level reads; session-local scoring is unaffected.
5. **Low base rate:** true intraday reversals are rare; expect most
   >60 fires to be "not yet" rather than wrong — the gauge's job is
   mostly to say "not yet" and occasionally to say "defend".
6. **Calibration is in-sample** on the lab's SPY window and uses a
   20-day ADX-median proxy built from the same 5-min data. Treat the
   numbers as a sanity check of the construction, not as an edge claim.

## v2 candidates (not built)

- "Ping me only when >60" scheduled alert (Josh-style: silent otherwise).
- Wire Schwab real-time (equities) / TopstepX real-time (futures).
- Real order-flow adapter if a delta/CVD feed becomes available.
- Per-ticker calibration once more history exists.
