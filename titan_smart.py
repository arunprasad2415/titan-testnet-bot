#!/usr/bin/env python3
"""Titan testnet terminal trader, smart mode.

Same idea as the console script people pass around (momentum entry, TP/SL per
position, global drawdown guard) but it talks to the API instead of clicking DOM
buttons, so fills, entry price and PnL are real numbers rather than scraped text.

  python3 titan_smart.py --market BTC-USDCX --size 0.002 --leverage 5 \
      --tp 1.2 --sl 0.9 --timeout 90 --max-dd 15 --dry-run

Fee reality check (taker 4.5 bps per side = 0.09% round trip):
a TP of 0.4% ROE at 5x is a 0.08% price move, which is smaller than the fees, so
that setting loses on every "win". The bot refuses to run with TP under the fee
floor unless --allow-thin-tp is passed.
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


def jitter(base, spread=0.35):
    """Human-ish timing: never the same interval twice."""
    return max(0.5, random.gauss(base, base * spread))


def rsize(base, lo=0.75, hi=1.30):
    """Size varies per cycle, so the tape does not read as one fixed bot."""
    return round(base * random.uniform(lo, hi), 6)
TAKER_BPS = 4.5
MAKER_BPS = 1.5
FEEDS = {
    "BTC-USDCX": ("kraken", "XBTUSD"),
    "ETH-USDCX": ("kraken", "ETHUSD"),
    "SOL-USDCX": ("kraken", "SOLUSD"),
    "CC-USDCX": ("kraken", "CCUSD"),
}


def spot(market):
    kind, pair = FEEDS[market]
    if kind == "coinbase":
        url = f"https://api.coinbase.com/v2/prices/{pair}/spot"
        with urllib.request.urlopen(url, timeout=10) as r:
            return float(json.load(r)["data"]["amount"])
    url = f"https://api.kraken.com/0/public/Ticker?pair={pair}"
    with urllib.request.urlopen(url, timeout=10) as r:
        return float(next(iter(json.load(r)["result"].values()))["c"][0])


def tick(price):
    if price >= 1000:
        step = 0.5
    elif price >= 100:
        step = 0.05
    elif price >= 1:
        step = 0.005
    else:
        step = 0.00005
    return round(round(price / step) * step, 6)


class Smart:
    def __init__(self, a):
        self.a = a
        self.client = None if a.dry_run else TitanClient(a.session)
        self.samples = []
        self.realized = 0.0
        self.cycles = 0
        self.side = None
        self.entry = 0.0

    # ---------- price ----------
    def price(self):
        """Venue mark first: an external spot feed can be cached and read as zero
        movement, which makes the momentum gate never fire."""
        if self.client:
            st, res = self.client.mark(self.a.market)
            if st == 200 and res.get("markPrice"):
                return float(res["markPrice"])
        return spot(self.a.market)

    # ---------- signal ----------
    def sample(self):
        self.samples.append((time.time(), self.price()))
        cutoff = time.time() - 20
        self.samples = [s for s in self.samples if s[0] >= cutoff]

    def momentum(self):
        if len(self.samples) < 8:
            return 0.0
        a, b = self.samples[0][1], self.samples[-1][1]
        return (b - a) / a

    def wait_signal(self, max_wait=300):
        gate = getattr(self.a, "momentum", 0.01) / 100.0   # percent -> fraction
        t0 = time.time()
        last_note = 0.0
        while time.time() - t0 < max_wait:
            if os.path.exists(KILL):
                return None
            self.sample()
            m = self.momentum()
            if abs(m) >= gate:
                return m
            if time.time() - last_note > 5:
                last_note = time.time()
                print(f"    waiting: move {m*100:+.4f}% over 20s, gate {gate*100:.3f}%", flush=True)
            time.sleep(1)
        return None            # no random fallback: no signal means no trade

    # ---------- orders ----------
    def open_side(self, side):
        slip = self.a.slip
        if getattr(self.a, "stealth", False):
            slip = random.uniform(self.a.slip * 0.6, self.a.slip * 2.2)
        px = price5(self.price() * (1 + slip if side == "buy" else 1 - slip))
        if self.a.dry_run:
            self.entry = self.price()
            self.side = side
            print(f"  DRY open {side} {self.a.size} @ {px} (entry {self.entry:.2f})")
            return True
        size = self.a.size
        if getattr(self.a, "stealth", False):
            size = rsize(self.a.size)
        self.last_size = size
        st, res = self.client.order(self.a.market, side, size, px,
                                    tif="ioc", reduce_only=False)
        print(f"  order {side} -> {st} {json.dumps(res)[:200]}")
        if st not in (200, 201):
            return False
        self.entry = self.price()
        self.side = side
        return True

    def close_pos(self, reason):
        side = "sell" if self.side == "buy" else "buy"
        px = price5(self.price() * (1 - self.a.slip if side == "sell" else 1 + self.a.slip))
        if self.a.dry_run:
            print(f"  DRY close {side} {self.a.size} @ {px} ({reason})")
            self.side = None
            return True
        st, res = self.client.order(self.a.market, side, getattr(self, "last_size", self.a.size), px,
                                    tif="ioc", reduce_only=True)
        print(f"  close {side} -> {st} {json.dumps(res)[:200]} ({reason})")
        if st in (200, 201):
            self.side = None
        return st in (200, 201)

    def roe(self):
        if not self.entry or not self.side:
            return 0.0
        move = (self.price() - self.entry) / self.entry
        return move * self.a.leverage * 100 * (1 if self.side == "buy" else -1)

    # ---------- loop ----------
    def run(self):
        a = self.a
        print(f"market {a.market} size {a.size} lev {a.leverage}x tp {a.tp}% sl {a.sl}% "
              f"timeout {a.timeout}s dd {a.max_dd}% dry={a.dry_run}")
        while self.cycles < a.cycles:
            if os.path.exists(KILL):
                print("KILL file, stopping")
                break
            self.cycles += 1
            print(f"cycle {self.cycles}: waiting for momentum")
            if getattr(self.a, "stealth", False):
                self.a.momentum = max(0.002, random.gauss(self.a.momentum, self.a.momentum * 0.3))
            mom = self.wait_signal()
            if mom is None:
                print("  no signal in window, skipping")
                continue
            side = "buy" if mom > 0 else "sell"
            print(f"  signal {side} momentum {mom*100:.3f}%")
            if not self.open_side(side):
                print("  entry failed, stop")
                break

            t0 = time.time()
            closed = "timeout"
            while True:
                if os.path.exists(KILL):
                    closed = "kill"
                    break
                r = self.roe()
                print(f"    roe {r:+.2f}%  ({time.time()-t0:.0f}s)", end="\r", flush=True)
                if r >= a.tp:
                    closed = "TP"
                    break
                if r <= -a.sl:
                    closed = "SL"
                    break
                if time.time() - t0 >= a.timeout:
                    break
                time.sleep(1)
            final = self.roe()
            self.realized += final
            print(f"\n  closed by {closed}, roe {final:+.2f}%, total {self.realized:+.2f}%")
            self.close_pos(closed)

            if self.realized <= -a.max_dd:
                print(f"  drawdown guard hit ({self.realized:.2f}% <= -{a.max_dd}%), stopping")
                break
            if getattr(a, "stealth", False):
                pause = jitter(a.pause, 0.6)
                if self.cycles % random.randint(4, 9) == 0:
                    pause = random.uniform(25, 90)      # occasional long break
                    print(f"  idle {pause:.0f}s (human pause)")
                time.sleep(pause)
            else:
                time.sleep(a.pause)

        if self.side:
            self.close_pos("shutdown")
        if self.client:
            st, bal = self.client.balance()
            print("balance:", st, json.dumps(bal)[:300])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="BTC-USDCX")
    ap.add_argument("--size", type=float, required=True)
    ap.add_argument("--leverage", type=int, default=5)
    ap.add_argument("--tp", type=float, default=1.2, help="ROE percent")
    ap.add_argument("--sl", type=float, default=0.9, help="ROE percent")
    ap.add_argument("--timeout", type=int, default=90)
    ap.add_argument("--max-dd", type=float, default=15.0)
    ap.add_argument("--cycles", type=int, default=10)
    ap.add_argument("--pause", type=float, default=3.0)
    ap.add_argument("--slip", type=float, default=0.0004)
    ap.add_argument("--stealth", action="store_true",
                    help="randomise timing, size, price offset and gate so the flow is not a fixed loop")
    ap.add_argument("--momentum", type=float, default=0.01,
                    help="momentum gate in percent over a 20s window")
    ap.add_argument("--session", default="/root/titan/session.json")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-thin-tp", action="store_true")
    a = ap.parse_args()

    # fee floor: two taker legs expressed as ROE percent at this leverage
    floor_roe = (TAKER_BPS * 2 / 100) * a.leverage
    if a.tp < floor_roe and not a.allow_thin_tp:
        print(f"refusing: TP {a.tp}% ROE is below the fee floor {floor_roe:.2f}% "
              f"(2x {TAKER_BPS} bps at {a.leverage}x). Raise --tp or pass --allow-thin-tp.")
        sys.exit(2)

    Smart(a).run()


if __name__ == "__main__":
    main()
