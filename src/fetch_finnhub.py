from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_BASE_URL = "https://finnhub.io/api/v1"
_FIXTURE_NEWS = Path(__file__).parent.parent / "tests" / "fixtures" / "finnhub_news.json"
_FIXTURE_COMPANY = Path(__file__).parent.parent / "tests" / "fixtures" / "finnhub_company_news.json"
_FIXTURE_QUOTES = Path(__file__).parent.parent / "tests" / "fixtures" / "finnhub_quotes.json"


def _get(url: str, client: httpx.Client, max_retries: int = 3) -> list | dict:
    for attempt in range(max_retries):
        try:
            response = client.get(url, timeout=30.0)
            if response.status_code == 429:
                wait = 2 ** attempt
                logger.warning("Finnhub 429 — backing off %ds (attempt %d)", wait, attempt + 1)
                time.sleep(wait)
                continue
            response.raise_for_status()
            return response.json()
        except httpx.TimeoutException:
            wait = 2 ** attempt
            logger.warning("Finnhub timeout — backing off %ds (attempt %d)", wait, attempt + 1)
            if attempt < max_retries - 1:
                time.sleep(wait)
    raise RuntimeError(f"Finnhub: failed after {max_retries} attempts: {url}")


def fetch_market_news(api_key: str, client: httpx.Client) -> list[dict]:
    """Fetch general and forex market news from Finnhub."""
    all_news: list[dict] = []
    for category in ("general", "forex"):
        url = f"{_BASE_URL}/news?category={category}&token={api_key}"
        logger.info("Finnhub: fetching market news category=%s", category)
        try:
            items = _get(url, client)
            if isinstance(items, list):
                logger.info("Finnhub: got %d items for category=%s", len(items), category)
                all_news.extend(items)
        except Exception as e:
            logger.warning("Finnhub market news failed for category=%s: %s", category, e)
    return all_news


def fetch_company_news(
    api_key: str,
    tickers: list[str],
    client: httpx.Client,
    lookback_hours: int = 30,
) -> list[dict]:
    """Fetch company-specific news for each ticker in the watchlist."""
    now = datetime.now(timezone.utc)
    date_from = (now - timedelta(hours=lookback_hours)).strftime("%Y-%m-%d")
    date_to = now.strftime("%Y-%m-%d")

    all_news: list[dict] = []
    for ticker in tickers:
        url = (
            f"{_BASE_URL}/company-news?symbol={ticker}"
            f"&from={date_from}&to={date_to}&token={api_key}"
        )
        try:
            items = _get(url, client)
            if isinstance(items, list):
                # Inject ticker into related field — Finnhub omits it for company-news calls
                for item in items:
                    item.setdefault("related", ticker)
                logger.info("Finnhub company news: %d items for %s", len(items), ticker)
                all_news.extend(items)
        except Exception as e:
            logger.warning("Finnhub company news failed for %s: %s", ticker, e)
        time.sleep(1.0)  # stay under 60 req/min with ~50 tickers

    return all_news


def fetch_quotes(api_key: str, tickers: list[str], client: httpx.Client) -> dict[str, dict]:
    """Fetch real-time quotes for each ticker. Returns {ticker: quote_dict}."""
    quotes: dict[str, dict] = {}
    for ticker in tickers:
        url = f"{_BASE_URL}/quote?symbol={ticker}&token={api_key}"
        try:
            data = _get(url, client)
            if isinstance(data, dict) and "c" in data:
                quotes[ticker] = data
        except Exception as e:
            logger.warning("Finnhub quote failed for %s: %s", ticker, e)
        time.sleep(1.0)  # stay under 60 req/min

    logger.info("Finnhub quotes fetched for %d/%d tickers", len(quotes), len(tickers))
    return quotes


# --- Mock helpers ---

def fetch_market_news_mock() -> list[dict]:
    with open(_FIXTURE_NEWS, encoding="utf-8") as f:
        return json.load(f)


def fetch_company_news_mock() -> list[dict]:
    with open(_FIXTURE_COMPANY, encoding="utf-8") as f:
        return json.load(f)


def fetch_quotes_mock() -> dict[str, dict]:
    with open(_FIXTURE_QUOTES, encoding="utf-8") as f:
        return json.load(f)


def all_watchlist_tickers(settings: dict) -> tuple[list[str], list[str]]:
    """
    Derive leader tickers from themes config.
    Returns (company_news_tickers, quote_only_tickers).
    ETFs (listed in etf_symbols) are quote-only — company-news returns empty for them.
    """
    etf_set = {t.upper() for t in settings.get("etf_symbols", [])}
    seen: set[str] = set()
    company_news: list[str] = []
    quotes_only: list[str] = []
    for theme_data in settings.get("themes", {}).values():
        for t in theme_data.get("leaders", []):
            u = t.upper()
            if u not in seen:
                seen.add(u)
                if u in etf_set:
                    quotes_only.append(t)
                else:
                    company_news.append(t)
    return company_news, quotes_only
