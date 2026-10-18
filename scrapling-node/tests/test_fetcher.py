"""test_fetcher.py — integration tests (real network) + SSRF guard unit tests.

Integration tests are marked ``integration`` and skip automatically when the
network is unavailable, so the suite still passes offline.
"""
from __future__ import annotations

import asyncio
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sip01_node.fetcher import SSRFError, TieredFetcher, _check_public_host  # noqa: E402


def _online() -> bool:
    try:
        socket.setdefaulttimeout(5)
        socket.create_connection(("example.com", 443)).close()
        return True
    except OSError:
        return False


ONLINE = _online()
integration = pytest.mark.skipif(not ONLINE, reason="no network in sandbox")


def test_ssrf_guard_refuses_loopback():
    with pytest.raises(SSRFError):
        _check_public_host("http://127.0.0.1/admin")


def test_ssrf_guard_refuses_rfc1918():
    with pytest.raises(SSRFError):
        _check_public_host("http://192.168.1.1/")
    with pytest.raises(SSRFError):
        _check_public_host("http://10.0.0.5/")


def test_ssrf_guard_refuses_cgnat():
    with pytest.raises(SSRFError):
        _check_public_host("http://100.64.0.1/")


def test_ssrf_guard_refuses_ipv6_loopback():
    with pytest.raises(SSRFError):
        _check_public_host("http://[::1]/")


@integration
def test_tier1_fetch_example_com():
    """Real network: tier-1 Fetcher must retrieve example.com."""
    fetcher = TieredFetcher(stealth="off", timeout=25, max_page_kb=512)
    result = asyncio.run(fetcher.fetch("https://example.com"))
    assert result.status == 200
    assert result.tier == 1
    assert "Example Domain" in result.html


@integration
def test_tier1_fetch_second_site():
    """Real network: a second independent site through the same path."""
    fetcher = TieredFetcher(stealth="off", timeout=25, max_page_kb=512)
    result = asyncio.run(fetcher.fetch("https://www.iana.org/domains/example"))
    assert result.status == 200
    assert "iana" in result.html.lower()


@integration
def test_ssrf_guard_allows_public():
    _check_public_host("https://example.com/")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
