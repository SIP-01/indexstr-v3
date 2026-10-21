#!/usr/bin/env bash
# verify_install.sh — post-install verification ritual for a SIP-01 scrapling node.
# Usage: scripts/verify_install.sh [path-to-scrapling-node]
# Exits 0 only if every check passes. Publishes nothing.
set -u
cd "${1:-.}" || { echo "FAIL: path not found: ${1:-.}"; exit 1; }

PASS=0; FAIL=0
ok()   { echo "PASS: $1"; PASS=$((PASS+1)); }
bad()  { echo "FAIL: $1"; FAIL=$((FAIL+1)); }

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
  bad "sip01_node not importable — run from the scrapling-node dir with deps installed"
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

# 5. Dry-run crawl of example.com — builds + validates event, publishes nothing
OUT=$(python3 -m sip01_node --once --dry-run --seed https://example.com 2>&1)
if echo "$OUT" | grep -q "widx:0f115db062b7c0dd030b16878c99dea5"; then
  ok "dry-run built a valid kind-39697 event (spec vector d-value match)"
else
  bad "dry-run did not produce the expected event — output: $(echo "$OUT" | tail -3)"
fi

# 6. Key file perms (if a key was generated)
KEY=$(find "${SIP01_DATA_DIR:-./data}" -name indexer.key 2>/dev/null | head -1)
if [ -n "$KEY" ]; then
  P=$(stat -c '%a' "$KEY" 2>/dev/null || stat -f '%Lp' "$KEY" 2>/dev/null)
  [ "$P" = "600" ] && ok "indexer key perms 600" || bad "indexer key perms $P — chmod 600 $KEY"
else
  ok "no generated key file to check (dry-run may not create one)"
fi

echo "----------------------------------------"
if [ "$FAIL" -eq 0 ]; then
  echo "ALL CHECKS PASSED ($PASS) — node is safe to start."
  exit 0
else
  echo "$FAIL CHECK(S) FAILED ($PASS passed) — fix before going live."
  exit 1
fi
