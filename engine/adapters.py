"""Data adapters for the reversal gauge.

The engine only talks to the BarSource interface below: it asks for
tz-aware OHLCV bars and never cares which feed produced them.

v1 ships YahooAdapter (delayed ~15 min, public, no auth).
SchwabAdapter and TopstepXAdapter are documented stubs: the method
signatures and the fields each feed must return are fixed, so wiring a
live feed later means implementing one class, not touching the engine.

A real order-flow feed (delta / CVD / footprint) slots in via the
optional ``flow`` hook on ``get_intraday`` -- see engine.score_inputs.
Neither Yahoo, Schwab retail, nor TopstepX retail exposes true
aggressor delta, so v1 scores volume as a proxy and leaves the hook
open.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Instrument session specs. Everything the engine needs to know about *when*
# a session starts/ends and which levels are computable lives here, keyed by
# the gauge ticker the user typed.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SessionSpec:
    label: str            # display label, e.g. "SPY"
    yahoo_symbol: str     # symbol to pull from Yahoo
    tz: str               # timezone used for session logic
    kind: str             # "equity_rth" | "futures"
    # Equity RTH session (used when kind == "equity_rth"):
    rth_open: str = "09:30"
    rth_close: str = "16:00"
    # Futures sessions (used when kind == "futures"), "HH:MM" in spec tz:
    day_open: str = "08:30"     # day session open
    day_close: str = "15:15"    # day session close (daily halt follows)
    night_open: str = "17:00"   # overnight session open (prior calendar day)
    night_close: str = "08:30"  # overnight session close
    # Opening-range window for level computation, "HH:MM-HH:MM" in spec tz:
    or_window: str = "09:30-09:45"
    round_step: float = 1.0     # grid for "round number" levels
    has_overnight: bool = False  # True when the feed carries overnight bars


SPECS = {
    # SPY: RTH only, no premarket in the Yahoo pull -> no overnight levels.
    "SPY": SessionSpec(label="SPY", yahoo_symbol="SPY", tz="America/New_York",
                       kind="equity_rth", or_window="09:30-09:45",
                       round_step=1.0, has_overnight=False),
    # MES: CME equity-index future, nearly 24/5 with a 15-min daily halt.
    # VWAP/levels follow the existing lab convention: day session
    # 08:30-15:15 CT, overnight session 17:00-08:30 CT.
    "MES": SessionSpec(label="MES", yahoo_symbol="MES=F", tz="America/Chicago",
                       kind="futures", day_open="08:30", day_close="15:15",
                       night_open="17:00", night_close="08:30",
                       or_window="08:30-08:45", round_step=25.0,
                       has_overnight=True),
}


def resolve(ticker: str) -> SessionSpec:
    """Map a user-typed ticker to a SessionSpec.

    SPY/MES get first-class specs. Anything else is treated as an RTH
    equity (SPY-style session) under its own label -- this is the
    expansion path Josh asked for ("select any ticker" later).
    """
    t = (ticker or "SPY").upper().strip()
    if t in SPECS:
        return SPECS[t]
    return SessionSpec(label=t, yahoo_symbol=t, tz="America/New_York",
                       kind="equity_rth", or_window="09:30-09:45",
                       round_step=1.0, has_overnight=False)


# ---------------------------------------------------------------------------
# BarSource interface
# ---------------------------------------------------------------------------
class BarSource(ABC):
    """A feed of OHLCV bars.

    ``get_intraday`` must return a DataFrame indexed by tz-aware
    timestamps with columns Open/High/Low/Close/Volume, sorted
    ascending, no duplicate index values. ``note`` is a short human
    string describing the feed/latency for display (e.g. "Yahoo ~15m
    delayed").
    """

    @abstractmethod
    def get_intraday(self, spec: SessionSpec, days: int = 5):
        raise NotImplementedError

    @property
    @abstractmethod
    def note(self) -> str:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# v1: Yahoo Finance (public, delayed ~15 min, no auth)
# ---------------------------------------------------------------------------
class YahooAdapter(BarSource):
    @property
    def note(self) -> str:
        return "Yahoo Finance, ~15 min delayed"

    def _ensure(self):
        import importlib
        for pkg in ("yfinance", "pandas"):
            try:
                importlib.import_module(pkg)
            except ImportError:
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", "--quiet",
                     "--break-system-packages", pkg], check=True)
        import pandas as pd
        import yfinance as yf
        return pd, yf

    def get_intraday(self, spec: SessionSpec, days: int = 5):
        pd, yf = self._ensure()

        def flat(df):
            if isinstance(df.columns, pd.MultiIndex):
                df = df.copy()
                df.columns = df.columns.get_level_values(0)
            return df

        df = pd.DataFrame()
        # MES=F multi-day pulls are flaky; fall back to shorter windows.
        for p in (f"{days}d", "2d", "1d"):
            try:
                df = flat(yf.download(spec.yahoo_symbol, period=p,
                                      interval="5m", progress=False,
                                      prepost=False))
            except Exception:
                df = pd.DataFrame()
            if not df.empty and len(df) >= 30:
                break
        if df.empty:
            return df
        df = df[~df.index.duplicated(keep="last")].sort_index()
        # yfinance sometimes returns tz-naive; the engine needs tz-aware.
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        return df


# ---------------------------------------------------------------------------
# STUB: Charles Schwab (equities) -- wire later, do not implement blind.
#
# What the engine needs from this adapter: the same OHLCV DataFrame as
# YahooAdapter.get_intraday, but REAL-TIME (Schwab gives free real-time
# quotes/bars to brokerage account holders).
#
# To wire it:
#   1. Josh registers a developer app at developer.schwab.com -> client
#      ID + client secret (OAuth2). This is account-level setup friction,
#      not code.
#   2. Implement the OAuth flow (authorization-code grant, refresh
#      tokens) and store tokens via the Secure Vault -- never in chat,
#      never in a file.
#   3. GET /marketdata/v1/pricehistory?symbol=SPY&periodType=day&
#      frequencyType=minute&frequency=5&needExtendedHoursData=false
#      returns candles: datetime(ms), open, high, low, close, volume.
#      Map to the OHLCV DataFrame; tz = America/New_York.
#   4. Set note = "Schwab, real-time".
#
# Schwab does NOT provide true order-flow (bid/ask aggressor delta,
# footprint). The volume proxy + flow hook in the engine stays as-is.
# ---------------------------------------------------------------------------
class SchwabAdapter(BarSource):
    @property
    def note(self) -> str:
        return "Schwab real-time (not wired)"

    def get_intraday(self, spec: SessionSpec, days: int = 5):
        raise NotImplementedError(
            "SchwabAdapter is a stub: needs Josh's developer-app OAuth "
            "credentials (Secure Vault) before it can pull pricehistory. "
            "See the wiring notes in adapters.py.")


# ---------------------------------------------------------------------------
# TopstepX (futures) -- WIRED 2026-09-20.
#
# Pulls MES 5-min bars via POST /api/History/retrieveBars through the
# topstep-history skill CLI (~/workspace/skills/topstep-history/).
#
# Credentials: the Secure Vault cannot hold this pair (TopstepX login
# needs username + API key together in one request body; the vault
# deposits one secret). They arrive as TOPSTEPX_USERNAME /
# TOPSTEPX_API_KEY environment variables, supplied by Josh in chat for
# transient use. This adapter forwards them to the CLI subprocess env
# only -- never writes them anywhere. When they are absent it falls
# back to YahooAdapter (~15 min delayed) so the gauge still works.
#
# TopstepX retail does NOT expose true delta/CVD/footprint aggregates,
# so the flow hook stays proxy-based here too. (Their charts render
# footprint client-side; the API doesn't serve the aggregates.)
# ---------------------------------------------------------------------------
class TopstepXAdapter(BarSource):
    CLI = os.path.expanduser(
        "~/workspace/skills/topstep-history/bin/topstepx.py")
    # spec label -> TopstepX root symbol; the CLI resolves the front contract
    ROOTS = {"MES": "MES"}

    def __init__(self):
        self._fallback = YahooAdapter()
        self._used_fallback = False

    @property
    def note(self) -> str:
        if self._used_fallback:
            return "Yahoo Finance, ~15 min delayed (TopstepX creds absent)"
        return "TopstepX, real-time"

    def _creds(self):
        u = os.environ.get("TOPSTEPX_USERNAME", "")
        k = os.environ.get("TOPSTEPX_API_KEY", "")
        return (u, k) if (u and k) else (None, None)

    def get_intraday(self, spec: SessionSpec, days: int = 5):
        import pandas as pd
        import io
        u, k = self._creds()
        if not u or not k or spec.label not in self.ROOTS:
            self._used_fallback = True
            return self._fallback.get_intraday(spec, days)
        days = max(1, min(int(days), 60))
        env = dict(os.environ, TOPSTEPX_USERNAME=u, TOPSTEPX_API_KEY=k)
        try:
            raw = subprocess.run(
                [sys.executable, self.CLI, "bars",
                 "--symbol", self.ROOTS[spec.label],
                 "--timeframe", "5m", "--days", str(days)],
                capture_output=True, text=True, timeout=300, env=env)
        except Exception:
            self._used_fallback = True
            return self._fallback.get_intraday(spec, days)
        if raw.returncode != 0 or "timestamp" not in raw.stdout:
            self._used_fallback = True
            return self._fallback.get_intraday(spec, days)
        df = pd.read_csv(io.StringIO(raw.stdout))
        if df.empty:
            self._used_fallback = True
            return self._fallback.get_intraday(spec, days)
        df = df.rename(columns={
            "timestamp": "ts", "open": "Open", "high": "High",
            "low": "Low", "close": "Close", "volume": "Volume"})
        df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert(spec.tz)
        df = df.set_index("ts").sort_index()
        df = df[~df.index.duplicated(keep="last")]
        self._used_fallback = False
        return df[["Open", "High", "Low", "Close", "Volume"]]


def default_source(spec=None) -> BarSource:
    """v1.2 feed: Schwab gateway (real-time) for equities, TopstepX for futures.

    The gateway is Josh's own read-only proxy (custom.schwab-gateway
    credential in the Secure Vault; skill at
    ~/workspace/skills/schwab-gateway/). MES and other futures specs use
    TopstepXAdapter (real-time 5-min history via the topstep-history
    skill); it falls back to YahooAdapter (~15 min delayed) when the
    transient TopstepX credentials are absent.
    """
    if spec is not None and spec.kind == "futures":
        return TopstepXAdapter()
    return GatewayFirstSource()


# ---------------------------------------------------------------------------
# v1.1: Schwab gateway (Josh's own read-only proxy, real-time for
# equities) with Yahoo fallback. Futures (MES) stay on Yahoo: the
# gateway/Schwab has no futures feed (verified 2026-09-20: MES history
# returns empty).
# ---------------------------------------------------------------------------
class SchwabGatewayAdapter(BarSource):
    CLI = os.path.expanduser(
        "~/workspace/skills/schwab-gateway/bin/schwab_gateway.py")

    @property
    def note(self) -> str:
        return "Schwab gateway, real-time"

    def get_intraday(self, spec: SessionSpec, days: int = 5):
        import pandas as pd
        days = max(1, min(int(days), 10))
        try:
            raw = subprocess.run(
                [sys.executable, self.CLI, "history", spec.label,
                 "--param", "periodType=day",
                 "--param", f"period={days}",
                 "--param", "frequencyType=minute",
                 "--param", "frequency=5"],
                capture_output=True, text=True, timeout=60)
        except Exception:
            return pd.DataFrame()
        if raw.returncode != 0 or not raw.stdout.strip():
            return pd.DataFrame()
        try:
            payload = json.loads(raw.stdout)
        except Exception:
            return pd.DataFrame()
        candles = payload.get("candles") or []
        if not candles:
            return pd.DataFrame()
        df = pd.DataFrame(candles).rename(columns={
            "open": "Open", "high": "High", "low": "Low",
            "close": "Close", "volume": "Volume"})
        # Schwab epoch-ms are UTC instants; wall clock is the spec tz.
        df["ts"] = pd.to_datetime(
            df["datetime"], unit="ms", utc=True).dt.tz_convert(spec.tz)
        df = df.set_index("ts").sort_index()
        df = df[~df.index.duplicated(keep="last")]
        return df[["Open", "High", "Low", "Close", "Volume"]]


class GatewayFirstSource(BarSource):
    """Try the Schwab gateway; fall back to Yahoo on any failure."""

    def __init__(self):
        self._used: BarSource = SchwabGatewayAdapter()

    @property
    def note(self) -> str:
        return self._used.note

    def get_intraday(self, spec: SessionSpec, days: int = 5):
        df = SchwabGatewayAdapter().get_intraday(spec, days)
        if df is not None and not df.empty and len(df) >= 30:
            self._used = SchwabGatewayAdapter()
            return df
        self._used = YahooAdapter()
        return YahooAdapter().get_intraday(spec, days)
