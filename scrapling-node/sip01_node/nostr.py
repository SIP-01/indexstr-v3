"""Nostr NIP-01 event construction and async relay pool.

BIP-340 key derivation, Schnorr signing and verification use coincurve's
C-backed libsecp256k1 implementation. Python code in this module never performs
secret-dependent elliptic-curve scalar multiplication.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
from dataclasses import dataclass, field

from coincurve import PrivateKey, PublicKeyXOnly

log = logging.getLogger(__name__)


def schnorr_sign(msg32: bytes, seckey_bytes: bytes, aux: bytes | None = None) -> bytes:
    """BIP340 Schnorr-sign a 32-byte message using libsecp256k1."""
    if len(msg32) != 32:
        raise ValueError("BIP340 message must be 32 bytes")
    if len(seckey_bytes) != 32:
        raise ValueError("BIP340 secret key must be 32 bytes")
    if aux is None:
        aux = secrets.token_bytes(32)
    if len(aux) != 32:
        raise ValueError("BIP340 auxiliary randomness must be 32 bytes")
    return PrivateKey(seckey_bytes).sign_schnorr(msg32, aux_randomness=aux)


def schnorr_verify(msg32: bytes, pubkey32: bytes, sig64: bytes) -> bool:
    """Verify a BIP340 Schnorr signature using libsecp256k1."""
    if len(msg32) != 32 or len(pubkey32) != 32 or len(sig64) != 64:
        return False
    try:
        return PublicKeyXOnly(pubkey32).verify(sig64, msg32)
    except (TypeError, ValueError):
        return False


# ---------------- bech32 (BIP-173, NIP-19) ----------------------------------
# ---------------- bech32 (BIP-173, NIP-19) ----------------------------------
_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def _hrp_expand(hrp: str) -> list[int]:
    return [ord(x) >> 5 for x in hrp] + [0] + [ord(x) & 31 for x in hrp]


def _polymod(vals) -> int:
    chk = 1
    for v in vals:
        top = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ v
        for i, g in enumerate([0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]):
            if (top >> i) & 1:
                chk ^= g
    return chk


def _convertbits(data, frombits: int, tobits: int, pad: bool = True) -> list[int]:
    acc, bits, out = 0, 0, []
    mx = (1 << tobits) - 1
    for v in data:
        acc = (acc << frombits) | v
        bits += frombits
        while bits >= tobits:
            bits -= tobits
            out.append((acc >> bits) & mx)
    if pad and bits:
        out.append((acc << (tobits - bits)) & mx)
    elif bits >= frombits or ((acc << (tobits - bits)) & mx):
        raise ValueError("invalid padding")
    return out


def bech32_encode(hrp: str, data_bytes: bytes) -> str:
    """Bech32-encode bytes under a human-readable prefix (NIP-19)."""
    data = _convertbits(data_bytes, 8, 5)
    pm = _polymod(_hrp_expand(hrp) + data + [0] * 6) ^ 1
    return hrp + "1" + "".join(_CHARSET[d] for d in data + [(pm >> 5 * (5 - i)) & 31 for i in range(6)])


def bech32_decode(s: str) -> tuple[str, bytes]:
    """Decode a bech32 string into (hrp, payload bytes)."""
    if any(ord(c) < 33 or ord(c) > 126 for c in s):
        raise ValueError("invalid character")
    if s.lower() != s and s.upper() != s:
        raise ValueError("mixed case")
    s = s.lower()
    pos = s.rfind("1")
    if pos < 1 or pos + 7 > len(s):
        raise ValueError("bad separator")
    hrp = s[:pos]
    data = [_CHARSET.index(c) for c in s[pos + 1:]]
    if _polymod(_hrp_expand(hrp) + data) != 1:
        raise ValueError("bad checksum")
    return hrp, bytes(_convertbits(data[:-6], 5, 8, False))


def nsec_encode(seckey_hex: str) -> str:
    """Encode a 32-byte secret key (hex) as nsec1..."""
    return bech32_encode("nsec", bytes.fromhex(seckey_hex))


def npub_encode(pubkey_hex: str) -> str:
    """Encode a 32-byte public key (hex) as npub1..."""
    return bech32_encode("npub", bytes.fromhex(pubkey_hex))


def nsec_decode(s: str) -> str:
    """Decode nsec1... into a hex secret key. Raises ValueError otherwise."""
    hrp, raw = bech32_decode(s.strip())
    if hrp != "nsec" or len(raw) != 32:
        raise ValueError("not an nsec key")
    return raw.hex()


# ---------------- NIP-01 keys & events --------------------------------------
def generate_seckey() -> str:
    """Generate a valid random secret key (hex) with libsecp256k1 validation."""
    while True:
        sk = secrets.token_bytes(32)
        try:
            PrivateKey(sk)
        except ValueError:
            continue
        return sk.hex()


def pubkey_from_seckey(seckey_hex: str) -> str:
    """Derive the x-only BIP340 public key (hex) from a secret key (hex)."""
    try:
        seckey = bytes.fromhex(seckey_hex)
    except (TypeError, ValueError) as exc:
        raise ValueError("secret key must be 32-byte hex") from exc
    if len(seckey) != 32:
        raise ValueError("secret key must be 32 bytes")
    return PrivateKey(seckey).public_key_xonly.format().hex()


def event_id(ev: dict) -> str:
    """Compute the NIP-01 event id (sha256 of the serialized commitment)."""
    serialized = json.dumps(
        [0, ev["pubkey"], ev["created_at"], ev["kind"], ev["tags"], ev["content"]],
        separators=(",", ":"), ensure_ascii=False,
    ).encode()
    return hashlib.sha256(serialized).hexdigest()


def sign_event(ev: dict, seckey_hex: str) -> dict:
    """Fill in pubkey, id and sig. `ev` needs kind, created_at, tags, content."""
    ev = dict(ev)
    ev["pubkey"] = pubkey_from_seckey(seckey_hex)
    ev["id"] = event_id(ev)
    ev["sig"] = schnorr_sign(bytes.fromhex(ev["id"]), bytes.fromhex(seckey_hex)).hex()
    return ev


def verify_event(ev: dict) -> bool:
    """Verify the NIP-01 event commitment and BIP340 signature."""
    try:
        if event_id(ev) != ev.get("id"):
            return False
        return schnorr_verify(
            bytes.fromhex(ev["id"]),
            bytes.fromhex(ev["pubkey"]),
            bytes.fromhex(ev["sig"]),
        )
    except (KeyError, TypeError, ValueError):
        return False



# ---------------- async relay pool ------------------------------------------
@dataclass
class PublishAck:
    """Outcome of publishing one event to one relay."""

    relay: str
    event_id: str
    ok: bool
    message: str = ""
    timed_out: bool = False


@dataclass
class _RelayState:
    url: str
    failures: int = 0
    backoff_until: float = 0.0
    ws: object | None = field(default=None, repr=False)


class RelayPool:
    """Async Nostr relay pool: connect, publish with OK-ack tracking, backoff.

    Connections are opened lazily per publish and kept alive for reuse.
    A relay that fails is backed off exponentially (2s, 4s, ... capped at
    ``max_backoff``) so a dead relay never stalls the fan-out.

    Concurrent publishes to the same relay are serialized with a per-relay
    lock so EVENT/OK messages cannot interleave.
    """

    def __init__(
        self,
        relays: list[str],
        *,
        open_timeout: float = 10.0,
        ack_timeout: float = 15.0,
        max_backoff: float = 300.0,
    ) -> None:
        self.relays = list(relays)
        self.open_timeout = open_timeout
        self.ack_timeout = ack_timeout
        self.max_backoff = max_backoff
        self._states: dict[str, _RelayState] = {r: _RelayState(r) for r in self.relays}
        self._locks: dict[str, asyncio.Lock] = {r: asyncio.Lock() for r in self.relays}

    async def _connect(self, st: _RelayState) -> object:
        import websockets  # deferred: keeps the module importable without the dep

        loop = asyncio.get_running_loop()
        if st.backoff_until > loop.time():
            raise ConnectionError(f"relay {st.url} in backoff")
        if st.ws is not None:
            return st.ws
        st.ws = await asyncio.wait_for(websockets.connect(st.url), timeout=self.open_timeout)
        st.failures = 0
        return st.ws

    def _note_failure(self, st: _RelayState) -> None:
        st.failures += 1
        delay = min(self.max_backoff, 2.0 ** st.failures)
        st.backoff_until = asyncio.get_running_loop().time() + delay
        if st.ws is not None:
            try:
                asyncio.ensure_future(st.ws.close())
            except Exception:  # pragma: no cover - best effort
                pass
            st.ws = None

    async def _publish_one(self, st: _RelayState, ev: dict) -> PublishAck:
        eid = ev.get("id", "")
        lock = self._locks[st.url]
        async with lock:
            try:
                ws = await self._connect(st)
                await ws.send(json.dumps(["EVENT", ev], ensure_ascii=False))
                while True:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=self.ack_timeout))
                    kind = msg[0] if msg else ""
                    if kind == "OK" and len(msg) >= 3 and msg[1] == eid:
                        if msg[2]:
                            return PublishAck(st.url, eid, True, msg[3] if len(msg) > 3 else "")
                        return PublishAck(st.url, eid, False, msg[3] if len(msg) > 3 else "rejected")
                    if kind == "NOTICE":
                        log.info("NOTICE from %s: %s", st.url, msg[1] if len(msg) > 1 else "")
                    # keep reading until our OK (or timeout)
            except asyncio.TimeoutError:
                self._note_failure(st)
                return PublishAck(st.url, eid, False, "ack timeout", timed_out=True)
            except Exception as exc:
                self._note_failure(st)
                return PublishAck(st.url, eid, False, f"{type(exc).__name__}: {exc}")

    async def publish(self, ev: dict) -> list[PublishAck]:
        """Fan an event out to every relay; returns one ack per relay."""
        states = list(self._states.values())
        acks = await asyncio.gather(*(self._publish_one(st, ev) for st in states))
        for ack in acks:
            if ack.ok:
                log.info("published %s -> %s OK", ack.event_id[:12], ack.relay)
            else:
                log.warning("publish %s -> %s FAILED (%s)", ack.event_id[:12], ack.relay, ack.message)
        return list(acks)

    async def query(self, relay: str, filt: dict, *, timeout: float = 15.0) -> list[dict]:
        """One-shot query against a single relay (used for verification/tests)."""
        import websockets

        events: list[dict] = []
        ws = await asyncio.wait_for(websockets.connect(relay), timeout=self.open_timeout)
        try:
            await ws.send(json.dumps(["REQ", "q", filt]))
            while True:
                try:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout))
                except asyncio.TimeoutError:
                    break
                if msg[0] == "EVENT" and msg[1] == "q":
                    events.append(msg[2])
                elif msg[0] == "EOSE":
                    break
                elif msg[0] == "CLOSED":
                    break
        finally:
            await ws.close()
        return events

    async def close(self) -> None:
        """Close all open relay connections."""
        for st in self._states.values():
            if st.ws is not None:
                try:
                    await st.ws.close()
                except Exception:  # pragma: no cover - best effort
                    pass
                st.ws = None
