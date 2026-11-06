"""Shared IMAP helpers: email validation, IMAP date formatting, body extraction."""

import email
import email.message
import email.utils
import html
import re
from datetime import datetime
from typing import Optional

_ENG_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

_EMAIL_RE = re.compile(r"^[\w.+-]+@[\w.-]+\.\w+$")


def safe_email_for_query(email_addr: str) -> str | None:
    """Validate email format and escape for SQL query interpolation."""
    if not _EMAIL_RE.match(email_addr):
        return None
    return email_addr.replace("'", "''")


def extract_sender_address(from_header: str) -> Optional[str]:
    """Return the lowercased mailbox address from a From header, or None.

    Uses RFC 5322 address parsing so an address typed into the display name
    (``"boss@corp.com" <mallory@evil.com>``) is not mistaken for the sender.
    """
    _, addr = email.utils.parseaddr(str(from_header or ""))
    addr = (addr or "").strip().lower()
    if not addr or not _EMAIL_RE.match(addr):
        return None
    return addr


# Markers where quoted history starts in HTML replies (Gmail, Outlook, Apple
# Mail, Thunderbird). Everything from the first one on is the quoted original.
_HTML_QUOTE_START_RE = re.compile(
    r"<blockquote\b|<div[^>]+class=[\"\'][^\"\']*gmail_quote"
    r"|<div[^>]+id=[\"\'](?:divRplyFwdMsg|appendonsend)[\"\']"
    r"|<div[^>]+class=[\"\'][^\"\']*moz-cite-prefix",
    re.IGNORECASE,
)


def html_to_text(markup: str) -> str:
    """Convert an HTML email body to plain text, dropping quoted history.

    Good enough for keyword detection: block-level tags become line breaks,
    remaining tags are stripped and entities decoded.
    """
    if not markup:
        return ""
    quote = _HTML_QUOTE_START_RE.search(markup)
    if quote:
        markup = markup[:quote.start()]
    markup = re.sub(r"(?is)<(script|style|head)\b.*?</\1\s*>", " ", markup)
    markup = re.sub(r"(?i)<br\s*/?>|</(?:p|div|li|tr|h[1-6])\s*>", "\n", markup)
    markup = re.sub(r"(?s)<[^>]+>", "", markup)
    text = html.unescape(markup)
    return "\n".join(line.strip() for line in text.splitlines())


def _decode_part(part) -> str:
    payload = part.get_payload(decode=True)
    if not payload:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except (LookupError, UnicodeDecodeError):
        return payload.decode("utf-8", errors="replace")


def imap_date(dt: datetime) -> str:
    """Format a datetime as an IMAP SINCE date with English month names."""
    return f"{dt.day:02d}-{_ENG_MONTHS[dt.month - 1]}-{dt.year}"


def extract_email_body(msg: email.message.EmailMessage,
                       content_types=("text/plain",),
                       html_fallback: bool = False) -> str:
    """Extract the plain-text body from an email message.

    Args:
        msg: The parsed email message.
        content_types: Tuple of content types to extract. Defaults to text/plain.
        html_fallback: When no matching part has content, convert the
            ``text/html`` part(s) instead (quoted history removed). A
            single-part HTML message is always converted rather than returned
            as raw markup.
    """
    body = ""
    if msg.is_multipart():
        html_parts: list[str] = []
        for part in msg.walk():
            ct = part.get_content_type()
            if ct in content_types:
                text = _decode_part(part)
                if text:
                    body += text + "\n"
            elif html_fallback and ct == "text/html":
                html_parts.append(_decode_part(part))
        if not body.strip() and html_parts:
            body = "\n".join(html_to_text(h) for h in html_parts if h)
    else:
        body = _decode_part(msg)
        if msg.get_content_type() == "text/html":
            body = html_to_text(body)
    return body
