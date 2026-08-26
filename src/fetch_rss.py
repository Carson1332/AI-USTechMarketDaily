"""RSS news sources — the depth layer Finnhub's wire feed can't provide.

Finnhub general news is mostly agency one-liners ("Shares drift ahead of Nvidia
earnings"). The digest needs pieces with a subject, a number and a source, which is what
CNBC/CoinDesk/The Defiant publish. One feed failing costs that feed's items, nothing more.
"""
from __future__ import annotations

import json
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_UA = "fin-news-daily/1.0 (personal market digest)"
_ATOM = "{http://www.w3.org/2005/Atom}"


def _text(node, *names: str) -> str:
    """First non-empty child matching any of `names`, RSS or Atom namespaced."""
    for name in names:
        for tag in (name, _ATOM + name):
            child = node.find(tag)
            if child is not None:
                if child.text and child.text.strip():
                    return child.text.strip()
                href = child.get("href")
                if href:
                    return href.strip()
    return ""


def _parse_date(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt is None:
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _parse_feed(content: bytes, source: str, market: str) -> list[dict]:
    root = ET.fromstring(content)
    nodes = root.findall(".//item") or root.findall(f".//{_ATOM}entry")

    out: list[dict] = []
    for node in nodes:
        title = _text(node, "title")
        link = _text(node, "link")
        if not title or not link:
            continue
        published = _parse_date(_text(node, "pubDate", "published", "updated"))
        if published is None:
            continue
        out.append({
            "title": title,
            "url": link,
            "source": source,
            "market": market,
            "summary": _text(node, "description", "summary")[:600],
            "published_at": published,
        })
    return out


def fetch_feeds(feeds: list[dict], client: httpx.Client | None = None) -> list[dict]:
    """Fetch every configured feed. Returns raw dicts for normalize.normalize_rss_batch()."""
    if not feeds:
        return []

    owns_client = client is None
    client = client or httpx.Client(timeout=20.0, follow_redirects=True)
    results: list[dict] = []
    try:
        for feed in feeds:
            name = feed.get("name", "RSS")
            url = feed.get("url", "")
            market = feed.get("market", "equity")
            if not url:
                continue
            try:
                response = client.get(url, headers={"User-Agent": _UA})
                response.raise_for_status()
                items = _parse_feed(response.content, name, market)
            except (httpx.HTTPError, ET.ParseError) as e:
                logger.warning("RSS %s failed (%s: %s)", name, type(e).__name__, e)
                continue
            logger.info("RSS %s: %d items", name, len(items))
            results.extend(items)
    finally:
        if owns_client:
            client.close()

    logger.info("RSS: %d items from %d feed(s)", len(results), len(feeds))
    return results


_YAHOO_TPL = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"


def fetch_ticker_feeds(
    tickers: list[str],
    client: httpx.Client | None = None,
    max_tickers: int = 24,
) -> list[dict]:
    """Yahoo's per-ticker headline RSS — free, keyless, and scoped to the watchlist.

    This is the relevance layer: the editorial feeds cover what's big, this covers what's
    *yours*. The publisher mix skews to SEO and opinion sites, which is fine — event
    scoring filters those out, and genuine corporate actions ("announces closing of
    private offering of convertible senior notes") come through with high signal.
    """
    if not tickers:
        return []

    owns_client = client is None
    client = client or httpx.Client(timeout=20.0, follow_redirects=True)
    results: list[dict] = []
    failed: list[str] = []
    try:
        for symbol in tickers[:max_tickers]:
            try:
                response = client.get(
                    _YAHOO_TPL.format(symbol=symbol),
                    headers={"User-Agent": _UA},
                )
                response.raise_for_status()
                items = _parse_feed(response.content, f"Yahoo/{symbol}", "equity")
            except (httpx.HTTPError, ET.ParseError):
                failed.append(symbol)
                continue
            for item in items:
                item["anchor_ticker"] = symbol
            results.extend(items)
    finally:
        if owns_client:
            client.close()

    logger.info(
        "Yahoo ticker feeds: %d items from %d/%d ticker(s)%s",
        len(results), len(tickers[:max_tickers]) - len(failed), len(tickers[:max_tickers]),
        f" (failed: {', '.join(failed)})" if failed else "",
    )
    return results


_FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "rss_sample.json"


def fetch_feeds_mock(*_args, **_kwargs) -> list[dict]:
    """Replay a captured RSS snapshot so mock runs exercise the real ranking path.

    Regenerate with: python -m src.fetch_rss --capture
    """
    if not _FIXTURE.exists():
        logger.warning("No RSS fixture at %s — mock run will have no RSS items", _FIXTURE)
        return []
    with open(_FIXTURE, encoding="utf-8") as f:
        raw = json.load(f)
    for entry in raw:
        entry["published_at"] = datetime.fromisoformat(entry["published_at"])
    logger.info("RSS fixture: %d items", len(raw))
    return raw


def _capture(settings_path: str = "config/settings.yml") -> None:
    """Snapshot the live feeds into the fixture used by mock mode."""
    import yaml

    with open(settings_path, encoding="utf-8") as f:
        feeds = yaml.safe_load(f).get("rss_feeds", [])
    items = fetch_feeds(feeds)
    for entry in items:
        entry["published_at"] = entry["published_at"].isoformat()
    _FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    with open(_FIXTURE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)
    print(f"Wrote {len(items)} items to {_FIXTURE}")


if __name__ == "__main__":
    import sys

    if "--capture" in sys.argv:
        logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(name)s: %(message)s")
        _capture()
    else:
        print("usage: python -m src.fetch_rss --capture")
