"""Shared unsubscribe log and settings storage.

Consolidates load/save operations that were previously duplicated between
imap_unsubscribe.py and link_unsubscribe.py.
"""

import asyncio
import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger("unsubscribe_log")

LOG_FILE = settings.data_path("unsubscribe_log.json")
SETTINGS_FILE = settings.data_path("unsubscribe_settings.json")
# Authoritative dedup source for the scanners, kept independent of the display
# log so that deleting/clearing log records does not cause already-handled
# subscribers to be processed (and re-unsubscribed) again.
PROCESSED_FILE = settings.data_path("unsubscribe_processed.json")

# How long a processed email blocks a *new IMAP reply* from the same sender.
# Without a TTL a contact who unsubscribes, is manually re-subscribed, and
# later replies "remove me" again would be silently ignored (and the reply
# marked seen) — a compliance hole. After the TTL passes, a fresh reply is
# handled. Entries are NOT deleted at expiry: the link scanner needs the
# handled-at time indefinitely (ListMonk keeps "unsubscribed" status forever,
# so forgetting an email made the link scanner re-log it as a new request).
PROCESSED_TTL_DAYS = 180
_PROCESSED_TTL_SECONDS = PROCESSED_TTL_DAYS * 24 * 60 * 60

_DEFAULT_SETTINGS = {
    "blocklist_enabled": False,
}

_log_lock = asyncio.Lock()

# Serialises the IMAP reply scanner against the link scanner so the same
# subscriber cannot be processed (and logged) by both sources concurrently —
# each scanner only self-serialises with its own lock, which leaves a window
# for cross-source duplicates. Held for each scanner's full pass.
scanner_lock = asyncio.Lock()


def _backup_corrupt_file(file_path: Path) -> None:
    """Move a corrupt JSON state file aside so the next write starts clean.

    Returning [] on parse failure is only safe for reads; if the file were
    left in place, the following append would silently replace the entire
    history with the new records. The corrupt bytes are preserved as
    ``<name>.<timestamp>.bak`` (gitignored via ``*.bak``) for recovery.
    """
    backup = file_path.with_name(f"{file_path.name}.{int(time.time())}.bak")
    try:
        os.replace(file_path, backup)
        logger.error(
            "Corrupt JSON state file %s moved to %s; starting from empty state",
            file_path, backup,
        )
    except OSError:
        logger.error("Corrupt JSON state file %s could not be moved aside", file_path)


def _normalize_campaign_key(key: str) -> str:
    """Convert old MM/YY keys to YYYY-MM format for proper sorting."""
    if not key or '/' not in key:
        return key
    parts = key.split('/')
    if len(parts) == 2 and len(parts[0]) == 2 and len(parts[1]) == 2:
        month, year_short = parts
        return f"20{year_short}-{month}"
    return key


def _atomic_write_json(file_path: Path, data: Any, indent: int = 2, ensure_ascii: bool = True) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    temp_file = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=file_path.parent, delete=False, encoding="utf-8") as f:
            temp_file = f.name
            json.dump(data, f, indent=indent, ensure_ascii=ensure_ascii)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_file, file_path)
    except Exception:
        if temp_file and os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except OSError:
                pass
        raise


def load_log() -> list[dict]:
    try:
        records = json.loads(LOG_FILE.read_text(encoding="utf-8"))
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
    except json.JSONDecodeError:
        # A corrupt file must never silently become "empty history": move the
        # bytes aside and start clean so the next save cannot overwrite them.
        _backup_corrupt_file(LOG_FILE)
        return []
    except (FileNotFoundError, IsADirectoryError, PermissionError):
        return []


def save_log(records: list[dict]) -> None:
    _atomic_write_json(LOG_FILE, records, indent=2, ensure_ascii=False)


async def append_log(new_records: list[dict]) -> None:
    """Atomically append records to the log file under lock.

    The file read/parse/write runs in a worker thread: JSON dumps of a large
    log held the event loop hostage (stalling every request) while ``_log_lock``
    was held.
    """
    async with _log_lock:
        existing = await asyncio.to_thread(load_log)
        existing.extend(new_records)
        await asyncio.to_thread(save_log, existing)


def _load_processed_map() -> dict[str, float]:
    """Return the full processed map ``{email: handled_at_epoch}``.

    Supports both storage formats: the original JSON list of emails and the
    current JSON object storing a per-email timestamp. Entries past
    ``PROCESSED_TTL_DAYS`` are kept (see the note on the TTL above);
    ``load_processed_emails`` applies the TTL for the IMAP scanner.
    """
    try:
        data = json.loads(PROCESSED_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        _backup_corrupt_file(PROCESSED_FILE)
        data = None
    except (FileNotFoundError, IsADirectoryError, PermissionError):
        data = None

    result: dict[str, float] = {}
    if isinstance(data, dict) and isinstance(data.get("processed"), dict):
        for email, ts in data["processed"].items():
            # Legacy entries (missing timestamps) are kept; treat as "now"
            # so they get a fresh TTL window rather than instant expiry.
            try:
                result[str(email).lower()] = float(ts) if ts else time.time()
            except (TypeError, ValueError):
                result[str(email).lower()] = time.time()
    elif isinstance(data, list):
        for e in data:
            if e:
                result[str(e).lower()] = time.time()
    elif data is None:
        # First run (or unreadable): seed from the existing display log so
        # previously processed records are not handled twice after this
        # file is introduced.
        seeded = {r["email"].lower() for r in load_log() if r.get("email")}
        if seeded:
            new_map = {e: time.time() for e in seeded}
            try:
                _save_processed_map(new_map)
            except OSError:
                pass
            return new_map
        return {}
    return result


def load_processed_map_all() -> dict[str, float]:
    """Every handled email with its handled-at time, ignoring the TTL.

    Used by the link scanner, which compares these times against ListMonk's
    ``subscription_updated_at`` to recognise genuinely new unsubscribes.
    """
    return _load_processed_map()


def load_processed_emails() -> set[str]:
    """Emails handled within ``PROCESSED_TTL_DAYS`` (IMAP reply dedup)."""
    cutoff = time.time() - _PROCESSED_TTL_SECONDS
    return {e for e, ts in _load_processed_map().items() if ts > cutoff}


def _save_processed_map(processed: dict[str, float]) -> None:
    payload = {"version": 1, "processed": processed}
    _atomic_write_json(PROCESSED_FILE, payload, indent=None, ensure_ascii=True)


def save_processed_emails(emails: set[str]) -> None:
    """Persist a processed set, preserving timestamps of known entries."""
    current = _load_processed_map()
    now = time.time()
    for e in emails:
        current.setdefault(str(e).lower(), now)
    _save_processed_map(current)


async def mark_processed(emails) -> None:
    """Persist emails as handled (union with the existing set).

    Runs the file IO in a worker thread while holding ``_log_lock`` — see
    ``append_log``.
    """
    async with _log_lock:
        current = await asyncio.to_thread(_load_processed_map)
        now = time.time()
        for e in emails:
            if e:
                # Overwrite, not setdefault: a re-handled email must get a
                # fresh handled-at time, or the link scanner would see the
                # newer ListMonk timestamp and re-process it every scan.
                current[str(e).lower()] = now
        await asyncio.to_thread(_save_processed_map, current)


async def unmark_processed(emails) -> None:
    """Remove emails from the handled set so a future unsubscribe is detected."""
    async with _log_lock:
        current = await asyncio.to_thread(_load_processed_map)
        for e in emails:
            if e:
                current.pop(str(e).lower(), None)
        await asyncio.to_thread(_save_processed_map, current)


def load_settings() -> dict:
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        return {**_DEFAULT_SETTINGS, **data}
    except json.JSONDecodeError:
        # Corrupt settings must not be silently overwritten on the next save.
        _backup_corrupt_file(SETTINGS_FILE)
        return dict(_DEFAULT_SETTINGS)
    except (FileNotFoundError, IsADirectoryError, PermissionError):
        return dict(_DEFAULT_SETTINGS)


def save_settings(data: dict) -> None:
    merged = {**load_settings(), **data}
    _atomic_write_json(SETTINGS_FILE, merged, indent=2, ensure_ascii=True)
