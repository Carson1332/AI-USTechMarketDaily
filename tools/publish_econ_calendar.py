"""Publish an upcoming US macro calendar from a local Futu OpenD gateway.

Why this is a separate local script rather than part of the digest:
OpenD is a gateway on your own machine (127.0.0.1:11111); the digest runs on a GitHub
Actions runner, which cannot reach it. So this writes a small JSON that the digest reads:

    local machine              repo                       CI
    OpenD → this script  →  data/econ_calendar.json  →  src/fetch_econ.py → digest

Run it manually (or from Task Scheduler) whenever you want the calendar refreshed —
weekly is plenty, since it publishes a forward window:

    python tools/publish_econ_calendar.py
    python tools/publish_econ_calendar.py --days 21 --commit

Finnhub's /calendar/economic is premium-only (403), which is why this exists at all.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

OUT_DEFAULT = _REPO_ROOT / "data" / "econ_calendar.json"

# `star` alone is not a usable filter: HIGH includes weekly oil/gas inventories and
# treasury auctions, which dominate by count and move nothing in an equity digest.
# Match on what the release actually is instead.
MARKET_MOVING = re.compile(
    r"\b(CPI|PPI|PCE|GDP|nonfarm|non-farm|payroll|unemployment rate|jobless claims"
    r"|interest rate|federal funds|FOMC|rate decision|retail sales|ISM|PMI"
    r"|consumer confidence|consumer sentiment|durable goods|housing starts"
    r"|building permits|industrial output|industrial production|trade balance"
    r"|initial claims|core inflation|inflation rate)\b",
    re.IGNORECASE,
)

# Recurring noise that slips past the pattern above.
NOISE = re.compile(
    r"(inventor|auction|bid multiple|allocation percentage|oil wells|rig count"
    r"|API |EIA |strategic petroleum|Cushing)",
    re.IGNORECASE,
)


def _to_iso(ts) -> str:
    """Futu returns unix epoch seconds as a float; the parquet export used datetimes."""
    try:
        value = float(ts)
    except (TypeError, ValueError):
        return str(ts)
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


def _num(value):
    if value in (None, "", "--"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fetch(days: int, host: str, port: int, countries: set[str]) -> list[dict]:
    import futu as ft

    begin = date.today()
    end = begin + timedelta(days=days)

    ctx = ft.OpenQuoteContext(host=host, port=port)
    try:
        ret, state = ctx.get_global_state()
        if ret != ft.RET_OK:
            raise RuntimeError(f"OpenD get_global_state failed: {state}")
        if not state.get("qot_logined"):
            raise RuntimeError("OpenD is running but not logged in for quotes")

        frames, page, guard = [], None, 0
        while guard < 20:                      # the API paginates; don't loop forever
            guard += 1
            result = ctx.get_economic_calendar(
                begin_date=begin.isoformat(), end_date=end.isoformat(),
                next_page=page,
            )
            if result[0] != ft.RET_OK:
                raise RuntimeError(f"get_economic_calendar failed: {result[1]}")
            frames.append(result[1])
            page = result[2] if len(result) > 2 else None
            has_next = bool(result[3]) if len(result) > 3 else False
            if not has_next or not page:
                break
    finally:
        ctx.close()

    import pandas as pd

    df = pd.concat(frames, ignore_index=True)
    print(f"fetched {len(df)} raw rows for {begin} -> {end}")

    rows: list[dict] = []
    for _, r in df.iterrows():
        title = str(r.get("title", "")).strip()
        country = str(r.get("country", "")).strip()
        if countries and country not in countries:
            continue
        if NOISE.search(title) or not MARKET_MOVING.search(title):
            continue
        rows.append({
            "title": title,
            "timestamp": _to_iso(r.get("timestamp")),
            "country": country,
            "star": str(r.get("star", "")).strip(),
            "previous": _num(r.get("previous")),
            "consensus": _num(r.get("consensus")),
            "actual": _num(r.get("actual")),
        })

    rows.sort(key=lambda x: x["timestamp"])
    seen, deduped = set(), []
    for row in rows:
        key = (row["title"], row["timestamp"])
        if key not in seen:
            seen.add(key)
            deduped.append(row)
    return deduped


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14, help="forward window (default 14)")
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=11111)
    ap.add_argument("--countries", default="United States",
                    help="comma-separated; blank keeps every country")
    ap.add_argument("--commit", action="store_true",
                    help="git add+commit the file (does NOT push)")
    args = ap.parse_args()

    countries = {c.strip() for c in args.countries.split(",") if c.strip()}
    rows = fetch(args.days, args.host, args.port, countries)

    payload = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "window_days": args.days,
        "countries": sorted(countries),
        "events": rows,
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    print(f"\nwrote {out_path}: {len(rows)} market-moving event(s)")
    for row in rows[:12]:
        con = f"預估 {row['consensus']}" if row["consensus"] is not None else "無預估"
        print(f"  {row['timestamp'][:16]}  {row['star']:6} {row['title'][:64]}  ({con})")

    if args.commit:
        rel = os.path.relpath(out_path, _REPO_ROOT)
        subprocess.run(["git", "add", rel], cwd=_REPO_ROOT, check=True)
        subprocess.run(
            ["git", "commit", "-m", f"econ calendar: refresh {date.today().isoformat()}"],
            cwd=_REPO_ROOT, check=False,
        )
        print("\ncommitted (not pushed) — run `git push` when ready")


if __name__ == "__main__":
    main()
