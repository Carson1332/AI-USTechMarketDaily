from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime

from src.models import NewsItem

logger = logging.getLogger(__name__)


def recency_decay(published_at: datetime, now: datetime, half_life_hours: float = 12.0) -> float:
    """Exponential decay: 1.0 at publication, 0.5 at half_life_hours, 0.25 at 2×half_life."""
    age_hours = (now - published_at).total_seconds() / 3600
    age_hours = max(0.0, age_hours)
    return max(0.0, min(1.0, 0.5 ** (age_hours / half_life_hours)))


def _minmax_normalize(values: list[float]) -> list[float]:
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi == lo:
        return [1.0] * len(values)
    return [(v - lo) / (hi - lo) for v in values]


def score_items(items: list[NewsItem], weights: dict, now: datetime) -> list[NewsItem]:
    """Compute rank_score for each item. Mutates items in-place, returns them."""
    if not items:
        return items

    source_counts = [float(i.source_count) for i in items]
    pct_changes = [
        abs(i.price_metric["pct_change"]) if i.price_metric else 0.0
        for i in items
    ]

    norm_counts = _minmax_normalize(source_counts)
    norm_moves = _minmax_normalize(pct_changes)

    w_cov = weights.get("coverage", 0.35)
    w_rec = weights.get("recency", 0.25)
    w_sent = weights.get("sentiment", 0.20)
    w_move = weights.get("move", 0.20)

    for i, item in enumerate(items):
        decay = recency_decay(item.published_at, now)
        sent_score = abs(item.sentiment) if item.sentiment is not None else 0.0
        item.rank_score = (
            w_cov * norm_counts[i]
            + w_rec * decay
            + w_sent * sent_score
            + w_move * norm_moves[i]
        )

    items.sort(key=lambda x: x.rank_score, reverse=True)
    return items


def select_top(items: list[NewsItem], max_total: int, min_per_theme: int = 1) -> list[NewsItem]:
    """
    Select top items enforcing per-theme minimums.
    Items must already be sorted by rank_score descending.
    """
    by_theme: dict[str, list[NewsItem]] = defaultdict(list)
    for item in items:
        by_theme[item.theme].append(item)

    selected: list[NewsItem] = []
    used_ids: set[int] = set()

    # First pass: guarantee min_per_theme from each theme that has items
    for theme_items in by_theme.values():
        for item in theme_items[:min_per_theme]:
            selected.append(item)
            used_ids.add(id(item))

    # Second pass: fill remaining slots from the globally sorted list
    for item in items:
        if len(selected) >= max_total:
            break
        if id(item) not in used_ids:
            selected.append(item)
            used_ids.add(id(item))

    # Re-sort the final selection by score so the digest reads best-first
    selected.sort(key=lambda x: x.rank_score, reverse=True)

    theme_counts = {th: sum(1 for i in selected if i.theme == th) for th in by_theme}
    logger.info("Selected %d/%d items: %s", len(selected), len(items), theme_counts)
    return selected


def sector_scoreboard(quotes: dict, scoreboard_etfs: list[dict]) -> list[dict]:
    """Return [{label, etf, pct_change}] sorted best-to-worst using one ETF per sector."""
    result = []
    for entry in scoreboard_etfs:
        etf = entry["etf"].upper()
        q = quotes.get(etf)
        if q and q.get("c") and q.get("pc") and q["pc"] != 0:
            pct = round((q["c"] - q["pc"]) / q["pc"] * 100, 2)
            result.append({"label": entry["label"], "etf": etf, "pct_change": pct})
    result.sort(key=lambda x: x["pct_change"], reverse=True)
    logger.info("Sector scoreboard: %d ETFs", len(result))
    return result
