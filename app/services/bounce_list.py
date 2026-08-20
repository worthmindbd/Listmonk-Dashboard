"""
Paginated bounce listing with opener exclusion — fast path for UI pages.
"""

from typing import Optional

from app.services.bounce_filters import (
    bounce_campaign_id,
    email_opened_campaign,
    exclude_openers_from_bounces,
    filter_bounces_excluding_openers,
)
from app.services.hard_bounce_cache import get_all_hard_bounce_counts, get_hard_bounce_count
from app.services.listmonk_client import ListMonkClient
from app.services.opener_cache import get_cached_opener_emails, is_cached

LM_FETCH_SIZE = 100
MAX_LM_PAGES = 300
CHECK_CONCURRENCY = 25


async def filter_bounces_excluding_openers_fast(
    client: ListMonkClient,
    bounces: list[dict],
) -> list[dict]:
    """Filter a bounce batch using one lightweight query per bounce."""
    if not bounces:
        return bounces

    campaign_ids = {cid for b in bounces if (cid := bounce_campaign_id(b))}
    if campaign_ids and all(is_cached(cid) for cid in campaign_ids):
        opener_map = {
            cid: get_cached_opener_emails(cid) or set() for cid in campaign_ids
        }
        return exclude_openers_from_bounces(bounces, opener_map)

    import asyncio

    sem = asyncio.Semaphore(CHECK_CONCURRENCY)
    keep: list[dict | None] = [None] * len(bounces)

    async def check(idx: int, bounce: dict):
        cid = bounce_campaign_id(bounce)
        email = bounce.get("email")
        if not cid or not email:
            keep[idx] = bounce
            return
        async with sem:
            opened = await email_opened_campaign(client, email, cid)
        if not opened:
            keep[idx] = bounce

    await asyncio.gather(*(check(i, b) for i, b in enumerate(bounces)))
    return [b for b in keep if b is not None]


def estimate_filtered_hard_total(campaign_id: Optional[int] = None) -> int | None:
    counts = get_all_hard_bounce_counts()
    if not counts:
        return None
    if campaign_id:
        return get_hard_bounce_count(campaign_id)
    return sum(counts.values())


async def fetch_filtered_bounces_page(
    client: ListMonkClient,
    page: int,
    per_page: int,
    bounce_type: str,
    campaign_id: Optional[int] = None,
    source: str = "",
) -> dict:
    """Return one page of bounces without loading the full dataset first."""
    needed = page * per_page
    collected: list[dict] = []
    lm_page = 1

    while len(collected) < needed and lm_page <= MAX_LM_PAGES:
        res = await client.get_bounces(
            lm_page, LM_FETCH_SIZE, campaign_id, source, bounce_type,
        )
        data = res.get("data", {})
        batch = [b for b in data.get("results", []) if b.get("type") == bounce_type]
        if not batch:
            break
        collected.extend(await filter_bounces_excluding_openers_fast(client, batch))
        if lm_page * LM_FETCH_SIZE >= data.get("total", 0):
            break
        lm_page += 1

    start = (page - 1) * per_page
    results = collected[start:start + per_page]

    # Total should match what the user sees: hard-bounce cache is authoritative
    # for hard bounces (already excludes openers); otherwise use the actual
    # filtered count so pagination never overcounts.
    total = len(collected)
    if bounce_type == "hard":
        cached = estimate_filtered_hard_total(campaign_id)
        if cached is not None:
            total = cached

    return {"data": {"results": results, "total": total}}


async def fetch_all_filtered_bounces(
    client: ListMonkClient,
    bounce_type: str = "",
    campaign_id: Optional[int] = None,
    source: str = "",
) -> list[dict]:
    """Full filtered list for export/delete — uses bulk path when openers are cached."""
    all_bounces = await client.paginate_all(
        client.get_bounces, per_page=500,
        campaign_id=campaign_id, source=source,
    )
    if bounce_type:
        all_bounces = [b for b in all_bounces if b.get("type") == bounce_type]
    if not all_bounces:
        return []
    if len(all_bounces) <= 500:
        return await filter_bounces_excluding_openers_fast(client, all_bounces)
    return await filter_bounces_excluding_openers(client, all_bounces)
