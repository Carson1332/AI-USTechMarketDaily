from __future__ import annotations

import re

from src.models import NewsItem

_CHINA_KW = re.compile(
    r"\b(China|Chinese|Beijing|PBOC|Shanghai|Shenzhen|CSI|A-share|A-shares|yuan|renminbi|RMB|Alibaba|Baidu|JD\.com|PDD|Temu|Meituan)\b",
    re.IGNORECASE,
)
_HK_KW = re.compile(
    r"\b(Hong Kong|Hang Seng|HKEX|HSI|HKD|Hong Kong Dollar)\b",
    re.IGNORECASE,
)
_US_KW = re.compile(
    r"\b(Fed|Federal Reserve|S&P|Nasdaq|NYSE|Wall Street|SEC|Treasury|FOMC|Dow|S&P 500)\b",
    re.IGNORECASE,
)


def assign_region(item: NewsItem, china_adrs_set: set[str]) -> NewsItem:
    """Set item.region based on ticker ADR list then keyword scan. Returns item."""
    if any(t.upper() in china_adrs_set for t in item.tickers):
        item.region = "China"
        return item
    text = item.title + " " + item.summary
    if _HK_KW.search(text):
        item.region = "HK"
    elif _CHINA_KW.search(text):
        item.region = "China"
    elif _US_KW.search(text):
        item.region = "US"
    else:
        item.region = "Global"
    return item


def assign_theme(
    item: NewsItem,
    themes_cfg: dict,
    theme_order: list[str],
    primary_theme_map: dict[str, str],
) -> NewsItem:
    """
    Set item.theme and item.anchor_ticker deterministically.

    Priority 1: Leader ticker signal — first ticker in item.tickers that maps to a theme.
                 Alpha Vantage returns tickers in relevance order, so position = proxy for relevance.
    Priority 2: Keyword signal — highest keyword-match count across themes in theme_order.
                 Ties broken by lower theme_order index (earlier = higher priority).
    Priority 3: Fallback → "macro_other".
    """
    # Step 1 — leader ticker signal
    for ticker in item.tickers:
        theme_key = primary_theme_map.get(ticker.upper())
        if theme_key:
            item.theme = theme_key
            item.anchor_ticker = ticker.upper()
            return item

    # Step 2 — keyword signal
    text = (item.title + " " + item.summary).lower()
    best_count = 0
    best_index = len(theme_order)
    best_theme = "macro_other"

    for idx, theme_key in enumerate(theme_order):
        keywords = themes_cfg.get(theme_key, {}).get("keywords", [])
        count = sum(
            1 for kw in keywords
            if re.search(r"\b" + re.escape(kw.lower()) + r"\b", text)
        )
        if count > best_count or (count == best_count and idx < best_index and count > 0):
            best_count = count
            best_index = idx
            best_theme = theme_key

    item.theme = best_theme
    return item
