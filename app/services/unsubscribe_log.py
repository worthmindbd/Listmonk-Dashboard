"""Shared unsubscribe log and settings storage.

Consolidates load/save operations that were previously duplicated between
imap_unsubscribe.py and link_unsubscribe.py.
"""

import asyncio
import json
import re
from pathlib import Path

LOG_FILE = Path(__file__).resolve().parent.parent.parent / "unsubscribe_log.json"
SETTINGS_FILE = Path(__file__).resolve().parent.parent.parent / "unsubscribe_settings.json"

_DEFAULT_SETTINGS = {
    "blocklist_enabled": False,
}

_log_lock = asyncio.Lock()


def _normalize_campaign_key(key: str) -> str:
    """Convert old MM/YY keys to YYYY-MM format for proper sorting."""
    if not key or '/' not in key:
        return key
    parts = key.split('/')
    if len(parts) == 2 and len(parts[0]) == 2 and len(parts[1]) == 2:
        month, year_short = parts
        return f"20{year_short}-{month}"
    return key


def load_log() -> list[dict]:
    try:
        records = json.loads(LOG_FILE.read_text())
        migrated = False
        for r in records:
            old_key = r.get("campaign_key", "")
            new_key = _normalize_campaign_key(old_key)
            if new_key != old_key:
                r["campaign_key"] = new_key
                migrated = True
        if migrated:
            save_log(records)
        return records
    except (FileNotFoundError, json.JSONDecodeError, IsADirectoryError, PermissionError):
        return []


def save_log(records: list[dict]) -> None:
    LOG_FILE.write_text(json.dumps(records, indent=2, ensure_ascii=False))


async def append_log(new_records: list[dict]) -> None:
    """Atomically append records to the log file under lock."""
    async with _log_lock:
        existing = load_log()
        existing.extend(new_records)
        save_log(existing)


def load_settings() -> dict:
    try:
        data = json.loads(SETTINGS_FILE.read_text())
        return {**_DEFAULT_SETTINGS, **data}
    except (FileNotFoundError, json.JSONDecodeError, IsADirectoryError, PermissionError):
        return dict(_DEFAULT_SETTINGS)


def save_settings(data: dict) -> None:
    merged = {**load_settings(), **data}
    SETTINGS_FILE.write_text(json.dumps(merged, indent=2))
