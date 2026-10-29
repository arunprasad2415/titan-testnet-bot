#!/usr/bin/env bash
# Titan testnet CLI farmer launcher.
# Uses the project venv that already has eth-account and pynacl installed.
set -e
cd "$(dirname "$0")"
exec /root/pw-venv/bin/python titan_cli.py "$@"
