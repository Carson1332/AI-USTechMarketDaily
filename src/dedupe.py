from __future__ import annotations

import logging
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from rapidfuzz import fuzz

from src.models import NewsItem

logger = logging.getLogger(__name__)

_TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "oc", "ref", "fbclid", "gclid", "cmp", "ito", "guccounter", "source",
})

_JUNK_RE = re.compile(
    r"\bstocks?\s+to\s+buy\b"
    r"|\bbest\s+.*\b(stocks?|etfs?)\b"
    r"|\bforget\b.*\bbuy\b"
    r"|\b\d+\s+(?:ai\s+)?(?:stocks?|etfs?|reasons?)\b"
    r"|\bshould\s+you\s+buy\b"
    r"|\bif\s+you'?d?\s+invested\b"
    r"|\bmotley\s+fool\b"
    r"|\b(zacks|moderate buy|strong buy)\b"
    r"|\bhere'?s\s+why\b.*\bcould\b",
    re.I,
)


def _canonical_url(url: str) -> str:
    """Strip only tracking params; keep identity params (e.g. ?id=) for dedup."""
    try:
        p = urlsplit(url.strip())
    except ValueError:
        return url.strip().lower()
    netloc = p.netloc.lower().removeprefix("www.")
    path = p.path.rstrip("/")
    kept = sorted((k, v) for k, v in parse_qsl(p.query) if k.lower() not in _TRACKING_PARAMS)
    return urlunsplit(("https", netloc, path, urlencode(kept), ""))


def filter_quality(items: list[NewsItem]) -> list[NewsItem]:
    kept = [it for it in items if not _JUNK_RE.search(it.title)]
    logger.info("Quality filter: %d → %d", len(items), len(kept))
    return kept


def _merge(keeper: NewsItem, duplicate: NewsItem) -> NewsItem:
    """Merge duplicate into keeper: increment source_count, keep best fields."""
    keeper.source_count += duplicate.source_count
    # Prefer item with non-None sentiment, or stronger absolute sentiment
    if keeper.sentiment is None and duplicate.sentiment is not None:
        keeper.sentiment = duplicate.sentiment
    elif (
        keeper.sentiment is not None
        and duplicate.sentiment is not None
        and abs(duplicate.sentiment) > abs(keeper.sentiment)
    ):
        keeper.sentiment = duplicate.sentiment
    # Keep the earlier publication time
    if duplicate.published_at < keeper.published_at:
        keeper.published_at = duplicate.published_at
    # Keep the longer summary
    if len(duplicate.summary) > len(keeper.summary):
        keeper.summary = duplicate.summary
    return keeper


def dedupe(items: list[NewsItem], threshold: int = 85) -> list[NewsItem]:
    """
    Two-pass deduplication:
      Pass 1 — exact URL key match
      Pass 2 — fuzzy title match using token_sort_ratio
    """
    # Pass 1: URL dedup
    url_seen: dict[str, NewsItem] = {}
    for item in items:
        key = _canonical_url(item.url)
        if key in url_seen:
            url_seen[key] = _merge(url_seen[key], item)
            logger.debug("URL dedup: merged %r", item.title[:60])
        else:
            url_seen[key] = item

    url_deduped = list(url_seen.values())

    # Pass 2: fuzzy title dedup
    accepted: list[NewsItem] = []
    for item in url_deduped:
        merged = False
        for existing in accepted:
            score = fuzz.token_sort_ratio(item.title, existing.title)
            if score >= threshold:
                _merge(existing, item)
                logger.debug(
                    "Fuzzy dedup (score=%d): %r ← %r",
                    score,
                    existing.title[:50],
                    item.title[:50],
                )
                merged = True
                break
        if not merged:
            accepted.append(item)

    logger.info("Dedup: %d → %d items", len(items), len(accepted))
    return accepted
