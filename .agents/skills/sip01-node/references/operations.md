# Operations — running, updating, troubleshooting a SIP-01 node

Contents: run modes · updating · key management · confirming publication · troubleshooting.

## Run modes

**Docker (recommended):** `docker compose up -d` — `restart: unless-stopped` is
already set; data persists in the `node-data` volume (queue, outbox, key).
Logs: `docker compose logs -f`. Stop: `docker compose down` (graceful — the
node finishes in-flight fetches on SIGTERM).

**systemd (bare metal):**

```ini
[Unit]
Description=SIP-01 scrapling node
After=network-online.target

[Service]
WorkingDirectory=/opt/sip01/scrapling-node
Environment=SIP01_NSEC_FILE=/opt/sip01/nsec   # or Environment=SIP01_NSEC=...
ExecStart=/opt/sip01/scrapling-node/.venv/bin/python -m sip01_node --config config.toml
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**Umbrel / home server:** run the Docker path inside any always-on container
host. No inbound ports needed — the node only dials out (HTTPS + wss).

## Updating

```bash
git pull   # or re-copy scrapling-node/ from the repo
pip install -r requirements.txt        # picks up a moved pin if changed
python -m sip01_node --selftest        # MUST pass before restart
docker compose up -d --build           # or: systemctl restart sip01-node
```

The sqlite queue/outbox survive updates; recrawl state is preserved.

## Key management

- Find the node pubkey (npub): printed at startup, or derive from the key file.
  Never copy `indexer.key` between nodes — one key = one indexer identity.
- **Rotate:** delete `<data_dir>/indexer.key` (or point `SIP01_NSEC` at a new
  key) and restart. A fresh indexer identity starts clean; old observations
  stay valid under the old pubkey.
- Backup: the only state worth backing up is `indexer.key` (identity) — the
  queue/outbox are rebuildable.

## Confirming publication

```bash
# any Nostr client or CLI, filter by the node pubkey:
{"kinds":[39697],"authors":["<node-pubkey-hex>"],"limit":5}
# heartbeats (indexstr mode):
{"kinds":[16919],"authors":["<node-pubkey-hex>"],"limit":5}
```

Zero results right after start ≠ broken — relays ack asynchronously and some
drop addressable events from unknown keys; check node logs for `ok/total` ack
counts first.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ModuleNotFoundError: scrapling` | deps not installed in the active env | activate the venv; `pip install -r requirements.txt` |
| Browser tiers raise `TierUnavailableError` | browsers not installed | `scrapling install` (needs ~1 GB), or set `stealth = "off"` |
| pip crawl fails on `patchright` download | slow/filtered network | retry; tier-1 HTTP works without browsers meanwhile |
| `0/N relays acked` persistently | relays down or key rate-limited | check logs per relay; outbox retains events and retries; verify relays with a NIP-11 probe |
| Crawl pauses with "bandwidth budget exhausted" | `hourly_bandwidth_mb` hit | intended; raise the budget or wait for the window to roll |
| Pages skipped as thin | JS-only site, tier-1 fetch | set `stealth = "auto"`/`"always"` + `scrapling install` |
| `SSRF guard refused` for a legit public URL | DNS resolves to private IP (split-horizon) | check local DNS; the guard is working as designed |
| Queue grows but nothing dispatches | all rows future-`next_try` (freshness) | intended backoff; `--once` drains only due rows |
| After upgrade: selftest fails | vendored protocol file touched | restore `sip01_node/sip01.py` from the repo; never hand-edit it |

## Security posture (state this to users)

Node key is env-only/0600, never logged · SSRF guard on every fetch incl.
redirects and robots.txt · events self-validated before publish · relay list is
a fixed allowlist (ws/wss only) · no inbound listeners.
