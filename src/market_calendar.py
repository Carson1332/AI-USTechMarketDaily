from __future__ import annotations

from datetime import date

import exchange_calendars as xcals

_XNYS = xcals.get_calendar("XNYS")


def is_us_market_open(d: date | None = None) -> bool:
    """Return True if the NYSE is open for its regular session on date d (default today)."""
    if d is None:
        d = date.today()
    return _XNYS.is_session(d)
