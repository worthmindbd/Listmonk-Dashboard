"""
Bounce filtering: subscribers who opened a campaign cannot have bounced it.

Used at ingestion (skip false positives), display/export, and bounce counts.
"""

from app.services.listmonk_client import ListMonkClient


def campaign_views_query(campaign_id: int) -> str:
    return (
        f"subscribers.id IN (SELECT subscriber_id FROM campaign_views "
        f"WHERE campaign_id={campaign_id})"
    )


def _normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def bounce_campaign_id(bounce: dict) -> int | None:
    cid = bounce.get("campaign", {}).get("id")
    return int(cid) if cid else None


async def get_campaign_opener_emails(
    client: ListMonkClient, campaign_id: int,
) -> set[str]:
    """Return lowercased emails of subscribers who opened the campaign."""
    subs = await client.paginate_all(
        client.get_subscribers, per_page=500,
        query=campaign_views_query(campaign_id),
    )
    return {_normalize_email(s["email"]) for s in subs if s.get("email")}


async def subscriber_opened_campaign(
    client: ListMonkClient, subscriber_id: int, campaign_id: int,
) -> bool:
    """True if the subscriber has a campaign_views row for this campaign."""
    query = (
        f"subscribers.id = {subscriber_id} "
        f"AND subscribers.id IN (SELECT subscriber_id FROM campaign_views "
        f"WHERE campaign_id={campaign_id})"
    )
    result = await client.get_subscribers(1, 1, query)
    return result.get("data", {}).get("total", 0) > 0


async def build_opener_emails_by_campaign(
    client: ListMonkClient, campaign_ids: set[int],
) -> dict[int, set[str]]:
    opener_map: dict[int, set[str]] = {}
    for cid in campaign_ids:
        opener_map[cid] = await get_campaign_opener_emails(client, cid)
    return opener_map


def exclude_openers_from_bounces(
    bounces: list[dict],
    opener_emails_by_campaign: dict[int, set[str]],
) -> list[dict]:
    """Drop bounces whose email opened the attributed campaign."""
    filtered: list[dict] = []
    for b in bounces:
        cid = bounce_campaign_id(b)
        email = _normalize_email(b.get("email", ""))
        if cid and email and email in opener_emails_by_campaign.get(cid, set()):
            continue
        filtered.append(b)
    return filtered


async def filter_bounces_excluding_openers(
    client: ListMonkClient,
    bounces: list[dict],
) -> list[dict]:
    """Exclude opener false-positives across one or many campaigns."""
    campaign_ids = {cid for b in bounces if (cid := bounce_campaign_id(b))}
    if not campaign_ids:
        return bounces
    opener_map = await build_opener_emails_by_campaign(client, campaign_ids)
    return exclude_openers_from_bounces(bounces, opener_map)


async def filter_campaign_hard_bounces(
    client: ListMonkClient,
    campaign_id: int,
    bounces: list[dict],
) -> list[dict]:
    """Hard bounces for one campaign, excluding subscribers who opened it."""
    hard = [b for b in bounces if b.get("type") == "hard"]
    openers = await get_campaign_opener_emails(client, campaign_id)
    return exclude_openers_from_bounces(hard, {campaign_id: openers})
