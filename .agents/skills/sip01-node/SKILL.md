---
name: sip01-node
description: "Install, configure, verify, and operate a SIP-01 Scrapling node — the headless Python crawler that publishes kind 39697 web index observations to the decentralized SIP-01 search index on Nostr. Use whenever asked to: set up / install / deploy a SIP-01 node, crawlstr node, or indexstr node on a server, VPS, Umbrel, Raspberry Pi, or Docker host; run a headless SIP-01 crawler; configure SIP01_NSEC or config.toml for the scrapling-node; troubleshoot a running node (zero acks, outbox growth, bandwidth pauses, stealth/browser tier issues); or verify a node install. Triggers on 'SIP-01 node', 'scrapling node', 'crawlstr node', 'indexstr node', 'kind 39697 crawler', 'headless nostr indexer'."
---

# SIP-01 Scrapling Node — setup & operation

A headless Python crawler node for the SIP-01 protocol: it crawls the web with
the Scrapling framework and publishes signed **kind 39697 web index
observations** to Nostr relays, byte-compatible with the browser nodes
(Crawlstr v3 / indexstr v3).

Code lives at:
- `github.com/SIP-01/Crawlstr-v3` → `scrapling-node/` (source tag `crawlstr/v3`)
- `github.com/SIP-01/indexstr-v3` → `scrapling-node/` (source tag `indexstr/v3`, adds heartbeat + intake flags)

## Non-negotiables

1. **Never hand-roll protocol logic.** Event building/validation comes from the
   vendored `sip01_node/sip01.py`. Byte-compatibility is rule zero — a
   one-character drift in URL normalization breaks dedup against every other
   indexer. After ANY change, run `python -m sip01_node --selftest`.
2. **`SIP01_NSEC` is env-only.** Never write it to config.toml, never commit it,
   never print it. If unset, the node generates an indexer key into
   `<data_dir>/indexer.key` (mode 0600). The key is disposable and pseudonymous
   — rotation just makes a new indexer.
3. **Verify before going live.** Always run the verification ritual (below)
   before letting a fresh install publish.
4. **Get explicit user consent before the first real run.** Dry-run first, show
   the user the built event, then start the publisher. Observations are public
   and permanent on relays.
5. Pin = `scrapling[fetchers]==0.4.15`. Scrapling is pre-1.0 and churns; do not
   upgrade the pin without re-running the full verification ritual.

## Install

Prereqs: Python ≥ 3.10 (3.12 recommended) OR Docker. Disk: `scrapling[fetchers]`
pulls ~110 MB of wheels (patchright/playwright packages) even without browser
binaries; `scrapling install` adds ~1 GB of browsers on top. On slow links,
retry pip if a wheel download stalls.

**Docker (preferred for servers/Umbrel):**

```bash
git clone https://github.com/SIP-01/Crawlstr-v3.git && cd Crawlstr-v3/scrapling-node
cp config.example.toml config.toml   # edit seeds/relays/source
echo 'SIP01_NSEC=nsec1...' > .env    # optional; omit to auto-generate
chmod 600 .env
docker compose up -d                 # logs: docker compose logs -f
```

**pip (bare metal):**

```bash
git clone https://github.com/SIP-01/Crawlstr-v3.git && cd Crawlstr-v3/scrapling-node
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
scrapling install        # OPTIONAL — only for Dynamic/Stealthy browser tiers
python -m sip01_node --config config.toml
```

For the indexstr variant use the `SIP-01/indexstr-v3` repo (config defaults to
`source = "indexstr/v3"`), or pass `--source indexstr/v3` — the CLI enables the
heartbeat (kind 16919) + intake flags the same way the config loader does.

## Verify (mandatory ritual)

With the node's venv activated (the script calls bare `python3`):

```bash
bash scripts/verify_install.sh <path-to-scrapling-node>
```

The script ships with this skill at `scripts/verify_install.sh` and is also
mirrored into both node repos at `.agents/skills/sip01-node/scripts/`. Invoke
it with `bash` — do not rely on an exec bit. It runs against isolated
throwaway data dirs, so re-runs are safe and the node's real `./data` is
never touched.

Checks, in order: Python ≥ 3.10 → package imports → scrapling pin → selftest
(spec §13 vectors) → dry-run crawl of https://example.com producing
`d = widx:0f115db062b7c0dd030b16878c99dea5` → real key generation with mode
0600 → outbox retry path via a dead loopback relay (signed, **nothing
published**) → secret never appears in logs. All green = safe to start.

Needs live HTTPS to example.com; on offline/air-gapped hosts run with
`VERIFY_SKIP_NETWORK=1` (checks 5–6 skip — tell the user they were skipped).

Equivalent manual commands:

```bash
python -m sip01_node --selftest
python -m sip01_node --once --dry-run --seed https://example.com
```

## Operate

- **Run once and exit:** `--once`. **Forever:** omit it (freshness recrawls,
  outbox flushes, heartbeats run on schedule).
- **Confirm publishing:** query a SIP-01 relay for the node's pubkey:
  `{"kinds":[39697],"authors":["<node-pubkey>"],"limit":5}`.
- Config reference (every option + env override): [references/config.md](references/config.md)
- Updates, key rotation, systemd, logs, troubleshooting table:
  [references/operations.md](references/operations.md)

## Known caveats (bake into expectations)

- Tier ladder is Fetcher (TLS-impersonated HTTP) → DynamicFetcher →
  StealthyFetcher, escalating only on block signatures. Browser tiers need
  `scrapling install`; stealth is best-effort, not a guarantee.
- Browser-tier fetches can hang past their own timeout upstream (Scrapling
  #468/#491) — the node wraps every fetch in a hard `asyncio.wait_for`; do not
  remove that.
- Relay fan-out failures are normal: zero-ack events go to the sqlite outbox
  and retry. "Published" counts relay-ACKed events only.
