"""fetcher.py — tiered Scrapling fetch engine with SSRF guard and hard timeouts.

Tier ladder (escalate only when blocked):

1. ``Fetcher``        — plain HTTP via curl_cffi with TLS impersonation.
2. ``DynamicFetcher`` — headless Chromium (needs ``scrapling install``).
3. ``StealthyFetcher``— patchright-based stealth (needs ``scrapling install``).

Browser-tier fetches can hang past their own ``timeout`` argument (upstream
issues #468/#491), so every browser fetch is wrapped in an
``asyncio.wait_for`` hard timeout. Tier 1 runs in a thread with the same
hard timeout applied from the async side.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
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


def _check_public_host(url: str) -> None:
    """SSRF guard: refuse URLs whose host resolves to a non-public address.

    Rejects loopback, private (RFC1918), link-local, CGNAT (100.64.0.0/10),
    multicast, reserved and unspecified addresses after DNS resolution.
    """
    host = urlsplit(url).hostname
    if not host:
        raise SSRFError(f"no host in URL: {url!r}")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise FetchError(f"DNS resolution failed for {host}: {exc}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
            or ip in ipaddress.ip_network("100.64.0.0/10")
        ):
            raise SSRFError(f"{host} resolves to non-public address {ip}")


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


class TieredFetcher:
    """Async facade over Scrapling's fetch tiers with escalation on blocks."""

    def __init__(
        self,
        *,
        stealth: str = "auto",
        timeout: float = 30.0,
        max_page_kb: int = 512,
        adaptive_db: str | None = None,
    ) -> None:
        if stealth not in ("off", "auto", "always"):
            raise ValueError("stealth must be 'off', 'auto' or 'always'")
        self.stealth = stealth
        self.timeout = timeout
        self.max_page_kb = max_page_kb
        self.adaptive_db = adaptive_db

    # -- individual tiers (sync, run in a worker thread) ---------------------
    def _tier1(self, url: str):
        from scrapling.fetchers import Fetcher

        return Fetcher.get(url, impersonate="chrome", timeout=int(self.timeout), follow_redirects=True)

    def _tier2(self, url: str):
        try:
            from scrapling.fetchers import DynamicFetcher
        except ImportError as exc:  # pragma: no cover
            raise TierUnavailableError("DynamicFetcher unavailable") from exc
        try:
            return DynamicFetcher.fetch(url, timeout=int(self.timeout * 1000))
        except Exception as exc:
            if "install" in str(exc).lower() or "executable" in str(exc).lower():
                raise TierUnavailableError(
                    "browser backend not installed (run `scrapling install`)") from exc
            raise

    def _tier3(self, url: str):
        try:
            from scrapling.fetchers import StealthyFetcher
        except ImportError as exc:  # pragma: no cover
            raise TierUnavailableError("StealthyFetcher unavailable") from exc
        try:
            kwargs = {"timeout": int(self.timeout * 1000)}
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
        await asyncio.to_thread(_check_public_host, url)

        tiers: list = []
        if self.stealth == "always":
            tiers = [self._tier3, self._tier2, self._tier1]
        elif self.stealth == "auto":
            tiers = [self._tier1, self._tier2, self._tier3]
        else:
            tiers = [self._tier1]

        last_exc: Exception | None = None
        for i, tier_fn in enumerate(tiers, start=1):
            tier_no = {"_tier1": 1, "_tier2": 2, "_tier3": 3}[tier_fn.__name__]
            try:
                resp = await self._run_tier(tier_fn, url)
            except TierUnavailableError as exc:
                log.info("tier %d unavailable for %s: %s", tier_no, url, exc)
                last_exc = exc
                continue
            except (asyncio.TimeoutError, TimeoutError) as exc:
                log.warning("tier %d timed out for %s", tier_no, url)
                last_exc = FetchError(f"tier {tier_no} hard timeout")
                continue
            except Exception as exc:
                log.warning("tier %d failed for %s: %s", tier_no, url, exc)
                last_exc = exc
                continue

            status = _status_of(resp)
            html = _html_of(resp)
            if _looks_blocked(status, html) and i < len(tiers):
                log.info("tier %d blocked for %s (status %s) — escalating", tier_no, url, status)
                continue
            if status >= 400:
                raise FetchError(f"HTTP {status} for {url}")
            _cap_size(html, status, self.max_page_kb)
            return FetchResult(
                url=url, final_url=_final_url_of(resp, url), html=html,
                status=status, tier=tier_no,
            )
        if last_exc is not None and isinstance(last_exc, FetchError):
            raise last_exc
        raise BlockedError(f"all tiers exhausted for {url}")
