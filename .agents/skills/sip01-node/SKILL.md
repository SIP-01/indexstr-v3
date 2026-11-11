---
name: sip01-node
description: Install, configure, verify and operate the SIP-01 Scrapling crawler node that publishes kind 39697 Web Index Observation events. Use for deployment on a VPS, server, Umbrel, Raspberry Pi or Docker host; crawl queues, outbox, relays, Scrapling tiers and node troubleshooting. Requires Python 3.11 or Docker. Do not use this skill for protocol changes to the core SIP-01 format; use the sip-01 skill.
---

# SIP-01 Scrapling Node — setup and operations

## Scope and implementation sources

This skill covers the headless Scrapling crawler in:
- https://github.com/SIP-01/Crawlstr-v3/tree/main/scrapling-node (source crawlstr/v3)
- https://github.com/SIP-01/indexstr-v3/tree/main/scrapling-node (source indexstr/v3; enables kind 16919 heartbeat and intake flags)

The node publishes signed kind 39697 observations to configured Nostr relays. The canonical protocol remains the SIP-01 draft at https://github.com/SIP-01/SIP-01.

## Non-negotiable operating rules

1. Use the vendored protocol implementation. Never hand-roll URL normalization or hashes. After any protocol change, run python -m sip01_node --selftest.
2. **Python 3.11 minimum.** The implementation imports the standard-library tomllib module, which is not present in Python 3.10. Python 3.12 is a good supported runtime. Do not describe Python 3.10 as supported unless a tomli fallback is implemented and tested.
3. By default, the node creates a separate indexer identity in <data_dir>/indexer.key with mode 0600. Do not reuse a human user's Nostr key or copy one node's indexer key to another node.
4. Never place a private key in a model prompt, command-line argument, shell history, repository, ordinary logs or stdout. Do not run commands that echo an nsec. If a deployment injects SIP01_NSEC from a secret manager, keep that secret outside the repo and restrict access. The supported override is SIP01_NSEC; SIP01_NSEC_FILE is not implemented.
5. Dry-run and inspect the unsigned event before enabling publishing. Obtain explicit user approval before the first real run because observations are public Nostr events and can be replicated.
6. Keep the Scrapling dependency pinned at scrapling[fetchers]==0.4.15 until the full verification suite is rerun against a deliberate dependency update.
7. The SSRF guard is part of the security boundary. Do not bypass it or weaken redirect/DNS checks.

## Install

### Docker

~~~bash
git clone https://github.com/SIP-01/Crawlstr-v3.git
cd Crawlstr-v3/scrapling-node
cp config.example.toml config.toml
# Set seeds and relay policy in config.toml; do not put private keys there.
docker compose up -d
docker compose logs -f
~~~

The node generates its own indexer key in its configured data directory when no SIP01_NSEC secret is injected. Verify the actual volume mount and data_dir before starting the service.

### Python / VPS / Umbrel host

Requires Python 3.11 or newer.

~~~bash
git clone https://github.com/SIP-01/Crawlstr-v3.git
cd Crawlstr-v3/scrapling-node
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m sip01_node --selftest
python -m sip01_node --once --dry-run --seed https://example.com
~~~

Browser tiers are optional. Install browser binaries only when DynamicFetcher or StealthyFetcher is required; the HTTP tier can run without them.

For indexstr mode, use the indexstr-v3 repository or set source to indexstr/v3. That mode enables the kind 16919 heartbeat and intake flags.

## Verification ritual

Run the packaged verification script with the node's virtual environment activated:

~~~bash
bash scripts/verify_install.sh <path-to-scrapling-node>
~~~

The script uses isolated temporary data directories and a dead loopback relay for the outbox check. It does not intentionally publish to a public relay. It requires live HTTPS to example.com for network checks. If VERIFY_SKIP_NETWORK=1, network-dependent checks are skipped and must not be reported as passed.

The script must reject Python versions below 3.11 before importing sip01_node. A successful version check alone is not proof the full node is healthy; inspect every verification result.

## Configuration and operation

- TOML configuration is in config.toml; SIP01_<OPTION> variables override normal options.
- The supported external key variable is SIP01_NSEC. No SIP01_NSEC_FILE variable is implemented.
- The generated key file defaults to <data_dir>/indexer.key and must remain mode 0600.
- Queue and outbox databases are rebuildable, but the indexer key identifies the publisher and should be backed up encrypted and offline.
- Never delete the current key before a replacement identity and rollback plan are ready.
- Relay acknowledgements indicate per-relay acceptance, not global replication or guaranteed search availability.

For systemd, use the supplied reference service in references/operations.md. Use a dedicated service user and StateDirectory. Leave the optional SIP01_NSEC environment file out when using the generated key. If an external secret is required, inject only the supported SIP01_NSEC variable from a root-owned, mode-0600 EnvironmentFile or a deployment secret manager; do not add the secret to config.toml.

## Troubleshooting expectations

- Zero relay acknowledgements can be a connectivity, relay-policy or rate-limit issue; inspect each relay result and the persistent outbox.
- An increasing outbox is not proof of successful publication.
- Browser tiers need browser installation and remain best-effort against anti-bot systems.
- A dry-run proves event construction, not that a relay accepts or indexes a real event.
- A public observation event is not private; never crawl authenticated/private pages or publish private URLs or query strings.

## Related references

- references/config.md — supported config options and environment overrides.
- references/operations.md — systemd, key lifecycle, update and troubleshooting procedures.
