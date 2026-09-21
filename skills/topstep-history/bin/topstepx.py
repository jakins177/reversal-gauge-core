#!/usr/bin/env python3
"""TopstepX (ProjectX Gateway) history fetcher.

Pulls futures candle history (e.g. MESU26) from Josh's Topstep account.
Credentials are NEVER stored: they arrive via environment variables at
runtime and live only in process memory.

    TOPSTEPX_USERNAME   TopstepX login username
    TOPSTEPX_API_KEY    TopstepX API key

Every authenticated request goes only to api.topstepx.com.

Usage:
    topstepx.py bars --symbol MESU26 --timeframe 5m --start 2026-07-01 --end 2026-09-20 --out bars.csv
    topstepx.py bars --symbol MESU26 --timeframe 5m --days 3          # last N sessions
    topstepx.py search --text MESU26
    topstepx.py auth-check
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

BASE = "https://api.topstepx.com"
ALLOWED_HOSTS = {"api.topstepx.com"}

UNIT = {  # -> (unit int, unitNumber)
    "1m": (2, 1), "5m": (2, 5), "15m": (2, 15),
    "1h": (4, 1), "1d": (5, 1),
}


class TopstepError(RuntimeError):
    pass


def _post(path: str, payload: dict, token: str | None = None,
          retries: int = 6) -> dict:
    """POST JSON via curl subprocess.

    curl is used instead of urllib because this API's server intermittently
    truncates chunked responses to Python's http.client. The request body is
    piped through stdin (``--data @-``) so credentials never appear in argv.
    """
    host = urllib.parse.urlparse(BASE).hostname
    if host not in ALLOWED_HOSTS:
        raise TopstepError("refusing request to unexpected host")
    body_bytes = json.dumps(payload).encode()
    last: Exception | None = None
    for attempt in range(retries):
        try:
            cmd = ["curl", "-sS", "-m", "90", "-w", "\n%{http_code}",
                   BASE + path, "-X", "POST",
                   "-H", "Content-Type: application/json"]
            if token:
                cmd += ["-H", f"Authorization: Bearer {token}"]
            cmd += ["--data", "@-"]
            p = subprocess.run(cmd, input=body_bytes,
                               capture_output=True, timeout=120)
            if p.returncode != 0:
                raise TopstepError(f"curl exit {p.returncode}: "
                                   f"{p.stderr.decode()[:200]}")
            out = p.stdout.decode()
            http_code = out.rsplit("\n", 1)[-1].strip()
            resp_body = out.rsplit("\n", 1)[0]
            if not http_code.startswith("2"):
                raise TopstepError(f"HTTP {http_code} on {path}: "
                                   f"{resp_body[:200]}")
            try:
                data = json.loads(resp_body)
            except json.JSONDecodeError:
                raise TopstepError(f"non-JSON response from {path}")
            break
        except TopstepError:
            raise
        except Exception as e:  # flaky server: drops/truncates connections often
            last = e
            time.sleep(2 * (attempt + 1))
    else:
        raise TopstepError(f"{path} failed after {retries} tries: {last}")
    # ProjectX convention: HTTP 200 does not mean success
    if not data.get("success", False):
        raise TopstepError(f"{path} failed: errorCode={data.get('errorCode')} "
                           f"msg={data.get('errorMessage')}")
    return data


def login() -> str:
    username = os.environ.get("TOPSTEPX_USERNAME", "")
    api_key = os.environ.get("TOPSTEPX_API_KEY", "")
    if not username or not api_key:
        raise TopstepError("TOPSTEPX_USERNAME and TOPSTEPX_API_KEY must be set")
    data = _post("/api/Auth/loginKey", {"userName": username, "apiKey": api_key})
    token = data.get("token") or ""
    if not token:
        raise TopstepError("loginKey returned no token")
    return token


def search_contract(token: str, text: str, live: bool = False) -> list[dict]:
    data = _post("/api/Contract/search",
                 {"searchText": text, "live": live}, token)
    return data.get("contracts", []) or []


def resolve_contract(token: str, symbol: str) -> dict:
    # Accept a full contract id (CON.F.US.MES.Z26), a display name (MESZ6),
    # or a root+month code (MESZ26). Search with the short root text, then match.
    sym = symbol.upper()
    if sym.startswith("CON."):
        search_text = sym.split(".")[-2]  # MES from CON.F.US.MES.Z26
    else:
        search_text = "".join(ch for ch in sym if ch.isalpha())
    contracts = search_contract(token, search_text)
    for c in contracts:
        if str(c.get("id", "")).upper() == sym:
            return c
    for c in contracts:
        if str(c.get("name", "")).upper() == sym:
            return c
    # last resort: root+month prefix match on the name (MESZ6 ~ MESZ26)
    root_month = "".join(ch for ch in sym if ch.isalpha())
    for c in contracts:
        if str(c.get("name", "")).upper().startswith(root_month):
            return c
    names = [str(c.get("name")) for c in contracts[:10]]
    raise TopstepError(f"contract {symbol!r} not found; candidates: {names}")


def retrieve_bars(token: str, contract_id: str, start: datetime, end: datetime,
                  unit: int, unit_number: int, limit: int = 20000,
                  live: bool = False) -> list[dict]:
    # API returns newest-first; paginate backwards in time
    bars: list[dict] = []
    cursor = end
    seen = 0
    while True:
        data = _post("/api/History/retrieveBars", {
            "contractId": contract_id,
            "live": live,
            "startTime": start.astimezone(timezone.utc).isoformat(),
            "endTime": cursor.astimezone(timezone.utc).isoformat(),
            "unit": unit,
            "unitNumber": unit_number,
            "limit": limit,
            "includePartialBar": False,
        }, token)
        chunk = data.get("bars") or []
        if not chunk:
            break
        bars.extend(chunk)
        seen += len(chunk)
        if len(chunk) < limit:
            break
        oldest = min(b["t"] for b in chunk)
        cursor = datetime.fromisoformat(oldest.replace("Z", "+00:00")) - timedelta(seconds=1)
        if cursor <= start:
            break
        time.sleep(0.7)  # stay under 50 req / 30 s
    bars = [b for b in bars
            if start <= datetime.fromisoformat(b["t"].replace("Z", "+00:00")) <= end]
    bars.sort(key=lambda b: b["t"])
    return bars


def cmd_auth_check(_):
    token = login()
    print(f"auth ok, token length {len(token)}")


def cmd_search(args):
    token = login()
    for c in search_contract(token, args.text):
        print(json.dumps({k: c.get(k) for k in
                          ("id", "name", "tickSize", "tickValue", "activeContract")},
                         default=str))


def _parse_date(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def cmd_bars(args):
    if args.timeframe not in UNIT:
        raise TopstepError(f"unsupported timeframe {args.timeframe!r}; use one of {sorted(UNIT)}")
    unit, unit_number = UNIT[args.timeframe]
    token = login()
    contract = resolve_contract(token, args.symbol)
    cid = str(contract["id"])
    print(f"# contract: {contract.get('name')} id={cid}", file=sys.stderr)
    now = datetime.now(timezone.utc)
    if args.days:
        end = now
        start = now - timedelta(days=args.days)
    else:
        start = _parse_date(args.start)
        end = _parse_date(args.end) if args.end else now
    bars = retrieve_bars(token, cid, start, end, unit, unit_number, live=args.live)
    print(f"# bars: {len(bars)}", file=sys.stderr)
    out = open(args.out, "w", newline="") if args.out else sys.stdout
    w = csv.writer(out)
    w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
    for b in bars:
        w.writerow([b["t"], b["o"], b["h"], b["l"], b["c"], b.get("v", 0)])
    if args.out:
        out.close()
        print(f"# wrote {args.out}", file=sys.stderr)


def main(argv=None):
    p = argparse.ArgumentParser(description="TopstepX history fetcher")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("auth-check"); s.set_defaults(fn=cmd_auth_check)
    s = sub.add_parser("search"); s.set_defaults(fn=cmd_search)
    s.add_argument("--text", required=True)

    s = sub.add_parser("bars"); s.set_defaults(fn=cmd_bars)
    s.add_argument("--symbol", required=True, help="e.g. MESU26")
    s.add_argument("--timeframe", default="5m",
                   choices=sorted(UNIT))
    s.add_argument("--start", help="ISO date, e.g. 2026-07-01")
    s.add_argument("--end", help="ISO date, default now")
    s.add_argument("--days", type=int, help="last N days instead of --start/--end")
    s.add_argument("--out", help="CSV path; default stdout")
    s.add_argument("--live", action="store_true",
                   help="live contracts instead of sim")

    args = p.parse_args(argv)
    try:
        args.fn(args)
    except TopstepError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
