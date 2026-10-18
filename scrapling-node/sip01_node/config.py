"""config.py — TOML configuration + environment overrides.

Config is loaded from a TOML file (stdlib ``tomllib``, Python >= 3.11).
Every option can be overridden with an environment variable named
``SIP01_<OPTION_NAME_UPPER>`` (lists are comma-separated).

Secret material (``SIP01_NSEC``) is read from the environment ONLY and is
never accepted from, or written to, the config file.
"""
from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# Default relay set (mirrors the live v3 nodes; wss:// implied).
DEFAULT_RELAYS: list[str] = [
    # SIP-01 index relays
    "wss://relay-na1.metanomalist.com",
    "wss://test-sip-relay.sip-01test.workers.dev",
    "wss://sip-relay-2.sip-booster-relay.workers.dev",
    "wss://sip-relay-3.uncaged-sip.workers.dev",
    "wss://sip-relay-4.sip-relay-4.workers.dev",
    # Search relays (NIP-50)
    "wss://relay.nostr.band",
    "wss://relay.ditto.pub",
    "wss://search.nos.today",
    "wss://relay.noswhere.com",
    # Write relays
    "wss://jskitty.cat/nostr",
    "wss://relay.primal.net",
    "wss://relay.damus.io",
    "wss://nostr.hifish.org",
]

_STEALTH_MODES = ("off", "auto", "always")


@dataclass
class Config:
    """Runtime configuration for the scrapling SIP-01 node."""

    seeds: list[str] = field(default_factory=list)
    relays: list[str] = field(default_factory=lambda: list(DEFAULT_RELAYS))
    source: str = "crawlstr/v3"
    data_dir: Path = Path("./data")
    max_depth: int = 3
    per_domain_delay: float = 5.0
    max_page_kb: int = 512
    hourly_bandwidth_mb: float = 256.0
    adaptive_store: bool = False
    stealth: str = "auto"
    concurrency: int = 4
    log_level: str = "INFO"
    fetch_timeout: float = 30.0
    publish_timeout: float = 15.0
    rss_discovery: bool = True
    heartbeat: bool = False  # kind 16919 heartbeat (enabled by indexstr/v3 mode)
    intake: bool = False  # reserved: indexstr/v3 intake mode flag

    # populated only from SIP01_NSEC env; never from file
    nsec: str | None = field(default=None, repr=False)

    @property
    def db_path(self) -> Path:
        """Path of the crawl-queue sqlite database."""
        return self.data_dir / "crawl.db"

    @property
    def outbox_path(self) -> Path:
        """Path of the publish-outbox sqlite database."""
        return self.data_dir / "outbox.db"

    @property
    def key_path(self) -> Path:
        """Path of the persisted indexer key file (mode 0600)."""
        return self.data_dir / "indexer.key"

    @property
    def adaptive_db_path(self) -> Path:
        """Path of Scrapling's adaptive (selector relocation) sqlite store."""
        return self.data_dir / "adaptive.db"


def _env_key(name: str) -> str:
    return "SIP01_" + name.upper()


def _coerce_like(current, raw: str):
    """Coerce an env string to the type of the current field value."""
    if isinstance(current, bool):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(current, int):
        return int(raw)
    if isinstance(current, float):
        return float(raw)
    if isinstance(current, list):
        return [s.strip() for s in raw.split(",") if s.strip()]
    if isinstance(current, Path):
        return Path(raw)
    return raw


def load_config(path: str | os.PathLike | None = None) -> Config:
    """Load config from a TOML file, then apply SIP01_* env overrides.

    ``SIP01_NSEC`` is honored from the environment only; if it appears in the
    TOML file it is ignored with a warning.
    """
    cfg = Config()
    file_keys: set[str] = set()
    if path is not None:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
        for key, value in data.items():
            if key == "nsec":
                log.warning("ignoring 'nsec' in config file — use the SIP01_NSEC env var instead")
                continue
            if not hasattr(cfg, key):
                raise ValueError(f"unknown config option: {key!r}")
            current = getattr(cfg, key)
            if isinstance(current, Path):
                value = Path(value)
            setattr(cfg, key, value)
            file_keys.add(key)
    # env overrides win over the file
    for f in (Config.__dataclass_fields__.values() if hasattr(Config, "__dataclass_fields__") else []):
        name = f.name
        if name == "nsec":
            continue
        raw = os.environ.get(_env_key(name))
        if raw is not None:
            setattr(cfg, name, _coerce_like(getattr(cfg, name), raw))
    cfg.nsec = os.environ.get("SIP01_NSEC") or None

    if cfg.stealth not in _STEALTH_MODES:
        raise ValueError(f"stealth must be one of {_STEALTH_MODES}, got {cfg.stealth!r}")
    if cfg.max_depth < 0:
        raise ValueError("max_depth must be >= 0")
    if cfg.concurrency < 1:
        raise ValueError("concurrency must be >= 1")
    if cfg.source == "indexstr/v3":
        cfg.heartbeat = True
        cfg.intake = True
    cfg.data_dir = Path(cfg.data_dir)
    return cfg
