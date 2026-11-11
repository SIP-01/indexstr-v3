# Operations — SIP-01 Scrapling Node

## Run modes

**Docker:** use docker compose up -d. Confirm the compose volume maps the configured data directory persistently. Logs: docker compose logs -f. Stop gracefully with docker compose down.

**systemd (bare metal):**

~~~ini
[Unit]
Description=SIP-01 Scrapling Node
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=sip01
Group=sip01
WorkingDirectory=/opt/sip01/scrapling-node
StateDirectory=sip01
Environment=SIP01_DATA_DIR=/var/lib/sip01
# Optional only when an externally managed key is required:
# EnvironmentFile=-/etc/sip01/sip01-node.env
ExecStart=/opt/sip01/scrapling-node/.venv/bin/python -m sip01_node --config config.toml
Restart=on-failure
RestartSec=10
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/sip01

[Install]
WantedBy=multi-user.target
~~~

When the optional EnvironmentFile is used, it must contain the supported variable SIP01_NSEC. The file should be provisioned securely outside the repository, owned by root and mode 0600. Do not put the key in the unit file itself. The default and preferred setup omits EnvironmentFile so the node creates its own key file.

**Important:** the application reads SIP01_NSEC from the environment. It does not implement SIP01_NSEC_FILE. Do not configure that unsupported name.

## Updating

~~~bash
git pull
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m sip01_node --selftest
~~~

Only restart after the selftest passes. For Docker, rebuild the image after dependency/source updates. Back up the current data directory before upgrades that alter persistence formats.

## Key management

- By default the node creates <data_dir>/indexer.key with mode 0600.
- The key is the node's persistent publisher identity. Losing it creates a new indexer identity; old observations remain attributed to the old pubkey.
- Back up indexer.key encrypted, offline and with restricted recovery access. Do not store plaintext copies in the repository, a shared volume or a general-purpose agent workspace.
- Do not copy one node's key to another node; each node should have a distinct identity.
- Plan rotation before changing identities. Keep an encrypted recovery copy of the old identity until the replacement identity is confirmed operational. Do not simply delete the current key as the first rotation step.

## Confirming publication

Query the node's public indexer pubkey for kind 39697. For indexstr mode, inspect kind 16919 heartbeats too. A relay OK acknowledgement means that particular relay accepted the event; it is not proof of global replication or search indexing.

## Troubleshooting

| Symptom | Cause | Action |
|---|---|---|
| ModuleNotFoundError: tomllib | Python is older than 3.11 | Install/use Python 3.11+ and recreate the virtual environment |
| ModuleNotFoundError: scrapling | dependencies missing from active environment | activate the node venv and install requirements.txt |
| Browser tiers unavailable | browser binaries are not installed | install browser dependencies or use stealth=off |
| 0/N relays acknowledged | relays unreachable, rejecting or rate-limiting | inspect per-relay logs and outbox retries |
| Outbox grows | relay acknowledgements are not arriving | verify relay URLs/connectivity and inspect retry state |
| Crawl paused on bandwidth | hourly bandwidth limit reached | review the configured budget; do not disable without a resource plan |
| SSRF guard refused URL | destination resolves to private/reserved address | treat the guard as expected; only change seed URLs after verifying destination ownership |
| Selftest fails after update | protocol code or dependency drift | do not publish; restore the known-good release and investigate |

## Security posture

The node should have no inbound listener, a dedicated local identity, restrictive key-file permissions, bounded fetch timeouts/concurrency, public-destination SSRF protection including redirects, and explicit user approval before first publication. These controls must be verified on the actual deployed revision; this document is not a runtime attestation.
