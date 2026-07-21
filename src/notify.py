from __future__ import annotations

import html
import logging
import re
from collections import defaultdict
from datetime import date, datetime

import httpx

from src.models import NewsItem

logger = logging.getLogger(__name__)

_TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"
_MSG_LIMIT = 4096
_REGION_EMOJI = {"US": "🇺🇸", "China": "🇨🇳", "HK": "🇭🇰", "Global": "🌍"}


class Notifier:
    """Base interface — implement send() to add new delivery channels."""
    def send(self, text: str) -> None:
        raise NotImplementedError


class TelegramNotifier(Notifier):
    def __init__(self, token: str, chat_id: str, disable_preview: bool = True):
        self.token = token
        self.chat_id = chat_id
        self.disable_preview = disable_preview

    def send(self, text: str) -> None:
        send_message(self.token, self.chat_id, text, self.disable_preview)


def _escape(text: str) -> str:
    """HTML-escape for Telegram HTML parse mode."""
    return html.escape(str(text))


def _strip_markdown(text: str) -> str:
    """Remove Markdown symbols that show up literally in Telegram HTML mode."""
    lines = []
    for line in text.splitlines():
        line = re.sub(r"^#{1,6}\s*", "", line)
        line = re.sub(r"\*{1,3}(.*?)\*{1,3}", r"\1", line)
        line = re.sub(r"_{2}(.*?)_{2}", r"\1", line)
        lines.append(line)
    return "\n".join(lines)


def _format_item(item: NewsItem) -> str:
    pm = item.price_metric
    price_tag = ""
    if pm:
        sign = "+" if pm["pct_change"] >= 0 else ""
        price_tag = f"<b>[{_escape(pm['symbol'])} {sign}{pm['pct_change']:.1f}%]</b> "

    sent_tag = ""
    if item.sentiment is not None:
        sign = "+" if item.sentiment >= 0 else ""
        sent_tag = f" <i>(sentiment {sign}{item.sentiment:.2f})</i>"

    source_count = f" ×{item.source_count}" if item.source_count > 1 else ""
    flag = _REGION_EMOJI.get(item.region, "🌍")

    return (
        f"• {flag} {price_tag}"
        f'<a href="{_escape(item.url)}">{_escape(item.title)}</a>'
        f" <i>({_escape(item.source)}{source_count})</i>{sent_tag}"
    )


def format_bullet_brief(
    items: list[NewsItem],
    settings: dict,
    market_date: date,
) -> str:
    """One scannable message: all stories grouped by theme with tappable links."""
    date_str = market_date.strftime("%d %b %Y")

    by_theme: dict[str, list[NewsItem]] = defaultdict(list)
    for item in items:
        by_theme[item.theme].append(item)

    theme_order = settings.get("theme_order", [])
    themes_cfg = settings.get("themes", {})

    parts = [f"<b>Stories — {date_str}</b>", ""]
    all_keys = theme_order + [k for k in by_theme if k not in theme_order]
    for key in all_keys:
        theme_items = by_theme.get(key, [])
        if not theme_items:
            continue
        label = themes_cfg.get(key, {}).get("label", key)
        parts.append(f"<b>{_escape(label)}</b>")
        for item in theme_items:
            parts.append(_format_item(item))
        parts.append("")
    return "\n".join(parts)


def format_theme_block(
    label: str,
    summary_text: str,
    header_prefix: str,
    market_date: date,
) -> str:
    """LLM prose analysis for one theme — links live in the bullet brief, not here."""
    date_str = market_date.strftime("%d %b %Y")

    parts = [f"<b>{_escape(header_prefix)} — {_escape(label)} | {date_str}</b>", ""]

    if summary_text.strip():
        parts.append(_escape(_strip_markdown(summary_text.strip())))

    return "\n".join(parts)


def split_message(text: str, limit: int = _MSG_LIMIT) -> list[str]:
    """Split text into chunks that fit within Telegram's message limit."""
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    paragraphs = text.split("\n\n")
    current = ""

    for para in paragraphs:
        candidate = (current + "\n\n" + para).lstrip("\n") if current else para
        if len(candidate) <= limit:
            current = candidate
        else:
            if current:
                chunks.append(current)
            if len(para) > limit:
                lines = para.split("\n")
                sub = ""
                for line in lines:
                    candidate_line = (sub + "\n" + line).lstrip("\n") if sub else line
                    if len(candidate_line) <= limit:
                        sub = candidate_line
                    else:
                        if sub:
                            chunks.append(sub)
                        sub = line[:limit]
                if sub:
                    current = sub
                else:
                    current = ""
            else:
                current = para

    if current:
        chunks.append(current)

    return chunks


def send_message(
    token: str,
    chat_id: str,
    text: str,
    disable_preview: bool = True,
) -> None:
    url = _TELEGRAM_URL.format(token=token)
    body = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": disable_preview,
    }
    with httpx.Client(timeout=30.0) as client:
        response = client.post(url, json=body)
    if not response.is_success:
        raise RuntimeError(
            f"Telegram sendMessage failed {response.status_code}: {response.text[:300]}"
        )
    logger.debug("Telegram: sent message (%d chars)", len(text))


def send_digest(
    token: str,
    chat_id: str,
    items: list[NewsItem],
    narrative: str,
    settings: dict,
    now_utc: datetime,
    market_date: date,
    scoreboard: list[dict] | None = None,
) -> None:
    """Send the daily digest: header → narrative → bullet brief."""
    tg_cfg = settings.get("telegram", {})
    header_prefix = tg_cfg.get("header_prefix", "📈 Daily Market Digest")
    disable_preview = tg_cfg.get("disable_web_page_preview", True)

    if not items:
        send_message(token, chat_id, "📰 No significant news today.", disable_preview)
        return

    date_str = market_date.strftime("%d %b %Y")
    total = len(items)

    # 1. Header — date + count + regions + compact scoreboard
    active_regions = sorted({item.region for item in items})
    region_flags = " ".join(_REGION_EMOJI.get(r, "🌍") for r in active_regions)
    header_parts = [
        f"<b>{_escape(header_prefix)}</b>",
        f"📅 {date_str} · {total} stories · {region_flags}",
    ]
    if scoreboard:
        # Best 3 + worst 1 as a quick snapshot
        top = scoreboard[:3]
        worst = [scoreboard[-1]] if len(scoreboard) > 3 else []
        entries = top + (["…"] if worst else []) + worst
        board_line = " · ".join(
            f"{e['label']} {e['pct_change']:+.1f}%" if isinstance(e, dict) else e
            for e in entries
        )
        header_parts.append(f"<b>板塊:</b> <i>{_escape(board_line)}</i>")
    send_message(token, chat_id, "\n".join(header_parts), disable_preview)

    # 2. Narrative — Chinese 7-section prose from DeepSeek
    if narrative and narrative.strip():
        for chunk in split_message(_escape(_strip_markdown(narrative))):
            send_message(token, chat_id, chunk, disable_preview)

    # 3. Bullet brief — all story links grouped by theme
    brief = format_bullet_brief(items, settings, market_date)
    for chunk in split_message(brief):
        send_message(token, chat_id, chunk, disable_preview)
