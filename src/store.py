from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from src import render
from src.models import NewsItem

logger = logging.getLogger(__name__)

# Narrative sections look like "三、板塊誰領升誰領跌" — number varies as the prompt evolves,
# so continuity matches on the title text, not the index.
_SECTION_RE = re.compile(r"^[一二三四五六七八九十]+、\s*(.*)$")

# Emitted by summarize.summarize_mock — marks an archive file a --mock run wrote.
_MOCK_MARKER = "[MOCK 市場摘要]"


def _split_sections(narrative: str) -> list[tuple[str, str]]:
    """Split a narrative into (section_title, body) pairs."""
    sections: list[tuple[str, list[str]]] = []
    for line in narrative.splitlines():
        m = _SECTION_RE.match(line.strip())
        if m:
            sections.append((m.group(1).strip(), []))
        elif sections:
            sections[-1][1].append(line)
    return [(title, "\n".join(body).strip()) for title, body in sections]


def _extract_narrative(content: str) -> str:
    """Pull the LLM narrative out of an archive file (between the H1 and the first rule)."""
    body = content.split("\n# ", 1)[-1]
    body = body.split("\n", 1)[-1]          # drop the H1 line itself
    return body.split("\n---", 1)[0].strip()


def load_previous_context(
    archive_dir: Path,
    market_date: date,
    lookback_days: int = 4,
    max_chars: int = 1200,
) -> str:
    """Return a short recap of the most recent prior digest, for narrative continuity.

    Keeps only the sections worth settling against today: the one-liner the reader was
    left with, the running thesis, and the check the model promised to verify. Returns
    "" when no recent archive exists (first run, or a long market holiday).
    """
    for back in range(1, lookback_days + 1):
        prev_date = market_date - timedelta(days=back)
        path = archive_dir / f"{prev_date.isoformat()}.md"
        if not path.exists():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning("Could not read previous archive %s: %s", path, e)
            return ""
        if _MOCK_MARKER in content:
            # A --mock run also writes to archive/; never feed its placeholder text back.
            logger.info("Continuity: skipping mock archive %s", path.name)
            continue
        narrative = _extract_narrative(content)
        if not narrative:
            return ""

        narrative = re.sub(r"^以上為市場資訊.*$", "", narrative, flags=re.M).rstrip()

        wanted = ("一句話", "敘事", "驗證")
        picked = [
            f"{title}\n{body}"
            for title, body in _split_sections(narrative)
            if any(k in title for k in wanted) and body
        ]
        if not picked:
            return ""
        recap = f"（上一個交易日 {prev_date.isoformat()} 的說法）\n" + "\n\n".join(picked)
        logger.info("Continuity: loaded %s (%d chars)", path.name, len(recap))
        return recap[:max_chars]

    logger.info("Continuity: no archive found in the last %d days", lookback_days)
    return ""


def build_markdown(
    items: list[NewsItem],
    narrative: str,
    settings: dict,
    now_utc: datetime,
    market_date: date,
    scoreboard: list[dict] | None = None,
    snapshot_rows: list[dict] | None = None,
    rotation: list[dict] | None = None,
    crypto_prices: list[dict] | None = None,
    earnings: list[dict] | None = None,
    econ_events: list[dict] | None = None,
) -> str:
    """Build the full archive Markdown document."""
    date_str = market_date.isoformat()
    tz = ZoneInfo(settings.get("timezone", "Australia/Perth"))
    generated_at = now_utc.astimezone(tz).isoformat()

    active_themes = sorted({item.theme for item in items})

    lines: list[str] = [
        "---",
        f'date: "{date_str}"',
        f"items_total: {len(items)}",
        f"themes: [{', '.join(active_themes)}]",
        f'generated_at: "{generated_at}"',
        "---",
        "",
        f"# Daily Market Digest — {date_str}",
        "",
        f"{settings.get('telegram', {}).get('header_prefix', '📊 市場日報')} | "
        f"{render.date_header_cn(market_date)}",
        "",
    ]

    # LLM narrative (7-section Chinese text)
    if narrative:
        lines.append(narrative)
        lines.append("")

    # Forward-looking macro calendar
    if econ_events:
        lines.append("---")
        lines.append("")
        lines.append("## 未來一週總經數據")
        lines.append("")
        lines.extend(render.econ_markdown(econ_events))
        lines.append("")

    # Forward-looking earnings calendar
    if earnings:
        lines.append("---")
        lines.append("")
        lines.append("## 未來一週財報")
        lines.append("")
        lines.extend(render.earnings_markdown(earnings))
        lines.append("")

    # Crypto spot — 24h rolling, not a session move
    if crypto_prices:
        lines.append("---")
        lines.append("")
        lines.append("## 加密現貨 (24h)")
        lines.append("")
        lines.extend(render.crypto_markdown(crypto_prices))
        lines.append("")

    # Computed bucket rotation — where money moved between big categories
    if rotation:
        lines.append("---")
        lines.append("")
        lines.append("## 板塊輪動")
        lines.append("")
        lines.extend(render.rotation_markdown(rotation))
        lines.append("")

    # Computed stock snapshot — same numbers the LLM was given
    if snapshot_rows:
        lines.append("---")
        lines.append("")
        lines.append("## 個股快照")
        lines.append("")
        lines.extend(render.snapshot_markdown(snapshot_rows))
        lines.append("")

    # Computed sector scoreboard
    if scoreboard:
        lines.append("---")
        lines.append("")
        lines.append("## 板塊 ETF 計分板")
        lines.append("")
        for e in scoreboard:
            lines.append(f"- {e['label']} ({e['etf']}): {e['pct_change']:+.1f}%")
        lines.append("")

    # Story Index
    lines.append("---")
    lines.append("")
    lines.append("## Story Index")
    lines.append("")

    themes_cfg = settings.get("themes", {})
    theme_order = settings.get("theme_order", [])
    present_themes = sorted({item.theme for item in items})
    all_keys = theme_order + [k for k in present_themes if k not in theme_order]

    by_theme: dict[str, list[NewsItem]] = defaultdict(list)
    for item in items:
        by_theme[item.theme].append(item)

    for key in all_keys:
        theme_items = by_theme.get(key, [])
        if not theme_items:
            continue
        label = themes_cfg.get(key, {}).get("label", key)
        lines.append(f"### {label}")
        lines.append("")
        for item in theme_items:
            pm = item.price_metric
            badge = f"**[{pm['symbol']} {pm['pct_change']:+.1f}%]** " if pm else ""
            source_badge = f" ×{item.source_count}" if item.source_count > 1 else ""
            sent_str = f" · sentiment {item.sentiment:+.2f}" if item.sentiment is not None else ""
            region_flag = {"US": "🇺🇸", "China": "🇨🇳", "HK": "🇭🇰", "Global": "🌍"}.get(item.region, "🌍")
            lines.append(
                f"- {region_flag} {badge}[{item.title}]({item.url})"
                f" *({item.source}{source_badge})*{sent_str}"
            )
        lines.append("")

    return "\n".join(lines)


def save(content: str, now_utc: datetime, archive_dir: Path, settings: dict, market_date: date) -> Path:
    """Write the digest to archive/YYYY-MM-DD.md using the US market date."""
    archive_dir.mkdir(parents=True, exist_ok=True)
    path = archive_dir / f"{market_date.isoformat()}.md"
    path.write_text(content, encoding="utf-8")
    logger.info("Archive written: %s", path)
    return path
