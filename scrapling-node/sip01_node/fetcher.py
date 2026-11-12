"""fetcher.py — tiered Scrapling fetch engine with SSRF guard and hard timeouts.

Tier ladder (escalate only when blocked):

1. ``Fetcher``        — plain HTTP via curl_cffi with TLS impersonation.
2. ``DynamicFetcher`` — headless Chromium (needs ``scrapling install``).
3. ``StealthyFetcher``— patchright-based stealth (needs ``scrapling install``).

Browser-tier fetches can hang past their own ``timeout`` argument (upstream
issues #468/#491), so every browser fetch is wrapped in an
``asyncio.wait_for`` hard timeout. Tier 1 runs in a thread with the same
hard timeout applied from the async side.

After every successful response the final URL (post-redirect) is re-checked
against the public-host SSRF guard so a redirect cannot escape the pre-request
check.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

# Markers (case-insensitive) that indicate an anti-bot interstitial rather
# than real content. Used to decide whether to escalate tiers.
_BLOCK_MARKERS = (
    "cf-chl",
    "cf-browser-verification",
    "just a moment...",
    "attention required! | cloudflare",
    "checking your browser",
    "verify you are human",
    "ddos-guard",
    "px-captcha",
)
_BLOCK_STATUSES = {403, 429, 503}


class FetchError(Exception):
    """Base class for fetch failures."""


class SSRFError(FetchError):
    """URL resolves to a private/loopback/reserved address — refused."""


class SizeCapError(FetchError):
    """Response exceeded the configured page-size cap."""


class BlockedError(FetchError):
    """Target presented an anti-bot block after all allowed tiers."""


class TierUnavailableError(FetchError):
    """A browser tier was requested but its backend is not installed."""


@dataclass
class FetchResult:
    """Result of a successful tiered fetch."""

    url: str
    final_url: str
    html: str
    status: int
    tier: int


def _check_public_host(url: str) -> tuple[str, ...]:
    """Resolve a URL and return canonical public IPs for connection pinning.

    All A/AAAA answers must be globally routable. The caller must use the
    returned answers for the actual connection; a preflight DNS check followed
    by a second resolver lookup is vulnerable to DNS rebinding.
    """
    try:
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        host = parts.hostname
        port = parts.port
    except ValueError as exc:
        raise SSRFError(f"invalid URL authority: {exc}") from exc

    if scheme not in ("http", "https"):
        raise SSRFError("only http and https URLs are permitted")
    if not host:
        raise SSRFError("URL has no hostname")
    if parts.username is not None or parts.password is not None:
        raise SSRFError("credentials in target URLs are not permitted")
    if "%" in host:
        raise SSRFError("scoped IP literals are not permitted")

    port = port if port is not None else (443 if scheme == "https" else 80)
    if port == 0:
        raise SSRFError("port 0 is not a valid crawl destination")

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        try:
            ascii_host = host.encode("idna").decode("ascii")
            infos = socket.getaddrinfo(ascii_host, port, type=socket.SOCK_STREAM)
        except (UnicodeError, socket.gaierror, OSError) as exc:
            raise FetchError(f"DNS resolution failed for {host}: {exc}") from exc
        addresses = tuple(dict.fromkeys(
            ipaddress.ip_address(info[4][0].split("%", 1)[0])
            for info in infos
        ))
    else:
        addresses = (literal,)

    if not addresses:
        raise FetchError(f"DNS returned no addresses for {host}")
    cgnat = ipaddress.ip_network("100.64.0.0/10")
    for ip in addresses:
        if not ip.is_global or ip in cgnat:
            raise SSRFError(f"{host} resolves to non-public address {ip}")
    return tuple(str(ip) for ip in addresses)


def _curl_resolve_entry(url: str, addresses: tuple[str, ...]) -> str | None:
    """Build a CURLOPT_RESOLVE entry for a hostname; IP literals need none."""
    parts = urlsplit(url)
    host = parts.hostname
    if not host:
        raise SSRFError("URL has no hostname")
    try:
        ipaddress.ip_address(host)
        return None
    except ValueError:
        ascii_host = host.encode("idna").decode("ascii")
    port = parts.port if parts.port is not None else (443 if parts.scheme.lower() == "https" else 80)
    values = ",".join(
        f"[{address}]" if ipaddress.ip_address(address).version == 6 else address
        for address in addresses
    )
    return f"{ascii_host}:{port}:{values}"


def _looks_blocked(status: int, html: str) -> bool:
    """Heuristic: does this response look like an anti-bot interstitial?"""
    if status in _BLOCK_STATUSES:
        head = html[:4000].lower()
        return any(m in head for m in _BLOCK_MARKERS) or status in (403, 503)
    # a 200 that is actually a challenge page
    head = html[:4000].lower()
    return "just a moment..." in head and "cf-chl" in head


def _cap_size(html: str, status: int, max_kb: int) -> None:
    if len(html.encode("utf-8", errors="replace")) > max_kb * 1024:
        raise SizeCapError(f"response exceeds {max_kb} KiB cap")


def _status_of(resp) -> int:
    return int(getattr(resp, "status", 200) or 200)


def _html_of(resp) -> str:
    body = getattr(resp, "body", None)
    if isinstance(body, (bytes, bytearray)):
        return bytes(body).decode("utf-8", errors="replace")
    if isinstance(body, str) and body:
        return body
    text = getattr(resp, "text", None)
    return text if isinstance(text, str) else str(resp)


def _final_url_of(resp, fallback: str) -> str:
    return str(getattr(resp, "url", None) or fallback)


def _safe_url_for_log(url: str) -> str:
    """Return an origin-only URL without credentials, path, query or fragment."""
    try:
        parts = urlsplit(url)
        host = parts.hostname
        if not parts.scheme or not host:
            return "<invalid-url>"
        display_host = f"[{host}]" if ":" in host else host
        port = parts.port
        authority = f"{display_host}:{port}" if port is not None else display_host
        return f"{parts.scheme.lower()}://{authority}"
    except ValueError:
        return "<invalid-url>"


class TieredFetcher:
    """Async facade over Scrapling's fetch tiers with escalation on blocks."""

    def __init__(
        self,
        *,
        stealth: str = "auto",
        timeout: float = 30.0,
        max_page_kb: int = 512,
        adaptive_db: str | None = None,
        safe_egress_proxy: str | None = None,
    ) -> None:
        if stealth not in ("off", "auto", "always"):
            raise ValueError("stealth must be 'off', 'auto' or 'always'")
        self.stealth = stealth
        self.timeout = timeout
        self.max_page_kb = max_page_kb
        self.adaptive_db = adaptive_db
        # Proxy URLs can contain credentials; never include this value in logs.
        self.safe_egress_proxy = (
            safe_egress_proxy or os.environ.get("SIP01_SAFE_EGRESS_PROXY") or None
        )

    # -- individual tiers (sync, run in a worker thread) ---------------------
    def _tier1(self, url: str):
        """Fetch with DNS-pinned libcurl and manually guarded redirects."""
        from curl_cffi import CurlOpt
        from curl_cffi.requests import Session
        from scrapling.engines.toolbelt.convertor import ResponseFactory
        from urllib.parse import urljoin

        current = url
        max_redirects = 10
        for redirect_count in range(max_redirects + 1):
            addresses = _check_public_host(current)
            resolve_entry = _curl_resolve_entry(current, addresses)
            curl_options = {CurlOpt.RESOLVE: [resolve_entry]} if resolve_entry else {}

            # libcurl never follows a redirect automatically. Resolve, validate
            # and pin each Location before opening the next connection.
            with Session(
                curl_options=curl_options,
                allow_redirects=False,
                max_redirects=0,
                timeout=self.timeout,
                impersonate="chrome",
                trust_env=False,
            ) as session:
                response = session.get(current)

            status = int(response.status_code)
            location = response.headers.get("Location")
            if status in (301, 302, 303, 307, 308) and location:
                if redirect_count >= max_redirects:
                    raise FetchError(f"redirect limit ({max_redirects}) exceeded")
                next_url = urljoin(current, location)
                _check_public_host(next_url)
                current = next_url
                continue

            return ResponseFactory.from_http_request(
                response, {}, meta={"ssrf_dns_pinned": True}
            )

        raise FetchError(f"redirect limit ({max_redirects}) exceeded")

    def _require_safe_browser_proxy(self) -> str:
        if not self.safe_egress_proxy:
            raise TierUnavailableError(
                "browser tiers are disabled: configure SIP01_SAFE_EGRESS_PROXY "
                "to an operator-verified SSRF-enforcing egress proxy"
            )
        try:
            parts = urlsplit(self.safe_egress_proxy)
            if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
                raise ValueError("proxy URL must use http or https")
            if parts.username is not None or parts.password is not None:
                raise ValueError("proxy URL userinfo is not supported")
            if parts.query or parts.fragment or parts.path not in ("", "/"):
                raise ValueError("proxy URL must not contain a path, query or fragment")
            if parts.port == 0:
                raise ValueError("proxy port 0 is not valid")
            if any(ch.isspace() for ch in self.safe_egress_proxy):
                raise ValueError("proxy URL contains whitespace")
        except ValueError as exc:
            raise TierUnavailableError(f"invalid safe egress proxy configuration: {exc}") from exc
        return self.safe_egress_proxy

    def _tier2(self, url: str):
        proxy = self._require_safe_browser_proxy()
        try:
            from scrapling.fetchers import DynamicFetcher
        except ImportError as exc:  # pragma: no cover
            raise TierUnavailableError("DynamicFetcher unavailable") from exc
        try:
            return DynamicFetcher.fetch(url, timeout=int(self.timeout * 1000), proxy=proxy)
        except Exception as exc:
            if "install" in str(exc).lower() or "executable" in str(exc).lower():
                raise TierUnavailableError(
                    "browser backend not installed (run `scrapling install`)") from exc
            raise

    def _tier3(self, url: str):
        proxy = self._require_safe_browser_proxy()
        try:
            from scrapling.fetchers import StealthyFetcher
        except ImportError as exc:  # pragma: no cover
            raise TierUnavailableError("StealthyFetcher unavailable") from exc
        try:
            kwargs = {"timeout": int(self.timeout * 1000), "proxy": proxy}
            if self.adaptive_db:
                kwargs["adaptive"] = False
            return StealthyFetcher.fetch(url, headless=True, **kwargs)
        except Exception as exc:
            if "install" in str(exc).lower() or "executable" in str(exc).lower():
                raise TierUnavailableError(
                    "browser backend not installed (run `scrapling install`)") from exc
            raise

    async def _run_tier(self, tier_fn, url: str):
        """Run one tier in a thread under a hard asyncio.wait_for timeout."""
        # hard timeout: browser tiers are known to ignore their own timeout
        return await asyncio.wait_for(asyncio.to_thread(tier_fn, url), timeout=self.timeout + 5.0)

    async def fetch(self, url: str) -> FetchResult:
        """Fetch ``url`` through the tier ladder. Raises FetchError subclasses."""
        tiers: list = []
        if self.stealth == "always":
            self._require_safe_browser_proxy()
            tiers = [self._tier3, self._tier2, self._tier1]
        elif self.stealth == "auto":
            tiers = [self._tier1]
            if self.safe_egress_proxy:
                self._require_safe_browser_proxy()
                tiers.extend([self._tier2, self._tier3])
        else:
            tiers = [self._tier1]

        last_exc: Exception | None = None
        for i, tier_fn in enumerate(tiers, start=1):
            tier_no = {"_tier1": 1, "_tier2": 2, "_tier3": 3}[tier_fn.__name__]
            try:
                resp = await self._run_tier(tier_fn, url)
            except TierUnavailableError as exc:
                log.info("tier %d unavailable for %s (%s)", tier_no, _safe_url_for_log(url), type(exc).__name__)
                last_exc = exc
                continue
            except (asyncio.TimeoutError, TimeoutError) as exc:
                log.warning("tier %d timed out for %s", tier_no, _safe_url_for_log(url))
                last_exc = FetchError(f"tier {tier_no} hard timeout")
                continue
            except Exception as exc:
                log.warning("tier %d failed for %s (%s)", tier_no, _safe_url_for_log(url), type(exc).__name__)
                last_exc = exc
                continue

            status = _status_of(resp)
            html = _html_of(resp)
            final = _final_url_of(resp, url)
            # Defense in depth; Tier 1 already validates and pins every hop.
            if final != url:
                await asyncio.to_thread(_check_public_host, final)
            if _looks_blocked(status, html) and i < len(tiers):
                log.info("tier %d blocked for %s (status %s) — escalating", tier_no, _safe_url_for_log(url), status)
                continue
            if status >= 400:
                raise FetchError(f"HTTP {status} for {_safe_url_for_log(url)}")
            _cap_size(html, status, self.max_page_kb)
            return FetchResult(
                url=url, final_url=final, html=html,
                status=status, tier=tier_no,
            )
        if last_exc is not None and isinstance(last_exc, FetchError):
            raise last_exc
        raise BlockedError(f"all tiers exhausted for {url}")
