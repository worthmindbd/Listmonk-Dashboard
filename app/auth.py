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
from typing import Optional
from fastapi import Request, Response
from app.config import settings

logger = logging.getLogger("auth")

# Session cookie name
COOKIE_NAME = "lmpro_session"
# Session duration: 7 days
SESSION_MAX_AGE = 7 * 24 * 60 * 60

# Secret key for signing cookies (auto-generated on first run, persisted to DATA_DIR or .env)

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_or_create_secret_key() -> str:
    """Load SESSION_SECRET from env, persisted key file in DATA_DIR, or generate a new one."""
    key = os.getenv("SESSION_SECRET", "")
    if key:
        return key

    key_file = settings.data_path("session_secret.key")
    if key_file.exists():
        try:
            persisted = key_file.read_text(encoding="utf-8").strip()
            if persisted:
                os.environ["SESSION_SECRET"] = persisted
                return persisted
        except OSError:
            pass

    key = secrets.token_hex(32)

    # Persist to DATA_DIR so sessions survive restarts across Docker & dev
    try:
        key_file.write_text(key, encoding="utf-8")
        os.environ["SESSION_SECRET"] = key
        logger.info("Generated and persisted SESSION_SECRET to %s", key_file)
        return key
    except OSError:
        pass

    # Fallback to appending to .env if DATA_DIR write fails
    dotenv_path = _REPO_ROOT / ".env"
    try:
        with open(dotenv_path, "a") as f:
            f.write(f"\nSESSION_SECRET={key}\n")
        os.environ["SESSION_SECRET"] = key
        logger.info("Generated and persisted SESSION_SECRET to .env")
    except OSError:
        logger.warning(
            "SESSION_SECRET not set and could not be persisted. "
            "Sessions will break on restart. "
            "Set SESSION_SECRET in .env for persistent sessions."
        )
    return key


_secret_key = _load_or_create_secret_key()


def _sign(value: str) -> str:
    """Create HMAC signature for a value."""
    return hmac.new(_secret_key.encode(), value.encode(), hashlib.sha256).hexdigest()


def _is_secure_cookie(request: Optional[Request] = None) -> bool:
    cookie_secure_env = os.getenv("COOKIE_SECURE", "").strip().lower()
    if cookie_secure_env in ("1", "true", "yes"):
        return True
    if cookie_secure_env in ("0", "false", "no"):
        return False
    if request is not None:
        if request.url.scheme == "https":
            return True
        if request.headers.get("x-forwarded-proto", "").lower() == "https":
            return True
        return False
    return False


def create_session(response: Response, request: Optional[Request] = None):
    """Set a signed session cookie on the response."""
    timestamp = str(int(time.time()))
    signature = _sign(timestamp)
    token = f"{timestamp}:{signature}"
    is_secure = _is_secure_cookie(request)
    response.set_cookie(
        COOKIE_NAME, token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=is_secure,
        path="/",
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
    response.delete_cookie(
        COOKIE_NAME,
        httponly=True,
        samesite="lax",
        path="/",
    )


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
