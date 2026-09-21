"""Head-to-head: Schwab gateway vs Yahoo on the SAME SPY session.

The gateway only serves the latest RTH session (verified 2026-09-20:
period/startDate params ignored, always 78 bars = one session), so a
full 60-session recalibration on Schwab data is impossible. This script
does the honest alternative: score every bar of Friday 2026-09-18 with
the FROZEN v1 weights on each feed separately and report where the
feeds agree and where they diverge.

Usage: ~/workspace/.venv-mesvwap/bin/python3 schwab_vs_yahoo.py
"""
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import engine
from adapters import resolve, SchwabGatewayAdapter, YahooAdapter

WARMUP = 30  # engine.score needs >= 30 bars


def friday_session(df):
    """Keep only the 2026-09-18 RTH session."""
    day = pd.Timestamp("2026-09-18", tz="America/New_York")
    nxt = day + pd.Timedelta(days=1)
    return df[(df.index >= day) & (df.index < nxt)]


def main():
    spec = resolve("SPY")

    g_schwab = SchwabGatewayAdapter().get_intraday(spec, days=5)
    g_schwab = friday_session(g_schwab)
    print(f"Schwab Friday bars: {len(g_schwab)} "
          f"({g_schwab.index[0]} -> {g_schwab.index[-1]})")

    g_yahoo = YahooAdapter().get_intraday(spec, days=5)
    g_yahoo = friday_session(g_yahoo)
    print(f"Yahoo  Friday bars: {len(g_yahoo)} "
          f"({g_yahoo.index[0]} -> {g_yahoo.index[-1]})")

    common = g_schwab.index.intersection(g_yahoo.index)
    print(f"common timestamps: {len(common)}")
    s = g_schwab.loc[common]
    y = g_yahoo.loc[common]

    print("\n--- feed agreement (same timestamps) ---")
    for col in ["Open", "High", "Low", "Close"]:
        d = (s[col] - y[col]).abs()
        print(f"{col:6s} max_abs_diff={d.max():.4f}  mean_abs_diff={d.mean():.4f}")
    vd = s["Volume"] - y["Volume"]
    print(f"Volume max_abs_diff={vd.abs().max():,.0f}  "
          f"mean_abs_diff={vd.abs().mean():,.0f}")
    vr = (s["Volume"] / y["Volume"].replace(0, np.nan)).replace(
        [np.inf, -np.inf], np.nan).dropna()
    print(f"Volume Schwab/Yahoo ratio: median={vr.median():.3f} "
          f"mean={vr.mean():.3f} min={vr.min():.3f} max={vr.max():.3f}")

    # --- per-bar scoring walk on each feed's own bars ---
    rows = []
    for i in range(WARMUP, len(common)):
        ts = common[i]
        hs = g_schwab.loc[common[: i + 1]]
        hy = g_yahoo.loc[common[: i + 1]]
        rs = engine.score(hs, spec)
        ry = engine.score(hy, spec)
        rows.append({
            "ts": ts,
            "s_score": rs["score"], "s_band": rs["band"],
            "s_dir": rs["direction"],
            "s_fired": "+".join(sorted(rs.get("fired_inputs", []))) or "-",
            "y_score": ry["score"], "y_band": ry["band"],
            "y_dir": ry["direction"],
            "y_fired": "+".join(sorted(ry.get("fired_inputs", []))) or "-",
        })
    r = pd.DataFrame(rows)
    print(f"\n--- scored bars per feed: {len(r)} ---")

    d = (r["s_score"] - r["y_score"]).abs()
    print(f"score |Schwab - Yahoo|: mean={d.mean():.2f} max={d.max()} "
          f"median={d.median():.2f}")
    print(f"score correlation: {r['s_score'].corr(r['y_score']):.3f}")
    print(f"band agreement: {(r['s_band'] == r['y_band']).mean():.1%}")
    print(f"direction agreement: {(r['s_dir'] == r['y_dir']).mean():.1%}")

    print("\nband mix -- Schwab vs Yahoo:")
    print(pd.concat([
        r["s_band"].value_counts().rename("schwab"),
        r["y_band"].value_counts().rename("yahoo"),
    ], axis=1).fillna(0).astype(int).to_string())

    print(f"\nmax score -- Schwab: {r['s_score'].max()}  "
          f"Yahoo: {r['y_score'].max()}")
    print(f"mean score -- Schwab: {r['s_score'].mean():.1f}  "
          f"Yahoo: {r['y_score'].mean():.1f}")

    print("\ncomponent fire counts (bars where input added points):")
    for inp in ["climax", "volume", "rejection", "absorption",
                "location", "rsi_gated"]:
        fs = r["s_fired"].str.contains(inp).sum()
        fy = r["y_fired"].str.contains(inp).sum()
        print(f"  {inp:10s} schwab={fs:3d}  yahoo={fy:3d}")

    big = r[d >= 10]
    print(f"\nbars with |score diff| >= 10: {len(big)}")
    if len(big):
        print(big[["ts", "s_score", "y_score", "s_band", "y_band",
                   "s_fired", "y_fired"]].to_string(index=False))

    r.to_csv(os.path.join(HERE, "schwab_vs_yahoo_bars.csv"), index=False)
    print("\nbar-level log -> schwab_vs_yahoo_bars.csv")


if __name__ == "__main__":
    main()
