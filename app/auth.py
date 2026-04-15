"""
Simple session-based authentication for the dashboard.
Uses a username/password from .env and signed cookies.
"""

import hashlib
import hmac
import logging
import os
import secrets
import time
from fastapi import Request, Response
from app.config import settings

logger = logging.getLogger("auth")

# Session cookie name
COOKIE_NAME = "lmpro_session"
# Session duration: 7 days
SESSION_MAX_AGE = 7 * 24 * 60 * 60

# Secret key for signing cookies (auto-generated on first run, persists in .env)
_secret_key = os.getenv("SESSION_SECRET", "")
if not _secret_key:
    _secret_key = secrets.token_hex(32)
    logger.warning("SESSION_SECRET not set; generated ephemeral key. "
                   "Set SESSION_SECRET in .env for persistent sessions across restarts.")


def _sign(value: str) -> str:
    """Create HMAC signature for a value."""
    return hmac.new(_secret_key.encode(), value.encode(), hashlib.sha256).hexdigest()


def create_session(response: Response):
    """Set a signed session cookie on the response."""
    timestamp = str(int(time.time()))
    signature = _sign(timestamp)
    token = f"{timestamp}:{signature}"
    response.set_cookie(
        COOKIE_NAME, token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
    )


def verify_session(request: Request) -> bool:
    """Check if the request has a valid session cookie."""
    token = request.cookies.get(COOKIE_NAME)
    if not token or ":" not in token:
        return False

    timestamp, signature = token.split(":", 1)

    # Verify signature
    if not hmac.compare_digest(signature, _sign(timestamp)):
        return False

    # Check expiry
    try:
        created = int(timestamp)
        if time.time() - created > SESSION_MAX_AGE:
            return False
    except ValueError:
        return False

    return True


def clear_session(response: Response):
    """Remove the session cookie."""
    response.delete_cookie(COOKIE_NAME)


def check_credentials(username: str, password: str) -> bool:
    """Validate login credentials against .env values."""
    valid_user = os.getenv("DASHBOARD_USER", "admin")
    valid_pass = os.getenv("DASHBOARD_PASS", "admin")
    return (
        hmac.compare_digest(username, valid_user)
        and hmac.compare_digest(password, valid_pass)
    )
