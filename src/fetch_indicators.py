from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_FIXTURE_FEAR_GREED = Path(__file__).parent.parent / "tests" / "fixtures" / "fear_greed.json"

_FEAR_GREED_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"


def fetch_fear_greed(client: httpx.Client) -> dict:
    """Fetch CNN Fear & Greed index.
    Returns {"score": int, "rating": str} or {"score": None, "rating": "N/A"} on error.
    Unofficial endpoint — may change without notice.
    """
    try:
        response = client.get(_FEAR_GREED_URL, timeout=15.0)
        response.raise_for_status()
        data = response.json()
        fg = data.get("fear_and_greed", {})
        score = fg.get("score")
        rating = fg.get("rating", "N/A")
        if score is not None:
            logger.info("Fear & Greed: score=%s rating=%s", round(score), rating)
            return {"score": round(float(score)), "rating": rating}
    except Exception as e:
        logger.warning("Fear & Greed fetch failed: %s", e)
    return {"score": None, "rating": "N/A"}


# --- Mock helpers ---

def fetch_fear_greed_mock() -> dict:
    with open(_FIXTURE_FEAR_GREED, encoding="utf-8") as f:
        return json.load(f)


