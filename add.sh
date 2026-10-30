#!/usr/bin/env bash
# Store a wallet. Just run ./add.sh and answer the questions.
#   ./add.sh                      interactive
#   ./add.sh alice 0xKEY [proxy]  one line
set -e
cd "$(dirname "$0")"
exec /root/pw-venv/bin/python titan_add.py "$@"
