#!/usr/bin/env python3
"""Store wallets with plain questions. No arguments needed.

  ./add.sh                      # asks how many, then one by one
  ./add.sh alice 0xKEY [proxy]  # or straight from the command line
"""
import getpass
import json
import os
import sys

from eth_account import Account

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(HERE, "accounts.json")
DEFAULTS = {
    "market": "BTC-USDCX", "size_usd": 150, "size": 0.002, "leverage": 5,
    "tp": 1.2, "sl": 0.9, "timeout": 90, "max_dd": 15, "cycles": 0,
    "stealth": True, "momentum": 0.01,
}


def ask(prompt, secret=False):
    """Hide the key only on a real terminal; from a pipe, read plainly so tests work."""
    try:
        if secret and sys.stdin.isatty():
            return getpass.getpass(prompt)
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        print("\n  stopped, nothing else saved")
        raise SystemExit(1)


def load():
    if os.path.exists(CFG):
        try:
            return json.load(open(CFG))
        except Exception:
            pass
    return {"defaults": DEFAULTS, "accounts": []}


def save(cfg):
    json.dump(cfg, open(CFG, "w"), indent=1)
    os.chmod(CFG, 0o600)


def store(cfg, name, key, proxy=""):
    if not key.startswith("0x"):
        key = "0x" + key
    addr = Account.from_key(key).address
    entry = {"name": name, "privateKey": key}
    if proxy:
        entry["proxy"] = proxy
    cfg["accounts"] = [a for a in cfg.get("accounts", []) if a.get("name") != name] + [entry]
    save(cfg)
    print(f"    saved {name}  {addr}" + (f"  via {proxy.split('@')[-1]}" if proxy else "  direct"))
    return addr


def ask_wallet(cfg, index, total, existing):
    """One wallet. A bad key only re-asks the key, never the name."""
    print(f"\n  wallet {index} of {total}")
    name = ask("    name: ").strip() or f"wallet{index}"
    if name in existing:
        print(f"    {name} is already stored, this replaces it")
    while True:
        key = ask("    private key (hidden, stays on this machine): ").strip()
        if not key:
            print("    no key given, try again")
            continue
        if not key.startswith("0x"):
            key = "0x" + key
        try:
            Account.from_key(key)
            break
        except Exception:
            print("    that is not a valid private key, try again")
    proxy = ask("    proxy (press Enter for direct): ").strip()
    store(cfg, name, key, proxy)
    existing.add(name)


def main():
    cfg = load()

    if len(sys.argv) >= 3:
        name, key = sys.argv[1], sys.argv[2]
        proxy = sys.argv[3] if len(sys.argv) > 3 else ""
        if not key.startswith("0x"):
            key = "0x" + key
        try:
            store(cfg, name, key, proxy)
        except Exception:
            print("  that is not a valid private key")
            sys.exit(1)
        print(f"\n{len(cfg['accounts'])} wallet(s) stored. start with: ./run.sh")
        return

    print("Titan Auto Trade Bot - add wallets")
    print("----------------------------------")
    print(f"  {len(cfg['accounts'])} already stored")

    while True:
        raw = ask("\n  how many wallets do you want to add? [1]: ").strip() or "1"
        try:
            count = int(raw)
            if count < 1:
                raise ValueError
            break
        except ValueError:
            print("  give me a number, 1 or more")

    existing = {a.get("name") for a in cfg.get("accounts", [])}
    for i in range(1, count + 1):
        ask_wallet(cfg, i, count, existing)

    print(f"\n{len(cfg['accounts'])} wallet(s) stored in {CFG} (mode 600):")
    for a in cfg["accounts"]:
        print(f"  {a.get('name'):12} {(a.get('proxy') or 'direct').split('@')[-1]}")
    print("\nstart them all with:  ./run.sh")


if __name__ == "__main__":
    main()
