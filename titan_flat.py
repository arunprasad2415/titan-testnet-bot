#!/usr/bin/env python3
"""Close every open Titan position and leave the account flat.

Stopping the bot does not close its trade, so run this after `systemctl stop`.

  ./flat.sh            # close everything now
  ./flat.sh --dry-run  # show what would be closed
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from titan_client import TitanClient, price5   # noqa: E402

SESSION = "/root/titan/sessions/main.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", default=SESSION)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    c = TitanClient(a.session)
    st, pos = c.positions()
    if st != 200:
        print("cannot read positions:", st, json.dumps(pos)[:200])
        sys.exit(1)

    items = pos.get("items", [])
    if not items:
        print("already flat, nothing to close")
        return

    for p in items:
        market = p["market"]
        size = float(p["size"])
        if abs(size) < 1e-9:
            continue
        side = "sell" if size > 0 else "buy"
        st_m, mark = c.mark(market)
        px = float(mark.get("markPrice", 0)) if st_m == 200 else 0
        px = price5(px * (0.998 if side == "sell" else 1.002))
        print(f"{market} {size:+.6f} -> {side} {abs(size)} @ {px}"
              + (" (dry run)" if a.dry_run else ""))
        if a.dry_run:
            continue
        st2, res = c.order(market, side, abs(size), px, tif="ioc", reduce_only=True)
        print("   ", st2, json.dumps(res)[:160])
        time.sleep(1)

    if a.dry_run:
        return
    time.sleep(2)
    st, pos = c.positions()
    left = [(p["market"], p["size"]) for p in pos.get("items", [])]
    print("open after flatten:", left or "none")
    st, bal = c.balance()
    print("balance:", bal.get("balance"))


if __name__ == "__main__":
    main()
