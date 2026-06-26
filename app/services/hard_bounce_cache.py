"""
Hard bounce cache: maintains counts of hard bounces per campaign.
Updated periodically in background to avoid slow on-demand fetching.
"""

import asyncio
import logging
from app.services.listmonk_client import listmonk
from app.services.bounce_filters import filter_bounces_excluding_openers

logger = logging.getLogger("hard_bounce_cache")

# Cache: {campaign_id: hard_bounce_count}
_hard_bounce_counts: dict[int, int] = {}
_last_updated: str = ""

BATCH_SIZE = 500
UPDATE_INTERVAL = 5 * 60  # 5 minutes


def _is_client_ready() -> bool:
    """Check if ListMonk client is ready (has valid HTTP client)."""
    return listmonk._client is not None


async def update_hard_bounce_counts():
    """Fetch all bounces and count hard bounces per campaign."""
    global _hard_bounce_counts, _last_updated
    from datetime import datetime

    try:
        logger.info("Updating hard bounce counts...")
        all_bounces = await listmonk.paginate_all(
            listmonk.get_bounces, per_page=BATCH_SIZE,
        )
        hard_bounces = [b for b in all_bounces if b.get("type") == "hard"]
        hard_bounces = await filter_bounces_excluding_openers(listmonk, hard_bounces)

        counts: dict[int, int] = {}
        for b in hard_bounces:
            cid = b.get("campaign", {}).get("id")
            if cid:
                counts[cid] = counts.get(cid, 0) + 1

        _hard_bounce_counts = counts
        _last_updated = datetime.now().isoformat()
        logger.info(f"Hard bounce counts updated: {len(counts)} campaigns")
    except Exception as e:
        logger.error(f"Failed to update hard bounce counts: {e}", exc_info=True)


def get_hard_bounce_count(campaign_id: int) -> int:
    """Get cached hard bounce count for a campaign."""
    return _hard_bounce_counts.get(campaign_id, 0)


def get_all_hard_bounce_counts() -> dict[int, int]:
    """Get all cached hard bounce counts."""
    return _hard_bounce_counts.copy()


def get_last_updated() -> str:
    """Get last update timestamp."""
    return _last_updated


async def start_cache_updater():
    """Background task to keep cache updated."""
    logger.info("Starting hard bounce cache updater")

    # Wait for client to be ready before first update
    for _ in range(10):  # Wait up to 5 seconds
        if _is_client_ready():
            break
        await asyncio.sleep(0.5)

    # Initial update
    await update_hard_bounce_counts()

    while True:
        await asyncio.sleep(UPDATE_INTERVAL)
        await update_hard_bounce_counts()