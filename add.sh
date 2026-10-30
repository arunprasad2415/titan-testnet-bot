#!/usr/bin/env bash
# Store a wallet once, then ./run.sh starts it forever.
#
#   ./add.sh alice 0xPRIVATE_KEY
#   ./add.sh bob   0xPRIVATE_KEY socks5://user:pass@1.2.3.4:1080
set -e
cd "$(dirname "$0")"
PY=/root/pw-venv/bin/python

if [ $# -lt 2 ]; then
    echo "usage: ./add.sh NAME 0xPRIVATE_KEY [PROXY]"
    echo "example: ./add.sh alice 0xabc...def"
    exit 1
fi

NAME="$1"
KEY="$2"
PROXY="${3:-}"

case "$KEY" in
    0x*) ;;
    *) KEY="0x$KEY" ;;
esac

if [ -n "$PROXY" ]; then
    exec "$PY" titan_multi.py --add "$NAME" "$KEY" --proxy "$PROXY"
fi
exec "$PY" titan_multi.py --add "$NAME" "$KEY"
