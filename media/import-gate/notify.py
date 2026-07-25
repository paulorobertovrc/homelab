"""ntfy push (best-effort; never raises into the caller)."""
import logging
from email.header import Header

import requests

logger = logging.getLogger(__name__)


def _header_safe(title: str) -> str:
    """A Title header CPython can actually put on the wire.

    http.client encodes str header values as latin-1, so an emoji raises
    UnicodeEncodeError before a single byte leaves the process -- and push's
    except-and-continue turned that into a silently dropped notification. Every
    emoji-titled alert this stack sends was lost this way between 2026-07-04 and
    2026-07-25, including the quarantine alert, while the ASCII-only titles kept
    arriving and made the channel look healthy.

    RFC 2047 is the encoding HTTP headers define for this, and ntfy decodes it --
    verified against the live server, which returned the emoji title intact. Titles
    that already fit latin-1 pass through untouched so the common case stays readable
    on the wire.
    """
    try:
        title.encode("latin-1")
        return title
    except UnicodeEncodeError:
        return str(Header(title, "utf-8").encode())


def push(ntfy_url: str, title: str, tags: str, priority: int, message: str) -> None:
    try:
        requests.post(
            ntfy_url,
            data=message.encode("utf-8"),
            headers={"Title": _header_safe(title), "Tags": tags,
                     "Priority": str(priority)},
            timeout=10,
        )
    except Exception as e:
        # Still never raises into the caller -- a dead ntfy must not break an import
        # or a queue-watch cycle. But it no longer vanishes: a swallowed alert is
        # worse than no alert, and silence here is precisely what hid the bug above.
        logger.warning("ntfy push failed (%s): %s", title, e)
