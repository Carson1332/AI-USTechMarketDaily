from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from src.models import NewsItem

logger = logging.getLogger(__name__)


def build_markdown(
    items: list[NewsItem],
    narrative: str,
    settings: dict,
    now_utc: datetime,
    scoreboard: list[dict] | None = None,
) -> str:
    """Build the full archive Markdown document."""
    tz = ZoneInfo(settings.get("timezone", "Australia/Perth"))
    local_dt = now_utc.astimezone(tz)
    date_str = local_dt.strftime("%Y-%m-%d")
    generated_at = local_dt.isoformat()

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
    ]

    # LLM narrative (7-section Chinese text)
    if narrative:
        lines.append(narrative)
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


def save(content: str, now_utc: datetime, archive_dir: Path, settings: dict) -> Path:
    """Write the digest to archive/YYYY-MM-DD.md using Perth/AWST date."""
    tz = ZoneInfo(settings.get("timezone", "Australia/Perth"))
    local_date = now_utc.astimezone(tz).date()
    archive_dir.mkdir(parents=True, exist_ok=True)
    path = archive_dir / f"{local_date.isoformat()}.md"
    path.write_text(content, encoding="utf-8")
    logger.info("Archive written: %s", path)
    return path
