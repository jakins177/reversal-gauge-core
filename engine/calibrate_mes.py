"""MESZ26 calibration run for the reversal gauge (frozen v1 weights).

Same methodology as calibrate.py (SPY): score every eligible bar with the
FROZEN a priori weights, report P(structural reversal within 6 bars |
score band). "Reversal" is structural (engine.reversal_within logic): a
close beyond the last counter-trend swing extreme that holds (no reclaim
within 2 bars). Bars only scored when >= 6 future bars exist in the SAME
session. No weight was adjusted to improve these numbers.

Data: hidden_files/topstepx/mesz26_5m_full.csv (TopstepX 5-min bars,
2026-07-23 -> 2026-09-21, UTC).
"""
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import engine
from adapters import resolve

DATA = os.path.join(HERE, "..", "topstepx", "mesz26_5m_full.csv")
HIST = 400      # trailing bars handed to the scorer (matches live pulls)
WARMUP = 60     # bars skipped per session (ATR/RSI/EMA/volume warmup)
HORIZON = 6
K = 2


def load():
    df = pd.read_csv(DATA, parse_dates=["timestamp"])
    df = df.set_index("timestamp")
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df = df.rename(columns={"open": "Open", "high": "High", "low": "Low",
                            "close": "Close", "volume": "Volume"})
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df


def adx30_ctx(hist5: pd.DataFrame):
    """30-min ADX now vs median-of-history proxy, from 5-min bars."""
    if len(hist5) < 60:
        return None, None
    agg = {"Open": "first", "High": "max", "Low": "min",
           "Close": "last", "Volume": "sum"}
    h30 = hist5.resample("30min").agg(agg).dropna()
    if len(h30) < 30:
        return None, None
    ax, _, _ = engine.adx(h30, 14)
    ax = ax.dropna()
    if not len(ax):
        return None, None
    return float(ax.iloc[-1]), float(ax.median())


def reversal_within_fast(df, at_iloc, direction, sh_arr, sl_arr,
                         closes, lows, highs, horizon=HORIZON):
    """Equivalent to engine.reversal_within, with precomputed swings."""
    if direction not in ("uptrend", "downtrend"):
        return False
    if direction == "uptrend":
        cand = sl_arr[sl_arr <= at_iloc]
        if len(cand) == 0:
            return False
        extreme = lows[cand[-1]]
        for j in range(at_iloc + 1, min(at_iloc + 1 + horizon, len(df))):
            if closes[j] < extreme:
                if all(closes[m] < extreme
                       for m in range(j + 1, min(j + 3, len(df)))):
                    return True
                break
    else:
        cand = sh_arr[sh_arr <= at_iloc]
        if len(cand) == 0:
            return False
        extreme = highs[cand[-1]]
        for j in range(at_iloc + 1, min(at_iloc + 1 + horizon, len(df))):
            if closes[j] > extreme:
                if all(closes[m] > extreme
                       for m in range(j + 1, min(j + 3, len(df)))):
                    return True
                break
    return False


def main():
    t0 = time.time()
    df = load()
    spec = resolve("MES")
    sessions = engine.split_sessions(df, spec)
    labels = sorted(sessions)
    print(f"bars: {len(df)} | sessions: {len(labels)}", flush=True)

    closes = df["Close"].to_numpy()
    lows = df["Low"].to_numpy()
    highs = df["High"].to_numpy()
    sh, sl = engine.fractal_swings(df, k=K)
    sh_arr = np.array(sh)
    sl_arr = np.array(sl)

    rows = []
    for li, lab in enumerate(labels):
        sess = sessions[lab]
        for pos in range(WARMUP, len(sess)):
            gi = df.index.get_loc(sess.index[pos])
            if pos + HORIZON >= len(sess):
                continue
            hist = df.iloc[max(0, gi - HIST + 1):gi + 1]
            a_now, a_med = adx30_ctx(hist)
            res = engine.score(hist, spec, df30=None)
            if a_now is not None:
                dt = engine.day_type(hist, spec, res["levels"], a_now, a_med)
                res["day_type"] = dt["label"]
                res["day_type_detail"] = dt
                if res["direction"] != "chop":
                    _, cur = engine.current_session(hist, spec)
                    pts, why = engine.input_rsi_gated(
                        cur, res["direction"], res["levels"], dt)
                    comps = [c for c in res["components"]
                             if c["input"] != "rsi_gated"]
                    if pts != 0 or why:
                        comps.append({"input": "rsi_gated", "points": pts,
                                      "why": why})
                    res["components"] = comps
                    total = sum(c["points"] for c in comps)
                    total = int(round(max(0, min(100, total))))
                    res["score"] = total
                    res["band"] = ("high risk" if total > 60 else
                                   "caution" if total >= 30
                                   else "trend healthy")
            if res["direction"] == "chop":
                continue
            rev = reversal_within_fast(df, gi, res["direction"], sh_arr,
                                       sl_arr, closes, lows, highs)
            rows.append({"score": res["score"], "band": res["band"],
                         "direction": res["direction"],
                         "day_type": res["day_type"],
                         "reversal": rev,
                         "fired": "+".join(sorted(
                             c["input"] for c in res["components"]
                             if c["points"] > 0)) or "-"})
        print(f"  {lab}: done ({time.time()-t0:.0f}s)", flush=True)

    r = pd.DataFrame(rows)
    base = r["reversal"].mean()
    print(f"\nscored bars: {len(r)} | sessions: {len(labels)}")
    print(f"BASE RATE P(reversal within {HORIZON} bars) = "
          f"{base:.3f} ({int(r['reversal'].sum())}/{len(r)})")
    print("\nband        n      P(rev)   share")
    for b in ["trend healthy", "caution", "high risk"]:
        s = r[r["band"] == b]
        if len(s):
            print(f"{b:13s} {len(s):5d}   {s['reversal'].mean():.3f}   "
                  f"{len(s) / len(r):.1%}")
        else:
            print(f"{b:13s}     0      -        -")
    hi = r[r["band"] == "high risk"]
    if len(hi):
        print(f"\n>60 false-positive rate (fired, no reversal): "
              f"{1 - hi['reversal'].mean():.1%}  (n={len(hi)})")
    print("\nmean score | reversal:", round(r[r.reversal]["score"].mean(), 1),
          "| no reversal:", round(r[~r.reversal]["score"].mean(), 1))
    print("\nmax score:", r["score"].max())
    print("\ndirection mix among scored bars:")
    print(r["direction"].value_counts(normalize=True).round(3).to_string())
    print("\nday-type mix among scored bars:")
    print(r["day_type"].value_counts(normalize=True).round(3).to_string())
    print("\nP(rev) by day-type x band:")
    print(r.groupby(["day_type", "band"])["reversal"]
          .agg(["mean", "count"]).round(3).to_string())
    if len(hi):
        print("\nmost common firing input sets (>60 band):")
        print(hi["fired"].value_counts().head(8).to_string())
    r.to_csv(os.path.join(HERE, "calibration_bars_mes.csv"), index=False)
    print("\nbar-level log -> calibration_bars_mes.csv")


if __name__ == "__main__":
    main()
