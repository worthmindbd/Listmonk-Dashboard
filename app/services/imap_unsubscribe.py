"""
IMAP Unsubscribe Monitor: Scans an IMAP inbox for reply emails containing
unsubscribe keywords. When found, automatically unsubscribes the sender
from all ListMonk lists and blocklists them.

Storage: unsubscribe_log.json (same pattern as schedule.json)
"""

import imaplib
import email
import email.policy
import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from app.config import settings
from app.services.listmonk_client import ListMonkClient

logger = logging.getLogger("imap_unsubscribe")

LOG_FILE = Path(__file__).resolve().parent.parent.parent / "unsubscribe_log.json"
SETTINGS_FILE = Path(__file__).resolve().parent.parent.parent / "unsubscribe_settings.json"

_DEFAULT_SETTINGS = {
    "blocklist_enabled": False,
}

UNSUBSCRIBE_KEYWORDS = [
    "remove me",
    "unsubscribe me",
    "exclude me",
]

KEYWORD_PATTERN = re.compile(
    "|".join(re.escape(kw) for kw in UNSUBSCRIBE_KEYWORDS),
    re.IGNORECASE,
)


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
        # Auto-migrate old MM/YY keys to YYYY-MM
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
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_log(records: list[dict]) -> None:
    LOG_FILE.write_text(json.dumps(records, indent=2, ensure_ascii=False))


def load_settings() -> dict:
    try:
        data = json.loads(SETTINGS_FILE.read_text())
        return {**_DEFAULT_SETTINGS, **data}
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(_DEFAULT_SETTINGS)


def save_settings(data: dict) -> None:
    merged = {**load_settings(), **data}
    SETTINGS_FILE.write_text(json.dumps(merged, indent=2))


def _extract_body(msg: email.message.EmailMessage) -> str:
    """Extract plain-text body from an email message."""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    try:
                        body += payload.decode(charset, errors="replace")
                    except (LookupError, UnicodeDecodeError):
                        body += payload.decode("utf-8", errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            try:
                body = payload.decode(charset, errors="replace")
            except (LookupError, UnicodeDecodeError):
                body = payload.decode("utf-8", errors="replace")
    return body


def _extract_reply_only(full_body: str) -> str:
    """
    Extract ONLY the user's reply text, stripping all quoted/forwarded
    content from the email body.

    This prevents false-positive keyword matches from our own email
    template text (e.g., footer saying "Reply with 'Remove me'").

    Handles common quote patterns:
    - Lines starting with ">" (standard quoting)
    - "On <date> <someone> wrote:" markers
    - "From: <address>" forwarding headers
    - "-----Original Message-----" (Outlook)
    - "Sent: " / "To: " / "Subject: " header blocks in quoted replies
    """
    lines = full_body.splitlines()
    reply_lines = []

    # Patterns that indicate the start of quoted/forwarded content
    quote_start_patterns = [
        # "On Mon, Jan 1, 2026 at 12:00 PM John Doe <john@example.com> wrote:"
        re.compile(r'^On\s+.+wrote:\s*$', re.IGNORECASE),
        # "-----Original Message-----"
        re.compile(r'^-{2,}\s*Original Message\s*-{2,}', re.IGNORECASE),
        # "From: Name <email>" or "From: email" at start of line (forwarded header)
        re.compile(r'^From:\s+.+@.+', re.IGNORECASE),
        # "Sent: 3/7/26 12:17 AM" (Outlook-style quoted header)
        re.compile(r'^Sent:\s+\d', re.IGNORECASE),
        # "________" or "========" separator lines (common in some clients)
        re.compile(r'^[_=]{5,}\s*$'),
        # Gmail-style: "> " quoted lines (3+ consecutive = definitely quoted block)
        # We handle ">" lines individually below
    ]

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Skip empty lines at the very beginning
        if not reply_lines and not stripped:
            continue

        # Check if this line starts a quoted section
        is_quote_start = False
        for pattern in quote_start_patterns:
            if pattern.match(stripped):
                is_quote_start = True
                break

        if is_quote_start:
            # Everything from here is quoted content — stop collecting
            break

        # Lines starting with ">" are quoted text — skip them
        if stripped.startswith('>'):
            # If we haven't collected any reply yet, keep looking
            # If we already have reply text and hit ">", the reply is above
            if reply_lines:
                break
            continue

        reply_lines.append(line)

    reply_text = '\n'.join(reply_lines).strip()

    # If we couldn't extract a reply (e.g., entire body is quoted),
    # return empty string so no false match occurs
    return reply_text


def _extract_sender_email(msg: email.message.EmailMessage) -> Optional[str]:
    """Extract the sender's email address from the From header."""
    from_header = msg.get("From", "")
    match = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", from_header)
    return match.group(0).lower() if match else None


def _clean_subject(subject: str) -> str:
    """Strip Re:/Fwd:/FW: prefixes and extra whitespace."""
    cleaned = re.sub(r'^(Re|Fwd|FW|Fw)\s*:\s*', '', subject, flags=re.IGNORECASE).strip()
    # Recursively strip if multiple prefixes
    if re.match(r'^(Re|Fwd|FW|Fw)\s*:', cleaned, re.IGNORECASE):
        return _clean_subject(cleaned)
    return cleaned


def _match_campaign(
    campaigns: list[dict],
    email_date: Optional[datetime] = None,
    sub_list_ids: Optional[set] = None,
) -> Optional[dict]:
    """
    Match a reply email to the most recent ListMonk campaign created on or
    before the email date whose target lists intersect the subscriber's lists.

    If `sub_list_ids` is provided, only campaigns whose `lists` include at
    least one of those IDs are considered — this prevents attributing a reply
    to a campaign the subscriber never received. If a campaign has no `lists`
    field populated (API sometimes omits it), fall back to date-only matching
    for that campaign, mirroring link_unsubscribe._pick_campaign_for_list_ids.

    Returns {campaign_id, campaign_name, campaign_subject, matched_list_id}
    or None if nothing qualifies.
    """
    if not email_date:
        email_date = datetime.utcnow()
    if not campaigns:
        return None

    email_naive = email_date.replace(tzinfo=None) if email_date.tzinfo else email_date

    best_match = None
    best_date = None

    for camp in campaigns:
        created = camp.get("created_at", "")
        if not created:
            continue
        try:
            camp_date = datetime.fromisoformat(created[:10])
        except (ValueError, TypeError):
            continue

        if camp_date > email_naive:
            continue

        camp_list_ids = [l.get("id") for l in (camp.get("lists") or [])]
        matched_list_id = None
        if sub_list_ids and camp_list_ids:
            for lid in camp_list_ids:
                if lid in sub_list_ids:
                    matched_list_id = lid
                    break
            if matched_list_id is None:
                continue

        if best_date is None or camp_date > best_date:
            best_date = camp_date
            best_match = {
                "campaign_id": camp.get("id"),
                "campaign_name": camp.get("name", ""),
                "campaign_subject": camp.get("subject", ""),
                "matched_list_id": matched_list_id,
            }

    return best_match


def connect_imap() -> Optional[imaplib.IMAP4_SSL | imaplib.IMAP4]:
    """Connect to the IMAP server. Returns None on failure."""
    if not settings.imap_configured:
        logger.warning("IMAP not configured, skipping scan")
        return None
    try:
        if settings.imap_use_ssl:
            conn = imaplib.IMAP4_SSL(settings.imap_host, settings.imap_port)
        else:
            conn = imaplib.IMAP4(settings.imap_host, settings.imap_port)
        conn.login(settings.imap_user, settings.imap_pass)
        return conn
    except Exception as e:
        logger.error(f"IMAP connection failed: {e}")
        return None


def check_imap_status() -> dict:
    """Check IMAP connection status without scanning."""
    if not settings.imap_configured:
        return {"configured": False, "connected": False, "error": "IMAP not configured in .env"}
    try:
        conn = connect_imap()
        if conn:
            conn.logout()
            return {"configured": True, "connected": True, "error": None}
        return {"configured": True, "connected": False, "error": "Connection failed"}
    except Exception as e:
        return {"configured": True, "connected": False, "error": str(e)}


import asyncio
_scan_lock = asyncio.Lock()


def _reattribute_existing_records(
    records: list[dict], campaigns_list: list[dict]
) -> tuple[int, int]:
    """
    One-time backfill: re-run list-aware attribution on existing email-source
    records that were stored with the old date-only matcher.

    - If a record matches a different campaign, update it in place.
    - If a record matches no campaign, mark it for removal (caller filters).
    - Skips records already marked with `reattributed_at`, records without
      `lists_removed`, and non-email records.

    Returns (changed_count, removed_count).
    """
    changed = 0
    removed = 0
    for r in records:
        if r.get("source") != "email":
            continue
        if r.get("reattributed_at"):
            continue
        lists_removed = r.get("lists_removed") or []
        if not lists_removed:
            continue

        ts = r.get("timestamp", "")
        try:
            rec_date = datetime.fromisoformat(ts) if ts else datetime.utcnow()
        except ValueError:
            rec_date = datetime.utcnow()

        new_match = _match_campaign(
            campaigns_list, rec_date, set(lists_removed)
        )
        if new_match:
            new_cid = new_match["campaign_id"]
            if r.get("campaign_id") != new_cid:
                r["campaign_id"] = new_cid
                r["campaign_name"] = new_match["campaign_name"]
                r["matched_list_id"] = new_match.get("matched_list_id")
                changed += 1
            r["reattributed_at"] = datetime.utcnow().isoformat()
        else:
            r["_remove"] = True
            removed += 1

    return changed, removed


async def scan_and_unsubscribe(client: ListMonkClient) -> dict:
    """
    Scan IMAP inbox for unsubscribe requests and process them.
    Returns summary of actions taken.
    """
    if _scan_lock.locked():
        return {"scanned": 0, "matched": 0, "processed": 0, "errors": 0,
                "message": "Scan already in progress"}

    async with _scan_lock:
        conn = connect_imap()
        if not conn:
            return {"scanned": 0, "matched": 0, "processed": 0, "errors": 0,
                    "message": "IMAP not configured or connection failed"}

        processed = 0
        matched = 0
        errors = 0
        scanned = 0
        new_records = []

        try:
            # Fetch campaigns FIRST to determine date filter
            try:
                camp_result = await client.get_campaigns(
                    page=1, per_page=100, order_by="created_at", order="DESC"
                )
                campaigns_list = camp_result.get("data", {}).get("results", [])
                print(f"[IMAP] Fetched {len(campaigns_list)} campaigns for matching")
            except Exception as e:
                logger.error(f"Failed to fetch campaigns: {e}")
                print(f"[IMAP] ERROR fetching campaigns: {e}")
                campaigns_list = []

            # Backfill: re-attribute any pre-fix email records once, so the
            # "Unsubscribes by Campaign" view self-heals on the next scan.
            if campaigns_list:
                try:
                    existing_for_backfill = load_log()
                    changed, removed = _reattribute_existing_records(
                        existing_for_backfill, campaigns_list
                    )
                    if changed or removed:
                        cleaned = [r for r in existing_for_backfill if not r.get("_remove")]
                        save_log(cleaned)
                        if changed:
                            print(f"[IMAP] Re-attributed {changed} existing records")
                            logger.info(f"Backfill: re-attributed {changed} existing records")
                        if removed:
                            print(f"[IMAP] Removed {removed} unattributable records")
                            logger.info(f"Backfill: removed {removed} unattributable records")
                except Exception as e:
                    logger.error(f"Backfill reattribution failed: {e}")
                    print(f"[IMAP] Backfill error (non-fatal): {e}")

            # Determine the latest campaign's creation date to filter emails
            latest_campaign_date = None
            for camp in campaigns_list:
                created = camp.get("created_at", "")
                if created:
                    try:
                        latest_campaign_date = datetime.fromisoformat(created[:10])
                        break  # campaigns are sorted DESC, first one is latest
                    except (ValueError, TypeError):
                        continue

            conn.select("INBOX")

            # Use IMAP SINCE filter to only fetch emails from the campaign month
            if latest_campaign_date:
                # Search from the 1st of the campaign month
                since_date = latest_campaign_date.replace(day=1)
                since_str = since_date.strftime("%d-%b-%Y")
                status, msg_ids = conn.search(None, f'(SINCE {since_str})')
                print(f"[IMAP] Searching emails SINCE {since_str} (campaign month)")
            else:
                # Fallback: scan last 30 days if no campaigns found
                since_date = datetime.utcnow() - timedelta(days=30)
                since_str = since_date.strftime("%d-%b-%Y")
                status, msg_ids = conn.search(None, f'(SINCE {since_str})')
                print(f"[IMAP] No campaigns found, searching emails SINCE {since_str}")

            if status != "OK" or not msg_ids[0]:
                conn.logout()
                return {"scanned": 0, "matched": 0, "processed": 0, "errors": 0,
                        "message": "No emails found in inbox for the campaign period"}

            ids = msg_ids[0].split()
            scanned = len(ids)
            print(f"[IMAP] Found {scanned} emails in campaign period, scanning all")
            logger.info(f"IMAP scan: {scanned} emails in campaign period")

            existing_log = load_log()
            # Deduplicate using Message-ID header and sender email
            processed_msg_ids = {r.get("message_id") for r in existing_log if r.get("message_id")}
            processed_emails_set = {r["email"] for r in existing_log}

            for msg_id in ids:
                try:
                    status, data = conn.fetch(msg_id, "(RFC822)")
                    if status != "OK":
                        continue

                    raw_email = data[0][1]
                    msg = email.message_from_bytes(raw_email, policy=email.policy.default)

                    # Dedup: skip if we already processed this email
                    msg_message_id = msg.get("Message-ID", "").strip()
                    if msg_message_id and msg_message_id in processed_msg_ids:
                        continue

                    body = _extract_body(msg)
                    # Only scan the user's actual reply, NOT quoted
                    # template content (which may contain "Remove me" etc.)
                    reply_text = _extract_reply_only(body)

                    sender_email_preview = _extract_sender_email(msg)
                    subject_preview = msg.get("Subject", "(no subject)")

                    keyword_match = KEYWORD_PATTERN.search(reply_text)

                    if not keyword_match:
                        # Log if the full body HAD keywords but reply didn't
                        if KEYWORD_PATTERN.search(body):
                            print(f"[IMAP] FILTERED OUT: {sender_email_preview} "
                                  f"('{subject_preview}') — keyword only in "
                                  f"quoted/template content, not in actual reply")
                        continue

                    matched += 1
                    sender_email = _extract_sender_email(msg)
                    if not sender_email:
                        logger.warning(f"Could not extract sender email from message")
                        continue

                    # Skip if this sender was already processed
                    if sender_email in processed_emails_set:
                        continue

                    matched_keyword = keyword_match.group(0).lower()
                    subject = msg.get("Subject", "(no subject)")
                    print(f"[IMAP] Keyword match: {sender_email} ('{matched_keyword}')")
                    logger.info(f"Unsubscribe match: {sender_email} (keyword: '{matched_keyword}')")

                    # Parse email date for campaign matching
                    email_date = None
                    date_str = msg.get("Date", "")
                    if date_str:
                        try:
                            from email.utils import parsedate_to_datetime
                            email_date = parsedate_to_datetime(date_str)
                        except Exception:
                            email_date = datetime.utcnow()
                    else:
                        email_date = datetime.utcnow()

                    # Look up subscriber in ListMonk first so we know which
                    # lists they're on before attributing to a campaign.
                    try:
                        result = await client.get_subscribers(
                            1, 1, f"subscribers.email = '{sender_email}'"
                        )
                        subscribers = result.get("data", {}).get("results", [])

                        if not subscribers:
                            logger.info(f"Sender {sender_email} not found in ListMonk, skipping")
                            continue

                        subscriber = subscribers[0]
                        sub_id = subscriber["id"]
                        sub_lists = [lst["id"] for lst in subscriber.get("lists", [])]

                        # Only process if subscriber belongs to a campaign's
                        # target list — otherwise skip entirely.
                        campaign = _match_campaign(
                            campaigns_list, email_date, set(sub_lists)
                        )
                        if not campaign:
                            print(f"[IMAP] No list-matched campaign for {sender_email}, skipping")
                            logger.info(f"No list-matched campaign for '{subject}' from {sender_email}; skipping")
                            continue

                        # Unsubscribe from all lists
                        if sub_lists:
                            await client.modify_list_memberships({
                                "ids": [sub_id],
                                "action": "unsubscribe",
                                "target_list_ids": sub_lists,
                                "status": "unsubscribed",
                            })

                        # Conditionally blocklist based on user setting
                        scan_settings = load_settings()
                        if scan_settings.get("blocklist_enabled", False):
                            await client.blocklist_subscriber(sub_id)
                            print(f"[IMAP] Blocklisted: {sender_email}")

                        record = {
                            "email": sender_email,
                            "name": subscriber.get("name", ""),
                            "source": "email",
                            "keyword": matched_keyword,
                            "subject": subject,
                            "message_id": msg_message_id,
                            "campaign_key": f"{email_date.year}-{email_date.month:02d}",
                            "campaign_id": campaign["campaign_id"],
                            "campaign_name": campaign["campaign_name"],
                            "matched_list_id": campaign.get("matched_list_id"),
                            "subscriber_id": sub_id,
                            "lists_removed": sub_lists,
                            "timestamp": datetime.utcnow().isoformat(),
                        }
                        new_records.append(record)
                        processed_emails_set.add(sender_email)  # Prevent duplicates in same scan
                        processed += 1
                        action = "Unsubscribed + Blocklisted" if scan_settings.get("blocklist_enabled") else "Unsubscribed"
                        logger.info(f"{action}: {sender_email} (campaign: {campaign['campaign_name']})")

                    except Exception as e:
                        errors += 1
                        logger.error(f"Failed to process {sender_email}: {e}")

                except Exception as e:
                    errors += 1
                    logger.error(f"Failed to parse email {msg_id}: {e}")

            # Save new records
            if new_records:
                existing_log = load_log()
                existing_log.extend(new_records)
                save_log(existing_log)

        except Exception as e:
            logger.error(f"IMAP scan error: {e}")
            errors += 1
        finally:
            try:
                conn.logout()
            except Exception:
                pass

        return {
            "scanned": scanned,
            "matched": matched,
            "processed": processed,
            "errors": errors,
            "message": f"Scan complete: {processed} unsubscribed",
            "timestamp": datetime.utcnow().isoformat(),
        }


def get_stats() -> dict:
    """Return aggregate stats from the unsubscribe log."""
    records = load_log()
    total = len(records)

    today = datetime.utcnow().date().isoformat()
    today_count = sum(1 for r in records if r.get("timestamp", "").startswith(today))

    week_ago = (datetime.utcnow() - timedelta(days=7)).isoformat()
    week_count = sum(1 for r in records if r.get("timestamp", "") >= week_ago)

    # Source breakdown — records without 'source' are treated as 'email'
    link_count = sum(1 for r in records if r.get("source") == "link")
    email_count = total - link_count

    return {
        "total": total,
        "today": today_count,
        "this_week": week_count,
        "link_count": link_count,
        "email_count": email_count,
    }
