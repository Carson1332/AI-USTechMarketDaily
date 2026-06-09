"""
Daily Market News Digest — pipeline orchestrator.
Run with:  python -m src.main
Mock mode: python -m src.main --mock   (or MOCK=1 python -m src.main)
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from src import (
    config, dedupe, fetch_finnhub, fetch_indicators,
    normalize, notify, rank, store, summarize, tag,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")


def _attach_quotes(items: list, quotes: dict, leaders_set: set[str]) -> list:
    """Set price_metric on each item using the anchor leader ticker."""
    for item in items:
        candidate = None
        if item.anchor_ticker:
            candidate = item.anchor_ticker.upper()
        elif len(item.tickers) <= 2:
            candidate = next(
                (t.upper() for t in item.tickers if t.upper() in leaders_set), None
            )
        else:
            # broad story: badge only if exactly one leader is present
            hits = [t.upper() for t in item.tickers if t.upper() in leaders_set]
            if len(hits) == 1:
                candidate = hits[0]
        if candidate:
            q = quotes.get(candidate)
            if q:
                pm = normalize.normalize_finnhub_quote(candidate, q)
                if pm:
                    item.price_metric = pm
    return items


def main() -> None:
    settings = config.load_settings()
    mock = config.is_mock_mode()

    if mock:
        logger.info("=== MOCK MODE — no live API calls, no Telegram send ===")
        secrets: dict = {}
    else:
        secrets = config.load_secrets()

    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(hours=settings.get("lookback_hours", 30))

    # Build theme helpers from config
    leaders_set = config.build_leaders_set(settings)
    primary_theme_map = config.build_primary_theme_map(settings)
    china_adrs_set = {t.upper() for t in settings.get("china_adrs", [])}
    themes_cfg = settings.get("themes", {})
    theme_order = settings.get("theme_order", [])
    scoreboard_etfs = settings.get("scoreboard_etfs", [])
    gauge_tickers = settings.get("gauges_tickers", [])

    # Quote tickers: scoreboard ETFs + gauges + anchor set for price badges
    scoreboard_etf_tickers = [e["etf"] for e in scoreboard_etfs]
    anchor_tickers = settings.get("anchor_tickers", [])
    all_quote_tickers = list(dict.fromkeys(scoreboard_etf_tickers + gauge_tickers + anchor_tickers))

    threshold = settings["selection"]["dedupe_title_threshold"]

    # ── 1. FETCH ──────────────────────────────────────────────────────────────
    raw_items = []
    quotes: dict = {}
    fear_greed: dict = {}

    if mock:
        logger.info("Loading mock fixtures...")
        finn_news = fetch_finnhub.fetch_market_news_mock()
        quotes = fetch_finnhub.fetch_quotes_mock()
        fear_greed = fetch_indicators.fetch_fear_greed_mock()
    else:
        with httpx.Client(timeout=30.0) as client:
            # Finnhub market news
            try:
                finn_news = fetch_finnhub.fetch_market_news(secrets["FINNHUB_API_KEY"], client)
            except Exception as e:
                logger.warning("Finnhub market news failed: %s", e)
                finn_news = []

            # Finnhub quotes (scoreboard ETFs + gauges + anchor tickers)
            try:
                quotes = fetch_finnhub.fetch_quotes(secrets["FINNHUB_API_KEY"], all_quote_tickers, client)
            except Exception as e:
                logger.warning("Finnhub quotes failed: %s", e)
                quotes = {}

            # CNN Fear & Greed
            fear_greed = fetch_indicators.fetch_fear_greed(client)

    # ── 2. NORMALIZE ──────────────────────────────────────────────────────────
    raw_items = normalize.normalize_finnhub_batch(finn_news)
    logger.info("Normalized: %d raw items", len(raw_items))

    # ── 3. FILTER by lookback window ──────────────────────────────────────────
    if mock:
        items = raw_items   # fixture timestamps are historical; skip filter in mock
        logger.info("Mock mode: skipping lookback filter, using all %d items", len(items))
    else:
        items = [i for i in raw_items if i.published_at >= cutoff]
        logger.info("After lookback filter (%dh): %d items", settings.get("lookback_hours", 30), len(items))

    # ── 4. SOURCE FILTER ──────────────────────────────────────────────────────
    whitelist = settings.get("sources", {}).get("whitelist", [])
    blacklist = settings.get("sources", {}).get("blacklist", [])
    if whitelist:
        items = [i for i in items if i.source in whitelist]
    if blacklist:
        items = [i for i in items if i.source not in blacklist]

    # ── 5. TAG — region + theme ───────────────────────────────────────────────
    for i in items:
        tag.assign_region(i, china_adrs_set)
        tag.assign_theme(i, themes_cfg, theme_order, primary_theme_map)

    # ── 6. DEDUPE ─────────────────────────────────────────────────────────────
    items = dedupe.filter_quality(items)
    items = dedupe.dedupe(items, threshold=threshold)

    # ── 7. ATTACH QUOTES ──────────────────────────────────────────────────────
    items = _attach_quotes(items, quotes, leaders_set)

    # ── 8. RANK + SELECT ──────────────────────────────────────────────────────
    items = rank.score_items(items, settings["rank_weights"], now_utc)
    items = rank.select_top(
        items,
        max_total=settings["selection"]["max_items_total"],
        min_per_theme=settings["selection"].get("min_items_per_theme", 1),
    )

    # ── 8b. BUILD MARKET DATA ─────────────────────────────────────────────────
    scoreboard = rank.sector_scoreboard(quotes, scoreboard_etfs)

    gauges: dict = {}
    for t in gauge_tickers:
        q = quotes.get(t)
        if q:
            pm = normalize.normalize_finnhub_quote(t, q)
            if pm:
                gauges[t] = pm  # {symbol, pct_change, current}

    # VIX fallback: Finnhub spot VIX returns c=0; use VIXY ETF as proxy
    if gauges.get("VIX", {}).get("current", 0) == 0:
        vixy_q = quotes.get("VIXY")
        if vixy_q:
            pm = normalize.normalize_finnhub_quote("VIX", vixy_q)
            if pm:
                gauges["VIX"] = pm
                logger.info("VIX spot unavailable, using VIXY as proxy")

    indicators = {"fear_greed": fear_greed}

    tz = ZoneInfo(settings.get("timezone", "Australia/Perth"))
    date_str = now_utc.astimezone(tz).strftime("%Y-%m-%d")

    logger.info(
        "Market data ready: %d scoreboard ETFs, %d gauges, fear_greed=%s",
        len(scoreboard),
        len(gauges),
        fear_greed.get("score"),
    )

    if not items:
        logger.warning("No items after full pipeline — sending 'no news' message")
        if not mock:
            try:
                notify.send_message(
                    secrets["TELEGRAM_BOT_TOKEN"],
                    secrets["TELEGRAM_CHAT_ID"],
                    "📰 No significant market news today.",
                )
            except Exception as e:
                logger.error("Telegram send failed: %s", e)
        content = store.build_markdown([], "", settings, now_utc, scoreboard=scoreboard)
        store.save(content, now_utc, Path("archive"), settings)
        return

    # Sort by rank_score so items[0] leads
    items.sort(key=lambda i: i.rank_score, reverse=True)

    logger.info(
        "Final selection: %d items — %s",
        len(items),
        {th: sum(1 for i in items if i.theme == th) for th in set(i.theme for i in items)},
    )

    # ── 9. SUMMARIZE ──────────────────────────────────────────────────────────
    narrative: str = ""
    if mock:
        narrative = summarize.summarize_mock(date_str, gauges, scoreboard, indicators, items)
    else:
        try:
            with httpx.Client(timeout=120.0) as client:
                narrative = summarize.summarize_digest(
                    date_str, gauges, scoreboard, indicators, items,
                    model=settings["model"]["openrouter_model"],
                    api_key=secrets["OPENROUTER_API_KEY"],
                    client=client,
                )
        except Exception as e:
            logger.warning("Summarization failed, continuing without narrative: %s", e)

    # ── 10. STORE ─────────────────────────────────────────────────────────────
    content = store.build_markdown(items, narrative, settings, now_utc, scoreboard=scoreboard)
    archive_path = store.save(content, now_utc, Path("archive"), settings)
    logger.info("Archive: %s", archive_path)

    # ── 11. NOTIFY ────────────────────────────────────────────────────────────
    if mock:
        out = ("\n" + "=" * 70 + "\n" + content + "\n" + "=" * 70 + "\n\n[MOCK] Telegram send skipped.\n")
        sys.stdout.buffer.write(out.encode("utf-8", errors="replace"))
        sys.stdout.flush()
    else:
        try:
            notify.send_digest(
                token=secrets["TELEGRAM_BOT_TOKEN"],
                chat_id=secrets["TELEGRAM_CHAT_ID"],
                items=items,
                narrative=narrative,
                settings=settings,
                now_utc=now_utc,
                scoreboard=scoreboard,
            )
            logger.info("Telegram: digest sent successfully")
        except Exception as e:
            logger.error("Telegram send failed: %s", e)
            # Don't crash the run — archive is already written


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logger.exception("Fatal error: %s", exc)
        sys.exit(1)
