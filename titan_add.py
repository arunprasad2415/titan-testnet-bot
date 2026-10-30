#!/usr/bin/env python3
"""Store wallets with plain questions. No arguments needed.

  ./add.sh                      # just answers
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
    try:
        addr = Account.from_key(key).address
    except Exception:
        print("  that is not a valid private key, skipped")
        return False
    entry = {"name": name, "privateKey": key}
    if proxy:
        entry["proxy"] = proxy
    cfg["accounts"] = [a for a in cfg.get("accounts", []) if a.get("name") != name] + [entry]
    save(cfg)
    print(f"  saved {name}  {addr}" + (f"  via {proxy.split('@')[-1]}" if proxy else "  direct"))
    return True


def main():
    cfg = load()

    if len(sys.argv) >= 3:
        name, key = sys.argv[1], sys.argv[2]
        proxy = sys.argv[3] if len(sys.argv) > 3 else ""
        store(cfg, name, key, proxy)
        print(f"\n{len(cfg['accounts'])} wallet(s) stored. start with: ./run.sh")
        return

    print("Titan Auto Trade Bot - add a wallet")
    print("-----------------------------------")
    while True:
        name = input("  wallet name (anything you like): ").strip()
        if not name:
            print("  a name is required")
            continue
        key = getpass.getpass("  private key (hidden, stays on this machine): ").strip()
        if not key:
            print("  no key, skipped")
        else:
            proxy = input("  proxy (press Enter for direct): ").strip()
            store(cfg, name, key, proxy)
        again = input("  add another wallet? [y/N]: ").strip().lower()
        if again != "y":
            break

    print(f"\n{len(cfg['accounts'])} wallet(s) stored in {CFG} (mode 600):")
    for a in cfg["accounts"]:
        print(f"  {a.get('name'):12} {(a.get('proxy') or 'direct').split('@')[-1]}")
    print("\nstart them all with:  ./run.sh")


if __name__ == "__main__":
    main()
