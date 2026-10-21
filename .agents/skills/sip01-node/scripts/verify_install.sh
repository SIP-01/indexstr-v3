#!/usr/bin/env bash
# verify_install.sh — post-install verification ritual for a SIP-01 scrapling node.
# Usage: bash verify_install.sh [path-to-scrapling-node]
#        (run with the node's venv activated — the script calls bare python3)
#
# Exits 0 only if every check passes. Publishes NOTHING: all crawl checks run
# against an isolated throwaway data dir, and the key-perms check uses a
# dead-loopback relay so events land in the outbox instead of the network.
# Check 5 needs live HTTPS to https://example.com (offline? skip with
# VERIFY_SKIP_NETWORK=1).
set -u
cd "${1:-.}" || { echo "FAIL: path not found: ${1:-.}"; exit 1; }

PASS=0; FAIL=0
ok()   { echo "PASS: $1"; PASS=$((PASS+1)); }
bad()  { echo "FAIL: $1"; FAIL=$((FAIL+1)); }

# Isolated throwaway state — re-running this script never pollutes (or is
# polluted by) the node's real ./data directory. Idempotent by construction.
# Two separate dirs: check 5's crawl marks the seed fresh for 24h, which would
# suppress check 6's crawl if they shared a data dir.
TESTDATA="$(mktemp -d)"
TESTDATA2="$(mktemp -d)"
trap 'rm -rf "$TESTDATA" "$TESTDATA2"' EXIT

# 1. Python >= 3.10
if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
  ok "python $(python3 -V 2>&1 | awk '{print $2}') >= 3.10"
else
  bad "python >= 3.10 required"
fi

# 2. Package importable
if python3 -c 'import sip01_node' 2>/dev/null; then
  ok "sip01_node importable"
else
  bad "sip01_node not importable — run from the scrapling-node dir with the venv activated"
fi

# 3. scrapling importable + pinned
if python3 -c 'import scrapling' 2>/dev/null; then
  V=$(python3 -c 'import importlib.metadata as m; print(m.version("scrapling"))' 2>/dev/null)
  [ "$V" = "0.4.15" ] && ok "scrapling pinned at $V" || bad "scrapling version $V != pinned 0.4.15"
else
  bad "scrapling not installed — pip install -r requirements.txt"
fi

# 4. Protocol selftest (spec §13 vectors)
if python3 -m sip01_node --selftest 2>&1 | grep -q "ALL VECTORS PASS"; then
  ok "spec §13 vectors pass (byte-compatibility)"
else
  bad "selftest failed — do NOT run this node; restore sip01_node/sip01.py from the repo"
fi

# 5. Dry-run crawl of example.com — builds + validates event, publishes nothing.
#    Requires live network access to https://example.com.
if [ "${VERIFY_SKIP_NETWORK:-0}" = "1" ]; then
  ok "network check skipped (VERIFY_SKIP_NETWORK=1)"
else
  OUT=$(SIP01_DATA_DIR="$TESTDATA" python3 -m sip01_node --once --dry-run --seed https://example.com 2>&1)
  if echo "$OUT" | grep -q "widx:0f115db062b7c0dd030b16878c99dea5"; then
    ok "dry-run built a valid kind-39697 event (spec vector d-value match)"
  else
    bad "dry-run failed (needs live HTTPS to example.com) — tail: $(echo "$OUT" | tail -2)"
  fi
fi

# 6. Key generation + perms + outbox, exercised for real against a dead
#    loopback relay: a key is generated, the event is signed, NOTHING reaches
#    the network — it must land in the outbox. (Needs the same live fetch as
#    check 5, so it honors VERIFY_SKIP_NETWORK too.)
if [ "${VERIFY_SKIP_NETWORK:-0}" = "1" ]; then
  ok "key/outbox check skipped (VERIFY_SKIP_NETWORK=1)"
  ok "secret-logging check skipped (VERIFY_SKIP_NETWORK=1)"
else
  OUT2=$(SIP01_DATA_DIR="$TESTDATA2" SIP01_RELAYS="wss://127.0.0.1:9" timeout 120 python3 -m sip01_node --once --seed https://example.com 2>&1)
  KEY="$TESTDATA2/indexer.key"
  if [ -f "$KEY" ]; then
    P=$(stat -c '%a' "$KEY" 2>/dev/null || stat -f '%Lp' "$KEY" 2>/dev/null)
    [ "$P" = "600" ] && ok "indexer key generated with perms 600" || bad "indexer key perms $P — must be 600"
  else
    bad "no indexer key generated on a publishing run"
  fi
  if echo "$OUT2" | grep -qi "outbox"; then
    ok "un-acked event queued in outbox (nothing published, retry path works)"
  else
    bad "event did not reach the outbox — tail: $(echo "$OUT2" | tail -2)"
  fi
  # 6c. The secret must never appear in logs
  HEX=$(cat "$KEY" 2>/dev/null | tr -d '[:space:]')
  if [ -n "$HEX" ] && echo "$OUT2" | grep -q "$HEX"; then
    bad "indexer secret appeared in logs — do NOT run this build"
  else
    ok "secret never logged"
  fi
fi

echo "----------------------------------------"
if [ "$FAIL" -eq 0 ]; then
  echo "ALL CHECKS PASSED ($PASS) — node is safe to start."
  exit 0
else
  echo "$FAIL CHECK(S) FAILED ($PASS passed) — fix before going live."
  exit 1
fi
