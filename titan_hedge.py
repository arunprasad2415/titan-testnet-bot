#!/usr/bin/env python3
"""Titan testnet points farmer: paired long/short at the same mark, net-flat.

Each cycle fires two crossing limit orders around the mark price, one buy and one
sell of the same size. Both fill as taker, the position nets to zero, and the
account books 2x size of volume, which is what the weekly points score.

  python3 titan_hedge.py --market BTC-USDCX --size 0.002 --cycles 20 --dry-run
  python3 titan_hedge.py --market BTC-USDCX --size 0.002 --cycles 20

Safety rails: --max-notional caps cumulative volume, a KILL file stops it between
cycles, and any non-2xx answer stops the loop instead of retrying blind.
"""
import argparse
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from titan_client import TitanClient  # noqa: E402

KILL = "/root/titan/KILL"
FEEDS = {
    "BTC-USDCX": ("coinbase", "BTC-USD"),
    "ETH-USDCX": ("coinbase", "ETH-USD"),
    "SOL-USDCX": ("coinbase", "SOL-USD"),
    "CC-USDCX": ("kraken", "CCUSD"),
}


def _coinbase(pair):
    url = f"https://api.coinbase.com/v2/prices/{pair}/spot"
    with urllib.request.urlopen(url, timeout=15) as r:
        return float(json.load(r)["data"]["amount"])


def _kraken(pair):
    url = f"https://api.kraken.com/0/public/Ticker?pair={pair}"
    with urllib.request.urlopen(url, timeout=15) as r:
        d = json.load(r)["result"]
        return float(next(iter(d.values()))["c"][0])


def mark_price(market):
    src = FEEDS.get(market)
    if not src:
        raise SystemExit(f"no price feed for {market}")
    kind, pair = src
    return _coinbase(pair) if kind == "coinbase" else _kraken(pair)


def fmt(price, sz_decimals):
    """Round a price to the venue's tick (5 significant figures like the app)."""
    if price >= 1000:
        step = 0.5
    elif price >= 100:
        step = 0.05
    elif price >= 1:
        step = 0.005
    else:
        step = 0.00005
    return round(round(price / step) * step, 6)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="BTC-USDCX")
    ap.add_argument("--size", type=float, required=True, help="base size per leg")
    ap.add_argument("--cycles", type=int, default=10)
    ap.add_argument("--sleep", type=float, default=6.0, help="seconds between cycles")
    ap.add_argument("--slip", type=float, default=0.0006, help="crossing offset, 6 bps default")
    ap.add_argument("--max-notional", type=float, default=5000.0)
    ap.add_argument("--session", default="/root/titan/session.json")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    client = None
    if not args.dry_run:
        client = TitanClient(args.session)
        ready, info = client.account_ready()
        if not ready:
            print("account not ready:", json.dumps(info)[:220])
            sys.exit(1)
        print("account ok:", json.dumps(info)[:160])

    traded = 0.0
    for i in range(1, args.cycles + 1):
        if os.path.exists(KILL):
            print("KILL file present, stopping")
            break
        if traded >= args.max_notional:
            print(f"notional cap reached ({traded:.2f}), stopping")
            break

        try:
            px = mark_price(args.market)
        except Exception as e:
            print(f"cycle {i}: price feed failed ({e}), retrying once")
            time.sleep(3)
            px = mark_price(args.market)

        buy_px = fmt(px * (1 + args.slip), 5)
        sell_px = fmt(px * (1 - args.slip), 5)
        notional = args.size * px
        traded += notional * 2

        if args.dry_run:
            print(f"cycle {i}: BUY {args.size} @ {buy_px} | SELL {args.size} @ {sell_px} "
                  f"| mark {px:.2f} | notional {notional:.2f}")
            time.sleep(min(args.sleep, 1))
            continue

        st, res = client.order(args.market, "buy", args.size, buy_px, tif="IOC")
        print(f"cycle {i}: buy -> {st} {json.dumps(res)[:160]}")
        if st not in (200, 201):
            break
        time.sleep(1.5)
        st2, res2 = client.order(args.market, "sell", args.size, sell_px, tif="IOC")
        print(f"cycle {i}: sell -> {st2} {json.dumps(res2)[:160]}")
        if st2 not in (200, 201):
            break

        time.sleep(args.sleep)
        stp, pos = client.positions()
        if stp == 200:
            body = pos.get("positions", pos) if isinstance(pos, dict) else pos
            print(f"cycle {i}: positions {json.dumps(body)[:200]}")

    if client:
        st, bal = client.balance()
        print("final balance:", st, json.dumps(bal)[:300])


if __name__ == "__main__":
    main()
