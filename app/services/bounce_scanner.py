"""
Bounce Scanner: Connects to the bounce IMAP mailbox, reads bounce/DSN emails,
and checks if the bounce was caused by IP blacklisting (e.g. Spamhaus).

If a hard bounce was caused by an IP blacklist, the scanner:
1. Deletes the hard bounce record from ListMonk
2. Creates a soft bounce record instead
3. Re-enables (unblocks) the subscriber

Also provides a fix_existing_hard_bounces() function that scans all current
hard bounce records via the ListMonk API and reclassifies blacklist-related
ones as soft bounces.
"""

import asyncio
import imaplib
import email
import email.policy
import logging
import re
from datetime import datetime, timedelta
from typing import Optional

from app.config import settings
from app.services.listmonk_client import ListMonkClient

logger = logging.getLogger("bounce_scanner")

# Keywords in bounce messages that indicate IP/domain blacklisting (soft bounce)
BLACKLIST_KEYWORDS = [
    "spamhaus",
    "sbl.spamhaus",
    "xbl.spamhaus",
    "zen.spamhaus",
    "spamcop",
    "barracuda",
    "blocklist",
    "blacklist",
    "block list",
    "black list",
    "blacklisted",
    "blocklisted",
    "listed at",
    "listed on",
    "listed in",
    "realtime blackhole",
    "rbl",
    "dnsbl",
    "sorbs",
    "uceprotect",
    "ip reputation",
    "ip blocked",
    "ip rejected",
    "poor reputation",
    "bad reputation",
    "sender reputation",
    "rejected for policy",
    "policy reasons",
    "554 5.7.1",  # Common SMTP code for blacklist rejections
    "550 5.7.1",  # Rejected by policy
    "521 5.2.1",  # Blocked
    "5.7.1",      # SMTP enhanced status code (policy rejection/blacklist)
]

BLACKLIST_PATTERN = re.compile(
    "|".join(re.escape(kw) for kw in BLACKLIST_KEYWORDS),
    re.IGNORECASE,
)

_scan_lock = asyncio.Lock()


def connect_bounce_imap() -> Optional[imaplib.IMAP4_SSL]:
    """Connect to the bounce IMAP mailbox."""
    if not settings.bounce_imap_configured:
        logger.warning("Bounce IMAP not configured")
        return None
    try:
        if settings.bounce_imap_use_ssl:
            conn = imaplib.IMAP4_SSL(settings.bounce_imap_host,
                                     settings.bounce_imap_port)
        else:
            conn = imaplib.IMAP4(settings.bounce_imap_host,
                                 settings.bounce_imap_port)
        conn.login(settings.bounce_imap_user, settings.bounce_imap_pass)
        return conn
    except Exception as e:
        logger.error(f"Bounce IMAP connection failed: {e}")
        return None


def _extract_body(msg: email.message.EmailMessage) -> str:
    """Extract plain-text body from an email message."""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
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


def _extract_bounced_recipient(msg: email.message.EmailMessage, body: str) -> Optional[str]:
    """Extract the original recipient email from a bounce notification."""
    # Check common headers for the failed recipient
    for header in ["X-Failed-Recipients", "X-Original-To",
                    "Original-Recipient", "Final-Recipient"]:
        val = msg.get(header, "")
        if val:
            # Final-Recipient may be "rfc822; user@example.com"
            if ";" in val:
                val = val.split(";", 1)[1].strip()
            match = re.search(r'[\w.+-]+@[\w.-]+\.\w+', val)
            if match:
                return match.group(0).lower()

    # Check multipart DSN parts for Original-Recipient / Final-Recipient
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "message/delivery-status":
                dsn_payload = part.get_payload(decode=True)
                if dsn_payload:
                    dsn_text = dsn_payload.decode("utf-8", errors="replace")
                else:
                    dsn_text = str(part.get_payload())
                for line in dsn_text.splitlines():
                    if line.lower().startswith(("final-recipient:", "original-recipient:")):
                        match = re.search(r'[\w.+-]+@[\w.-]+\.\w+', line)
                        if match:
                            return match.group(0).lower()

    # Fallback: search the body for "To:" or email patterns near bounce keywords
    # Look for patterns like "delivery to <user@domain.com>"
    patterns = [
        re.compile(r'delivery\s+to\s+<?(\S+@\S+\.\w+)>?', re.IGNORECASE),
        re.compile(r'recipient[:\s]+<?(\S+@\S+\.\w+)>?', re.IGNORECASE),
        re.compile(r'address[:\s]+<?(\S+@\S+\.\w+)>?', re.IGNORECASE),
        re.compile(r'<(\S+@\S+\.\w+)>', re.IGNORECASE),
    ]
    for pat in patterns:
        match = pat.search(body)
        if match:
            addr = match.group(1).lower().rstrip(">")
            # Skip our own bounce address
            if addr != settings.bounce_imap_user.lower():
                return addr

    return None


def is_blacklist_bounce(body: str, subject: str = "") -> bool:
    """Check if a bounce email body indicates an IP/domain blacklist issue."""
    text = f"{subject}\n{body}"
    return bool(BLACKLIST_PATTERN.search(text))


async def scan_bounce_mailbox(client: ListMonkClient) -> dict:
    """
    Scan the bounce IMAP mailbox for blacklist-related bounces.
    For any hard bounces caused by IP blacklisting:
    - Delete the hard bounce
    - Create a soft bounce instead
    - Unblock the subscriber
    """
    if _scan_lock.locked():
        return {"scanned": 0, "fixed": 0, "errors": 0,
                "message": "Bounce scan already in progress"}

    async with _scan_lock:
        conn = connect_bounce_imap()
        if not conn:
            return {"scanned": 0, "fixed": 0, "errors": 0,
                    "message": "Bounce IMAP not configured or connection failed"}

        scanned = 0
        fixed = 0
        errors = 0
        fixed_emails = []

        try:
            conn.select("INBOX", readonly=True)

            # Search for bounce emails in the last 30 days
            since_date = datetime.utcnow() - timedelta(days=30)
            since_str = since_date.strftime("%d-%b-%Y")
            status, msg_ids = conn.search(None, f'(SINCE {since_str})')

            if status != "OK" or not msg_ids[0]:
                conn.logout()
                return {"scanned": 0, "fixed": 0, "errors": 0,
                        "message": "No emails found in bounce mailbox"}

            ids = msg_ids[0].split()
            scanned = len(ids)
            print(f"[BounceScanner] Found {scanned} emails in bounce mailbox")

            for msg_id in ids:
                try:
                    status, data = conn.fetch(msg_id, "(RFC822)")
                    if status != "OK":
                        continue

                    raw_email = data[0][1]
                    msg = email.message_from_bytes(raw_email, policy=email.policy.default)

                    subject = msg.get("Subject", "")
                    body = _extract_body(msg)

                    # Check if this is a blacklist-related bounce
                    if not is_blacklist_bounce(body, subject):
                        continue

                    # Extract the bounced recipient
                    recipient = _extract_bounced_recipient(msg, body)
                    if not recipient:
                        continue

                    # Find this subscriber in ListMonk
                    try:
                        sub_result = await client.get_subscribers(
                            1, 1, f"subscribers.email = '{recipient}'"
                        )
                        subs = sub_result.get("data", {}).get("results", [])
                        if not subs:
                            continue
                        subscriber = subs[0]
                    except Exception:
                        continue

                    # Find hard bounce records for this email
                    await _fix_subscriber_bounces(
                        client, subscriber, recipient,
                        f"Blacklist bounce: {subject[:100]}"
                    )
                    fixed += 1
                    fixed_emails.append(recipient)

                except Exception as e:
                    errors += 1
                    logger.error(f"Error processing bounce email: {e}")

        except Exception as e:
            logger.error(f"Bounce IMAP scan error: {e}")
            errors += 1
        finally:
            try:
                conn.logout()
            except Exception:
                pass

        result = {
            "scanned": scanned,
            "fixed": fixed,
            "errors": errors,
            "fixed_emails": fixed_emails,
            "timestamp": datetime.utcnow().isoformat(),
        }
        if fixed:
            print(f"[BounceScanner] Fixed {fixed} blacklist bounces (hard→soft)")
        return result


async def _fix_subscriber_bounces(client: ListMonkClient,
                                   subscriber: dict,
                                   email_addr: str,
                                   reason: str) -> bool:
    """
    For a given subscriber, find hard bounce records, delete them,
    create soft bounces, and unblock the subscriber.
    """
    sub_id = subscriber["id"]

    # Find all bounce records for this subscriber
    page = 1
    hard_bounces = []
    while True:
        result = await client.get_bounces(page, 100)
        data = result.get("data", {})
        results = data.get("results", [])
        if not results:
            break
        for b in results:
            if b.get("email", "").lower() == email_addr.lower() and b.get("type") == "hard":
                hard_bounces.append(b)
        if page * 100 >= data.get("total", 0):
            break
        page += 1

    if not hard_bounces:
        return False

    # Delete hard bounces and create soft bounces
    for bounce in hard_bounces:
        try:
            await client.delete_bounce(bounce["id"])
            # Create soft bounce to keep the record
            campaign_id = bounce.get("campaign", {}).get("id", 0)
            if campaign_id:
                try:
                    await client.create_bounce(
                        subscriber_id=sub_id,
                        campaign_id=campaign_id,
                        bounce_type="soft",
                        source="api",
                        meta={"original_type": "hard",
                              "reclassified": True,
                              "reason": reason},
                    )
                except Exception as e:
                    # Soft bounce creation is best-effort
                    logger.warning(f"Could not create soft bounce for {email_addr}: {e}")
        except Exception as e:
            logger.error(f"Failed to delete hard bounce {bounce['id']}: {e}")
            return False

    # Unblock subscriber if currently blocklisted
    if subscriber.get("status") == "blocklisted":
        try:
            await client.update_subscriber(sub_id, {
                "email": subscriber["email"],
                "name": subscriber.get("name", ""),
                "status": "enabled",
                "lists": [l["id"] for l in subscriber.get("lists", [])],
                "attribs": subscriber.get("attribs", {}),
            })
            logger.info(f"Unblocked {email_addr} (was blacklist bounce)")
        except Exception as e:
            logger.error(f"Failed to unblock {email_addr}: {e}")

    return True


async def fix_existing_hard_bounces(client: ListMonkClient) -> dict:
    """
    Fix hard bounces caused by IP blacklisting (Spamhaus etc.).
    Checks the classify_reason in bounce meta for policy rejection codes
    (5.7.1 etc.) and blacklist keywords.

    For matching hard bounces:
    - Delete the hard bounce record
    - Unblock the subscriber (re-enable)
    """
    print("[BounceScanner] Starting fix for existing hard bounces...")

    # Step 1: Fetch ALL hard bounces from ListMonk
    all_hard_bounces = []
    page = 1
    while True:
        result = await client.get_bounces(page, 500)
        data = result.get("data", {})
        results = data.get("results", [])
        if not results:
            break
        for b in results:
            if b.get("type") == "hard":
                all_hard_bounces.append(b)
        if page * 500 >= data.get("total", 0):
            break
        page += 1

    print(f"[BounceScanner] Found {len(all_hard_bounces)} total hard bounces")

    # Step 2: Identify blacklist-related bounces by classify_reason
    bounces_to_fix = []
    for b in all_hard_bounces:
        meta = b.get("meta", {})
        classify_reason = str(meta.get("classify_reason", ""))
        meta_str = str(meta)

        # Match on classify_reason containing policy rejection codes
        # or any blacklist keyword in the full meta
        if BLACKLIST_PATTERN.search(classify_reason) or BLACKLIST_PATTERN.search(meta_str):
            bounces_to_fix.append(b)

    print(f"[BounceScanner] {len(bounces_to_fix)} hard bounces identified as blacklist-related")

    if not bounces_to_fix:
        return {
            "total_hard_bounces": len(all_hard_bounces),
            "blacklist_related": 0,
            "fixed": 0,
            "unique_emails_fixed": 0,
            "subscribers_unblocked": 0,
            "errors": 0,
            "timestamp": datetime.utcnow().isoformat(),
        }

    # Step 3: Delete the hard bounce records
    fixed = 0
    errors = 0
    affected_emails = set()

    for b in bounces_to_fix:
        try:
            await client.delete_bounce(b["id"])
            fixed += 1
            affected_emails.add(b.get("email", "").lower())
        except Exception as e:
            errors += 1
            logger.error(f"Failed to delete bounce {b['id']}: {e}")

    print(f"[BounceScanner] Deleted {fixed} hard bounce records")

    # Step 4: Unblock all affected subscribers who are blocklisted
    subscribers_unblocked = 0
    for email_addr in affected_emails:
        try:
            sub_result = await client.get_subscribers(
                1, 1, f"subscribers.email = '{email_addr}'"
            )
            subs = sub_result.get("data", {}).get("results", [])
            if not subs:
                continue
            sub = subs[0]
            if sub.get("status") == "blocklisted":
                await client.update_subscriber(sub["id"], {
                    "email": sub["email"],
                    "name": sub.get("name", ""),
                    "status": "enabled",
                    "lists": [l["id"] for l in sub.get("lists", [])],
                    "attribs": sub.get("attribs", {}),
                })
                subscribers_unblocked += 1
        except Exception as e:
            logger.error(f"Failed to unblock {email_addr}: {e}")

    result = {
        "total_hard_bounces": len(all_hard_bounces),
        "blacklist_related": len(bounces_to_fix),
        "fixed": fixed,
        "unique_emails_fixed": len(affected_emails),
        "subscribers_unblocked": subscribers_unblocked,
        "errors": errors,
        "timestamp": datetime.utcnow().isoformat(),
    }
    print(f"[BounceScanner] Fix complete: {fixed} bounces deleted, "
          f"{subscribers_unblocked} subscribers unblocked, {errors} errors")
    return result
