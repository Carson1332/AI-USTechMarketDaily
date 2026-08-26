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
    config, dedupe, fetch_crypto, fetch_earnings, fetch_econ, fetch_finnhub,
    fetch_indicators, fetch_marketaux, fetch_rss, fetch_sec, fetch_yahoo,
    market_calendar, normalize, notify, rank, render, store, summarize, tag,
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


def _uncovered_movers(
    quotes: dict,
    snapshot_cfg: list[dict],
    news_raw: list[dict],
    aliases: dict,
    threshold: float,
) -> list[str]:
    """Snapshot tickers that moved hard but that nothing already fetched mentions.

    These are the only ones worth spending a metered Marketaux request on — a big move
    the digest currently has no explanation for. Biggest move first.
    """
    corpus = " ".join(item.get("title", "") for item in news_raw).lower()

    movers: list[tuple[float, str]] = []
    for entry in snapshot_cfg:
        ticker = entry["ticker"].upper()
        pm = normalize.normalize_finnhub_quote(ticker, quotes.get(ticker) or {})
        if not pm or abs(pm["pct_change"]) < threshold:
            continue
        names = [ticker] + list(aliases.get(ticker, []))
        if any(n.lower() in corpus for n in names):
            continue
        movers.append((abs(pm["pct_change"]), ticker))

    movers.sort(reverse=True)
    if movers:
        logger.info(
            "Uncovered movers (>=%.1f%%): %s",
            threshold, ", ".join(f"{t} {p:.1f}%" for p, t in movers),
        )
    return [t for _, t in movers]


def main() -> None:
    settings = config.load_settings()
    mock = config.is_mock_mode()

    if mock:
        logger.info("=== MOCK MODE — no live API calls, no Telegram send ===")
        secrets: dict = {}
    else:
        secrets = config.load_secrets()

    now_utc = datetime.now(timezone.utc)
    ny_tz = ZoneInfo("America/New_York")
    market_date = now_utc.astimezone(ny_tz).date()
    market_date_str = market_date.isoformat()

    if not mock and not market_calendar.is_us_market_open(market_date):
        logger.info("US market closed on %s — skipping digest", market_date_str)
        return

    cutoff = now_utc - timedelta(hours=settings.get("lookback_hours", 30))

    # Build theme helpers from config
    leaders_set = config.build_leaders_set(settings)
    primary_theme_map = config.build_primary_theme_map(settings)
    china_adrs_set = {t.upper() for t in settings.get("china_adrs", [])}
    themes_cfg = settings.get("themes", {})
    theme_order = settings.get("theme_order", [])
    scoreboard_etfs = settings.get("scoreboard_etfs", [])
    gauge_tickers = settings.get("gauges_tickers", [])

    # Quote tickers: scoreboard ETFs + gauges + anchor set for price badges + snapshot table
    scoreboard_etf_tickers = [e["etf"] for e in scoreboard_etfs]
    anchor_tickers = settings.get("anchor_tickers", [])
    snapshot_cfg = settings.get("stock_snapshot", [])
    snapshot_tickers = [e["ticker"].upper() for e in snapshot_cfg]
    all_quote_tickers = list(
        dict.fromkeys(scoreboard_etf_tickers + gauge_tickers + anchor_tickers + snapshot_tickers)
    )

    threshold = settings["selection"]["dedupe_title_threshold"]

    # ── 1. FETCH ──────────────────────────────────────────────────────────────
    raw_items = []
    quotes: dict = {}
    fear_greed: dict = {}

    rss_feeds = settings.get("rss_feeds", [])
    crypto_assets = settings.get("crypto_assets", [])
    rss_raw: list[dict] = []
    crypto_prices: list[dict] = []
    earnings: list[dict] = []

    if mock:
        logger.info("Loading mock fixtures...")
        finn_news = fetch_finnhub.fetch_market_news_mock()
        quotes = fetch_finnhub.fetch_quotes_mock()
        fear_greed = fetch_indicators.fetch_fear_greed_mock()
        rss_raw = fetch_rss.fetch_feeds_mock()
        crypto_prices = fetch_crypto.fetch_prices_mock(crypto_assets)
        earnings = fetch_earnings.fetch_calendar_mock(
            market_date=market_date, watchlist=set(snapshot_tickers)
        )
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

            # Yahoo fills only what Finnhub didn't return — never overwrites a Finnhub quote
            if settings.get("quotes", {}).get("yahoo_fallback", True):
                gaps = [t for t in all_quote_tickers if not (quotes.get(t) or {}).get("c")]
                if gaps:
                    quotes.update(fetch_yahoo.fetch_quotes(gaps, market_date=market_date))

            # RSS depth layer — equity + crypto publications
            rss_raw = fetch_rss.fetch_feeds(rss_feeds, client)

            # Relevance layer — per-ticker headlines for the snapshot watchlist
            if settings.get("ticker_news", {}).get("enabled", True):
                rss_raw += fetch_rss.fetch_ticker_feeds(
                    snapshot_tickers,
                    client,
                    max_tickers=settings["ticker_news"].get("max_tickers", 24),
                )

            # Primary-source layer — the issuer's own filing, not a report about it
            sec_cfg = settings.get("sec_filings", {})
            if sec_cfg.get("enabled", True):
                rss_raw += fetch_sec.fetch_filings(
                    snapshot_tickers, client,
                    lookback_hours=settings.get("lookback_hours", 30),
                    max_tickers=sec_cfg.get("max_tickers", 24),
                    request_delay=sec_cfg.get("request_delay", 0.15),
                )

            # Marketaux — only for movers nothing else covered. See fetch_marketaux docstring
            # for why it isn't used as a general source.
            mx_cfg = settings.get("marketaux", {})
            mx_key = secrets.get("MARKETAUX_API_KEY")
            if mx_cfg.get("enabled", True) and mx_key:
                uncovered = _uncovered_movers(
                    quotes, snapshot_cfg, rss_raw,
                    settings.get("ticker_aliases", {}),
                    threshold=mx_cfg.get("move_threshold", 4.0),
                )
                if uncovered:
                    rss_raw += fetch_marketaux.explain_movers(
                        uncovered, mx_key, client,
                        lookback_hours=settings.get("lookback_hours", 30),
                        min_match=mx_cfg.get("min_match_score", 40.0),
                        max_requests=mx_cfg.get("max_requests", 8),
                    )
                else:
                    logger.info("Marketaux: no uncovered movers — no requests spent")
            elif mx_cfg.get("enabled", True):
                logger.info("Marketaux: no MARKETAUX_API_KEY set — skipping")

            # Crypto spot prices (CoinGecko, no key)
            crypto_prices = fetch_crypto.fetch_prices(crypto_assets, client)

            # Forward-looking: which watched names report in the next week
            earnings = fetch_earnings.fetch_calendar(
                secrets["FINNHUB_API_KEY"], market_date,
                set(snapshot_tickers) | leaders_set, client,
                days_ahead=settings.get("earnings", {}).get("days_ahead", 7),
            )

            # CNN Fear & Greed
            fear_greed = fetch_indicators.fetch_fear_greed(client)

    # ── 2. NORMALIZE ──────────────────────────────────────────────────────────
    raw_items = normalize.normalize_finnhub_batch(finn_news)
    raw_items += normalize.normalize_rss_batch(rss_raw)
    logger.info("Normalized: %d raw items (Finnhub + RSS)", len(raw_items))

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
    # Fix mis-attributed per-ticker-feed items first; theme assignment reads item.tickers.
    relevance_index = rank.build_relevance_index(settings)
    rank.resolve_anchors(items, relevance_index)

    for i in items:
        tag.assign_region(i, china_adrs_set)
        tag.assign_theme(i, themes_cfg, theme_order, primary_theme_map)
        # A Finnhub wire story about stablecoins arrives tagged market="equity"; the theme
        # is the better signal for which market section it belongs in.
        if i.theme == "crypto":
            i.market = "crypto"

    # ── 6. DEDUPE ─────────────────────────────────────────────────────────────
    items = dedupe.filter_quality(items)
    items = dedupe.dedupe(items, threshold=threshold)

    # ── 7. ATTACH QUOTES ──────────────────────────────────────────────────────
    items = _attach_quotes(items, quotes, leaders_set)

    # ── 8. RANK + SELECT ──────────────────────────────────────────────────────
    items = rank.score_items(
        items, settings["rank_weights"], now_utc,
        settings.get("event_scoring", {}),
        relevance_index,
        settings.get("source_tiers", {}),
    )
    items = rank.select_top(
        items,
        max_total=settings["selection"]["max_items_total"],
        min_per_theme=settings["selection"].get("min_items_per_theme", 1),
        max_per_theme=settings["selection"].get("max_items_per_theme", 0),
        min_event=settings["selection"].get("min_event_score", 0.0),
        min_per_market=settings["selection"].get("min_items_per_market", {}),
        max_per_source=settings["selection"].get("max_items_per_source", 0),
    )

    # ── 8b. BUILD MARKET DATA ─────────────────────────────────────────────────
    scoreboard = rank.sector_scoreboard(quotes, scoreboard_etfs)
    rotation = rank.bucket_rotation(scoreboard)

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

    # Macro calendar comes from a file the local Futu publisher commits — the CI runner
    # can't reach OpenD itself. Missing or stale simply means no calendar this run.
    econ_events, econ_status = fetch_econ.load_calendar(
        Path(settings.get("econ_calendar", {}).get("path", "data/econ_calendar.json")),
        market_date=market_date,
        days_ahead=settings.get("econ_calendar", {}).get("days_ahead", 7),
        max_age_days=settings.get("econ_calendar", {}).get("max_age_days", 30),
        stale_after_days=settings.get("econ_calendar", {}).get("stale_after_days", 8),
    )

    snapshot_rows = render.build_snapshot_rows(
        quotes, snapshot_cfg, normalize.normalize_finnhub_quote
    )

    # A ticker that isn't trading yet (pre-launch ETF) or that neither source covers returns
    # c=0 and gets dropped silently everywhere downstream — say so instead.
    dead = [
        t for t in all_quote_tickers
        if not (quotes.get(t) or {}).get("c")
    ]
    if dead:
        logger.warning(
            "No usable quote from any source for %d configured ticker(s): %s",
            len(dead), ", ".join(dead),
        )

    indicators = {"fear_greed": fear_greed}
    date_str = market_date_str
    date_cn = render.date_header_cn(market_date)

    # Yesterday's one-liner, thesis and "how to verify" — so today can settle the account
    continuity_cfg = settings.get("continuity", {})
    prev_context = ""
    if continuity_cfg.get("enabled", True):
        prev_context = store.load_previous_context(
            Path("archive"),
            market_date,
            lookback_days=continuity_cfg.get("lookback_days", 4),
            max_chars=continuity_cfg.get("max_chars", 1200),
        )

    logger.info(
        "Market data ready: %d scoreboard ETFs, %d gauges, %d snapshot rows, fear_greed=%s",
        len(scoreboard),
        len(gauges),
        len(snapshot_rows),
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
        content = store.build_markdown(
            [], "", settings, now_utc, market_date=market_date,
            scoreboard=scoreboard, snapshot_rows=snapshot_rows,
        )
        store.save(content, now_utc, Path("archive"), settings, market_date=market_date)
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
        narrative = summarize.summarize_mock(
            date_str, gauges, scoreboard, indicators, items,
            snapshot_rows, prev_context, rotation, crypto_prices, earnings, econ_events,
        )
    else:
        try:
            with httpx.Client(timeout=120.0) as client:
                narrative = summarize.summarize_digest(
                    date_str, date_cn, gauges, scoreboard, indicators, items,
                    model=settings["model"]["openrouter_model"],
                    api_key=secrets["OPENROUTER_API_KEY"],
                    client=client,
                    snapshot_rows=snapshot_rows,
                    prev_context=prev_context,
                    rotation=rotation,
                    crypto_prices=crypto_prices,
                    earnings=earnings,
                    econ_events=econ_events,
                )
        except Exception as e:
            logger.warning("Summarization failed, continuing without narrative: %s", e)

    # ── 10. STORE ─────────────────────────────────────────────────────────────
    content = store.build_markdown(
        items, narrative, settings, now_utc, market_date=market_date,
        scoreboard=scoreboard, snapshot_rows=snapshot_rows, rotation=rotation,
        crypto_prices=crypto_prices, earnings=earnings, econ_events=econ_events,
    )
    archive_path = store.save(content, now_utc, Path("archive"), settings, market_date=market_date)
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
                market_date=market_date,
                scoreboard=scoreboard,
                snapshot_rows=snapshot_rows,
                rotation=rotation,
                crypto_prices=crypto_prices,
                earnings=earnings,
                econ_events=econ_events,
                econ_status=econ_status,
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
