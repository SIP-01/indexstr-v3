"""test_protocol.py — vendored SIP-01 selftest vectors + build→validate roundtrip."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sip01_node import nostr, sip01  # noqa: E402


def test_selftest_vectors():
    """All spec §13 normalization/content-hash vectors must pass."""
    assert sip01.selftest() is True


def test_normalize_strips_www_tracking_fragment():
    assert sip01.normalize_index_url(
        "HTTPS://WWW.Example.Com:443/page/?b=2&utm_source=x&a=1#top"
    ) == "https://example.com/page?a=1&b=2"


def test_normalize_rejects_non_http():
    assert sip01.normalize_index_url("ftp://example.com/x") is None
    assert sip01.normalize_index_url("javascript:alert(1)") is None


def test_build_validate_roundtrip():
    """An event built by the toolkit must validate clean."""
    ev = sip01.build_event(
        "https://example.com/",
        "Example Domain",
        description="An example page.",
        topics=["example", "test"],
        lang="en",
        published=1700000000,
        source="sip01-node/test",
    )
    result = sip01.validate_event(ev)
    assert result["valid"], result["errors"]


def test_signed_event_roundtrip():
    """build → sign → verify signature → validate must all pass."""
    seckey = nostr.generate_seckey()
    unsigned = sip01.build_event("https://example.com/", "Example Domain",
                                 source="sip01-node/test")
    signed = nostr.sign_event(unsigned, seckey)
    assert nostr.verify_event(signed)
    assert signed["pubkey"] == nostr.pubkey_from_seckey(seckey)
    assert sip01.validate_event(signed)["valid"]


def test_nsec_codec_roundtrip():
    seckey = nostr.generate_seckey()
    assert nostr.nsec_decode(nostr.nsec_encode(seckey)) == seckey


def test_validate_catches_bad_x():
    ev = sip01.build_event("https://example.com/", "Example Domain")
    ev["tags"] = [["x", "0" * 64] if t[0] == "x" else t for t in ev["tags"]]
    result = sip01.validate_event(ev)
    assert not result["valid"]
    assert any("x tag" in e for e in result["errors"])


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
