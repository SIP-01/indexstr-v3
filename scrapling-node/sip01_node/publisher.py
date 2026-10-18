"""publisher.py — build, sign and publish SIP-01 kind-39697 observations.

Event construction goes through the vendored :mod:`sip01_node.sip01`
toolkit (byte-compatible with the protocol reference). Signing uses the
indexer nsec from ``SIP01_NSEC`` or, when unset, a per-node key generated
once and persisted in the data dir with mode 0600. Publishing fans out via
:class:`~sip01_node.nostr.RelayPool` with per-relay OK-ack accounting and a
sqlite outbox that retries unacknowledged events. An optional kind-16919
heartbeat (indexstr/v3 mode) fires every 10 minutes.

The secret key is never logged and never written anywhere but the key file.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import time
from pathlib import Path

from . import nostr, sip01
from .nostr import PublishAck, RelayPool
from .parser import ParsedPage

log = logging.getLogger(__name__)

HEARTBEAT_KIND = 16919
HEARTBEAT_INTERVAL = 600  # seconds

_OUTBOX_SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox (
    event_id TEXT PRIMARY KEY,
    event_json TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_retry INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);
"""


def load_or_create_key(nsec: str | None, key_path: Path) -> str:
    """Resolve the indexer secret key (hex).

    Priority: ``SIP01_NSEC`` env (nsec1... or hex) > persisted key file >
    freshly generated key persisted with mode 0600.
    """
    if nsec:
        s = nsec.strip()
        if s.startswith("nsec1"):
            return nostr.nsec_decode(s)
        if len(s) == 64 and all(c in "0123456789abcdefABCDEF" for c in s):
            return s.lower()
        raise ValueError("SIP01_NSEC is neither nsec1... nor 64-char hex")
    if key_path.exists():
        if os.stat(key_path).st_mode & 0o077:
            os.chmod(key_path, 0o600)  # tighten perms, never log contents
        return key_path.read_text().strip()
    key_path.parent.mkdir(parents=True, exist_ok=True)
    seckey = nostr.generate_seckey()
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(seckey)
    log.info("generated new indexer key, pubkey=%s (key file: %s)",
             nostr.pubkey_from_seckey(seckey), key_path)
    return seckey


def build_observation(page: ParsedPage, *, source: str, url: str | None = None) -> dict:
    """Build an unsigned SIP-01 event from a parsed page and self-validate it.

    Raises ValueError if the built event fails protocol validation (should
    never happen — build is byte-compatible with the reference).
    """
    ev = sip01.build_event(
        url or page.url,
        page.title,
        description=page.description,
        topics=page.keywords,
        lang=page.lang,
        published=page.published,
        source=source,
        image=page.image,
    )
    result = sip01.validate_event({**ev, "pubkey": "0" * 64, "id": "0" * 64, "sig": "0" * 128})
    if not result["valid"]:
        raise ValueError("built event failed validation: " + "; ".join(result["errors"]))
    return ev


class Publisher:
    """Signs SIP-01 events and publishes them through the relay pool."""

    def __init__(
        self,
        pool: RelayPool,
        seckey_hex: str,
        *,
        outbox_path: Path | None = None,
        heartbeat: bool = False,
        source: str = "crawlstr/v3",
    ) -> None:
        self.pool = pool
        self._seckey = seckey_hex
        self.pubkey = nostr.pubkey_from_seckey(seckey_hex)
        self.source = source
        self.heartbeat = heartbeat
        self._last_heartbeat = 0.0
        self._db: sqlite3.Connection | None = None
        if outbox_path is not None:
            outbox_path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(outbox_path)
            self._db.executescript(_OUTBOX_SCHEMA)

    def sign(self, unsigned: dict) -> dict:
        """Sign an unsigned SIP-01 event with the indexer key."""
        return nostr.sign_event(unsigned, self._seckey)

    async def publish_event(self, signed: dict) -> list[PublishAck]:
        """Publish one signed event; queue into the outbox if no relay acks."""
        acks = await self.pool.publish(signed)
        if not any(a.ok for a in acks):
            self._outbox_add(signed)
        return acks

    async def publish_page(self, page: ParsedPage, *, url: str | None = None) -> tuple[dict, list[PublishAck]]:
        """Build, sign and publish an observation for a parsed page."""
        signed = self.sign(build_observation(page, source=self.source, url=url))
        acks = await self.publish_event(signed)
        return signed, acks

    # -- outbox --------------------------------------------------------------
    def _outbox_add(self, signed: dict) -> None:
        if self._db is None:
            return
        self._db.execute(
            "INSERT OR REPLACE INTO outbox (event_id, event_json, attempts, next_retry, created_at)"
            " VALUES (?, ?, 0, ?, ?)",
            (signed["id"], json.dumps(signed, ensure_ascii=False), int(time.time()), int(time.time())),
        )
        self._db.commit()
        log.warning("event %s queued in outbox (no relay acked)", signed["id"][:12])

    async def flush_outbox(self, *, max_batch: int = 20) -> int:
        """Retry due outbox events. Returns the number still pending."""
        if self._db is None:
            return 0
        now = int(time.time())
        rows = self._db.execute(
            "SELECT event_id, event_json FROM outbox WHERE next_retry <= ? ORDER BY created_at LIMIT ?",
            (now, max_batch),
        ).fetchall()
        for event_id, event_json in rows:
            acks = await self.pool.publish(json.loads(event_json))
            if any(a.ok for a in acks):
                self._db.execute("DELETE FROM outbox WHERE event_id = ?", (event_id,))
                log.info("outbox event %s delivered", event_id[:12])
            else:
                attempts = self._db.execute(
                    "SELECT attempts FROM outbox WHERE event_id = ?", (event_id,)).fetchone()[0]
                backoff = min(3600, 60 * (2 ** min(attempts, 6)))
                self._db.execute(
                    "UPDATE outbox SET attempts = attempts + 1, next_retry = ? WHERE event_id = ?",
                    (now + backoff, event_id),
                )
        self._db.commit()
        return self._db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]

    # -- heartbeat -----------------------------------------------------------
    def heartbeat_due(self) -> bool:
        """True when heartbeat is enabled and the 10-minute interval elapsed."""
        return self.heartbeat and (time.time() - self._last_heartbeat) >= HEARTBEAT_INTERVAL

    async def send_heartbeat(self, stats: dict | None = None) -> None:
        """Publish a kind-16919 node heartbeat (indexstr/v3 mode)."""
        content = {
            "node": "sip01-scrapling-node",
            "source": self.source,
            "ts": int(time.time()),
            "stats": stats or {},
        }
        ev = nostr.sign_event({
            "kind": HEARTBEAT_KIND,
            "created_at": int(time.time()),
            "content": json.dumps(content, separators=(",", ":")),
            "tags": [["d", f"sip01-node:{self.pubkey}"], ["alt", "SIP-01 node heartbeat"]],
        }, self._seckey)
        await self.pool.publish(ev)
        self._last_heartbeat = time.time()

    async def close(self) -> None:
        """Flush the outbox once and close the relay pool."""
        try:
            await asyncio.wait_for(self.flush_outbox(), timeout=20)
        except (asyncio.TimeoutError, Exception) as exc:  # noqa: BLE001 - best effort
            log.info("outbox flush on close: %s", exc)
        await self.pool.close()
        if self._db is not None:
            self._db.close()
