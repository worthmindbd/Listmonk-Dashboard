"""
Simple session-based authentication for the dashboard.
Uses a username/password from .env and signed cookies.
"""

import hashlib
import hmac
import json
import logging
import os
import secrets
import tempfile
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

# Login rate limiting (in-memory, per client IP)
LOGIN_MAX_ATTEMPTS = 10
LOGIN_WINDOW_SECONDS = 15 * 60
_MAX_TRACKED_IPS = 10_000
_login_failures: dict[str, list[float]] = {}


def is_login_rate_limited(ip: str) -> bool:
    """True if this IP has too many recent failed login attempts."""
    now = time.time()
    attempts = [t for t in _login_failures.get(ip, []) if now - t < LOGIN_WINDOW_SECONDS]
    if attempts:
        _login_failures[ip] = attempts
    return len(attempts) >= LOGIN_MAX_ATTEMPTS


def record_login_failure(ip: str) -> None:
    """Record a failed login attempt for an IP (bounded memory)."""
    if len(_login_failures) >= _MAX_TRACKED_IPS:
        now = time.time()
        for tracked_ip in list(_login_failures):
            attempts = [t for t in _login_failures[tracked_ip] if now - t < LOGIN_WINDOW_SECONDS]
            if attempts:
                _login_failures[tracked_ip] = attempts
            else:
                _login_failures.pop(tracked_ip, None)
        if len(_login_failures) >= _MAX_TRACKED_IPS:
            _login_failures.clear()
    _login_failures.setdefault(ip, []).append(time.time())


def clear_login_failures(ip: str) -> None:
    _login_failures.pop(ip, None)


def client_ip_from_request(request: Request) -> str:
    """Resolve the real client IP for rate limiting.

    Behind a reverse proxy ``request.client.host`` is the *proxy*, so every
    visitor shares one bucket and a handful of failed logins locks everyone
    out. ``X-Forwarded-For`` carries the original client, but it is
    client-controlled: only honour it when the immediate peer is a trusted
    proxy, otherwise an attacker can spoof a fresh IP per attempt and defeat
    the limit entirely.
    """
    peer = request.client.host if request.client else "unknown"

    trusted = os.getenv("TRUST_PROXY", "").strip().lower()
    if trusted in ("1", "true", "yes"):
        pass  # deployment states it always sits behind a trusted proxy
    elif trusted in ("0", "false", "no"):
        return peer
    else:
        # Default: only trust loopback/private peers (typical same-host Nginx).
        try:
            import ipaddress
            addr = ipaddress.ip_address(peer)
            trusted_peer = addr.is_loopback or addr.is_private
        except ValueError:
            trusted_peer = False
        if not trusted_peer:
            return peer

    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        # With the usual reverse-proxy setup ($proxy_add_x_forwarded_for) our
        # trusted proxy APPENDS the real client IP, so client-supplied entries
        # come first. Taking the LEFT-most entry therefore let an attacker
        # reset the rate-limit bucket with a fresh spoofed value per attempt.
        # The LAST entry is the peer our proxy actually saw — the only value
        # in the chain the client cannot forge.
        last = forwarded.split(",")[-1].strip()
        if last:
            return last
    return peer


def _load_or_create_secret_key() -> str:
    """Load SESSION_SECRET from env, persisted key file in DATA_DIR, or generate a new one."""
    key = os.getenv("SESSION_SECRET", "")
    if key:
        return key

    key_file = settings.data_path("session_secret.key")
    if key_file.exists():
        try:
            key_file.chmod(0o600)
        except OSError:
            pass
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
        try:
            key_file.chmod(0o600)
        except OSError:
            pass
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


def _credentials_fingerprint() -> str:
    """Digest of the configured login, mixed into every session signature.

    Changing DASHBOARD_USER or DASHBOARD_PASS therefore invalidates every
    existing session — previously a cookie stolen before a password rotation
    stayed valid for its full 7 days.
    """
    raw = f"{os.getenv('DASHBOARD_USER', '')}\0{os.getenv('DASHBOARD_PASS', '')}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _session_payload(timestamp: str, nonce: str) -> str:
    return f"{timestamp}:{nonce}:{_credentials_fingerprint()}"


# ── Server-side revocation ────────────────────────────────────────────────────
# Cookies are stateless, so logout used to only delete the browser's copy; the
# token itself stayed valid for 7 days. Each session now carries a random
# nonce, and logout records it here (persisted in DATA_DIR so a restart does
# not resurrect it). Entries expire with the session they revoke.

REVOKED_FILE_NAME = "revoked_sessions.json"
_revoked: dict[str, float] = {}       # nonce -> session expiry (epoch)
_revoked_loaded_mtime: Optional[float] = None


def _revoked_path() -> Path:
    return settings.data_path(REVOKED_FILE_NAME)


def invalidate_revocation_cache() -> None:
    """Drop the in-memory revocation set (tests; AGENTS.md rule 11)."""
    global _revoked_loaded_mtime
    _revoked.clear()
    _revoked_loaded_mtime = None


def _refresh_revoked() -> None:
    """(Re)load the revocation file when it changed since the last read."""
    global _revoked_loaded_mtime
    path = _revoked_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return
    if _revoked_loaded_mtime == mtime:
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("Could not read %s; keeping in-memory revocations", path)
        return
    now = time.time()
    if isinstance(data, dict):
        for nonce, exp in data.items():
            try:
                if float(exp) > now:
                    _revoked[str(nonce)] = float(exp)
            except (TypeError, ValueError):
                continue
    _revoked_loaded_mtime = mtime


def _revoke(nonce: str, expires_at: float) -> None:
    global _revoked_loaded_mtime
    _refresh_revoked()
    now = time.time()
    _revoked[nonce] = expires_at
    for n in [n for n, exp in _revoked.items() if exp <= now]:
        _revoked.pop(n, None)
    path = _revoked_path()
    try:
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix="revoked_", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(_revoked, f)
        os.replace(tmp, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
        _revoked_loaded_mtime = path.stat().st_mtime
    except OSError:
        # Still revoked for this process's lifetime; just not across restarts.
        logger.exception("Could not persist session revocation to %s", path)


def _parse_token(token: Optional[str]) -> Optional[tuple[str, str, int]]:
    """Validate a cookie token; return (nonce, timestamp, created) or None."""
    if not token or token.count(":") != 2:
        # Covers pre-revocation 2-part tokens: users simply log in once more.
        return None
    timestamp, nonce, signature = token.split(":")
    if not nonce or not hmac.compare_digest(signature, _sign(_session_payload(timestamp, nonce))):
        return None
    try:
        created = int(timestamp)
    except ValueError:
        return None
    return nonce, timestamp, created


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
    nonce = secrets.token_urlsafe(16)
    signature = _sign(_session_payload(timestamp, nonce))
    token = f"{timestamp}:{nonce}:{signature}"
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
    parsed = _parse_token(request.cookies.get(COOKIE_NAME))
    if parsed is None:
        return False
    nonce, _timestamp, created = parsed

    # Check expiry
    if time.time() - created > SESSION_MAX_AGE:
        return False

    # Check server-side revocation (logout)
    _refresh_revoked()
    if nonce in _revoked:
        return False

    return True


def clear_session(response: Response, request: Optional[Request] = None):
    """Revoke the current session server-side and remove the cookie."""
    if request is not None:
        parsed = _parse_token(request.cookies.get(COOKIE_NAME))
        if parsed is not None:
            nonce, _timestamp, created = parsed
            _revoke(nonce, created + SESSION_MAX_AGE)
    is_secure = _is_secure_cookie(request)
    response.delete_cookie(
        COOKIE_NAME,
        httponly=True,
        samesite="lax",
        secure=is_secure,
        path="/",
    )


def _constant_time_equals(a: str, b: str) -> bool:
    """Compare two strings in constant time.

    ``hmac.compare_digest`` raises ``TypeError`` on ``str`` inputs containing
    non-ASCII characters, which would turn a bad password into a 500. Encoding
    to UTF-8 first keeps the comparison constant-time for any input and makes
    non-ASCII credentials a normal authentication failure.
    """
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


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
    # Both sides are always evaluated so a wrong username does not return
    # measurably faster than a wrong password.
    user_ok = _constant_time_equals(username, valid_user)
    pass_ok = _constant_time_equals(password, valid_pass)
    return user_ok and pass_ok
