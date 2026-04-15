"""Shared IMAP helpers: email validation, IMAP date formatting."""

import re
from datetime import datetime

_ENG_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

_EMAIL_RE = re.compile(r"^[\w.+-]+@[\w.-]+\.\w+$")


def safe_email_for_query(email_addr: str) -> str | None:
    """Validate email format and escape for SQL query interpolation."""
    if not _EMAIL_RE.match(email_addr):
        return None
    return email_addr.replace("'", "''")


def imap_date(dt: datetime) -> str:
    """Format a datetime as an IMAP SINCE date with English month names."""
    return f"{dt.day:02d}-{_ENG_MONTHS[dt.month - 1]}-{dt.year}"
