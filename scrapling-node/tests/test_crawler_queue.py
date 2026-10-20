"""test_crawler_queue.py — CrawlQueue unit tests (tmp sqlite) + seed() smoke test.

Regression coverage: crawler.py is not imported by any other test module, which
is how the lost ``CrawlQueue.add()`` method once slipped through review. These
tests exercise the queue contract directly against a throwaway sqlite db.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sip01_node.config import Config  # noqa: E402
from sip01_node.crawler import (  # noqa: E402
    BASE_RECRAWL,
    INFLIGHT_STALE_SEC,
    MAX_ATTEMPTS,
    Crawler,
    CrawlQueue,
)

NOW = int(time.time())


@pytest.fixture()
def queue(tmp_path):
    q = CrawlQueue(tmp_path / "crawl.db")
    yield q
    q.close()


def _row(q: CrawlQueue, url: str):
    return q.db.execute(
        "SELECT url, depth, status, attempts, next_try FROM queue WHERE url = ?",
        (url,),
    ).fetchone()


def test_add_normalizes_and_dedups(queue):
    """add() normalizes per SIP-01 §7 and refuses duplicates (INSERT OR IGNORE)."""
    raw = "HTTPS://WWW.Example.Com:443/page/?b=2&utm_source=x&a=1#top"
    assert queue.add(raw, 0) is True
    normalized = "https://example.com/page?a=1&b=2"
    assert _row(queue, normalized) is not None
    # same URL again (either form) is a duplicate
    assert queue.add(normalized, 0) is False
    assert queue.add(raw, 0) is False
    assert queue.pending_count() == 1


def test_add_rejects_invalid_urls(queue):
    assert queue.add("ftp://example.com/x", 0) is False
    assert queue.add("javascript:alert(1)", 0) is False
    assert queue.add("not-a-url", 0) is False
    assert queue.pending_count() == 0


def test_next_batch_returns_due_rows_only(queue):
    assert queue.add("https://example.com/", 0) is True
    assert queue.add("https://example.com/later", 0) is True
    future = int(time.time()) + 3600
    queue.db.execute(
        "UPDATE queue SET next_try = ? WHERE url = ?",
        (future, "https://example.com/later"),
    )
    queue.db.commit()
    batch = queue.next_batch(10)
    assert [u for u, _ in batch] == ["https://example.com/"]


def test_claim_hides_from_redispatch(queue):
    url = "https://example.com/"
    queue.add(url, 0)
    queue.claim(url)
    assert queue.next_batch(10) == []
    assert _row(queue, url)[2] == "inflight"


def test_release_restores_without_attempt_increment(queue):
    url = "https://example.com/"
    queue.add(url, 0)
    queue.claim(url)
    queue.release(url)
    row = _row(queue, url)
    assert row[2] == "pending"
    assert row[3] == 0  # attempts untouched by release
    assert [u for u, _ in queue.next_batch(10)] == [url]


def test_mark_done_resurrects_pending_with_future_next_try(queue):
    url = "https://example.com/"
    queue.add(url, 0)
    queue.claim(url)
    queue.mark_done(url, "hash-a")
    row = _row(queue, url)
    assert row[2] == "pending"  # resurrected, not terminally done
    assert row[4] > int(time.time())  # next_try in the future
    # freshness filter: recently crawled page must not be redispatched
    assert queue.next_batch(10) == []


def test_mark_done_doubles_interval_on_unchanged_hash(queue):
    url = "https://example.com/"
    queue.add(url, 0)
    queue.mark_done(url, "hash-a")
    interval = queue.db.execute(
        "SELECT recrawl_after FROM pages WHERE url = ?", (url,)).fetchone()[0]
    assert interval == BASE_RECRAWL
    queue.mark_done(url, "hash-a")  # unchanged content
    interval = queue.db.execute(
        "SELECT recrawl_after FROM pages WHERE url = ?", (url,)).fetchone()[0]
    assert interval == BASE_RECRAWL * 2


def test_mark_done_resets_interval_on_changed_hash(queue):
    url = "https://example.com/"
    queue.add(url, 0)
    queue.mark_done(url, "hash-a")
    queue.mark_done(url, "hash-a")  # interval now 2x
    queue.mark_done(url, "hash-b")  # content changed
    interval = queue.db.execute(
        "SELECT recrawl_after FROM pages WHERE url = ?", (url,)).fetchone()[0]
    assert interval == BASE_RECRAWL


def test_mark_failed_backs_off_then_negative_caches(queue):
    url = "https://example.com/"
    queue.add(url, 0)
    for expected_attempts in range(1, MAX_ATTEMPTS):
        queue.mark_failed(url, "boom")
        row = _row(queue, url)
        assert row[2] == "pending"
        assert row[3] == expected_attempts
        assert row[4] > int(time.time())  # backoff: not immediately due
        assert queue.db.execute(
            "SELECT COUNT(*) FROM negative_cache").fetchone()[0] == 0
    # the MAX_ATTEMPTS-th failure lands in the negative cache
    queue.mark_failed(url, "boom")
    assert _row(queue, url)[3] == MAX_ATTEMPTS
    nc = queue.db.execute(
        "SELECT reason, expires FROM negative_cache WHERE url = ?", (url,)).fetchone()
    assert nc is not None and nc[0] == "boom"
    assert nc[1] > int(time.time())
    # excluded from dispatch while the negative cache entry is live
    assert queue.next_batch(10) == []
    assert queue.pending_count() == 1  # still pending: resurrects after TTL


def test_reclaim_stale_inflight(queue):
    url = "https://example.com/"
    queue.add(url, 0)
    queue.claim(url)
    assert queue.reclaim_stale_inflight() == 0  # fresh claim: nothing to reclaim
    stale = int(time.time()) - INFLIGHT_STALE_SEC - 60
    queue.db.execute("UPDATE queue SET added_at = ? WHERE url = ?", (stale, url))
    queue.db.commit()
    assert queue.reclaim_stale_inflight() == 1
    assert _row(queue, url)[2] == "pending"
    assert [u for u, _ in queue.next_batch(10)] == [url]


def test_crawler_seed_end_to_end(tmp_path):
    """Crawler imports and seed()→next_batch() works on a tmp database.

    Guards the seed path that once broke when CrawlQueue.add() went missing.
    """
    cfg = Config(data_dir=tmp_path, per_domain_delay=0.0)
    crawler = Crawler(cfg, None, None, dry_run=True)
    try:
        added = crawler.seed([
            "https://example.com/",
            "https://example.com/page?utm_source=x&b=2&a=1#frag",
            "ftp://not-http.example/",  # rejected
        ])
        assert added == 2
        urls = {u for u, depth in crawler.queue.next_batch(10)}
        assert urls == {
            "https://example.com/",
            "https://example.com/page?a=1&b=2",
        }
        assert all(depth == 0 for _, depth in crawler.queue.next_batch(10))
    finally:
        crawler.queue.close()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
