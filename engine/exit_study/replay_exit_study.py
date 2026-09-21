"""Exit-timing validation study: gauge-based exits vs fixed 2R target.

Replays Setup C v3 (11 trades) and Setup D (66 trades) SPY 5-min trade logs
bar-by-bar with frozen gauge v1 scores. Scoring block is identical to
../calibrate.py (including the 30-min ADX day-type injection), except bars
are scored whenever the live gauge would score them (no 6-future-bar
requirement — the live tool scores the last bar of the session too).

Exit modes per trade:
  fixed  - logged original outcome (verification target)
  gauge  - exit at close of first bar with score >= THRESH (35, frozen) AND
           gauge direction == trade direction; else fall back to logged outcome
  hybrid - per bar: stop-touch -> -1R; target-touch -> +2R; gauge trigger at
           close -> exit at close; else fall back to logged outcome

Run: python3 replay_exit_study.py
"""
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.dirname(HERE)
sys.path.insert(0, PARENT)
import engine
from adapters import resolve

BASE = os.path.expanduser(
    "~/workspace/goals/get-consistently-profitable-trading-futures-and-stocks/hidden_files")
DATA = os.path.join(BASE, "backtest_failed_break", "data", "spy_5m_raw.csv")
HIST = 400
WARMUP = 60
THRESH = 35          # frozen: 90th pct of calibration scored bars
RT_COST_USD = 2.0    # $2 round trip, lab cost model

DIRMAP = {"long": "uptrend", "short": "downtrend"}


def load_bars():
    df = pd.read_csv(DATA, parse_dates=["Datetime"], index_col="Datetime")
    if df.index.tz is None:
        df.index = df.index.tz_localize("America/New_York")
    return df[~df.index.duplicated(keep="last")].sort_index()


def adx30_ctx(hist5):
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


def score_bar(df, spec, gi):
    """Score the bar at global iloc gi, calibration-identical (ADX injected)."""
    hist = df.iloc[max(0, gi - HIST + 1):gi + 1]
    a_now, a_med = adx30_ctx(hist)
    res = engine.score(hist, spec, df30=None)
    if a_now is not None:
        dt = engine.day_type(hist, spec, res["levels"], a_now, a_med)
        res["day_type"] = dt["label"]
        res["day_type_detail"] = dt
        if res["direction"] != "chop":
            _, cur = engine.current_session(hist, spec)
            pts, why = engine.input_rsi_gated(cur, res["direction"],
                                             res["levels"], dt)
            comps = [c for c in res["components"] if c["input"] != "rsi_gated"]
            if pts != 0 or why:
                comps.append({"input": "rsi_gated", "points": pts, "why": why})
            res["components"] = comps
            total = sum(c["points"] for c in comps)
            total = int(round(max(0, min(100, total))))
            res["score"] = total
            res["band"] = ("high risk" if total > 60 else
                           "caution" if total >= 30 else "trend healthy")
    return res


def precompute_scores(df, spec):
    """Score every bar of every session (WARMUP skipped, like calibration)."""
    sessions = engine.split_sessions(df, spec)
    scores = {}
    for lab in sorted(sessions):
        sess = sessions[lab]
        for pos in range(WARMUP, len(sess)):
            gi = df.index.get_loc(sess.index[pos])
            res = score_bar(df, spec, gi)
            scores[sess.index[pos]] = (res["score"], res["direction"])
    return scores


def bar_at(df, session_date, hhmm):
    ts = pd.Timestamp(f"{session_date} {hhmm}").tz_localize("America/New_York")
    return df.index.get_loc(ts)


def simulate(tr, df, scores, thresh):
    """Return dict with fixed/gauge/hybrid net_r and diagnostics."""
    d = tr["direction"]
    entry, stop, target = tr["entry"], tr["stop"], tr["target"]
    risk = abs(entry - stop)
    cost_r = RT_COST_USD / tr["risk_usd"]
    want_dir = DIRMAP[d]

    def gross(px):
        return (px - entry) / risk if d == "long" else (entry - px) / risk

    sess = tr["session"]
    i0 = bar_at(df, sess, tr["entry_time"])
    i1 = bar_at(df, sess, tr["exit_time"])
    # Time exits execute at the OPEN of the exit bar (verified: logged exit
    # price == exit-bar open), so the exit bar's range is not tradeable.
    # Stop/target exits are detected by range touch, so include the exit bar.
    is_time_exit = tr["exit_reason"] not in ("target", "stop")
    bars = df.iloc[i0:i1] if is_time_exit else df.iloc[i0:i1 + 1]

    def stop_hit(b):
        return b["Low"] <= stop if d == "long" else b["High"] >= stop

    def target_hit(b):
        return b["High"] >= target if d == "long" else b["Low"] <= target

    out = {"fixed_net_r": tr["net_r"], "orig_exit_reason": tr["exit_reason"],
           "orig_gross_r": tr["gross_r"], "entry_time": tr["entry_time"],
           "direction": d, "risk_usd": tr["risk_usd"]}

    # --- gauge-only: first trigger bar at/after entry; stop checked first ---
    g = None
    for ts, b in bars.iterrows():
        sc, gd = scores.get(ts, (0, "chop"))
        if stop_hit(b):
            break  # stop would have ended the trade; fall back to original
        if sc >= thresh and gd == want_dir:
            g = {"exit_ts": ts, "exit_px": b["Close"], "score": sc,
                 "bars_held": len(df.iloc[i0:df.index.get_loc(ts) + 1]) - 1}
            break
    if g:
        g["gross_r"] = gross(g["exit_px"])
        g["net_r"] = g["gross_r"] - cost_r
    out["gauge"] = g

    # --- hybrid ---
    h = None
    for ts, b in bars.iterrows():
        if stop_hit(b):
            h = {"mode": "stop", "gross_r": -1.0,
                 "net_r": -1.0 - cost_r, "exit_ts": ts}
            break
        if target_hit(b):
            h = {"mode": "target", "gross_r": 2.0,
                 "net_r": 2.0 - cost_r, "exit_ts": ts}
            break
        sc, gd = scores.get(ts, (0, "chop"))
        if sc >= thresh and gd == want_dir:
            gr = gross(b["Close"])
            h = {"mode": "gauge", "gross_r": gr, "net_r": gr - cost_r,
                 "exit_ts": ts, "score": sc}
            break
    if h is None:  # e.g. time exit reached with no trigger
        h = {"mode": "fallback", "gross_r": tr["gross_r"], "net_r": tr["net_r"],
             "exit_ts": bars.index[-1]}
    out["hybrid"] = h
    out["gauge_net_r"] = g["net_r"] if g else tr["net_r"]
    out["gauge_fired"] = g is not None
    return out


def summarize(name, trades, sims):
    n = len(sims)
    def agg(key):
        return sum(s[key] for s in sims)
    rows = {}
    for mode, key in [("fixed", "fixed_net_r"), ("gauge", "gauge_net_r")]:
        vals = [s[key] for s in sims]
        wins = sum(1 for v in vals if v > 0)
        rows[mode] = {"net_r": sum(vals), "exp": sum(vals) / n,
                      "win_rate": wins / n, "n": n}
    hvals = [s["hybrid"]["net_r"] for s in sims]
    rows["hybrid"] = {"net_r": sum(hvals), "exp": sum(hvals) / n,
                      "win_rate": sum(1 for v in hvals if v > 0) / n, "n": n}
    hmodes = pd.Series([s["hybrid"]["mode"] for s in sims]).value_counts()
    rows["hybrid_modes"] = hmodes.to_dict()
    rows["gauge_fired_n"] = sum(1 for s in sims if s["gauge_fired"])

    # decomposition of gauge exits vs original outcome
    fired = [s for s in sims if s["gauge_fired"]]
    dec = {"n_fired": len(fired), "details": []}
    saved = left = 0.0
    n_cut_winners = n_rescued = n_other = 0
    for s in fired:
        gr = s["gauge"]["gross_r"]
        og = s["orig_gross_r"]
        if s["orig_exit_reason"] == "target":
            delta = 2.0 - gr          # R given up (positive = left on table)
            left += delta
            n_cut_winners += 1
            kind = "cut_winner"
        elif s["orig_exit_reason"] == "stop":
            delta = gr - (-1.0)       # R saved (positive = rescued)
            saved += delta
            n_rescued += 1
            kind = "rescued_loser"
        else:
            delta = gr - og
            if delta >= 0:
                saved += delta
            else:
                left -= delta
            n_other += 1
            kind = "time_exit_delta"
        dec["details"].append({"entry_time": s["entry_time"],
                               "direction": s["direction"],
                               "orig": s["orig_exit_reason"],
                               "gauge_gross_r": round(gr, 3),
                               "delta_r": round(delta, 3), "kind": kind})
    dec["r_left_on_table"] = round(left, 3)
    dec["r_saved"] = round(saved, 3)
    dec["n_cut_winners"] = n_cut_winners
    dec["n_rescued_losers"] = n_rescued
    dec["n_other"] = n_other
    rows["decomposition"] = dec
    rows["name"] = name
    return rows


def main():
    import pickle
    df = load_bars()
    spec = resolve("SPY")
    cache = os.path.join(HERE, "scores_cache.pkl")
    if os.path.exists(cache):
        scores = pickle.load(open(cache, "rb"))
        print(f"loaded {len(scores)} cached scores", flush=True)
    else:
        print("precomputing gauge scores for all session bars...", flush=True)
        scores = precompute_scores(df, spec)
        pickle.dump(scores, open(cache, "wb"))
        print(f"scored {len(scores)} bars", flush=True)

    results = {}
    sets = {
        "setup_c_v3": os.path.join(BASE, "retest_v3_setup_c",
                                   "trades_2R_primary.csv"),
        "setup_d": os.path.join(BASE, "backtest_guide_ema_pb",
                                "trades_ema_pullback.csv"),
    }
    for name, path in sets.items():
        trades = pd.read_csv(path).to_dict("records")
        sims = [simulate(tr, df, scores, THRESH) for tr in trades]
        results[name] = summarize(name, trades, sims)
        # verification gate: hybrid@inf must reproduce logged sums
        sims_inf = [simulate(tr, df, scores, float("inf")) for tr in trades]
        rep = sum(s["hybrid"]["net_r"] for s in sims_inf)
        logged = sum(tr["net_r"] for tr in trades)
        exp_mode = {"target": "target", "stop": "stop"}.get(
            trades[0]["exit_reason"], "fallback")
        modes_ok = all(
            {"target": "target", "stop": "stop"}.get(t["exit_reason"],
                                                   "fallback") == s["hybrid"]["mode"]
            for t, s in zip(trades, sims_inf))
        results[name]["verify_inf_repro"] = {
            "logged": round(logged, 4), "reproduced": round(rep, 4),
            "modes_agree": modes_ok,
            "match": modes_ok and abs(logged - rep) < 0.005}

    # combined
    all_sims = []
    for name, path in sets.items():
        trades = pd.read_csv(path).to_dict("records")
        all_sims += [simulate(tr, df, scores, THRESH) for tr in trades]
    results["combined"] = summarize("combined", [], all_sims)

    with open(os.path.join(HERE, "results.json"), "w") as f:
        json.dump(results, f, indent=2, default=str)
    for name, r in results.items():
        print(f"== {name} ==")
        for m in ("fixed", "gauge", "hybrid"):
            print(f"  {m}: net_r={r[m]['net_r']:.3f} exp={r[m]['exp']:.3f} "
                  f"win={r[m]['win_rate']:.1%}")
        print(f"  hybrid_modes={r['hybrid_modes']} "
              f"gauge_fired={r['gauge_fired_n']}")
        d = r["decomposition"]
        print(f"  decomp: fired={d['n_fired']} cut_winners={d['n_cut_winners']} "
              f"rescued={d['n_rescued_losers']} left_on_table={d['r_left_on_table']} "
              f"saved={d['r_saved']}")
        if "verify_inf_repro" in r:
            print(f"  verify: {r['verify_inf_repro']}")


if __name__ == "__main__":
    main()
