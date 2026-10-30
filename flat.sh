#!/usr/bin/env bash
cd "$(dirname "$0")"
exec /root/pw-venv/bin/python titan_flat.py "$@"
