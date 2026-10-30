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
import random
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from titan_client import TitanClient, price5  # noqa: E402

KILL = "/root/titan/KILL"
FEEDS = {
    "BTC-USDCX": ("kraken", "XBTUSD"),
    "ETH-USDCX": ("kraken", "ETHUSD"),
    "SOL-USDCX": ("kraken", "SOLUSD"),
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


def mark_price(market, client=None):
    if client is not None:
        st, res = client.mark(market)
        if st == 200 and res.get("markPrice"):
            return float(res["markPrice"])
    src = FEEDS.get(market)
    if not src:
        raise SystemExit(f"no price feed for {market}")
    kind, pair = src
    return _coinbase(pair) if kind == "coinbase" else _kraken(pair)


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
    ap.add_argument("--stealth", action="store_true",
                    help="randomise leg sizes, offsets and spacing so the pair is not a fixed mirror")
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
            px = mark_price(args.market, client)
        except Exception as e:
            print(f"cycle {i}: price feed failed ({e}), retrying once")
            time.sleep(3)
            try:
                px = mark_price(args.market, client)
            except Exception as e2:
                print(f"cycle {i}: feed still down ({e2}), skipping this cycle")
                time.sleep(args.sleep)
                continue

        slip = args.slip
        size_a = size_b = args.size
        gap = 1.5
        if args.stealth:
            slip = random.uniform(args.slip * 0.5, args.slip * 2.5)
            size_a = round(args.size * random.uniform(0.85, 1.2), 6)
            size_b = round(args.size * random.uniform(0.85, 1.2), 6)
            gap = random.uniform(1.0, 6.0)
        buy_px = price5(px * (1 + slip))
        sell_px = price5(px * (1 - slip))
        notional = (size_a + size_b) * px
        traded += notional

        if args.dry_run:
            print(f"cycle {i}: BUY {size_a} @ {buy_px} | SELL {size_b} @ {sell_px} "
                  f"| gap {gap:.1f}s | mark {px:.2f} | notional {notional:.2f}")
            time.sleep(min(args.sleep, 1))
            continue

        st, res = client.order(args.market, "buy", size_a, buy_px, tif="ioc")
        print(f"cycle {i}: buy {size_a} -> {st} {json.dumps(res)[:160]}")
        if st not in (200, 201):
            break
        time.sleep(gap)
        st2, res2 = client.order(args.market, "sell", size_b, sell_px, tif="ioc")
        print(f"cycle {i}: sell {size_b} -> {st2} {json.dumps(res2)[:160]}")
        if st2 not in (200, 201):
            break

        time.sleep(random.uniform(args.sleep * 0.6, args.sleep * 1.8) if args.stealth else args.sleep)
        stp, pos = client.positions()
        if stp == 200:
            body = pos.get("positions", pos) if isinstance(pos, dict) else pos
            print(f"cycle {i}: positions {json.dumps(body)[:200]}")

    if client:
        stp, pos = client.positions()
        if stp == 200:
            for p in pos.get("items", []):
                sz = float(p.get("size", 0))
                if abs(sz) < 1e-9:
                    continue
                side = "sell" if sz > 0 else "buy"
                market = p.get("market", args.market)
                stm, mk = client.mark(market)
                ref = float(mk.get("markPrice", 0)) if stm == 200 else mark_price(market, client)
                px = price5(ref * (0.999 if side == "sell" else 1.001))
                stf, rf = client.order(market, side, abs(sz), px, tif="ioc", reduce_only=True)
                print(f"flatten {market} {sz:+.6f} -> {stf} {json.dumps(rf)[:120]}")
        st, bal = client.balance()
        print("final balance:", st, json.dumps(bal)[:300])


if __name__ == "__main__":
    main()
