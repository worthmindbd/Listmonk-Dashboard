"""
Auto-unblock service: Finds subscribers who clicked links or opened campaigns
but were blocklisted BY A BOUNCE, and re-enables them + deletes their bounce
records.

Logic: If a subscriber clicked or opened, their address demonstrably works,
so a bounce-driven blocklist is a false positive.

Deliberate blocklists are never reversed. Engagement alone used to qualify,
which re-enabled (every 6 hours) people who were blocklisted because they
unsubscribed — almost everyone who replies "remove me" opened the email —
people an admin blocklisted by hand, and spam complainers (you have to open
an email to report it). Candidates must now:
  * have at least one hard/soft bounce record (the blocklist came from a bounce),
  * have no complaint bounce, and
  * not appear in the dashboard's unsubscribe records.
"""

import asyncio
import logging
from datetime import datetime, timezone
from app.services.listmonk_client import ListMonkClient, listmonk as listmonk_singleton
from app.config import settings
from app.services.task_utils import spawn
from app.services.unsubscribe_log import load_log, load_processed_map_all

logger = logging.getLogger("auto_unblock")

QUERY_BLOCKLISTED_ENGAGED = (
    "subscribers.status = 'blocklisted' "
    "AND (subscribers.id IN (SELECT DISTINCT subscriber_id FROM link_clicks) "
    "OR subscribers.id IN (SELECT DISTINCT subscriber_id FROM campaign_views)) "
    # Only bounce-driven blocklists are candidates...
    "AND subscribers.id IN (SELECT DISTINCT subscriber_id FROM bounces "
    "WHERE type IN ('hard', 'soft')) "
    # ...and never someone who filed a spam complaint.
    "AND subscribers.id NOT IN (SELECT DISTINCT subscriber_id FROM bounces "
    "WHERE type = 'complaint')"
)

# Backward-compatible alias
QUERY_BLOCKLISTED_CLICKERS = QUERY_BLOCKLISTED_ENGAGED

# One unblock pass at a time: the 6-hour loop and a manual POST
# /api/auto-unblock/run would otherwise traverse/delete the same records
# concurrently (duplicate unblocks and logged delete errors).
_unblock_lock = asyncio.Lock()

# Bound concurrent re-enable round trips so a large backlog cannot open
# hundreds of simultaneous requests to ListMonk.
_UNBLOCK_CONCURRENCY = 10


def _unsubscribed_emails() -> set[str]:
    """Every email the dashboard has ever handled as an unsubscribe request.

    Uses the full processed map (no TTL) plus the display log, so neither an
    expired dedup entry nor a deleted log record makes someone eligible.
    """
    emails = set(load_processed_map_all())
    emails.update((r.get("email") or "").lower() for r in load_log() if r.get("email"))
    emails.discard("")
    return emails


async def find_blocklisted_engaged(client: ListMonkClient) -> list[dict]:
    """Find bounce-blocklisted subscribers who clicked or opened a campaign.

    Excludes anyone the unsubscribe engine handled (see module docstring).
    """
    candidates = await client.paginate_all(
        client.get_subscribers, per_page=500, query=QUERY_BLOCKLISTED_ENGAGED,
    )
    if not candidates:
        return candidates
    suppressed = await asyncio.to_thread(_unsubscribed_emails)
    kept = [s for s in candidates if (s.get("email") or "").lower() not in suppressed]
    if len(kept) != len(candidates):
        logger.info(
            f"Auto-unblock: skipped {len(candidates) - len(kept)} subscriber(s) "
            "who unsubscribed (their blocklist is deliberate)"
        )
    return kept


async def find_blocklisted_clickers(client: ListMonkClient) -> list[dict]:
    """Backward-compatible alias for find_blocklisted_engaged."""
    return await find_blocklisted_engaged(client)


async def delete_bounce_records_for_subscribers(client: ListMonkClient, subscriber_ids: list[int]) -> int:
    """Delete all bounce records for the given subscriber IDs (bounded parallel)."""
    sem = asyncio.Semaphore(_UNBLOCK_CONCURRENCY)
    deleted = 0

    async def _delete(sid: int) -> None:
        nonlocal deleted
        async with sem:
            try:
                res = await client.get_subscriber_bounces(sid)
                bounces = res.get("data", [])
                for b in bounces:
                    bid = b.get("id")
                    if bid:
                        try:
                            await client.delete_bounce(bid)
                            deleted += 1
                        except Exception as e:
                            logger.error(f"Failed to delete bounce {bid}: {e}")
            except Exception as e:
                logger.error(f"Failed to fetch bounces for subscriber {sid}: {e}")

    await asyncio.gather(*(_delete(sid) for sid in subscriber_ids))
    return deleted


async def delete_bounce_records(client: ListMonkClient, emails: set[str]) -> int:
    """Delete all bounce records for the given email addresses (fallback)."""
    deleted = 0
    all_bounces = await client.paginate_all(client.get_bounces, per_page=500)
    bounce_ids_to_delete = [b["id"] for b in all_bounces if b.get("email") in emails]

    for bid in bounce_ids_to_delete:
        try:
            await client.delete_bounce(bid)
            deleted += 1
        except Exception as e:
            logger.error(f"Failed to delete bounce {bid}: {e}")

    return deleted


async def unblock_subscribers(client: ListMonkClient, subscribers: list[dict]) -> dict:
    """Unblock subscribers: set status to enabled + delete their bounce records.

    Serialized by ``_unblock_lock`` and bounded by ``_UNBLOCK_CONCURRENCY``.
    """
    async with _unblock_lock:
        return await _unblock_subscribers_impl(client, subscribers)


async def _unblock_subscribers_impl(client: ListMonkClient, subscribers: list[dict]) -> dict:
    success = 0
    failed = 0
    unblocked = []
    unblocked_ids = []
    sem = asyncio.Semaphore(_UNBLOCK_CONCURRENCY)

    # Step 1: Re-enable all subscribers (bounded parallel)
    async def _enable(s: dict) -> None:
        nonlocal success, failed
        async with sem:
            try:
                await client.update_subscriber(s["id"], {
                    "email": s["email"],
                    "name": s.get("name", ""),
                    "status": "enabled",
                    "lists": [l["id"] for l in s.get("lists", [])],
                    "attribs": s.get("attribs", {}),
                })
                success += 1
                unblocked.append(s["email"])
                unblocked_ids.append(s["id"])
                logger.info(f"Unblocked: {s['email']}")
            except Exception as e:
                failed += 1
                logger.error(f"Failed to unblock {s['email']}: {e}")

    await asyncio.gather(*(_enable(s) for s in subscribers))

    # Step 2: Delete bounce records for unblocked subscribers
    if unblocked_ids:
        bounces_deleted = await delete_bounce_records_for_subscribers(client, unblocked_ids)
        logger.info(f"Deleted {bounces_deleted} bounce records")
        if bounces_deleted > 0:
            from app.services.hard_bounce_cache import schedule_hard_bounce_update
            schedule_hard_bounce_update()
    else:
        bounces_deleted = 0

    return {
        "success": success,
        "failed": failed,
        "bounces_deleted": bounces_deleted,
        "unblocked": unblocked,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


async def run_auto_unblock() -> dict:
    """Main entry point: find and unblock all blocklisted engaged subscribers.
    Uses the singleton client when available, falls back to creating one."""
    try:
        subs = await find_blocklisted_engaged(listmonk_singleton)
        if not subs:
            logger.info("No blocklisted engaged subscribers found")
            return {"success": 0, "failed": 0, "bounces_deleted": 0,
                    "unblocked": [], "timestamp": datetime.now(timezone.utc).isoformat()}
        logger.info(f"Found {len(subs)} blocklisted engaged subscriber(s) to unblock")
        return await unblock_subscribers(listmonk_singleton, subs)
    except RuntimeError:
        # Singleton not started (CLI usage) — create standalone client
        client = ListMonkClient()
        await client.start()
        try:
            subs = await find_blocklisted_engaged(client)
            if not subs:
                return {"success": 0, "failed": 0, "bounces_deleted": 0,
                        "unblocked": [], "timestamp": datetime.now(timezone.utc).isoformat()}
            return await unblock_subscribers(client, subs)
        finally:
            await client.close()


# Standalone CLI usage
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    result = asyncio.run(run_auto_unblock())
    print(f"\nResult: {result['success']} unblocked, {result['failed']} failed, {result['bounces_deleted']} bounces deleted")
    for email in result["unblocked"]:
        print(f"  {email}")
