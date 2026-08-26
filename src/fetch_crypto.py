"""Crypto spot prices from CoinGecko's free endpoint (no API key).

Kept separate from the equity quote path because the semantics differ: crypto trades 24/7,
so "24h change" is a rolling window, not a session close-to-close move. The digest must not
present the two as the same kind of number.
"""
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

_URL = "https://api.coingecko.com/api/v3/simple/price"


def fetch_prices(assets: list[dict], client: httpx.Client | None = None) -> list[dict]:
    """Return [{symbol, label, usd, pct_24h}] in config order, skipping anything missing."""
    if not assets:
        return []

    ids = [a["id"] for a in assets if a.get("id")]
    if not ids:
        return []

    owns_client = client is None
    client = client or httpx.Client(timeout=20.0)
    try:
        response = client.get(
            _URL,
            params={
                "ids": ",".join(ids),
                "vs_currencies": "usd",
                "include_24hr_change": "true",
            },
        )
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("CoinGecko failed (%s: %s); skipping crypto prices", type(e).__name__, e)
        return []
    finally:
        if owns_client:
            client.close()

    rows: list[dict] = []
    for asset in assets:
        entry = data.get(asset.get("id", ""))
        if not entry or entry.get("usd") is None:
            continue
        rows.append({
            "symbol": asset.get("symbol", asset["id"].upper()),
            "label": asset.get("label", asset.get("symbol", "")),
            "usd": float(entry["usd"]),
            "pct_24h": round(float(entry.get("usd_24h_change") or 0.0), 2),
        })

    logger.info("CoinGecko: %d/%d assets", len(rows), len(assets))
    return rows


def fetch_prices_mock(assets: list[dict] | None = None, *_args, **_kwargs) -> list[dict]:
    """Deterministic stand-ins so mock runs exercise the same rendering path."""
    seed = [("BTC", 79800.0, 3.01), ("ETH", 2486.34, 1.02),
            ("SOL", 100.46, 6.12), ("HYPE", 81.88, 4.44)]
    by_symbol = {s: (usd, pct) for s, usd, pct in seed}
    rows = []
    for asset in assets or []:
        symbol = asset.get("symbol", "")
        if symbol in by_symbol:
            usd, pct = by_symbol[symbol]
            rows.append({"symbol": symbol, "label": asset.get("label", symbol),
                         "usd": usd, "pct_24h": pct})
    return rows
