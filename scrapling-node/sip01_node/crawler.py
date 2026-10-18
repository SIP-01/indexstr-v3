"""crawler.py — async crawl loop with sqlite queue, politeness and freshness.

- sqlite-backed queue (WAL mode): URL rows carry depth, status, retry time.
- Per-domain politeness: one request in flight per domain, ``per_domain_delay``
  pacing, and robots.txt ``crawl-delay`` wins when larger.
- robots.txt via stdlib ``urllib.robotparser``, fetched with the tier-1
  Scrapling Fetcher (in a worker thread), cached per domain. robots.txt URL
  is run through the public-host SSRF guard before the fetch.
- Freshness: recrawl after 24h, interval doubling per unchanged crawl,
  capped at 30 days. Failures go to a negative cache with backoff.
  Successful and terminal-failed URLs are left in 'pending' with next_try
  set so the existing next_batch filters actually resurface them.
- Same-domain link discovery up to ``max_depth``; optional RSS/sitemap
  discovery from the page head.
- Bandwidth budget: rolling hourly byte counter, crawling pauses when the
  configured ``hourly_bandwidth_mb`` is exhausted (claimed rows released).
- Graceful shutdown: SIGINT/SIGTERM stop the loop after in-flight work.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
import urllib.robotparser
from dataclasses import dataclass
from urllib.parse import urlsplit

from . import sip01
from .config import Config
from .fetcher import FetchError, SSRFError, TieredFetcher, _check_public_host
from .parser import ParsedPage, parse_page
from .publisher import Publisher

log = logging.getLogger(__name__)

_SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS queue (
    url TEXT PRIMARY KEY,
    depth INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending|inflight|done|failed
    attempts INTEGER NOT NULL DEFAULT 0,
    next_try INTEGER NOT NULL DEFAULT 0,
    added_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS pages (
    url TEXT PRIMARY KEY,
    last_crawled INTEGER NOT NULL,
    recrawl_after INTEGER NOT NULL,          -- seconds; doubles to 30d
    last_hash TEXT
);
CREATE TABLE IF NOT EXISTS negative_cache (
    url TEXT PRIMARY KEY,
    reason TEXT,
    expires INTEGER NOT NULL
);
"""

BASE_RECRAWL = 24 * 3600          # 24h
MAX_RECRAWL = 30 * 24 * 3600      # 30d
NEGATIVE_TTL = 6 * 3600           # 6h for fetch failures
MAX_ATTEMPTS = 4
ROBOTS_TTL = 24 * 3600
INFLIGHT_STALE_SEC = 600          # reclaim stuck inflight after 10 min


@dataclass
class CrawlStats:
    """Running counters for one crawl session."""

    fetched: int = 0
    parsed: int = 0
    published: int = 0
    skipped_thin: int = 0
    skipped_robots: int = 0
    failed: int = 0
    bytes: int = 0

    def as_dict(self) -> dict:
        return self.__dict__.copy()


class CrawlQueue:
    """sqlite-backed URL queue with freshness and negative caching."""

    def __init__(self, path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), timeout=30)
        self.db.executescript(_SCHEMA)
        # recover rows left in-flight by an unclean shutdown
        self.db.execute("UPDATE queue SET status = 'pending' WHERE status = 'inflight'")
        self.db.commit()

    def reclaim_stale_inflight(self) -> int:
        """Return long-running inflight claims to pending. Returns count reclaimed."""
        now = int(time.time())
        cur = self.db.execute(
            "UPDATE queue SET status = 'pending', next_try = ? "
            "WHERE status = 'inflight' AND added_at < ?",
            (now, now - INFLIGHT_STALE_SEC),
        )
        self.db.commit()
        return cur.rowcount

    def next_batch(self, limit: int) -> list[tuple[str, int]]:
        """Due pending URLs that are not negatively cached or fresh."""
        self.reclaim_stale_inflight()
        now = int(time.time())
        rows = self.db.execute(
            "SELECT q.url, q.depth FROM queue q"
            " WHERE q.status = 'pending' AND q.next_try <= ?"
            " AND NOT EXISTS (SELECT 1 FROM negative_cache n"
            "                WHERE n.url = q.url AND n.expires > ?)"
            " AND NOT EXISTS (SELECT 1 FROM pages p"
            "                WHERE p.url = q.url AND p.last_crawled + p.recrawl_after > ?)"
            " ORDER BY q.added_at LIMIT ?",
            (now, now, now, limit),
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def claim(self, url: str) -> None:
        """Mark a dispatched URL in-flight so it is not dispatched twice."""
        self.db.execute("UPDATE queue SET status = 'inflight' WHERE url = ?", (url,))
        self.db.commit()

    def release(self, url: str) -> None:
        """Return a claimed URL to pending without counting an attempt (bandwidth/stop)."""
        now = int(time.time())
        self.db.execute(
            "UPDATE queue SET status = 'pending', next_try = ? WHERE url = ? AND status = 'inflight'",
            (now, url),
        )
        self.db.commit()

    def mark_done(self, url: str, content_hash: str | None) -> None:
        """Record a successful crawl; leave pending with next_try so recrawl fires."""
        now = int(time.time())
        row = self.db.execute(
            "SELECT recrawl_after, last_hash FROM pages WHERE url = ?", (url,)).fetchone()
        if row is None:
            interval = BASE_RECRAWL
        elif content_hash is not None and row[1] == content_hash:
            interval = min(MAX_RECRAWL, row[0] * 2)
        else:
            interval = BASE_RECRAWL
        self.db.execute(
            "INSERT OR REPLACE INTO pages (url, last_crawled, recrawl_after, last_hash)"
            " VALUES (?, ?, ?, ?)",
            (url, now, interval, content_hash),
        )
        # resurrection: stay pending so next_batch sees it after the interval
        self.db.execute(
            "UPDATE queue SET status = 'pending', next_try = ? WHERE url = ?",
            (now + interval, url),
        )
        self.db.commit()

    def mark_failed(self, url: str, reason: str) -> None:
        """Record a failure: retry with backoff, then negative-cache + pending resurrection."""
        now = int(time.time())
        row = self.db.execute("SELECT attempts FROM queue WHERE url = ?", (url,)).fetchone()
        attempts = (row[0] if row else 0) + 1
        if attempts >= MAX_ATTEMPTS:
            self.db.execute(
                "INSERT OR REPLACE INTO negative_cache (url, reason, expires) VALUES (?, ?, ?)",
                (url, reason[:200], now + NEGATIVE_TTL),
            )
            # leave pending; next_batch excludes while negative_cache is active
            self.db.execute(
                "UPDATE queue SET status = 'pending', attempts = ?, next_try = ? WHERE url = ?",
                (attempts, now + NEGATIVE_TTL, url),
            )
        else:
            backoff = 60 * (2 ** attempts)
            self.db.execute(
                "UPDATE queue SET status = 'pending', attempts = ?, next_try = ? WHERE url = ?",
                (attempts, now + backoff, url),
            )
        self.db.commit()

    def pending_count(self) -> int:
        """Number of URLs still in pending state (any retry time)."""
        return self.db.execute(
            "SELECT COUNT(*) FROM queue WHERE status = 'pending'").fetchone()[0]

    def close(self) -> None:
        self.db.close()


class _RobotsCache:
    """Per-domain robots.txt cache using urllib.robotparser."""

    def __init__(self, fetcher: TieredFetcher) -> None:
        self._fetcher = fetcher
        self._cache: dict[str, tuple[float, urllib.robotparser.RobotFileParser | None]] = {}

    async def _load(self, domain: str) -> urllib.robotparser.RobotFileParser | None:
        from scrapling.fetchers import Fetcher

        rp = urllib.robotparser.RobotFileParser()
        url = f"https://{domain}/robots.txt"
        # SSRF guard before any fetch of robots.txt
        try:
            await asyncio.to_thread(_check_public_host, url)
        except SSRFError as exc:
            log.info("robots.txt SSRF refused for %s: %s — allowing", domain, exc)
            return None
        try:
            resp = await asyncio.wait_for(
                asyncio.to_thread(Fetcher.get, url, impersonate="chrome", timeout=15),
                timeout=20,
            )
            status = int(getattr(resp, "status", 200) or 200)
            if status >= 400:
                return None  # no robots -> everything allowed
            body = getattr(resp, "body", b"")
            text = body.decode("utf-8", errors="replace") if isinstance(body, (bytes, bytearray)) else str(body)
            rp.parse(text.splitlines())
            return rp
        except Exception as exc:
            log.info("robots.txt fetch failed for %s: %s — allowing", domain, exc)
            return None

    async def check(self, url: str, user_agent: str = "*") -> tuple[bool, float | None]:
        """(allowed, crawl_delay) for ``url`` per its domain's robots.txt."""
        domain = urlsplit(url).hostname or ""
        now = time.time()
        cached = self._cache.get(domain)
        if cached is None or now - cached[0] > ROBOTS_TTL:
            rp = await self._load(domain)
            cached = (now, rp)
            self._cache[domain] = cached
        rp = cached[1]
        if rp is None:
            return True, None
        return rp.can_fetch(user_agent, url), rp.crawl_delay(user_agent)


class _Bandwidth:
    """Rolling hourly bandwidth budget."""

    def __init__(self, hourly_mb: float) -> None:
        self.limit = int(hourly_mb * 1024 * 1024)
        self._window_start = time.time()
        self._used = 0

    def consume(self, n: int) -> None:
        self._roll()
        self._used += n

    def remaining(self) -> int:
        self._roll()
        return max(0, self.limit - self._used)

    def _roll(self) -> None:
        if time.time() - self._window_start >= 3600:
            self._window_start = time.time()
            self._used = 0


class Crawler:
    """Async crawl engine tying queue, fetcher, parser and publisher together."""

    def __init__(
        self,
        config: Config,
        fetcher: TieredFetcher,
        publisher: Publisher | None = None,
        *,
        dry_run: bool = False,
    ) -> None:
        self.config = config
        self.fetcher = fetcher
        self.publisher = publisher
        self.dry_run = dry_run
        self.queue = CrawlQueue(config.db_path)
        self.robots = _RobotsCache(fetcher)
        self.bandwidth = _Bandwidth(config.hourly_bandwidth_mb)
        self.stats = CrawlStats()
        self._stop = asyncio.Event()
        self._domain_locks: dict[str, asyncio.Lock] = {}
        self._domain_last: dict[str, float] = {}
        self._last_outbox_flush = 0.0

    # -- seeding ---------------------------------------------------------------
    def seed(self, urls: list[str]) -> int:
        """Enqueue seed URLs at depth 0. Returns number accepted."""
        return sum(1 for u in urls if self.queue.add(u, 0))

    def stop(self) -> None:
        """Request graceful shutdown (finishes in-flight fetches)."""
        self._stop.set()

    # -- politeness --------------------------------------------------------------
    async def _politeness(self, url: str, crawl_delay: float | None) -> None:
        """Per-domain: one in flight + pacing (config delay or robots crawl-delay)."""
        domain = urlsplit(url).hostname or ""
        lock = self._domain_locks.setdefault(domain, asyncio.Lock())
        await lock.acquire()
        delay = max(self.config.per_domain_delay, crawl_delay or 0)
        last = self._domain_last.get(domain, 0.0)
        wait = last + delay - time.time()
        if wait > 0:
            log.debug("politeness: waiting %.1fs for %s", wait, domain)
            await asyncio.sleep(wait)
        self._domain_locks[domain] = lock  # released by caller via _release_domain

    def _release_domain(self, url: str) -> None:
        domain = urlsplit(url).hostname or ""
        self._domain_last[domain] = time.time()
        lock = self._domain_locks.get(domain)
        if lock is not None and lock.locked():
            lock.release()

    # -- discovery ------------------------------------------------------------
    def _discover(self, page: ParsedPage, page_url: str, depth: int) -> None:
        """Same-domain link discovery up to max_depth (+ RSS/sitemap heads)."""
        if depth >= self.config.max_depth:
            return
        page_host = urlsplit(page_url).hostname
        added = 0
        for link in page.links:
            if urlsplit(link).hostname == page_host and self.queue.add(link, depth + 1):
                added += 1
        if added:
            log.debug("discovered %d new links from %s", added, page_url)
        if self.config.rss_discovery:
            for link in page.links:
                low = link.lower()
                if (low.endswith((".xml", ".rss", ".atom")) or "sitemap" in low) \
                        and urlsplit(link).hostname == page_host:
                    self.queue.add(link, min(depth + 1, self.config.max_depth))

    # -- one page ---------------------------------------------------------------
    async def crawl_one(self, url: str, depth: int) -> dict | None:
        """Fetch → parse → (build/sign/publish or dry-run print) one URL.

        Returns the signed (or unsigned in dry-run) event dict, or None.
        """
        allowed, crawl_delay = await self.robots.check(url)
        if not allowed:
            log.info("robots.txt disallows %s", url)
            self.stats.skipped_robots += 1
            self.queue.mark_done(url, None)
            return None
        if self.bandwidth.remaining() <= 0:
            log.warning("hourly bandwidth budget exhausted — releasing %s", url)
            self.queue.release(url)
            await asyncio.sleep(60)
            return None

        await self._politeness(url, crawl_delay)
        try:
            result = await self.fetcher.fetch(url)
        except SSRFError as exc:
            log.warning("SSRF guard refused %s: %s", url, exc)
            self.queue.mark_failed(url, "ssrf-refused")
            self.stats.failed += 1
            return None
        except FetchError as exc:
            log.warning("fetch failed for %s: %s", url, exc)
            self.queue.mark_failed(url, str(exc)[:200])
            self.stats.failed += 1
            return None
        finally:
            self._release_domain(url)

        self.stats.fetched += 1
        size = len(result.html.encode("utf-8", errors="replace"))
        self.bandwidth.consume(size)
        self.stats.bytes += size

        page = parse_page(result.html, result.final_url)
        if page is None:
            self.stats.skipped_thin += 1
            self.queue.mark_done(url, None)
            return None
        self.stats.parsed += 1
        self._discover(page, result.final_url, depth)

        if self.dry_run:
            from .publisher import build_observation

            ev = build_observation(page, source=self.config.source)
            print("DRY-RUN event for", result.final_url)
            import json as _json

            print(_json.dumps(ev, ensure_ascii=False, indent=2))
            self.queue.mark_done(url, sip01.content_hash(page.title, page.description))
            return ev

        if self.publisher is None:
            self.queue.mark_done(url, None)
            return None

        signed, acks = await self.publisher.publish_page(page)
        ok = sum(1 for a in acks if a.ok)
        log.info("published %s (%d/%d relays acked)", result.final_url, ok, len(acks))
        self.stats.published += 1 if ok else 0
        self.queue.mark_done(url, sip01.content_hash(page.title, page.description))
        return signed

    # -- main loop ---------------------------------------------------------------
    async def run(self, *, once: bool = False) -> CrawlStats:
        """Run the crawl loop until the queue drains (once) or stopped."""
        sem = asyncio.Semaphore(self.config.concurrency)
        in_flight: set[asyncio.Task] = set()

        async def worker(url: str, depth: int) -> None:
            async with sem:
                if self._stop.is_set():
                    self.queue.release(url)
                    return
                try:
                    await self.crawl_one(url, depth)
                except Exception:  # noqa: BLE001 - keep the loop alive
                    log.exception("unexpected error crawling %s", url)
                    self.queue.mark_failed(url, "internal-error")
                    self.stats.failed += 1

        try:
            while not self._stop.is_set():
                batch = self.queue.next_batch(self.config.concurrency * 2)
                if not batch:
                    if in_flight:
                        await asyncio.wait(set(in_flight))
                    in_flight.clear()
                    if once or self.queue.pending_count() == 0:
                        break
                    await asyncio.sleep(5)
                    continue
                for url, depth in batch:
                    if self._stop.is_set():
                        break
                    self.queue.claim(url)
                    task = asyncio.ensure_future(worker(url, depth))
                    in_flight.add(task)
                    task.add_done_callback(in_flight.discard)
                # periodic outbox flush + heartbeat
                if self.publisher is not None:
                    if time.time() - self._last_outbox_flush > 120:
                        self._last_outbox_flush = time.time()
                        await self.publisher.flush_outbox()
                    if self.publisher.heartbeat_due():
                        await self.publisher.send_heartbeat(self.stats.as_dict())
                await asyncio.sleep(0.5)
            if in_flight:
                await asyncio.wait(set(in_flight))
        finally:
            self.queue.close()
        return self.stats
