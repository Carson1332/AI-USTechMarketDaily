from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

_ROOT = Path(__file__).parent.parent
_SETTINGS_PATH = _ROOT / "config" / "settings.yml"

REQUIRED_SECRETS = [
    "ALPHAVANTAGE_API_KEY",
    "FINNHUB_API_KEY",
    "OPENROUTER_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
]


def load_settings() -> dict:
    with open(_SETTINGS_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_leaders_set(settings: dict) -> set[str]:
    """All unique leader tickers across all themes (uppercase)."""
    seen: set[str] = set()
    for theme_data in settings.get("themes", {}).values():
        for t in theme_data.get("leaders", []):
            seen.add(t.upper())
    return seen


def build_primary_theme_map(settings: dict) -> dict[str, str]:
    """Ticker → primary theme. First match in theme_order wins; overrides applied after."""
    result: dict[str, str] = {}
    for theme_key in settings.get("theme_order", []):
        for ticker in settings.get("themes", {}).get(theme_key, {}).get("leaders", []):
            result.setdefault(ticker.upper(), theme_key)
    for ticker, theme_key in settings.get("primary_theme_overrides", {}).items():
        result[ticker.upper()] = theme_key
    return result


def load_secrets() -> dict:
    load_dotenv(_ROOT / ".env")
    missing = [k for k in REQUIRED_SECRETS if not os.getenv(k)]
    if missing:
        raise EnvironmentError(
            f"Missing required environment variables: {', '.join(missing)}\n"
            f"Copy .env.example to .env and fill in all values."
        )
    return {k: os.environ[k] for k in REQUIRED_SECRETS}


def is_mock_mode() -> bool:
    return os.getenv("MOCK", "0") == "1" or "--mock" in sys.argv
