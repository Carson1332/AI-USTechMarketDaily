"""Yahoo Finance quote fallback for tickers Finnhub doesn't cover.

Deliberately a *fallback*, never the primary source: yfinance scrapes unofficial Yahoo
endpoints, breaks when Yahoo changes auth, and gets rate-limited from CI datacenter IPs.
Every failure path here returns {} so a broken Yahoo costs a few rows, not the digest.

Returns quotes in the Finnhub shape ({"c": last, "pc": prev_close}) so everything
downstream — normalize, rank, render — works without knowing where the data came from.
"""
from __future__ import annotations

import logging
from datetime import date

logger = logging.getLogger(__name__)


def _extract(df, ticker: str, multi: bool):
    """Pull the Close series for one ticker out of a yf.download frame."""
    if multi:
        if ticker not in df.columns.get_level_values(0):
            return None
        return df[ticker]["Close"].dropna()
    return df["Close"].dropna()


def fetch_quotes(
    tickers: list[str],
    market_date: date | None = None,
    timeout: int = 30,
) -> dict[str, dict]:
    """Fetch last/previous close for `tickers`.

    When `market_date` is given, a ticker whose most recent daily bar isn't that date is
    dropped — a stale bar reported as today's move is worse than a missing row.
    """
    tickers = [t.upper() for t in dict.fromkeys(tickers)]
    if not tickers:
        return {}

    try:
        import pandas as pd
        import yfinance as yf
    except ImportError as e:
        logger.warning("Yahoo fallback unavailable (%s); skipping %d ticker(s)", e, len(tickers))
        return {}

    # yfinance logs a multi-line ERROR per unknown symbol. Since being asked for symbols
    # that don't resolve is the normal case here, that noise reads as a failed run in CI —
    # mute it and report the outcome ourselves below.
    yf_logger = logging.getLogger("yfinance")
    prior_level = yf_logger.level
    yf_logger.setLevel(logging.CRITICAL)
    try:
        df = yf.download(
            tickers,
            period="5d",
            interval="1d",
            progress=False,
            group_by="ticker",
            auto_adjust=False,
            threads=True,
            timeout=timeout,
        )
    except Exception as e:
        logger.warning("Yahoo fallback failed (%s: %s); skipping %d ticker(s)",
                       type(e).__name__, e, len(tickers))
        return {}
    finally:
        yf_logger.setLevel(prior_level)

    if df is None or df.empty:
        logger.warning("Yahoo fallback returned no data for %d ticker(s)", len(tickers))
        return {}

    multi = isinstance(df.columns, pd.MultiIndex)
    quotes: dict[str, dict] = {}
    stale: list[str] = []
    missing: list[str] = []

    for ticker in tickers:
        try:
            closes = _extract(df, ticker, multi)
        except (KeyError, IndexError):
            closes = None
        if closes is None or len(closes) < 2:
            missing.append(ticker)
            continue

        if market_date is not None:
            last_bar = closes.index[-1]
            bar_date = last_bar.date() if hasattr(last_bar, "date") else last_bar
            if bar_date != market_date:
                stale.append(f"{ticker}({bar_date})")
                continue

        quotes[ticker] = {"c": float(closes.iloc[-1]), "pc": float(closes.iloc[-2])}

    if quotes:
        logger.info("Yahoo fallback: filled %d/%d — %s",
                    len(quotes), len(tickers), ", ".join(sorted(quotes)))
    if missing:
        logger.warning("Yahoo fallback: no data for %s", ", ".join(missing))
    if stale:
        logger.warning("Yahoo fallback: stale bar (expected %s), dropped %s",
                       market_date, ", ".join(stale))
    return quotes


def fetch_quotes_mock(*_args, **_kwargs) -> dict[str, dict]:
    """Mock mode makes no network calls — the fixture is the only quote source."""
    return {}
