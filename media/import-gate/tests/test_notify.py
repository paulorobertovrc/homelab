"""Regression suite for the ntfy push.

Written after a review found that EVERY emoji-titled notification this stack
sends had been silently discarded since 2026-07-04: CPython encodes HTTP header
values as latin-1, an emoji raises UnicodeEncodeError inside requests, and
push's bare `except Exception: pass` swallowed it. Confirmed live -- a real
quarantine fired on 2026-07-25 10:24 and never reached the phone.
"""
import logging

import pytest

import notify


class FakePost:
    """Stands in for requests.post, enforcing the constraint that actually broke us.

    http.client.putheader() does `value.encode('latin-1')` on str header values,
    so a header this fake accepts is a header the real stack can send.
    """

    def __init__(self, raise_exc=None):
        self.calls = []
        self.raise_exc = raise_exc

    def __call__(self, url, data=None, headers=None, timeout=None):
        if self.raise_exc:
            raise self.raise_exc
        for name, value in (headers or {}).items():
            value.encode("latin-1")   # raises exactly where the real client would
        self.calls.append((url, data, headers))
        return None


def _push(monkeypatch, title, raise_exc=None):
    post = FakePost(raise_exc)
    monkeypatch.setattr(notify.requests, "post", post)
    notify.push("http://ntfy/arr-media", title, "lock", 4, "corpo com emoji \U0001f512")
    return post


EMOJI_TITLES = [
    "\U0001f6ab queue-watch: grab pre-air",
    "\U0001f9f9 queue-watch: fila destravada",
    "⚠️ queue-watch: anomalia em massa",
    "\U0001f512 Quarentena",
    "⚠️ Import-gate desistiu",
]


@pytest.mark.parametrize("title", EMOJI_TITLES)
def test_emoji_titles_are_actually_sent(monkeypatch, title):
    """The whole point: these five titles used to be dropped before hitting the wire."""
    post = _push(monkeypatch, title)
    assert len(post.calls) == 1


@pytest.mark.parametrize("title", EMOJI_TITLES + ["Import-gate indisponivel", "ASCII only"])
def test_title_round_trips_back_to_the_original(monkeypatch, title):
    """ntfy decodes RFC 2047, so the phone must still show the original text.
    Verified against the live ntfy: the emoji survives the round trip."""
    from email.header import decode_header, make_header

    post = _push(monkeypatch, title)
    sent = post.calls[0][2]["Title"]
    assert str(make_header(decode_header(sent))) == title


def test_body_still_carries_utf8_untouched(monkeypatch):
    """Only the header is latin-1 constrained; the body is explicitly utf-8 encoded."""
    post = _push(monkeypatch, "\U0001f512 Quarentena")
    assert post.calls[0][1] == "corpo com emoji \U0001f512".encode("utf-8")


def test_non_title_headers_are_sent_as_before(monkeypatch):
    post = _push(monkeypatch, "\U0001f512 Quarentena")
    headers = post.calls[0][2]
    assert headers["Tags"] == "lock"
    assert headers["Priority"] == "4"


def test_push_never_raises_into_the_caller(monkeypatch):
    """Contract relied on by every call site: a dead ntfy must not break the caller."""
    _push(monkeypatch, "\U0001f512 Quarentena", raise_exc=RuntimeError("ntfy down"))


def test_failure_is_logged_not_silently_swallowed(monkeypatch, caplog):
    """A swallowed alert is worse than no alert -- it is exactly what hid this bug
    for three weeks. Still no raise, but it must leave a trace."""
    with caplog.at_level(logging.WARNING, logger="notify"):
        _push(monkeypatch, "\U0001f512 Quarentena", raise_exc=RuntimeError("ntfy down"))
    assert "ntfy down" in caplog.text
