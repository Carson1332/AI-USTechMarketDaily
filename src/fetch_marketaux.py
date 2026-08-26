"""Marketaux — targeted per-ticker news, used only to answer "why did X move?".

Deliberately NOT a general news source. Measured on the free tier (2026-08-25) it returns
3 articles per request no matter what `limit` asks for, and the sources it surfaces
(seekingalpha, financefeeds, insidermonkey, profitconfidential, ventureburn) are weaker
than the RSS feeds already in the pipeline — adding it broadly would lower article quality,
not raise it.

What it does uniquely well is answer a question the RSS feeds often can't: a snapshot
ticker moved sharply and nothing in today's feed mentions it. One symbol-scoped request
per such ticker fills that hole for a handful of requests a day.

`entities[].match_score` is the useful field — it separates "this article is about ANET"
(89) from "ANET appears in a market-wrap list" (9). Low scores are noise and get dropped.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx

logger = logging.getLogger(__name__)

_URL = "https://api.marketaux.com/v1/news/all"


def _articles_for(
    symbol: str,
    api_key: str,
    client: httpx.Client,
    since: str,
    min_match: float,
) -> list[dict]:
    try:
        response = client.get(
            _URL,
            params={
                "api_token": api_key,
                "symbols": symbol,
                "filter_entities": "true",
                "language": "en",
                "published_after": since,
                "limit": 3,
            },
            timeout=20.0,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("Marketaux %s failed (%s: %s)", symbol, type(e).__name__, e)
        return []

    if "error" in payload:
        logger.warning("Marketaux %s error: %s", symbol, payload["error"])
        return []

    out: list[dict] = []
    for article in payload.get("data", []):
        match = max(
            (
                float(e.get("match_score") or 0)
                for e in article.get("entities", [])
                if (e.get("symbol") or "").upper() == symbol
            ),
            default=0.0,
        )
        if match < min_match:
            continue
        published = article.get("published_at", "")
        try:
            published_at = datetime.fromisoformat(published.replace("Z", "+00:00"))
        except ValueError:
            continue
        out.append({
            "title": article.get("title", "").strip(),
            "url": article.get("url", "").strip(),
            "source": article.get("source", "Marketaux"),
            "market": "equity",
            "summary": (article.get("description") or article.get("snippet") or "")[:600],
            "published_at": published_at,
            "anchor_ticker": symbol,
            "match_score": match,
        })
    return out


def explain_movers(
    movers: list[str],
    api_key: str,
    client: httpx.Client | None = None,
    lookback_hours: int = 30,
    min_match: float = 40.0,
    max_requests: int = 8,
) -> list[dict]:
    """One request per ticker in `movers`, capped at `max_requests` to protect the daily quota.

    Returns raw dicts shaped for normalize.normalize_rss_batch().
    """
    if not movers or not api_key:
        return []

    targets = movers[:max_requests]
    if len(movers) > max_requests:
        logger.info(
            "Marketaux: %d movers but max_requests=%d — querying %s",
            len(movers), max_requests, ", ".join(targets),
        )

    since = (
        datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    ).strftime("%Y-%m-%dT%H:%M")

    owns_client = client is None
    client = client or httpx.Client(timeout=20.0)
    results: list[dict] = []
    try:
        for symbol in targets:
            results.extend(_articles_for(symbol, api_key, client, since, min_match))
    finally:
        if owns_client:
            client.close()

    covered = sorted({r["anchor_ticker"] for r in results})
    logger.info(
        "Marketaux: %d article(s) for %d/%d ticker(s) — %s",
        len(results), len(covered), len(targets), ", ".join(covered) or "none",
    )
    return results


def explain_movers_mock(*_args, **_kwargs) -> list[dict]:
    return []
