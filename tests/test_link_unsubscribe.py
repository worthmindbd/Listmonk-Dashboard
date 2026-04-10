import pytest
from unittest.mock import AsyncMock, MagicMock

# ── Helpers ──────────────────────────────────────────────────────────────────

def make_client(get_response=None):
    """Return a ListMonkClient whose _request is mocked."""
    from app.services.listmonk_client import ListMonkClient
    client = ListMonkClient.__new__(ListMonkClient)
    client._client = MagicMock()
    client._request = AsyncMock(return_value=get_response or {})
    return client


# ── get_subscribers_by_list_status ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_subscribers_by_list_status_passes_correct_params():
    fake_response = {"data": {"results": [], "total": 0}}
    client = make_client(fake_response)

    result = await client.get_subscribers_by_list_status(
        list_id=5,
        subscription_status="unsubscribed",
        page=1,
        per_page=100,
    )

    client._request.assert_called_once_with(
        "GET", "/api/subscribers",
        params={
            "list_id": 5,
            "subscription_status": "unsubscribed",
            "page": 1,
            "per_page": 100,
        }
    )
    assert result == fake_response
