"""SEC EDGAR 8-K/6-K filings for watchlist tickers — the primary-source event layer.

Every other feed is journalism about an event. This is the event itself: an 8-K is what a
US issuer is legally required to file when something material happens, filed same-day, with
item codes that say *what kind* of thing happened. The Marvell/Google warrant deal, for
instance, appears here as 8-K items 1.01 + 3.02 on the day it was disclosed.

Free, official, no API key. SEC asks for a descriptive User-Agent with contact details and
no more than 10 requests/second; `request_delay` keeps us well under that.

Foreign private issuers (NBIS, ASML, TSM…) file 6-K instead of 8-K, and 6-K carries no item
codes — those are included but can't be filtered by significance.
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone

import httpx

logger = logging.getLogger(__name__)

# SEC rejects requests without a descriptive User-Agent (403). The shared httpx.Client in
# main.py doesn't set one, so send it explicitly on every SEC request rather than relying
# on whatever client we're handed.
_HEADERS = {
    "User-Agent": "fin-news-daily/1.0 (personal market digest; carson83423086@gmail.com)",
    "Accept-Encoding": "gzip, deflate",
}

_TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
_FILING_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"
_FILING_INDEX = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/"

# 8-K item codes. English text on purpose: rank.event_score() matches an English
# vocabulary, and these phrases are what make a filing score as a real event.
_ITEMS = {
    "1.01": "Material Definitive Agreement",
    "1.02": "Termination of Material Definitive Agreement",
    "1.03": "Bankruptcy or Receivership",
    "2.01": "Completion of Acquisition or Disposition of Assets",
    "2.02": "Results of Operations and Financial Condition",
    "2.03": "Creation of a Direct Financial Obligation",
    "2.04": "Triggering Events That Accelerate a Financial Obligation",
    "2.05": "Costs Associated with Exit or Disposal Activities",
    "2.06": "Material Impairments",
    "3.01": "Notice of Delisting or Failure to Satisfy a Listing Rule",
    "3.02": "Unregistered Sales of Equity Securities",
    "3.03": "Material Modification to Rights of Security Holders",
    "4.01": "Changes in Registrant's Certifying Accountant",
    "4.02": "Non-Reliance on Previously Issued Financial Statements",
    "5.01": "Changes in Control of Registrant",
    "5.02": "Departure or Appointment of Directors or Officers",
    "5.03": "Amendments to Articles of Incorporation or Bylaws",
    "5.07": "Submission of Matters to a Vote of Security Holders",
    "7.01": "Regulation FD Disclosure",
    "8.01": "Other Events",
    "9.01": "Financial Statements and Exhibits",
}

# Routine housekeeping. A filing carrying only these is not worth a digest slot:
# 5.07 is the annual-meeting vote tally, 9.01 just says exhibits are attached.
_ROUTINE = {"5.07", "9.01"}


def load_ticker_cik_map(client: httpx.Client) -> dict[str, tuple[int, str]]:
    """{TICKER: (cik, company_name)} from SEC's published mapping (~10k tickers)."""
    try:
        response = client.get(_TICKER_MAP_URL, headers=_HEADERS)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("SEC ticker map failed (%s: %s)", type(e).__name__, e)
        return {}
    return {
        entry["ticker"].upper(): (int(entry["cik_str"]), entry.get("title", ""))
        for entry in payload.values()
        if entry.get("ticker")
    }


def _filing_url(cik: int, accession: str, document: str) -> str:
    acc = accession.replace("-", "")
    if document:
        return _FILING_URL.format(cik=cik, acc=acc, doc=document)
    return _FILING_INDEX.format(cik=cik, acc=acc)


def fetch_filings(
    tickers: list[str],
    client: httpx.Client,
    lookback_hours: int = 30,
    max_tickers: int = 24,
    request_delay: float = 0.15,
) -> list[dict]:
    """Recent 8-K/6-K filings for `tickers`, shaped for normalize.normalize_rss_batch()."""
    if not tickers:
        return []

    cik_map = load_ticker_cik_map(client)
    if not cik_map:
        return []

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).date()
    results: list[dict] = []
    unmapped: list[str] = []

    for symbol in [t.upper() for t in dict.fromkeys(tickers)][:max_tickers]:
        entry = cik_map.get(symbol)
        if not entry:
            unmapped.append(symbol)
            continue
        cik, company = entry

        try:
            response = client.get(_SUBMISSIONS_URL.format(cik=cik), headers=_HEADERS)
            response.raise_for_status()
            recent = response.json().get("filings", {}).get("recent", {})
        except (httpx.HTTPError, ValueError) as e:
            logger.warning("SEC submissions %s failed (%s)", symbol, type(e).__name__)
            continue
        finally:
            time.sleep(request_delay)

        forms = recent.get("form", [])
        for i, form in enumerate(forms):
            if form not in ("8-K", "6-K"):
                continue
            filed_raw = recent.get("filingDate", [])[i]
            try:
                filed = date.fromisoformat(filed_raw)
            except (ValueError, TypeError):
                continue
            if filed < cutoff:
                continue

            codes = [c.strip() for c in (recent.get("items", [])[i] or "").split(",") if c.strip()]
            if codes and all(c in _ROUTINE for c in codes):
                continue
            # A 6-K with no item codes says only "a foreign issuer filed something" — it
            # carries no indication of what happened, so it would rank on SEC's source
            # weight alone while telling the reader nothing. Real news from those issuers
            # reaches the digest through the ordinary feeds.
            if not codes:
                continue

            labels = [_ITEMS.get(c, c) for c in codes if c not in _ROUTINE]
            what = "; ".join(labels)
            results.append({
                "title": f"{company} filed {form}: {what}",
                "url": _filing_url(cik, recent.get("accessionNumber", [])[i],
                                   recent.get("primaryDocument", [""] * len(forms))[i]),
                "source": f"SEC EDGAR/{symbol}",
                "market": "equity",
                "summary": (
                    f"Form {form} filed with the SEC on {filed_raw} by {company} "
                    f"({symbol}). Item codes: {', '.join(codes) if codes else 'n/a'}. "
                    f"This is the issuer's own filing, not a news report."
                ),
                # SEC gives a filing date, not a time; anchor to mid-session so the
                # recency decay treats it as same-day rather than midnight-stale.
                "published_at": datetime(
                    filed.year, filed.month, filed.day, 16, 0, tzinfo=timezone.utc
                ),
                "anchor_ticker": symbol,
            })

    if unmapped:
        logger.info("SEC: no CIK for %s (likely non-US listings)", ", ".join(unmapped))
    logger.info(
        "SEC EDGAR: %d filing(s) in the last %dh for %d ticker(s)",
        len(results), lookback_hours, len(tickers[:max_tickers]),
    )
    return results


def fetch_filings_mock(*_args, **_kwargs) -> list[dict]:
    return []
