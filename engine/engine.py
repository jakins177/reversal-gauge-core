"""Reversal-risk gauge engine (v1).

Scores 0-100 how "exhausted" the prevailing intraday move looks on
lower timeframes (5-min primary). It is symmetric: it scores reversal
risk for the *prevailing* direction, reported in ``direction``.

The gauge NEVER issues an entry signal. Bands:
    <30  trend healthy -- leave it alone
    30-60 caution -- tighten stops, don't add
    >60  high risk -- protect/exit, arm a confirmation trigger (a
          structural break + failed retest). Never fade blind.

Weights below are a priori and documented in report.md. They were NOT
tuned to maximize the calibration outcome.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# A priori weights (documented, not tuned)
# ---------------------------------------------------------------------------
W_CLIMAX = 25      # ATR-expansion push into the extreme
W_VOLUME = 15      # volume spike on the extreme bar, shrinking follow-through
W_REJECT = 15      # >=40% wick at a known level, close back inside
W_ABSORB = 10      # >=2 tests of a level, closes fail to extend
W_LOCATE = 10      # extreme lands on a real level
W_RSI = 15         # RSI extreme, GATED by day-type (never scores alone)
W_RSI_TREND_PENALTY = -10  # RSI extreme on a trend day: trend healthy

TOL = 0.0005       # 0.05% -- "at a level" proximity tolerance
RSI_OB, RSI_OS = 70.0, 30.0


# ---------------------------------------------------------------------------
# Indicators (Wilder-style, matching the lab's existing implementations)
# ---------------------------------------------------------------------------
def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    h, l, c = df["High"], df["Low"], df["Close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(),
                    (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, min_periods=n, adjust=False).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    gain = d.clip(lower=0).ewm(alpha=1 / n, min_periods=n, adjust=False).mean()
    loss = (-d.clip(upper=0)).ewm(alpha=1 / n, min_periods=n,
                                  adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.fillna(50.0)


def adx(df: pd.DataFrame, n: int = 14):
    h, l, c = df["High"], df["Low"], df["Close"]
    up, dn = h.diff(), -l.diff()
    plus_dm = up.where((up > dn) & (up > 0), 0.0)
    minus_dm = dn.where((dn > up) & (dn > 0), 0.0)
    tr = pd.concat([h - l, (h - c.shift()).abs(),
                    (l - c.shift()).abs()], axis=1).max(axis=1)
    a = tr.ewm(alpha=1 / n, min_periods=n, adjust=False).mean()
    pdi = 100 * plus_dm.ewm(alpha=1 / n, min_periods=n, adjust=False).mean() / a
    mdi = 100 * minus_dm.ewm(alpha=1 / n, min_periods=n, adjust=False).mean() / a
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    ax = dx.ewm(alpha=1 / n, min_periods=n, adjust=False).mean()
    return ax, pdi, mdi


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, min_periods=n, adjust=False).mean()


def fractal_swings(df: pd.DataFrame, k: int = 2):
    """Fractal swing highs/lows -> (idx_list_high, idx_list_low) of ilocs."""
    h, l = df["High"].to_numpy(), df["Low"].to_numpy()
    sh, sl = [], []
    for i in range(k, len(df) - k):
        if h[i] == h[i - k:i + k + 1].max():
            sh.append(i)
        if l[i] == l[i - k:i + k + 1].min():
            sl.append(i)
    return sh, sl


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------
def _hm(ts, tz):
    return ts.tz_convert(tz).strftime("%H:%M")


def split_sessions(df: pd.DataFrame, spec) -> dict:
    """Slice bars into labeled sessions. Returns dict label -> DataFrame.

    Equity RTH: one 'rth' session per calendar day (spec tz).
    Futures: 'day' (day_open-day_close) and 'overnight' (night_open 17:00
    prior day -> night_close 08:30) sessions in spec tz.
    """
    tz = spec.tz
    out = {}
    if spec.kind == "equity_rth":
        for day, g in df.groupby(df.index.tz_convert(tz).date):
            g = g.between_time(spec.rth_open, spec.rth_close)
            if not g.empty:
                out[f"rth_{day}"] = g
    else:
        loc = df.index.tz_convert(tz)
        hm = loc.strftime("%H:%M")
        is_day = (hm >= spec.day_open) & (hm <= spec.day_close)
        is_nite = (hm >= spec.night_open) | (hm < spec.night_close)
        # Label overnight sessions by the calendar day they END on.
        for day, g in df[is_nite].groupby(loc[is_nite].date):
            if not g.empty:
                out[f"overnight_{day}"] = g
        for day, g in df[is_day].groupby(loc[is_day].date):
            if not g.empty:
                out[f"day_{day}"] = g
    return out


def current_session(df: pd.DataFrame, spec):
    """The session the last bar belongs to (or the most recent one)."""
    sessions = split_sessions(df, spec)
    if not sessions:
        return None, df.iloc[0:0]
    last_idx = df.index[-1]
    for label in sorted(sessions, reverse=True):
        if sessions[label].index[-1] >= last_idx:
            return label, sessions[label]
    label = sorted(sessions)[-1]
    return label, sessions[label]


# ---------------------------------------------------------------------------
# Levels
# ---------------------------------------------------------------------------
def session_vwap(sess: pd.DataFrame):
    if sess.empty or "Volume" not in sess or sess["Volume"].sum() == 0:
        return None
    tp = (sess["High"] + sess["Low"] + sess["Close"]) / 3
    return float((tp * sess["Volume"]).sum() / sess["Volume"].sum())


def compute_levels(df: pd.DataFrame, spec) -> dict:
    """Known levels for the current session. Values may be None."""
    lv = {}
    sessions = split_sessions(df, spec)
    labels = sorted(sessions)
    if not labels:
        return lv
    cur_label, cur = current_session(df, spec)
    ci = labels.index(cur_label)

    # Session VWAP + ATR bands (band width = session 14-bar ATR).
    vw = session_vwap(cur)
    lv["vwap"] = vw
    if vw is not None and len(cur) >= 14:
        sa = float(atr(cur, 14).iloc[-1])
        lv["vwap_up1"], lv["vwap_dn1"] = vw + sa, vw - sa
        lv["vwap_up2"], lv["vwap_dn2"] = vw + 2 * sa, vw - 2 * sa

    # Prior session high/low (prior RTH day for SPY; prior day-session
    # for MES; prior overnight session for the overnight leg).
    if ci >= 1:
        prev = sessions[labels[ci - 1]]
        lv["prior_high"] = float(prev["High"].max())
        lv["prior_low"] = float(prev["Low"].min())
    # Overnight high/low only when the feed carries overnight bars.
    if spec.has_overnight:
        on_labels = [l_ for l_ in labels[:ci + 1] if l_.startswith("overnight_")]
        if on_labels:
            on = sessions[on_labels[-1]]
            lv["overnight_high"] = float(on["High"].max())
            lv["overnight_low"] = float(on["Low"].min())

    # Opening-range edge (first 15 min of the session's RTH/day open).
    o0, o1 = spec.or_window.split("-")
    orb = cur.between_time(o0, o1)
    if not orb.empty:
        lv["or_high"] = float(orb["High"].max())
        lv["or_low"] = float(orb["Low"].min())

    # Round numbers near price.
    px = float(df["Close"].iloc[-1])
    step = spec.round_step
    base = round(px / step) * step
    lv["round_up"] = base + step if base <= px else base
    lv["round_dn"] = base - step if base >= px else base
    return {k: v for k, v in lv.items() if v is not None}


def near_level(price: float, levels: dict, tol: float = TOL):
    """Return the level name if price is within tol (fraction) of it."""
    for name, lv in levels.items():
        if lv and abs(price - lv) / lv <= tol:
            return name, lv
    return None, None


# ---------------------------------------------------------------------------
# Prevailing direction + day-type (live, no lookahead)
# ---------------------------------------------------------------------------
def prevailing_direction(df: pd.DataFrame, spec, levels: dict) -> str:
    """uptrend / downtrend / chop for the current session so far."""
    _, cur = current_session(df, spec)
    if cur is None or len(cur) < 6:
        return "chop"
    px = float(df["Close"].iloc[-1])
    e21 = float(ema(cur["Close"], 21).iloc[-1])
    e21_prev = float(ema(cur["Close"], 21).iloc[-3])
    vw = levels.get("vwap")
    above_vwap = (px > vw) if vw else True
    if px > e21 and e21 >= e21_prev and above_vwap:
        return "uptrend"
    if px < e21 and e21 <= e21_prev and not above_vwap:
        return "downtrend"
    return "chop"


def day_type(df: pd.DataFrame, spec, levels: dict,
             adx30_now: float | None = None,
             adx30_median: float | None = None) -> dict:
    """Classify the session so far: trend / range / mixed.

    Three live, no-lookahead components, each mapped to [0,1] where 1 =
    strongly trending:
      1. dir_share: by ~10:30 (spec tz) or last bar if earlier -- the
         signed move from the open as a share of the day's range so far.
      2. adx30 vs its 20-day median (optional; needs 30-min history).
      3. vwap_side: share of session closes on the dominant side of VWAP.
    """
    _, cur = current_session(df, spec)
    out = {"label": "mixed", "score": 0.5, "components": {}}
    if cur is None or len(cur) < 6:
        return out
    tz = spec.tz
    # 1. directional share by ~10:30 spec-tz (or last bar if earlier)
    cut = "10:30" if spec.kind == "equity_rth" else "10:30"
    morning = cur[cur.index.tz_convert(tz).strftime("%H:%M") <= cut]
    seg = morning if len(morning) >= 6 else cur
    rng = float(seg["High"].max() - seg["Low"].min())
    move = float(seg["Close"].iloc[-1] - seg["Open"].iloc[0])
    dir_share = abs(move) / rng if rng > 0 else 0.0
    out["components"]["dir_share"] = round(dir_share, 3)
    # 2. ADX(14) on 30-min vs 20-day median
    adx_c = 0.5
    if adx30_now is not None and adx30_median:
        adx_c = float(np.clip((adx30_now - adx30_median) / 20.0 + 0.5, 0, 1))
    out["components"]["adx_regime"] = round(adx_c, 3)
    # 3. share of closes on dominant side of VWAP
    vw = levels.get("vwap")
    vwap_c = 0.5
    if vw is not None:
        side = (cur["Close"] > vw).mean()
        vwap_c = float(max(side, 1 - side))
    out["components"]["vwap_side"] = round(vwap_c, 3)

    score = float(np.mean([dir_share, adx_c, vwap_c]))
    out["score"] = round(score, 3)
    out["label"] = "trend" if score >= 0.6 else ("range" if score <= 0.4
                                                else "mixed")
    return out


# ---------------------------------------------------------------------------
# Structural reversal definition (shared by live scoring narrative and the
# calibration harness): a close beyond the last counter-trend swing
# extreme that holds -- no immediate reclaim within 2 bars.
# ---------------------------------------------------------------------------
def reversal_within(df: pd.DataFrame, at_iloc: int, direction: str,
                    horizon: int = 6, k: int = 2) -> bool:
    """Was ``direction`` structurally reversed within ``horizon`` bars
    after bar ``at_iloc`` (exclusive)?"""
    if direction not in ("uptrend", "downtrend"):
        return False
    sub = df.iloc[:at_iloc + 1]
    sh, sl = fractal_swings(sub, k=k)
    closes = df["Close"].to_numpy()
    if direction == "uptrend":
        if not sl:
            return False
        extreme = df["Low"].to_numpy()[sl[-1]]  # last higher low
        for j in range(at_iloc + 1, min(at_iloc + 1 + horizon, len(df))):
            if closes[j] < extreme:  # break
                # hold: no reclaim within 2 bars
                if all(closes[m] < extreme
                       for m in range(j + 1, min(j + 3, len(df)))):
                    return True
                break
    else:
        if not sh:
            return False
        extreme = df["High"].to_numpy()[sh[-1]]  # last lower high
        for j in range(at_iloc + 1, min(at_iloc + 1 + horizon, len(df))):
            if closes[j] > extreme:
                if all(closes[m] > extreme
                       for m in range(j + 1, min(j + 3, len(df)))):
                    return True
                break
    return False


# ---------------------------------------------------------------------------
# The six inputs
# ---------------------------------------------------------------------------
def _extreme_bar(cur: pd.DataFrame, direction: str, lookback: int = 20):
    seg = cur.iloc[-lookback:] if len(cur) >= lookback else cur
    if direction == "uptrend":
        i = int(seg["High"].to_numpy().argmax())
    elif direction == "downtrend":
        i = int(seg["Low"].to_numpy().argmin())
    else:
        return None, None
    gi = seg.index[i]
    return cur.index.get_loc(gi), seg.iloc[i]


def input_climax(df, cur, direction, a14) -> tuple:
    """Push into the extreme covering >= 2x ATR(14)."""
    if direction not in ("uptrend", "downtrend") or len(cur) < 15:
        return 0, ""
    gi, bar = _extreme_bar(cur, direction)
    if gi is None or gi < len(df) - 3:  # extreme must be recent
        return 0, ""
    rng = float(bar["High"] - bar["Low"])
    a = float(a14.loc[bar.name]) if bar.name in a14.index else np.nan
    if not np.isfinite(a) or a <= 0:
        return 0, ""
    body_pos = (bar["Close"] - bar["Low"]) / rng if rng > 0 else 0.5
    strong_close = (body_pos >= 2 / 3) if direction == "uptrend" \
        else (body_pos <= 1 / 3)
    if rng >= 2.0 * a and strong_close:
        return W_CLIMAX, f"extreme bar range {rng / a:.1f}x ATR(14)"
    return 0, ""


def input_volume(df, cur, direction, flow_fn=None) -> tuple:
    """Volume spike on/near the extreme bar with shrinking follow-through.

    flow_fn is the adapter-ready hook for real order flow: if provided,
    call flow_fn(recent_bars) -> dict with e.g. {"delta_z": float,
    "cvd_divergence": bool}; a strongly negative delta_z (against an
    uptrend extreme) can add up to W_VOLUME instead of the proxy.
    v1 always uses the volume proxy.
    """
    if direction not in ("uptrend", "downtrend") or len(cur) < 25:
        return 0, ""
    if flow_fn is not None:
        try:
            f = flow_fn(cur.iloc[-30:])
            if isinstance(f, dict) and f.get("delta_z") is not None:
                dz = float(f["delta_z"])
                against = (direction == "uptrend" and dz <= -2.0) or \
                          (direction == "downtrend" and dz >= 2.0)
                if against:
                    return W_VOLUME, f"order-flow delta_z={dz:.1f} vs extreme"
                return 0, ""
        except Exception:
            pass  # fall through to the proxy
    gi, bar = _extreme_bar(cur, direction)
    if gi is None or gi < len(df) - 3:
        return 0, ""
    vols = cur["Volume"].to_numpy()
    epos = cur.index.get_loc(bar.name)
    med = float(np.median(vols[max(0, epos - 20):epos]))
    if med <= 0 or float(bar["Volume"]) < 2.0 * med:
        return 0, ""
    after = vols[epos + 1:cur.index.get_loc(cur.index[-1]) + 1]
    if len(after) and float(np.max(after)) < float(bar["Volume"]):
        return W_VOLUME, (f"vol {float(bar['Volume']) / med:.1f}x 20-bar "
                          f"median, follow-through shrinking")
    return 0, ""


def input_rejection(cur, direction, levels) -> tuple:
    """Wick >= 40% of bar range at a known level, close back inside."""
    if direction not in ("uptrend", "downtrend") or cur.empty:
        return 0, ""
    bar = cur.iloc[-1]
    rng = float(bar["High"] - bar["Low"])
    if rng <= 0:
        return 0, ""
    if direction == "uptrend":
        wick = float(bar["High"] - max(bar["Open"], bar["Close"]))
        extreme, name, lv = bar["High"], *near_level(float(bar["High"]),
                                                    levels)
    else:
        wick = float(min(bar["Open"], bar["Close"]) - bar["Low"])
        extreme, name, lv = bar["Low"], *near_level(float(bar["Low"]),
                                                   levels)
    if name is None or wick / rng < 0.40:
        return 0, ""
    # close back inside: close is >=25% of the range away from the extreme
    back_inside = (float(bar["High"] - bar["Close"]) / rng >= 0.25) \
        if direction == "uptrend" \
        else (float(bar["Close"] - bar["Low"]) / rng >= 0.25)
    if back_inside:
        return W_REJECT, f"{wick / rng:.0%} upper wick at {name}"
    return 0, ""


def input_absorption(cur, direction, levels, window: int = 12) -> tuple:
    """ >= 2 tests of the same level in `window` bars, closes fail to
    extend beyond it. """
    if direction not in ("uptrend", "downtrend") or len(cur) < window:
        return 0, ""
    seg = cur.iloc[-window:]
    for name, lv in levels.items():
        if direction == "uptrend":
            tests = (seg["High"] >= lv * (1 - TOL)) & \
                    (seg["High"] <= lv * (1 + TOL))
            extended = (seg["Close"] > lv * (1 + TOL)).any()
        else:
            tests = (seg["Low"] <= lv * (1 + TOL)) & \
                    (seg["Low"] >= lv * (1 - TOL))
            extended = (seg["Close"] < lv * (1 - TOL)).any()
        if int(tests.sum()) >= 2 and not extended:
            return W_ABSORB, f"{int(tests.sum())} tests of {name}, no extension"
    return 0, ""


def input_location(cur, direction, levels) -> tuple:
    """The session extreme sits on a real level."""
    if direction not in ("uptrend", "downtrend"):
        return 0, ""
    seg = cur.iloc[-20:] if len(cur) >= 20 else cur
    px = float(seg["High"].max()) if direction == "uptrend" \
        else float(seg["Low"].min())
    name, _ = near_level(px, levels)
    if name:
        return W_LOCATE, f"extreme at {name}"
    return 0, ""


def input_rsi_gated(cur, direction, levels, daytype) -> tuple:
    """RSI(14) extreme -- scored ONLY through the day-type gate.

    Range/chop day + (divergence or rejection at a level) -> +W_RSI.
    Trend day -> W_RSI_TREND_PENALTY (trend healthy, do not fade).
    Mixed -> small +W_RSI/3. Never scores alone: without a usable
    day-type read it contributes 0.
    """
    if direction not in ("uptrend", "downtrend") or len(cur) < 15:
        return 0, ""
    r = rsi(cur["Close"], 14)
    last = float(r.iloc[-1])
    hot = (direction == "uptrend" and last >= RSI_OB) or \
          (direction == "downtrend" and last <= RSI_OS)
    if not hot:
        return 0, ""
    label = daytype.get("label", "mixed")
    if label == "trend":
        return W_RSI_TREND_PENALTY, \
            f"RSI {last:.0f} on a trend day -- healthy, not a fade"
    # divergence: new 20-bar price extreme, RSI below its prior extreme
    div = False
    px = cur["Close"]
    if direction == "uptrend":
        e1, e2 = px.iloc[:-1].idxmax(), px.idxmax()
        div = e2 > e1 and float(r.loc[e2]) < float(r.loc[e1])
    else:
        e1, e2 = px.iloc[:-1].idxmin(), px.idxmin()
        div = e2 < e1 and float(r.loc[e2]) > float(r.loc[e1])
    rej_pts, _ = input_rejection(cur, direction, levels)
    if label == "range" and (div or rej_pts > 0):
        why = "divergence" if div else "rejection at level"
        return W_RSI, f"RSI {last:.0f} on range day + {why}"
    if label == "mixed":
        return W_RSI // 3, f"RSI {last:.0f}, day-type mixed"
    return 0, f"RSI {last:.0f} on range day, no confirmation"


# ---------------------------------------------------------------------------
# Composite
# ---------------------------------------------------------------------------
def score(df: pd.DataFrame, spec, df30=None, flow_fn=None) -> dict:
    """Score the last bar. Returns a full result dict."""
    res = {"ticker": spec.label, "score": 0, "band": "trend healthy",
           "direction": "chop", "day_type": "mixed", "components": [],
           "levels": {}, "notes": []}
    if df.empty or len(df) < 30:
        res["notes"].append("not enough bars to score")
        return res

    a14 = atr(df, 14)
    _, cur = current_session(df, spec)
    levels = compute_levels(df, spec)
    res["levels"] = {k: round(v, 2) for k, v in levels.items()}

    direction = prevailing_direction(df, spec, levels)
    res["direction"] = direction

    adx_now, adx_med = None, None
    if df30 is not None and len(df30) >= 30:
        ax, _, _ = adx(df30, 14)
        ax = ax.dropna()
        if len(ax):
            adx_now = float(ax.iloc[-1])
            adx_med = float(ax.median())  # ~20-day median proxy, documented
    dt = day_type(df, spec, levels, adx_now, adx_med)
    res["day_type"] = dt["label"]
    res["day_type_detail"] = dt

    if direction == "chop":
        res["band"] = "no signal"
        res["notes"].append("no prevailing direction -- gauge is symmetric "
                            "and only scores with-trend exhaustion")
        return res

    parts = [
        ("climax", input_climax(df, cur, direction, a14)),
        ("volume", input_volume(df, cur, direction, flow_fn)),
        ("rejection", input_rejection(cur, direction, levels)),
        ("absorption", input_absorption(cur, direction, levels)),
        ("location", input_location(cur, direction, levels)),
        ("rsi_gated", input_rsi_gated(cur, direction, levels, dt)),
    ]
    total = 0
    for name, (pts, why) in parts:
        total += pts
        if pts != 0 or why:
            res["components"].append({"input": name, "points": pts,
                                      "why": why})
    total = int(round(max(0, min(100, total))))
    res["score"] = total
    res["band"] = ("high risk" if total > 60 else
                   "caution" if total >= 30 else "trend healthy")
    res["fired_inputs"] = [c["input"] for c in res["components"]
                           if c["points"] > 0]
    return res
