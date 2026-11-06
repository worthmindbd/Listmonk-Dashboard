"""Shared helpers for attributing unsubscribes/bounces to the campaign a
subscriber actually received.

All three attribution paths (IMAP replies, link unsubscribes, bounce
ingestion) used to pick "the newest campaign by ``created_at``" regardless of
status — so creating next week's *draft* for a list immediately stole the
attribution of every new unsubscribe/bounce from that list. Only campaigns
that have actually gone out are eligible, and they are dated by when sending
started rather than when the draft was created.
"""

from datetime import datetime, timezone
from typing import Optional

# ListMonk statuses for campaigns that have not sent a single message yet.
UNSENT_STATUSES = frozenset({"draft", "scheduled"})


def parse_listmonk_dt(value) -> Optional[datetime]:
    """Parse a ListMonk timestamp into an aware UTC datetime (None if unusable)."""
    if not value or not isinstance(value, str):
        return None
    for candidate in (value, value[:10]):
        try:
            parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None


def is_sent_campaign(camp: dict) -> bool:
    """True unless the campaign is known not to have sent anything.

    A missing status is treated as sent so older API responses (and callers
    that only carry ids/dates) keep working.
    """
    return (camp.get("status") or "").lower() not in UNSENT_STATUSES


def campaign_sent_at(camp: dict) -> Optional[datetime]:
    """When the campaign started sending, falling back to ``created_at``."""
    return parse_listmonk_dt(camp.get("started_at")) or parse_listmonk_dt(camp.get("created_at"))
