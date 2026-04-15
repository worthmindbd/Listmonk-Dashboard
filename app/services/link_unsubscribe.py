"""
Link Unsubscribe Scanner: Polls ListMonk API for subscribers who used the
direct unsubscribe link. Applies the same actions as IMAP unsubscribes:
removes from all lists and optionally blocklists. Records in the shared
unsubscribe_log.json with source="link".
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from app.services.listmonk_client import ListMonkClient

logger = logging.getLogger("link_unsubscribe")

# Module-level paths — patchable in tests via monkeypatch.setattr
LOG_FILE = Path(__file__).resolve().parent.parent.parent / "unsubscribe_log.json"
SETTINGS_FILE = Path(__file__).resolve().parent.parent.parent / "unsubscribe_settings.json"

PER_PAGE = 100  # Patchable in tests

_DEFAULT_SETTINGS = {
    "blocklist_enabled": False,
}


def load_log() -> list[dict]:
    """Load the unsubscribe log from LOG_FILE (module-level, patchable in tests)."""
    try:
        return json.loads(LOG_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_log(records: list[dict]) -> None:
    """Persist the unsubscribe log to LOG_FILE (module-level, patchable in tests)."""
    LOG_FILE.write_text(json.dumps(records, indent=2, ensure_ascii=False))


def load_settings() -> dict:
    """Load scanner settings from SETTINGS_FILE (module-level, patchable in tests)."""
    try:
        data = json.loads(SETTINGS_FILE.read_text())
        return {**_DEFAULT_SETTINGS, **data}
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(_DEFAULT_SETTINGS)


def _pick_campaign_for_list_ids(campaigns: list, list_ids: set[int]) -> dict:
    """
    Pick the most recent campaign (created on or before now) that targets any
    of list_ids. `campaigns` must be pre-sorted DESC by created_at.
    Returns {campaign_id, campaign_name, campaign_key, matched_list_id}.
    matched_list_id is one of list_ids that the chosen campaign targets — used
    by the caller to set a representative list_id on the log record.
    """
    now = datetime.now(timezone.utc)
    for camp in campaigns:
        camp_list_ids = [l.get("id") for l in (camp.get("lists") or [])]
        # Intersect campaign's target lists with subscriber's unsubscribed lists.
        # If the API omits lists, fall back to time-based matching only.
        matched = None
        if camp_list_ids:
            for lid in camp_list_ids:
                if lid in list_ids:
                    matched = lid
                    break
            if matched is None:
                continue

        created = camp.get("created_at", "")
        if not created:
            continue
        try:
            camp_date = datetime.fromisoformat(created[:10]).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
        if camp_date <= now:
            return {
                "campaign_id": camp.get("id"),
                "campaign_name": camp.get("name", ""),
                "campaign_key": f"{camp_date.year}-{camp_date.month:02d}",
                "matched_list_id": matched if matched is not None else (next(iter(list_ids)) if list_ids else None),
            }

    return {
        "campaign_id": None,
        "campaign_name": "Unknown",
        "campaign_key": _current_campaign_key(),
        "matched_list_id": next(iter(list_ids)) if list_ids else None,
    }


def _current_campaign_key() -> str:
    now = datetime.now(timezone.utc)
    return f"{now.year}-{now.month:02d}"


async def scan_link_unsubscribes(client: ListMonkClient) -> dict:
    """
    Scan all ListMonk lists for link-unsubscribed subscribers and process them.
    Returns summary: {scanned_lists, new_found, processed, errors}.

    Attribution is per-subscriber, not per-list: when a subscriber appears as
    unsubscribed in multiple lists (which happens when the unsubscribe form
    cascades to all their lists), they are attributed to the most recent
    campaign targeting any of those lists — not to whichever list is scanned
    first.
    """
    scanned_lists = 0
    errors = 0

    existing_log = load_log()
    processed_emails = {r["email"] for r in existing_log}

    scan_settings = load_settings()
    blocklist_enabled = scan_settings.get("blocklist_enabled", False)

    try:
        lists_result = await client.get_lists(page=1, per_page=200, minimal=True)
        all_lists = lists_result.get("data", {}).get("results", [])
    except Exception as e:
        logger.error(f"Failed to fetch lists: {e}")
        return {"scanned_lists": 0, "new_found": 0, "processed": 0, "errors": 1,
                "message": f"Failed to fetch lists: {e}"}

    list_id_to_name = {lst.get("id"): lst.get("name", "") for lst in all_lists if lst.get("id")}

    # Phase 1: collect unique unsubscribed subscribers across all lists.
    # sub_map: subscriber_id -> {"sub": sub_obj, "unsub_list_ids": set[int]}
    sub_map: dict = {}
    for lst in all_lists:
        list_id = lst.get("id")
        if not list_id:
            continue

        scanned_lists += 1
        page = 1

        while True:
            try:
                result = await client.get_subscribers_by_list_status(
                    list_id=list_id,
                    subscription_status="unsubscribed",
                    page=page,
                    per_page=PER_PAGE,
                )
                subscribers = result.get("data", {}).get("results", [])
            except Exception as e:
                logger.error(f"Failed to fetch unsubscribed subscribers for list {list_id}: {e}")
                errors += 1
                break

            for sub in subscribers:
                sid = sub.get("id")
                if sid is None:
                    continue
                entry = sub_map.setdefault(sid, {"sub": sub, "unsub_list_ids": set()})
                entry["unsub_list_ids"].add(list_id)

            if len(subscribers) < PER_PAGE:
                break
            page += 1

    # Phase 2: fetch campaigns once for attribution
    try:
        camp_result = await client.get_campaigns(
            page=1, per_page=50, order_by="created_at", order="DESC"
        )
        campaigns = camp_result.get("data", {}).get("results", [])
    except Exception as e:
        logger.warning(f"Could not fetch campaigns: {e}")
        campaigns = []

    # Phase 3: process each unique subscriber
    new_records = []
    new_found = 0
    processed = 0

    for sid, entry in sub_map.items():
        sub = entry["sub"]
        unsub_list_ids = entry["unsub_list_ids"]
        email = (sub.get("email") or "").lower()
        if not email or email in processed_emails:
            continue

        new_found += 1
        sub_lists = [l["id"] for l in sub.get("lists", [])]

        try:
            if sub_lists:
                await client.modify_list_memberships({
                    "ids": [sid],
                    "action": "unsubscribe",
                    "target_list_ids": sub_lists,
                    "status": "unsubscribed",
                })
        except Exception as e:
            logger.error(f"Failed to unsubscribe {email} from lists: {e}")
            errors += 1
            continue  # Do not log — partial failure

        if blocklist_enabled:
            try:
                await client.blocklist_subscriber(sid)
                logger.info(f"[LINK] Blocklisted: {email}")
            except Exception as e:
                logger.error(f"Failed to blocklist {email}: {e}")
                # Partial success — still log the unsubscribe

        campaign = _pick_campaign_for_list_ids(campaigns, unsub_list_ids)
        primary_list_id = campaign.get("matched_list_id")
        list_name = list_id_to_name.get(primary_list_id, "")

        record = {
            "email": email,
            "name": sub.get("name", ""),
            "source": "link",
            "keyword": None,
            "list_id": primary_list_id,
            "campaign_id": campaign["campaign_id"],
            "campaign_name": campaign["campaign_name"],
            "campaign_key": campaign["campaign_key"],
            "subscriber_id": sid,
            "lists_removed": sub_lists,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        new_records.append(record)
        processed_emails.add(email)
        processed += 1
        action = "Unsubscribed + Blocklisted" if blocklist_enabled else "Unsubscribed"
        logger.info(f"[LINK] {action}: {email} (list: {list_name})")

    if new_records:
        from app.services.imap_unsubscribe import _log_lock
        async with _log_lock:
            existing_log = load_log()
            existing_log.extend(new_records)
            save_log(existing_log)

    return {
        "scanned_lists": scanned_lists,
        "new_found": new_found,
        "processed": processed,
        "errors": errors,
        "message": f"Link scan complete: {processed} unsubscribed",
    }
