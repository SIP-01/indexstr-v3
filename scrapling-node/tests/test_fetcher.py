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

def _fake_addr(ip: str):
    import ipaddress
    import socket
    parsed = ipaddress.ip_address(ip)
    if parsed.version == 4:
        return (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))
    return (socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ip, 0, 0, 0))


def test_public_host_returns_dns_addresses_for_pinning(monkeypatch):
    monkeypatch.setattr(
        "sip01_node.fetcher.socket.getaddrinfo",
        lambda host, port, type: [_fake_addr("8.8.8.8"), _fake_addr("1.1.1.1")],
    )
    assert _check_public_host("https://example.com/") == ("8.8.8.8", "1.1.1.1")


def test_public_host_rejects_mixed_public_and_private_dns_answers(monkeypatch):
    monkeypatch.setattr(
        "sip01_node.fetcher.socket.getaddrinfo",
        lambda host, port, type: [_fake_addr("8.8.8.8"), _fake_addr("10.0.0.5")],
    )
    with pytest.raises(SSRFError, match="non-public"):
        _check_public_host("https://rebind.example/")


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/",
        "http://user:pass@example.com/",
        "http://example.com:0/",
    ],
)
def test_public_host_rejects_unsafe_url_authority(url):
    with pytest.raises(SSRFError):
        _check_public_host(url)


def test_curl_resolve_entry_formats_ipv4_and_ipv6():
    from sip01_node.fetcher import _curl_resolve_entry

    assert _curl_resolve_entry("https://example.com/", ("8.8.8.8",)) == (
        "example.com:443:8.8.8.8"
    )
    assert _curl_resolve_entry(
        "https://example.com/", ("2606:4700:4700::1111",)
    ) == "example.com:443:[2606:4700:4700::1111]"
    assert _curl_resolve_entry("https://8.8.8.8/", ("8.8.8.8",)) is None


def test_tier1_pins_dns_and_disables_automatic_redirects(monkeypatch):
    from types import SimpleNamespace
    from curl_cffi import CurlOpt
    from sip01_node.fetcher import TieredFetcher

    monkeypatch.setattr(
        "sip01_node.fetcher.socket.getaddrinfo",
        lambda host, port, type: [_fake_addr("8.8.8.8")],
    )
    captured = {}

    class FakeResponse:
        status_code = 200
        headers = {}
        url = "https://example.com/"
        body = b"ok"
        content = b"ok"
        text = "ok"

    class FakeSession:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, url):
            captured["requested_url"] = url
            return FakeResponse()

    monkeypatch.setattr("curl_cffi.requests.Session", FakeSession)
    response = TieredFetcher(stealth="off")._tier1("https://example.com/")
    assert response.status == 200
    assert captured["allow_redirects"] is False
    assert captured["max_redirects"] == 0
    assert captured["trust_env"] is False
    assert captured["curl_options"][CurlOpt.RESOLVE] == ["example.com:443:8.8.8.8"]


def test_tier1_rejects_private_redirect_before_second_connection(monkeypatch):
    from sip01_node.fetcher import TieredFetcher

    monkeypatch.setattr(
        "sip01_node.fetcher.socket.getaddrinfo",
        lambda host, port, type: [_fake_addr("8.8.8.8")],
    )
    sessions = []

    class FakeResponse:
        status_code = 302
        headers = {"Location": "http://127.0.0.1/admin"}
        url = "https://example.com/"
        body = b""
        text = ""

    class FakeSession:
        def __init__(self, **_kwargs):
            self.requests = []
            sessions.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, url):
            self.requests.append(url)
            return FakeResponse()

    monkeypatch.setattr("curl_cffi.requests.Session", FakeSession)
    with pytest.raises(SSRFError, match="non-public"):
        TieredFetcher(stealth="off")._tier1("https://example.com/")
    assert len(sessions) == 1
    assert sessions[0].requests == ["https://example.com/"]


def test_browser_tiers_fail_closed_without_safe_egress_proxy():
    from sip01_node.fetcher import TierUnavailableError

    fetcher = TieredFetcher()
    with pytest.raises(TierUnavailableError, match="SIP01_SAFE_EGRESS_PROXY"):
        fetcher._tier2("https://example.com/")
    with pytest.raises(TierUnavailableError, match="SIP01_SAFE_EGRESS_PROXY"):
        fetcher._tier3("https://example.com/")


def test_browser_tier_uses_configured_safe_egress_proxy(monkeypatch):
    from types import SimpleNamespace
    from scrapling.fetchers import DynamicFetcher
    from sip01_node.fetcher import TieredFetcher

    captured = {}
    monkeypatch.setattr(
        DynamicFetcher, "fetch",
        lambda url, **kwargs: captured.update({"url": url, **kwargs}) or SimpleNamespace(),
    )
    proxy = "http://127.0.0.1:8899"
    TieredFetcher(safe_egress_proxy=proxy)._tier2("https://example.com/")
    assert captured["proxy"] == proxy
    assert captured["url"] == "https://example.com/"


def test_auto_mode_without_proxy_uses_only_safe_static_tier(monkeypatch):
    from types import SimpleNamespace
    import asyncio
    from sip01_node.fetcher import TieredFetcher

    fetcher = TieredFetcher(stealth="auto")
    calls = []

    async def fake_run_tier(tier_fn, url):
        calls.append(tier_fn.__name__)
        return SimpleNamespace(status=200, body=b"safe", url=url)

    monkeypatch.setattr(fetcher, "_run_tier", fake_run_tier)
    result = asyncio.run(fetcher.fetch("https://8.8.8.8/"))
    assert result.tier == 1
    assert calls == ["_tier1"]


def test_always_mode_without_proxy_fails_before_fetch():
    import asyncio
    from sip01_node.fetcher import TierUnavailableError, TieredFetcher

    with pytest.raises(TierUnavailableError, match="SIP01_SAFE_EGRESS_PROXY"):
        asyncio.run(TieredFetcher(stealth="always").fetch("https://8.8.8.8/"))

def test_safe_url_for_log_redacts_userinfo_path_and_query():
    from sip01_node.fetcher import _safe_url_for_log

    assert _safe_url_for_log(
        "https://alice:supersecret@example.com/private/path?token=secret#frag"
    ) == "https://example.com"


def test_browser_proxy_rejects_credentials_and_query_secrets():
    from sip01_node.fetcher import TierUnavailableError, TieredFetcher

    for proxy in (
        "http://user:password@127.0.0.1:8899",
        "http://127.0.0.1:8899/?token=secret",
    ):
        with pytest.raises(TierUnavailableError) as error:
            TieredFetcher(safe_egress_proxy=proxy)._require_safe_browser_proxy()
        assert "password" not in str(error.value)
        assert "secret" not in str(error.value)

def test_tier1_uses_real_dns_pinned_http_transport_without_scrapling_adapter(monkeypatch):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from sip01_node.fetcher import TieredFetcher

    received = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            received["path"] = self.path
            received["host"] = self.headers.get("Host")
            body = b"<html><title>pinned</title></html>"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_port
    monkeypatch.setattr(
        "sip01_node.fetcher._check_public_host",
        lambda _url: ("127.0.0.1",),
    )
    try:
        response = TieredFetcher(stealth="off", timeout=3)._tier1(
            f"http://example.invalid:{port}/pinned-check"
        )
        assert response.status == 200
        assert response.body == b"<html><title>pinned</title></html>"
        assert response.url == f"http://example.invalid:{port}/pinned-check"
        assert received["path"] == "/pinned-check"
        assert received["host"] == f"example.invalid:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

