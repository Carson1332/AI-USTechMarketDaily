from __future__ import annotations

import logging
from datetime import datetime, timezone

from src.models import NewsItem

logger = logging.getLogger(__name__)


def normalize_alpha(raw: dict) -> NewsItem | None:
    """Convert a single Alpha Vantage feed item to a NewsItem."""
    title = raw.get("title", "").strip()
    url = raw.get("url", "").strip()
    if not title or not url:
        return None

    try:
        ts_str = raw["time_published"]  # "20240115T060000"
        published_at = datetime.strptime(ts_str, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    except (KeyError, ValueError) as e:
        logger.warning("Alpha Vantage: bad timestamp on %r: %s", title[:60], e)
        return None

    tickers = [
        t["ticker"]
        for t in raw.get("ticker_sentiment", [])
        if t.get("ticker")
    ]

    try:
        sentiment = float(raw["overall_sentiment_score"])
    except (KeyError, TypeError, ValueError):
        sentiment = None

    return NewsItem(
        title=title,
        source=raw.get("source", "Alpha Vantage"),
        url=url,
        published_at=published_at,
        summary=raw.get("summary", ""),
        tickers=tickers,
        sentiment=sentiment,
        region="Global",        # tag.py will overwrite this
        price_metric=None,      # main.py attaches quotes later
    )


def normalize_alpha_batch(feed: list[dict]) -> list[NewsItem]:
    items = []
    for raw in feed:
        item = normalize_alpha(raw)
        if item:
            items.append(item)
    return items


def normalize_finnhub(raw: dict) -> NewsItem | None:
    """Convert a single Finnhub news item to a NewsItem."""
    headline = raw.get("headline", "").strip()
    url = raw.get("url", "").strip()
    if not headline or not url:
        return None

    unix_ts = raw.get("datetime")
    if not unix_ts:
        return None
    try:
        published_at = datetime.fromtimestamp(int(unix_ts), tz=timezone.utc)
    except (TypeError, ValueError, OSError) as e:
        logger.warning("Finnhub: bad timestamp on %r: %s", headline[:60], e)
        return None

    related = raw.get("related", "") or ""
    tickers = [t.strip() for t in related.split(",") if t.strip()]

    return NewsItem(
        title=headline,
        source=raw.get("source", "Finnhub"),
        url=url,
        published_at=published_at,
        summary=raw.get("summary", ""),
        tickers=tickers,
        sentiment=None,
        region="Global",
        price_metric=None,
    )


def normalize_finnhub_batch(raw_list: list[dict]) -> list[NewsItem]:
    items = []
    for raw in raw_list:
        item = normalize_finnhub(raw)
        if item:
            items.append(item)
    return items


def normalize_finnhub_quote(symbol: str, raw: dict) -> dict | None:
    """Return a price_metric dict from a Finnhub quote response."""
    try:
        current = float(raw["c"])
        prev_close = float(raw["pc"])
    except (KeyError, TypeError, ValueError):
        return None

    if prev_close == 0:
        return None

    pct_change = round(((current - prev_close) / prev_close) * 100, 2)
    return {"symbol": symbol, "pct_change": pct_change, "current": round(current, 2)}
