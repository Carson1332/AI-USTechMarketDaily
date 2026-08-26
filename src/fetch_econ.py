"""Reader for the macro calendar published by tools/publish_econ_calendar.py.

Deliberately does NOT import `futu`: OpenD is a local gateway and the digest runs on a
GitHub Actions runner that cannot reach 127.0.0.1. The two halves are decoupled through a
committed JSON file, which also keeps futu-api out of requirements.txt.

Finnhub's /calendar/economic is premium (403), so this is the digest's only source of
"what macro prints are coming", and section 九 leans on it for a verifiable next-day check.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_PATH = Path("data/econ_calendar.json")


def load_calendar(
    path: Path | str = DEFAULT_PATH,
    market_date: date | None = None,
    days_ahead: int = 7,
    max_age_days: float = 30.0,
    stale_after_days: float = 8.0,
) -> tuple[list[dict], dict]:
    """Return (events within `days_ahead` of `market_date`, status).

    The status dict drives the staleness banner in the digest. The publisher is run by
    hand on a weekly cadence, so "nobody refreshed it" is the expected failure and has to
    be visible where the reader actually looks — not only in CI logs.

    A missing or stale file is normal, so every failure path returns ([], status) rather
    than breaking the digest.
    """
    status = {"present": False, "age_days": None, "with_consensus": 0, "note": ""}
    path = Path(path)
    if not path.exists():
        logger.info("No macro calendar at %s — digest will run without it", path)
        status["note"] = "沒有總經行事曆檔案"
        return [], status

    try:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Macro calendar unreadable (%s: %s)", type(e).__name__, e)
        status["note"] = "總經行事曆檔案讀不出來"
        return [], status

    captured_raw = payload.get("captured_at", "")
    try:
        captured = datetime.fromisoformat(captured_raw)
    except ValueError:
        logger.warning("Macro calendar has no usable captured_at — ignoring it")
        status["note"] = "總經行事曆缺少擷取時間"
        return [], status
    if captured.tzinfo is None:
        captured = captured.replace(tzinfo=timezone.utc)

    age_days = (datetime.now(timezone.utc) - captured).total_seconds() / 86400
    status.update(present=True, age_days=round(age_days, 1))

    if age_days > max_age_days:
        logger.warning(
            "Macro calendar captured %.0f days ago (limit %.0f) — ignoring it. "
            "Re-run: python tools/publish_econ_calendar.py", age_days, max_age_days,
        )
        status["note"] = f"總經行事曆已過期 {age_days:.0f} 天,本次未採用"
        return [], status

    market_date = market_date or datetime.now(timezone.utc).date()
    rows: list[dict] = []
    for event in payload.get("events", []):
        try:
            when = datetime.fromisoformat(event["timestamp"])
        except (KeyError, ValueError):
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        days_out = (when.date() - market_date).days
        if not 0 <= days_out <= days_ahead:
            continue
        rows.append({**event, "days_out": days_out, "when": when})

    rows.sort(key=lambda r: r["when"])
    if rows:
        # Consensus estimates are only published about a week ahead, so a file captured
        # long ago carries events with no estimate. That is not a bug in the data, but it
        # does gut the "verify tomorrow" section, which needs a number to check against.
        with_consensus = sum(1 for r in rows if r.get("consensus") is not None)
        status["with_consensus"] = with_consensus
        logger.info(
            "Macro calendar: %d event(s) in the next %d days, %d with a consensus "
            "estimate (file %.1f days old)",
            len(rows), days_ahead, with_consensus, age_days,
        )
    else:
        logger.info(
            "Macro calendar has no events in the next %d days — window may need extending",
            days_ahead,
        )

    # Age alone drives the reminder. Inferring staleness from a missing consensus count is
    # too indirect — a week-old file can still carry estimates for events that have already
    # passed, so it would stay silent exactly when the refresh is overdue.
    if age_days > stale_after_days:
        logger.warning(
            "Macro calendar is %.0f days old (refresh cadence is weekly) — run "
            "tools/refresh_econ_calendar.ps1", age_days,
        )
        missing = ""
        if rows and status["with_consensus"] == 0:
            missing = ",未來事件都沒有市場預估"
        elif not rows:
            missing = ",未來一週已無事件"
        status["note"] = f"總經行事曆已 {age_days:.0f} 天未更新{missing}"

    return rows, status
