#!/usr/bin/env bash
# Titan Auto Trade Bot launcher.
#
#   ./run.sh              every wallet in accounts.json, or the setup wizard if none
#   ./run.sh --dry-run    same, but nothing is sent to the exchange
#   ./run.sh --only bob   just one wallet
#   ./run.sh --wizard     force the interactive single-wallet setup
set -e
cd "$(dirname "$0")"
PY=/root/pw-venv/bin/python

if [ "$1" = "--wizard" ]; then
    shift
    exec "$PY" titan_cli.py "$@"
fi

has_wallets() {
    [ -f accounts.json ] || return 1
    "$PY" - <<'EOF'
import json, sys
try:
    cfg = json.load(open("accounts.json"))
except Exception:
    sys.exit(1)
ok = [a for a in cfg.get("accounts", [])
      if a.get("privateKey", "").startswith("0x") and "REPLACE" not in a.get("privateKey", "")]
sys.exit(0 if ok else 1)
EOF
}

if has_wallets; then
    exec "$PY" titan_multi.py --config accounts.json "$@"
fi

exec "$PY" titan_cli.py "$@"
