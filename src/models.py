from __future__ import annotations

import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class NewsItem:
    title: str
    source: str
    url: str
    published_at: datetime          # must be UTC-aware
    summary: str
    tickers: list[str]
    sentiment: float | None         # -1..1 from Alpha Vantage; None for Finnhub-only items
    region: str                     # "US" | "China" | "HK" | "Global"
    price_metric: dict | None       # {"symbol": "NVDA", "pct_change": 4.2} or None
    source_count: int = 1           # incremented during dedup
    rank_score: float = 0.0
    theme: str = "macro_other"
    anchor_ticker: str | None = None

    def __post_init__(self) -> None:
        if self.published_at.tzinfo is None:
            raise ValueError(f"published_at must be UTC-aware, got naive datetime for: {self.title!r}")

    @property
    def url_key(self) -> str:
        """Canonical URL key stripped of query params and trailing slashes."""
        p = urllib.parse.urlparse(self.url)
        return (p.netloc + p.path).rstrip("/").lower()

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "source": self.source,
            "url": self.url,
            "published_at": self.published_at.isoformat(),
            "summary": self.summary,
            "tickers": self.tickers,
            "sentiment": self.sentiment,
            "region": self.region,
            "theme": self.theme,
            "price_metric": self.price_metric,
            "source_count": self.source_count,
            "rank_score": round(self.rank_score, 4),
        }
