from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import datetime

from src.models import NewsItem

logger = logging.getLogger(__name__)

_EVENT_CACHE: dict[int, re.Pattern] = {}


def _compile(phrases: list[str]) -> re.Pattern | None:
    """Case-insensitive alternation. Config groups related phrases on one comma-separated
    line for readability, so split those out here. Matched as substrings rather than
    \\b-bounded so multi-word and hyphenated forms ("steps down", "spin-off") both hit."""
    cleaned = []
    for entry in phrases or []:
        for phrase in str(entry).split(","):
            phrase = phrase.strip()
            if phrase:
                cleaned.append(re.escape(phrase))
    if not cleaned:
        return None
    # Longest-first so "price target" wins over a bare "target" prefix match.
    cleaned.sort(key=len, reverse=True)
    return re.compile("|".join(cleaned), re.IGNORECASE)


def _patterns(cfg: dict) -> tuple[re.Pattern | None, re.Pattern | None]:
    key = id(cfg)
    if key not in _EVENT_CACHE:
        _EVENT_CACHE[key] = (
            _compile(cfg.get("signals", [])),
            _compile(cfg.get("filler", [])),
        )
    return _EVENT_CACHE[key]


def build_relevance_index(settings: dict) -> dict[str, re.Pattern]:
    """Compile one pattern per watched ticker: the symbol plus any configured aliases.

    Event scoring answers "did something happen?"; this answers "does it concern anything
    you actually hold or track?". Without it a genuine event about a company you don't
    follow (a Shein IPO, a Porsche IT contract) outranks a smaller event about NVDA.
    """
    aliases: dict[str, list[str]] = settings.get("ticker_aliases", {}) or {}
    watched: set[str] = set()
    for entry in settings.get("stock_snapshot", []):
        watched.add(entry["ticker"].upper())
    for theme in settings.get("themes", {}).values():
        for leader in theme.get("leaders", []):
            watched.add(leader.upper())

    index: dict[str, re.Pattern] = {}
    for ticker in watched:
        # Word boundaries on symbols AND aliases. Short aliases are the dangerous ones:
        # without \b, AMZN's "AWS" matches inside "dr-aws-in", so "New Delhi draws in $73
        # billion" scored as an Amazon story. \b works for multi-word aliases too
        # ("Bloom Energy", "Advanced Micro").
        parts = [rf"\b{re.escape(ticker)}\b"]
        parts += [rf"\b{re.escape(a)}\b" for a in aliases.get(ticker, [])]
        index[ticker] = re.compile("|".join(parts), re.IGNORECASE)
    return index


def resolve_anchors(items: list[NewsItem], index: dict[str, re.Pattern]) -> None:
    """Correct the subject ticker on per-ticker-feed items. Must run BEFORE tagging.

    Yahoo's per-ticker feeds include loosely-related articles — a Coinbase story appears
    under MSFT because the body mentions Microsoft. Left alone that mis-tags the theme
    *and* badges the story with the wrong company's price. The title names the subject; a
    body mention does not, so a body-only match loses to any title match.
    """
    fixed = 0
    for item in items:
        if not item.anchor_ticker:
            continue
        title_hits = [t for t, pat in index.items() if pat.search(item.title)]
        if item.anchor_ticker in title_hits:
            continue
        if title_hits:
            item.anchor_ticker = title_hits[0]          # another company is the subject
        else:
            pattern = index.get(item.anchor_ticker)
            if not (pattern and pattern.search(item.summary[:400])):
                item.anchor_ticker = None                # subject isn't in the article
        item.tickers = [item.anchor_ticker] if item.anchor_ticker else []
        fixed += 1
    if fixed:
        logger.info("Re-anchored %d item(s) whose feed ticker wasn't the subject", fixed)


def relevance_score(item: NewsItem, index: dict[str, re.Pattern]) -> float:
    """1.0 if a watched name appears in the title, 0.6 if only in the summary, else 0.0.

    Also records the matched ticker on the item so the digest can badge it with a price.
    """
    if not index:
        return 0.0

    # Where the name appears matters. English headlines lead with the subject, so a ticker
    # in the opening is what the piece is about, while one near the end is usually an
    # aside — "Commodore 77 special edition uses more powerful AMD Artix XC7A100T" is a
    # retro-computer story, not an AMD story, and should not outrank real AMD news.
    matches = [(t, pat.search(item.title)) for t, pat in index.items()]
    matches = [(t, m) for t, m in matches if m]

    if matches:
        matches.sort(key=lambda x: x[1].start())
        subject, first = matches[0]
        title_hits = [t for t, _ in matches]
        if not item.anchor_ticker or item.anchor_ticker not in title_hits:
            item.anchor_ticker = subject
        item.tickers = item.tickers or title_hits
        position = first.start() / max(len(item.title), 1)
        return 1.0 if position <= 0.4 else 0.6

    body_hits = [t for t, pat in index.items() if pat.search(item.summary[:400])]
    if body_hits:
        item.tickers = item.tickers or body_hits
        return 0.6

    return 0.0


def source_score(item: NewsItem, tiers: dict, default: float = 0.3) -> float:
    """0..1 trust weight for where the story came from.

    Without this, the per-ticker Yahoo feeds win everything: they emit hundreds of items a
    day and every one mentions a watched ticker, so they max out relevance. Meanwhile the
    sources with the most signal per article — an SEC filing, a SemiAnalysis teardown —
    often don't name a ticker at all and score zero on relevance.

    Source names carry a suffix for per-ticker feeds ("Yahoo/NVDA", "SEC EDGAR/MRVL"), so
    match on the prefix before the slash.
    """
    if not tiers:
        return default
    family = item.source.split("/")[0].strip()
    if family in tiers:
        return float(tiers[family])
    for name, weight in tiers.items():
        if family.startswith(name):
            return float(weight)
    return default


def event_score(item: NewsItem, cfg: dict) -> float:
    """0..1 — does this headline report an event, or just describe the tape?

    "Marvell issues Google warrant for 59M shares" is an event. "Shares drift ahead of
    Nvidia earnings" is the tape with a headline attached. Recency alone can't tell them
    apart, which is why the wire one-liners used to dominate selection.
    """
    if not cfg.get("enabled", True):
        return 0.0
    signal_re, filler_re = _patterns(cfg)
    text = f"{item.title} {item.summary[:300]}"

    score = 0.0
    if signal_re:
        # Diminishing returns: two distinct signals is meaningfully better than one,
        # five is not meaningfully better than three.
        hits = len(set(m.lower() for m in signal_re.findall(text)))
        score = min(1.0, hits / 3.0)

    if filler_re and filler_re.search(item.title):
        score -= cfg.get("filler_penalty", 0.5)

    return max(0.0, min(1.0, score))


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


def score_items(
    items: list[NewsItem],
    weights: dict,
    now: datetime,
    event_cfg: dict | None = None,
    relevance_index: dict | None = None,
    source_tiers: dict | None = None,
) -> list[NewsItem]:
    """Compute rank_score for each item. Mutates items in-place, returns them."""
    if not items:
        return items

    event_cfg = event_cfg or {}
    relevance_index = relevance_index or {}
    source_tiers = source_tiers or {}
    source_counts = [float(i.source_count) for i in items]
    pct_changes = [
        abs(i.price_metric["pct_change"]) if i.price_metric else 0.0
        for i in items
    ]

    norm_counts = _minmax_normalize(source_counts)
    norm_moves = _minmax_normalize(pct_changes)

    w_evt = weights.get("event", 0.30)
    w_rel = weights.get("relevance", 0.20)
    w_src = weights.get("source", 0.25)
    w_cov = weights.get("coverage", 0.10)
    w_rec = weights.get("recency", 0.10)
    w_move = weights.get("move", 0.05)
    default_tier = weights.get("source_default", 0.3)

    for i, item in enumerate(items):
        item.event_score = event_score(item, event_cfg)
        item.relevance_score = relevance_score(item, relevance_index)
        item.source_score = source_score(item, source_tiers, default_tier)
        item.rank_score = (
            w_evt * item.event_score
            + w_rel * item.relevance_score
            + w_src * item.source_score
            + w_cov * norm_counts[i]
            + w_rec * recency_decay(item.published_at, now)
            + w_move * norm_moves[i]
        )

    items.sort(key=lambda x: x.rank_score, reverse=True)
    if event_cfg.get("enabled", True):
        strong = sum(1 for i in items if i.event_score >= 0.6)
        relevant = sum(1 for i in items if i.relevance_score > 0)
        logger.info(
            "Scoring: %d/%d items event>=0.6, %d/%d mention a watched name",
            strong, len(items), relevant, len(items),
        )
    return items


def select_top(
    items: list[NewsItem],
    max_total: int,
    min_per_theme: int = 1,
    max_per_theme: int = 0,
    min_event: float = 0.0,
    min_per_market: dict | None = None,
    max_per_source: int = 0,
) -> list[NewsItem]:
    """
    Select top items enforcing per-theme minimums and (optionally) a per-theme cap.
    Items must already be sorted by rank_score descending. max_per_theme=0 disables the cap.

    `min_event` drops items whose headline describes the tape or is an opinion column
    rather than reporting an event. A short digest of real events beats a full one padded
    with "Here's who could benefit" — so this filter is allowed to return fewer than
    max_total, and deliberately runs before the per-theme minimum.
    """
    if min_event > 0:
        eligible = [i for i in items if i.event_score >= min_event]
        if eligible:
            logger.info(
                "Event floor %.2f: %d/%d items eligible", min_event, len(eligible), len(items)
            )
            items = eligible
        else:
            logger.warning(
                "Event floor %.2f left nothing — ignoring it for this run", min_event
            )

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

    # Market quotas. Per-ticker feeds are all equities, so they flood the pool and can
    # crowd crypto out entirely — but crypto is a market the digest reports on in its own
    # section, not a theme that competes for slots. Reserve its floor before the free-for-all.
    for market, floor in (min_per_market or {}).items():
        have = sum(1 for i in selected if i.market == market)
        if have >= floor:
            continue
        for item in items:
            if have >= floor or len(selected) >= max_total:
                break
            if item.market == market and id(item) not in used_ids:
                selected.append(item)
                used_ids.add(id(item))
                have += 1
        if have < floor:
            logger.info("Market floor %s: only %d/%d eligible items", market, have, floor)

    # Second pass: fill remaining slots from the globally sorted list, but cap any single
    # theme. Without this, macro_other (the catch-all bucket every unmatched wire story
    # lands in) takes most of the digest and squeezes out the themes actually being tracked.
    counts: dict[str, int] = defaultdict(int)
    src_counts: dict[str, int] = defaultdict(int)
    for item in selected:
        counts[item.theme] += 1
        src_counts[item.source.split("/")[0]] += 1

    for item in items:
        if len(selected) >= max_total:
            break
        if id(item) in used_ids:
            continue
        if max_per_theme and counts[item.theme] >= max_per_theme:
            continue
        # Per-ticker feeds share one family name, so this caps "Yahoo" as a whole rather
        # than letting 24 separate Yahoo/<TICKER> sources each claim a slot.
        family = item.source.split("/")[0]
        if max_per_source and src_counts[family] >= max_per_source:
            continue
        selected.append(item)
        used_ids.add(id(item))
        counts[item.theme] += 1
        src_counts[family] += 1

    # Third pass: if the cap left the digest short (few themes had enough items), relax it
    # rather than removing it. Dropping the cap entirely just re-floods with the catch-all
    # theme, which is the problem the cap exists to prevent.
    if len(selected) < max_total and max_per_theme:
        relaxed = max_per_theme + 2
        for item in items:
            if len(selected) >= max_total:
                break
            if id(item) in used_ids or counts[item.theme] >= relaxed:
                continue
            selected.append(item)
            used_ids.add(id(item))
            counts[item.theme] += 1
        if len(selected) < max_total:
            logger.info(
                "Selection short of max_total (%d/%d) — per-theme cap held rather than "
                "padding with off-theme items", len(selected), max_total,
            )

    # Re-sort the final selection by score so the digest reads best-first
    selected.sort(key=lambda x: x.rank_score, reverse=True)

    theme_counts = {th: sum(1 for i in selected if i.theme == th) for th in by_theme}
    logger.info("Selected %d/%d items: %s", len(selected), len(items), theme_counts)
    return selected


def sector_scoreboard(quotes: dict, scoreboard_etfs: list[dict]) -> list[dict]:
    """Return [{label, etf, bucket, pct_change}] sorted best-to-worst using one ETF per sector."""
    result = []
    for entry in scoreboard_etfs:
        etf = entry["etf"].upper()
        q = quotes.get(etf)
        if q and q.get("c") and q.get("pc") and q["pc"] != 0:
            pct = round((q["c"] - q["pc"]) / q["pc"] * 100, 2)
            result.append({
                "label": entry["label"],
                "etf": etf,
                "bucket": entry.get("bucket", ""),
                "pct_change": pct,
            })
    result.sort(key=lambda x: x["pct_change"], reverse=True)
    logger.info("Sector scoreboard: %d ETFs", len(result))
    return result


def bucket_rotation(scoreboard: list[dict]) -> list[dict]:
    """Average each bucket's sectors to show where money moved, best-to-worst.

    A single sector ripping tells you little; a whole bucket moving against another
    is rotation. Equal-weighted on purpose — this is a direction read, not an index.
    """
    sums: dict[str, list[float]] = {}
    for entry in scoreboard:
        bucket = entry.get("bucket")
        if bucket:
            sums.setdefault(bucket, []).append(entry["pct_change"])

    result = [
        {"bucket": b, "avg_pct": round(sum(v) / len(v), 2), "count": len(v)}
        for b, v in sums.items()
    ]
    result.sort(key=lambda x: x["avg_pct"], reverse=True)
    if result:
        logger.info(
            "Rotation: %s",
            "  ".join(f"{r['bucket']} {r['avg_pct']:+.1f}%" for r in result),
        )
    return result
