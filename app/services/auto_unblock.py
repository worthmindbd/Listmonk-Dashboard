"""
Auto-unblock service: Finds subscribers who clicked links in any campaign
but are currently blocklisted, and re-enables them + deletes their bounce records.

Logic: If a subscriber clicked a link, they are a real engaged user.
Being blocklisted (usually from a bounce) is a false positive.
"""

import asyncio
import logging
from datetime import datetime, timezone
from app.services.listmonk_client import ListMonkClient, listmonk as listmonk_singleton
from app.config import settings

logger = logging.getLogger("auto_unblock")

QUERY_BLOCKLISTED_CLICKERS = (
    "subscribers.status = 'blocklisted' "
    "AND subscribers.id IN (SELECT DISTINCT subscriber_id FROM link_clicks)"
)


async def find_blocklisted_clickers(client: ListMonkClient) -> list[dict]:
    """Find all subscribers who clicked but are blocklisted."""
    return await client.paginate_all(
        client.get_subscribers, per_page=500, query=QUERY_BLOCKLISTED_CLICKERS,
    )


async def delete_bounce_records(client: ListMonkClient, emails: set[str]) -> int:
    """Delete all bounce records for the given email addresses."""
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
    """Unblock subscribers: set status to enabled + delete their bounce records."""
    success = 0
    failed = 0
    unblocked = []

    # Step 1: Re-enable all subscribers
    for s in subscribers:
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
            logger.info(f"Unblocked: {s['email']}")
        except Exception as e:
            failed += 1
            logger.error(f"Failed to unblock {s['email']}: {e}")

    # Step 2: Delete bounce records for unblocked emails
    if unblocked:
        bounces_deleted = await delete_bounce_records(client, set(unblocked))
        logger.info(f"Deleted {bounces_deleted} bounce records")
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
    """Main entry point: find and unblock all blocklisted clickers.
    Uses the singleton client when available, falls back to creating one."""
    try:
        subs = await find_blocklisted_clickers(listmonk_singleton)
        if not subs:
            logger.info("No blocklisted clickers found")
            return {"success": 0, "failed": 0, "bounces_deleted": 0,
                    "unblocked": [], "timestamp": datetime.now(timezone.utc).isoformat()}
        logger.info(f"Found {len(subs)} blocklisted clicker(s) to unblock")
        return await unblock_subscribers(listmonk_singleton, subs)
    except RuntimeError:
        # Singleton not started (CLI usage) — create standalone client
        client = ListMonkClient()
        await client.start()
        try:
            subs = await find_blocklisted_clickers(client)
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
