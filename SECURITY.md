# Security Policy

## Supported Versions

Only the latest commit on `main` is supported. The Python node pins
`scrapling[fetchers]==0.4.15` deliberately (pre-1.0 churn) — do not unpublish
or yank assumptions; upgrades are tested before the pin moves.

## Reporting a Vulnerability

Please **do not** open a public issue for security reports.

- Open a [GitHub private security advisory](../../security/advisories/new) on this repository, or
- Contact the maintainer via the Nostr account linked at [github.com/NostrDanish](https://github.com/NostrDanish).

We aim to acknowledge within 72 hours.

## Security Model (read before reporting)

- **Indexer keys are pseudonymous and disposable by design.** The per-device /
  per-node indexer keypair signs kind 39697 observations only. It holds no
  funds, no moderation rights, no user identity. `SIP01_NSEC` is read from the
  environment only — never from config files — and generated keys are written
  with mode `0600` and never logged.
- **Events carry page metadata, never queries.** A leak of observation content
  is not a privacy incident; a leak of *what a user searched* is impossible by
  construction (the protocol never transports it).
- **SSRF guard scope:** the Python node refuses private/loopback/link-local/
  CGNAT targets after DNS resolution, re-checks redirect targets, and runs
  robots.txt fetches through the same guard. The browser app guards at the
  fetch/proxy boundary.
- **Intake (indexstr mode) is reserved but unimplemented.** When it lands,
  `nostr.verify_event()` (BIP340 signature + id commitment) MUST run before
  `sip01.validate_event()` — schema validation alone does not authenticate
  authorship.
- **Relay fan-out is a hardcoded allowlist** of wss endpoints; user-supplied
  relays are normalized to ws/wss only.

## Out of Scope

- Relay operators seeing node IP addresses (inherent to Nostr).
- Content of crawled public web pages.
- The CORS-proxy privacy tradeoff, which is documented in the README.
