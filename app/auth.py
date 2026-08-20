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
from pathlib import Path
from fastapi import Request, Response
from app.config import settings

logger = logging.getLogger("auth")

# Session cookie name
COOKIE_NAME = "lmpro_session"
# Session duration: 7 days
SESSION_MAX_AGE = 7 * 24 * 60 * 60

# Secret key for signing cookies (auto-generated on first run, persisted to .env)

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_or_create_secret_key() -> str:
    """Load SESSION_SECRET from env, or generate a new one and persist it to .env."""
    key = os.getenv("SESSION_SECRET", "")
    if key:
        return key

    key = secrets.token_hex(32)

    # Try to persist so sessions survive restarts (dev / non-Docker).
    # In Docker, the .env is bind-mounted read-only; persistence fails
    # silently but the user should set SESSION_SECRET explicitly.
    dotenv_path = _REPO_ROOT / ".env"
    try:
        with open(dotenv_path, "a") as f:
            f.write(f"\nSESSION_SECRET={key}\n")
        os.environ["SESSION_SECRET"] = key
        logger.info("Generated and persisted SESSION_SECRET to .env")
    except OSError:
        logger.warning(
            "SESSION_SECRET not set; generated ephemeral key. "
            "Sessions will break on restart. "
            "Set SESSION_SECRET in .env for persistent sessions."
        )
    return key


_secret_key = _load_or_create_secret_key()


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
        secure=True,
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
    """Validate login credentials against DASHBOARD_USER / DASHBOARD_PASS env vars.

    Returns False when either env var is unset, so a misconfigured deployment
    is locked rather than silently accepting default credentials.
    """
    valid_user = os.getenv("DASHBOARD_USER", "")
    valid_pass = os.getenv("DASHBOARD_PASS", "")
    if not valid_user or not valid_pass:
        logger.error(
            "DASHBOARD_USER and DASHBOARD_PASS must be set in .env. "
            "Login is disabled until credentials are configured."
        )
        return False
    return (
        hmac.compare_digest(username, valid_user)
        and hmac.compare_digest(password, valid_pass)
    )
