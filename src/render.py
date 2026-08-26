"""Shared presentation helpers — used by summarize (prompt), store (archive) and notify (Telegram).

Every table here is built from real quote data, never from LLM output, so prices in the
digest can't drift from what Finnhub returned.
"""
from __future__ import annotations

import unicodedata
from datetime import date

_WEEKDAY_CN = ["一", "二", "三", "四", "五", "六", "日"]


def date_header_cn(d: date) -> str:
    """2026年8月6日（週四）"""
    return f"{d.year}年{d.month}月{d.day}日（週{_WEEKDAY_CN[d.weekday()]}）"


def _display_width(text: str) -> int:
    """Monospace column width — CJK glyphs occupy two cells."""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _display_width(text))


def _pct(value: float) -> str:
    return f"{value:+.1f}%"


def _arrow(value: float) -> str:
    if value >= 1.0:
        return "▲"
    if value <= -1.0:
        return "▼"
    return "－"


def build_snapshot_rows(quotes: dict, snapshot_cfg: list[dict], normalize_quote) -> list[dict]:
    """Turn config `stock_snapshot` entries + raw quotes into renderable rows.

    `normalize_quote` is normalize.normalize_finnhub_quote, injected to keep this module
    free of pipeline imports.
    """
    rows: list[dict] = []
    for entry in snapshot_cfg:
        ticker = entry.get("ticker", "").upper()
        raw = quotes.get(ticker)
        if not raw:
            continue
        pm = normalize_quote(ticker, raw)
        if not pm or not pm.get("current"):
            continue
        rows.append(
            {
                "ticker": ticker,
                "label": entry.get("label", ticker),
                "group": entry.get("group", ""),
                "current": pm["current"],
                "pct_change": pm["pct_change"],
            }
        )
    return rows


def _grouped(rows: list[dict]) -> list[tuple[str, list[dict]]]:
    """Preserve config order; consecutive rows sharing a group stay together."""
    out: list[tuple[str, list[dict]]] = []
    for r in rows:
        group = r.get("group", "")
        if not out or out[-1][0] != group:
            out.append((group, []))
        out[-1][1].append(r)
    return out


def snapshot_plain(rows: list[dict]) -> str:
    """Monospace-aligned block for Telegram <pre> and for the LLM prompt.

    Column widths are measured across every row so the columns line up through the
    group headings, not just within a group.
    """
    if not rows:
        return ""
    tw = max(_display_width(r["ticker"]) for r in rows)
    lw = max(_display_width(r["label"]) for r in rows)
    pw = max(len(format(r["current"], ",.2f")) for r in rows)

    lines: list[str] = []
    for group, members in _grouped(rows):
        if group:
            if lines:
                lines.append("")
            lines.append(f"【{group}】")
        for r in members:
            price = format(r["current"], ",.2f").rjust(pw)
            lines.append(
                f"{_pad(r['ticker'], tw)}  {_pad(r['label'], lw)}  "
                f"{price}  {_pct(r['pct_change']):>7}  {_arrow(r['pct_change'])}"
            )
    return "\n".join(lines)


def snapshot_markdown(rows: list[dict]) -> list[str]:
    """GitHub-flavoured table for the archive file."""
    if not rows:
        return []
    lines = ["| 分類 | 代號 | 名稱 | 現價 | 漲跌 |", "| --- | --- | --- | ---: | ---: |"]
    for r in rows:
        lines.append(
            f"| {r.get('group', '')} | {r['ticker']} | {r['label']} "
            f"| {r['current']:,.2f} | {_pct(r['pct_change'])} |"
        )
    return lines


def crypto_plain(rows: list[dict]) -> str:
    """Crypto spot block. Labelled 24h, not 'today' — it's a rolling window, and the
    digest must not let it read as a session close-to-close move like the equity table."""
    if not rows:
        return ""
    sw = max(_display_width(r["symbol"]) for r in rows)
    lw = max(_display_width(r["label"]) for r in rows)
    prices = [format(r["usd"], ",.2f") for r in rows]
    pw = max(len(p) for p in prices)
    return "\n".join(
        f"{_pad(r['symbol'], sw)}  {_pad(r['label'], lw)}  "
        f"{price.rjust(pw)}  {_pct(r['pct_24h']):>7}  {_arrow(r['pct_24h'])}"
        for r, price in zip(rows, prices)
    )


def crypto_markdown(rows: list[dict]) -> list[str]:
    if not rows:
        return []
    lines = ["| 資產 | 名稱 | 美元價 | 24h |", "| --- | --- | ---: | ---: |"]
    for r in rows:
        lines.append(f"| {r['symbol']} | {r['label']} | {r['usd']:,.2f} | {_pct(r['pct_24h'])} |")
    return lines


def _revenue_short(value) -> str:
    if not value:
        return "—"
    value = float(value)
    if value >= 1e9:
        return f"{value / 1e9:.2f}B"
    if value >= 1e6:
        return f"{value / 1e6:.0f}M"
    return f"{value:,.0f}"


def earnings_plain(rows: list[dict], horizon_days: int = 7) -> str:
    """Upcoming earnings, nearest first, with the day labelled relative to the digest date."""
    rows = [r for r in rows if 0 <= r.get("days_out", 99) <= horizon_days]
    if not rows:
        return ""
    labels = {0: "今天", 1: "明天", 2: "後天"}
    sw = max(_display_width(r["symbol"]) for r in rows)
    lines = []
    for r in rows:
        when = labels.get(r["days_out"], r["date"][5:])   # MM-DD beyond 後天
        eps = f"{r['eps_estimate']:.2f}" if r.get("eps_estimate") is not None else "—"
        lines.append(
            f"{_pad(r['symbol'], sw)}  {_pad(when, 6)} {_pad(r['hour'], 8)} "
            f"EPS預估 {eps:>6}  營收預估 {_revenue_short(r.get('revenue_estimate'))}"
        )
    return "\n".join(lines)


_STAR_MARK = {"HIGH": "★★★", "MEDIUM": "★★", "LOW": "★"}


def econ_plain(rows: list[dict], limit: int = 12) -> str:
    """Upcoming macro releases with consensus, so the digest can name a checkable event."""
    if not rows:
        return ""
    labels = {0: "今天", 1: "明天", 2: "後天"}
    lines = []
    for r in rows[:limit]:
        when = r["when"]
        day = labels.get(r["days_out"], when.strftime("%m-%d"))
        con = "—" if r.get("consensus") is None else f"{r['consensus']:g}"
        prev = "—" if r.get("previous") is None else f"{r['previous']:g}"
        lines.append(
            f"{_pad(day, 6)} {when.strftime('%H:%M')}UTC {_pad(_STAR_MARK.get(r.get('star',''), ''), 4)} "
            f"{r['title'][:52]}"
        )
        lines.append(f"{'':22}前值 {prev}  預估 {con}")
    return "\n".join(lines)


def econ_markdown(rows: list[dict]) -> list[str]:
    if not rows:
        return []
    lines = ["| 時間 (UTC) | 重要性 | 事件 | 前值 | 預估 |", "| --- | --- | --- | ---: | ---: |"]
    for r in rows:
        con = "—" if r.get("consensus") is None else f"{r['consensus']:g}"
        prev = "—" if r.get("previous") is None else f"{r['previous']:g}"
        lines.append(
            f"| {r['when'].strftime('%Y-%m-%d %H:%M')} | {r.get('star', '')} "
            f"| {r['title']} | {prev} | {con} |"
        )
    return lines


def earnings_markdown(rows: list[dict]) -> list[str]:
    if not rows:
        return []
    lines = ["| 代號 | 日期 | 時段 | EPS 預估 | 營收預估 |", "| --- | --- | --- | ---: | ---: |"]
    for r in rows:
        eps = f"{r['eps_estimate']:.2f}" if r.get("eps_estimate") is not None else "—"
        lines.append(
            f"| {r['symbol']} | {r['date']} | {r['hour']} | {eps} "
            f"| {_revenue_short(r.get('revenue_estimate'))} |"
        )
    return lines


def rotation_plain(buckets: list[dict]) -> str:
    """Bucket averages, best-to-worst — the 'money moved from X to Y' line."""
    if not buckets:
        return ""
    bw = max(_display_width(b["bucket"]) for b in buckets)
    return "\n".join(
        f"{_pad(b['bucket'], bw)}  {_pct(b['avg_pct']):>7}  {_arrow(b['avg_pct'])}"
        f"  ({b['count']}個板塊)"
        for b in buckets
    )


def rotation_markdown(buckets: list[dict]) -> list[str]:
    if not buckets:
        return []
    lines = ["| 大類 | 平均漲跌 | 板塊數 |", "| --- | ---: | ---: |"]
    for b in buckets:
        lines.append(f"| {b['bucket']} | {_pct(b['avg_pct'])} | {b['count']} |")
    return lines


def scoreboard_extremes(scoreboard: list[dict], n: int = 3) -> tuple[list[dict], list[dict]]:
    """Leaders and laggards from an already best-to-worst sorted scoreboard."""
    if not scoreboard:
        return [], []
    leaders = [e for e in scoreboard[:n] if e["pct_change"] > 0]
    laggards = [e for e in scoreboard[-n:] if e["pct_change"] < 0]
    laggards.reverse()  # worst first
    return leaders, laggards


def scoreboard_plain(scoreboard: list[dict], n: int = 3) -> str:
    """Compact 領漲/領跌 block — the part a reader actually scans."""
    leaders, laggards = scoreboard_extremes(scoreboard, n)
    parts = []
    if leaders:
        parts.append(
            "領漲  " + "   ".join(f"{e['label']} {_pct(e['pct_change'])}" for e in leaders)
        )
    if laggards:
        parts.append(
            "領跌  " + "   ".join(f"{e['label']} {_pct(e['pct_change'])}" for e in laggards)
        )
    return "\n".join(parts)
