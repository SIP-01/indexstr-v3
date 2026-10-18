"""__main__.py — CLI entry point for the scrapling SIP-01 node.

Usage:
    python -m sip01_node [--config config.toml] [--source crawlstr/v3]
        [--seed URL ...] [--once] [--dry-run] [--selftest]

--selftest runs the vendored protocol test vectors and exits.
--once     crawls until the queue drains, then exits.
--dry-run  builds + validates events and prints them; nothing is published.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys

from .config import Config, load_config


def _build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="sip01_node",
        description="Headless SIP-01 web index observation crawler (Scrapling engine).",
    )
    ap.add_argument("--config", help="path to TOML config file")
    ap.add_argument("--source", help="source tag (e.g. crawlstr/v3, indexstr/v3, sip01-node/test)")
    ap.add_argument("--seed", action="append", default=[], metavar="URL",
                    help="seed URL (repeatable)")
    ap.add_argument("--once", action="store_true", help="exit when the queue drains")
    ap.add_argument("--dry-run", action="store_true",
                    help="build and validate events without publishing")
    ap.add_argument("--selftest", action="store_true",
                    help="run SIP-01 protocol test vectors and exit")
    return ap


def _configure_logging(cfg: Config) -> None:
    logging.basicConfig(
        level=getattr(logging, cfg.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = _build_arg_parser().parse_args(argv)

    if args.selftest:
        from . import sip01

        return 0 if sip01.selftest() else 1

    cfg = load_config(args.config)
    if args.source:
        cfg.source = args.source
    if args.seed:
        cfg.seeds = list(dict.fromkeys([*cfg.seeds, *args.seed]))
    _configure_logging(cfg)
    log = logging.getLogger("sip01_node")

    from .crawler import Crawler
    from .fetcher import TieredFetcher
    from .nostr import RelayPool
    from .publisher import Publisher, load_or_create_key

    fetcher = TieredFetcher(
        stealth=cfg.stealth,
        timeout=cfg.fetch_timeout,
        max_page_kb=cfg.max_page_kb,
        adaptive_db=str(cfg.adaptive_db_path) if cfg.adaptive_store else None,
    )

    publisher: Publisher | None = None
    if not args.dry_run:
        seckey = load_or_create_key(cfg.nsec, cfg.key_path)
        pool = RelayPool(cfg.relays, ack_timeout=cfg.publish_timeout)
        publisher = Publisher(
            pool, seckey,
            outbox_path=cfg.outbox_path,
            heartbeat=cfg.heartbeat,
            source=cfg.source,
        )
        log.info("indexer pubkey: %s", publisher.pubkey)
    else:
        log.info("dry-run mode: events are built and validated, not published")

    crawler = Crawler(cfg, fetcher, publisher, dry_run=args.dry_run)
    added = crawler.seed(cfg.seeds)
    log.info("seeded %d/%d URLs", added, len(cfg.seeds))
    if added == 0 and not cfg.seeds:
        log.error("no seeds configured (use --seed or seeds in config)")
        return 2

    loop = asyncio.new_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, crawler.stop)
        except NotImplementedError:  # pragma: no cover - non-unix
            pass
    try:
        stats = loop.run_until_complete(crawler.run(once=args.once or args.dry_run))
    finally:
        if publisher is not None:
            loop.run_until_complete(publisher.close())
        loop.close()
    log.info("done: %s", stats.as_dict())
    return 0


if __name__ == "__main__":
    sys.exit(main())
