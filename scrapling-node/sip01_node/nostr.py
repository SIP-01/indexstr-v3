"""nostr.py — Nostr signing (NIP-01/BIP340, vendored, zero-dep) + async relay pool.

The key/signing half is vendored from the `nostr` skill's nostr_key.py
(pure stdlib secp256k1 BIP340 Schnorr). The relay half is an async
:class:`RelayPool` built on ``websockets`` with per-relay OK-ack tracking,
reconnect with exponential backoff, and a bounded in-flight publish window.

Nothing in this module ever logs secret key material.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# ---------------- secp256k1 field/curve (vendored from nostr_key.py) --------
P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
G = (
    0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
    0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8,
)
INF = None


def _inv(x: int, m: int = P) -> int:
    return pow(x, m - 2, m)


def _add(p, q):
    if p is INF:
        return q
    if q is INF:
        return p
    if p[0] == q[0]:
        if (p[1] + q[1]) % P == 0:
            return INF
        lam = (3 * p[0] * p[0]) * _inv(2 * p[1]) % P
    else:
        lam = (q[1] - p[1]) * _inv(q[0] - p[0]) % P
    x = (lam * lam - p[0] - q[0]) % P
    return (x, (lam * (p[0] - x) - p[1]) % P)


def _mul(k: int, pt=G):
    r = INF
    while k:
        if k & 1:
            r = _add(r, pt)
        pt = _add(pt, pt)
        k >>= 1
    return r


def _lift_x(x: int):
    y = pow((x * x * x + 7) % P, (P + 1) // 4, P)
    if y & 1:
        y = P - y
    return (x, y)


def _i2b(i: int) -> bytes:
    return i.to_bytes(32, "big")


def _b2i(b: bytes) -> int:
    return int.from_bytes(b, "big")


def _tagged(tag: str, *msgs: bytes) -> bytes:
    th = hashlib.sha256(tag.encode()).digest()
    h = hashlib.sha256(th + th)
    for m in msgs:
        h.update(m)
    return h.digest()


# ---------------- BIP340 Schnorr (vendored) --------------------------------
def schnorr_sign(msg32: bytes, seckey_bytes: bytes, aux: bytes | None = None) -> bytes:
    """BIP340 Schnorr-sign a 32-byte message. Returns a 64-byte signature."""
    d0 = _b2i(seckey_bytes)
    if not 1 <= d0 < N:
        raise ValueError("invalid secret key")
    pub = _mul(d0)
    d = N - d0 if pub[1] & 1 else d0
    aux = aux if aux is not None else secrets.token_bytes(32)
    t = bytes(a ^ b for a, b in zip(_i2b(d), _tagged("BIP0340/aux", aux)))
    k0 = _b2i(_tagged("BIP0340/nonce", t, _i2b(pub[0]), msg32)) % N
    if k0 == 0:
        raise RuntimeError("nonce is zero (astronomically unlikely)")
    R = _mul(k0)
    k = N - k0 if R[1] & 1 else k0
    e = _b2i(_tagged("BIP0340/challenge", _i2b(R[0]), _i2b(pub[0]), msg32)) % N
    return _i2b(R[0]) + _i2b((k + e * d) % N)


def schnorr_verify(msg32: bytes, pubkey32: bytes, sig64: bytes) -> bool:
    """Verify a BIP340 Schnorr signature."""
    if len(sig64) != 64 or len(pubkey32) != 32:
        return False
    px = _b2i(pubkey32)
    if px >= P:
        return False
    r, s = _b2i(sig64[:32]), _b2i(sig64[32:])
    if r >= P or s >= N:
        return False
    e = _b2i(_tagged("BIP0340/challenge", sig64[:32], pubkey32, msg32)) % N
    R = _add(_mul(s), _mul(N - e, _lift_x(px)))
    return R is not INF and not (R[1] & 1) and R[0] == r


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
    """Generate a new secret key (hex)."""
    while True:
        sk = secrets.token_bytes(32)
        if 1 <= _b2i(sk) < N:
            return sk.hex()


def pubkey_from_seckey(seckey_hex: str) -> str:
    """Derive the x-only BIP340 public key (hex) from a secret key (hex)."""
    return _i2b(_mul(_b2i(bytes.fromhex(seckey_hex)))[0]).hex()


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
    """Verify id commitment and BIP340 signature of a signed event."""
    if event_id(ev) != ev.get("id"):
        return False
    return schnorr_verify(bytes.fromhex(ev["id"]), bytes.fromhex(ev["pubkey"]), bytes.fromhex(ev["sig"]))


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
