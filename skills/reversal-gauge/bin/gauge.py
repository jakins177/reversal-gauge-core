#!/usr/bin/env python3
"""Reversal-risk gauge CLI.

Usage: gauge.py [TICKER]
  gauge.py SPY     SPY ETF, RTH session
  gauge.py MES     MES continuous contract (MES=F), globex sessions
  gauge.py AAPL    any Yahoo ticker -> treated as an RTH equity

Prints a 0-100 reversal-risk read for the prevailing intraday move on
5-min bars. Equities via Schwab gateway (real-time) with Yahoo fallback;
MES via TopstepX (real-time when credentials are present) with Yahoo
fallback. This is risk/exit context only -- the gauge never issues entries.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.expanduser("~"), "workspace",
                               "goals",
                               "get-consistently-profitable-trading-futures-and-stocks",
                               "hidden_files", "reversal_gauge"))

from adapters import resolve, default_source  # noqa: E402
import engine  # noqa: E402


def pull_30m(symbol):
    import subprocess
    try:
        import yfinance as yf
        import pandas as pd
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet",
                        "--break-system-packages", "yfinance", "pandas"],
                       check=True)
        import yfinance as yf
        import pandas as pd
    df = pd.DataFrame()
    for p in ("1mo", "5d"):
        try:
            d = yf.download(symbol, period=p, interval="30m",
                            progress=False, prepost=False)
            if isinstance(d.columns, pd.MultiIndex):
                d.columns = d.columns.get_level_values(0)
            if not d.empty and len(d) >= 30:
                df = d
                break
        except Exception:
            pass
    if df.index.tz is None and not df.empty:
        df.index = df.index.tz_localize("UTC")
    return df


def main():
    ticker = (sys.argv[1] if len(sys.argv) > 1 else "SPY").upper()
    spec = resolve(ticker)
    src = default_source(spec)

    df5 = src.get_intraday(spec, days=5)
    if df5.empty:
        print(f"{spec.label}: no data returned (market closed or feed "
              f"issue) [{src.note}].")
        return
    df30 = pull_30m(spec.yahoo_symbol)

    res = engine.score(df5, spec, df30=df30)

    last_ts = df5.index[-1].tz_convert("America/Chicago")
    px = float(df5["Close"].iloc[-1])
    print(f"REVERSAL GAUGE -- {res['ticker']} @ {px:.2f} "
          f"(data as of {last_ts.strftime('%a %H:%M CT')}; {src.note})")
    print(f"direction: {res['direction']} | day-type: {res['day_type']} "
          f"{res.get('day_type_detail', {}).get('components', {})}")
    print(f"score: {res['score']}/100 -- {res['band'].upper()}")
    if res["components"]:
        print("inputs:")
        for c in res["components"]:
            sign = "+" if c["points"] >= 0 else ""
            print(f"  {c['input']:10s} {sign}{c['points']:>3d}  {c['why']}")
    if res["levels"]:
        lv = ", ".join(f"{k}={v}" for k, v in res["levels"].items())
        print(f"levels: {lv}")
    for n in res["notes"]:
        print(f"note: {n}")
    band = res["band"]
    if band == "high risk":
        v = (f"VERDICT: reversal risk {res['score']}/100 (HIGH) against the "
             f"{res['direction']} -- protect/exit, arm a confirmation "
             f"trigger (structural break + failed retest). Never fade blind.")
    elif band == "caution":
        v = (f"VERDICT: reversal risk {res['score']}/100 (CAUTION) -- "
             f"tighten stops, do not add to the {res['direction']}.")
    else:
        v = (f"VERDICT: reversal risk {res['score']}/100 -- "
             f"{res['direction']} looks healthy; nothing to defend.")
    print(v)


if __name__ == "__main__":
    main()
