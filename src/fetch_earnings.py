"""Earnings calendar from Finnhub — the forward-looking half of the digest.

Everything else in the pipeline is backward-looking: what happened, what moved. The
calendar is the only input that says what is *about* to happen, which is what section 九
(明天怎麼驗證) needs to point at something concrete rather than inventing a check.

Finnhub's `/calendar/earnings` is included in the free tier (`/calendar/economic` is not —
it returns 403 — so macro events still have to come from the news feed).
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

import httpx

logger = logging.getLogger(__name__)

_URL = "https://finnhub.io/api/v1/calendar/earnings"

# Finnhub's `hour` field; blank is common and means unconfirmed.
_HOUR_CN = {"bmo": "盤前", "amc": "盤後", "dmh": "盤中"}


def _fmt_hour(hour: str) -> str:
    return _HOUR_CN.get((hour or "").strip().lower(), "時間未定")


def fetch_calendar(
    api_key: str,
    market_date: date,
    watchlist: set[str],
    client: httpx.Client | None = None,
    days_ahead: int = 7,
) -> list[dict]:
    """Upcoming earnings for watched tickers only.

    The unfiltered response is ~180 rows a week, nearly all micro-caps — useless as a
    digest block and a waste of prompt space. Filtering to the watchlist is what makes it
    readable.
    """
    if not api_key or not watchlist:
        return []

    owns_client = client is None
    client = client or httpx.Client(timeout=20.0)
    try:
        response = client.get(
            _URL,
            params={
                "from": market_date.isoformat(),
                "to": (market_date + timedelta(days=days_ahead)).isoformat(),
                "token": api_key,
            },
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("Finnhub earnings calendar failed (%s: %s)", type(e).__name__, e)
        return []
    finally:
        if owns_client:
            client.close()

    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for entry in payload.get("earningsCalendar", []):
        symbol = (entry.get("symbol") or "").upper()
        day = entry.get("date") or ""
        if symbol not in watchlist or not day:
            continue
        if (symbol, day) in seen:      # the API repeats some symbols
            continue
        seen.add((symbol, day))
        rows.append({
            "symbol": symbol,
            "date": day,
            "hour": _fmt_hour(entry.get("hour", "")),
            "eps_estimate": entry.get("epsEstimate"),
            "revenue_estimate": entry.get("revenueEstimate"),
            "days_out": (date.fromisoformat(day) - market_date).days,
        })

    rows.sort(key=lambda r: (r["date"], r["symbol"]))
    logger.info(
        "Earnings calendar: %d watched ticker(s) reporting in the next %d days — %s",
        len(rows), days_ahead,
        ", ".join(f"{r['symbol']}({r['date']})" for r in rows[:8]) or "none",
    )
    return rows


def fetch_calendar_mock(
    api_key: str = "",
    market_date: date | None = None,
    watchlist: set[str] | None = None,
    *_args,
    **_kwargs,
) -> list[dict]:
    """Two plausible rows so mock runs exercise the rendering and prompt path."""
    base = market_date or date.today()
    picks = [s for s in ("CRDO", "NVDA", "MU") if not watchlist or s in watchlist]
    return [
        {
            "symbol": symbol,
            "date": (base + timedelta(days=offset)).isoformat(),
            "hour": "盤後" if offset else "盤前",
            "eps_estimate": 1.19,
            "revenue_estimate": 479841323,
            "days_out": offset,
        }
        for offset, symbol in enumerate(picks[:2])
    ]
