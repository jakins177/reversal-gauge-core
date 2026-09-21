"""Calibration harness for the reversal gauge (honest, not tuned).

Walks the cached SPY 5-min window (2026-06-17 -> 2026-09-11, 4680 bars),
scores every eligible bar with the FROZEN a priori weights, and reports
P(structural reversal within next 6 bars | score band).

"Reversal" is structural (engine.reversal_within): a close beyond the
last counter-trend swing extreme that holds (no reclaim within 2 bars).
Bars are only scored when >= 6 future bars exist in the SAME session,
so the label is a clean intraday read. No weight was adjusted to
improve these numbers.
"""
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import engine
from adapters import resolve

DATA = os.path.join(HERE, "..", "backtest_failed_break", "data",
                    "spy_5m_raw.csv")
HIST = 400      # trailing bars handed to the scorer (matches live pulls)
WARMUP = 60     # bars skipped per session (ATR/RSI/EMA/volume warmup)
HORIZON = 6


def load():
    df = pd.read_csv(DATA, parse_dates=["Datetime"], index_col="Datetime")
    if df.index.tz is None:
        df.index = df.index.tz_localize("America/New_York")
    return df[~df.index.duplicated(keep="last")].sort_index()


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


def main():
    df = load()
    spec = resolve("SPY")
    sessions = engine.split_sessions(df, spec)
    labels = sorted(sessions)

    rows = []
    for li, lab in enumerate(labels):
        sess = sessions[lab]
        for pos in range(WARMUP, len(sess)):
            gi = df.index.get_loc(sess.index[pos])
            # need HORIZON future bars in the SAME session for a clean label
            if pos + HORIZON >= len(sess):
                continue
            hist = df.iloc[max(0, gi - HIST + 1):gi + 1]
            a_now, a_med = adx30_ctx(hist)
            res = engine.score(hist, spec, df30=None)
            # inject the 30m ADX context the live CLI computes from Yahoo
            if a_now is not None:
                dt = engine.day_type(hist, spec, res["levels"], a_now, a_med)
                res["day_type"] = dt["label"]
                res["day_type_detail"] = dt
                # re-score the RSI gate only if day-type changed the read
                # (weights frozen -- this just completes the input honestly)
                if res["direction"] != "chop":
                    _, cur = engine.current_session(hist, spec)
                    pts, why = engine.input_rsi_gated(
                        cur, res["direction"], res["levels"], dt)
                    # replace the rsi_gated component
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
            rev = engine.reversal_within(df, gi, res["direction"],
                                         horizon=HORIZON)
            rows.append({"score": res["score"], "band": res["band"],
                         "direction": res["direction"],
                         "day_type": res["day_type"],
                         "reversal": rev,
                         "fired": "+".join(sorted(
                             c["input"] for c in res["components"]
                             if c["points"] > 0)) or "-"})

    r = pd.DataFrame(rows)
    print(f"scored bars: {len(r)} | sessions: {len(labels)}")
    print(f"sessions scored: {r.shape[0]}")
    base = r["reversal"].mean()
    print(f"\nBASE RATE P(reversal within {HORIZON} bars) = "
          f"{base:.3f} ({int(r['reversal'].sum())}/{len(r)})")
    print("\nband        n      P(rev)   share")
    for b in ["trend healthy", "caution", "high risk"]:
        s = r[r["band"] == b]
        if len(s):
            print(f"{b:13s} {len(s):5d}   {s['reversal'].mean():.3f}   "
                  f"{len(s) / len(r):.1%}")
    hi = r[r["band"] == "high risk"]
    if len(hi):
        print(f"\n>60 false-positive rate (fired, no reversal): "
              f"{1 - hi['reversal'].mean():.1%}  (n={len(hi)})")
    print("\nmean score | reversal:", round(r[r.reversal]["score"].mean(), 1),
          "| no reversal:", round(r[~r.reversal]["score"].mean(), 1))
    print("\nday-type mix among scored bars:")
    print(r["day_type"].value_counts(normalize=True).round(3).to_string())
    print("\nP(rev) by day-type x band:")
    print(r.groupby(["day_type", "band"])["reversal"]
          .agg(["mean", "count"]).round(3).to_string())
    print("\nmost common firing input sets (>60 band):")
    print(hi["fired"].value_counts().head(8).to_string())
    r.to_csv(os.path.join(HERE, "calibration_bars.csv"), index=False)
    print("\nbar-level log -> calibration_bars.csv")


if __name__ == "__main__":
    main()
