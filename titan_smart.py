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
from titan_behavior import Profile            # noqa: E402

KILL = "/root/titan/KILL"


TAKER_BPS = 4.5
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


class Smart:
    def __init__(self, a, client=None):
        self.a = a
        self.prof = Profile(getattr(a, "name", "default")) if getattr(a, "stealth", False) else None
        self.cur_market = a.market
        self.cur_size = a.size
        self.cur_tif = "ioc"
        self.client = client if client is not None else (None if a.dry_run else TitanClient(a.session))
        self.proxy = getattr(a, "proxy", "") or ""
        self.proxy_dead_since = 0
        self.base_momentum = getattr(a, "momentum", 0.01)
        self.specs = {}
        if self.client:
            st, res = self.client.markets()
            if st == 200:
                self.specs = {m["market"]: m for m in res.get("markets", [])}
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
            st, res = self.client.mark(self.cur_market)
            if st == 200 and res.get("markPrice"):
                return float(res["markPrice"])
        return spot(self.cur_market)

    # ---------- signal ----------
    def sample(self):
        px = self.price()
        if px <= 0:
            return
        if self.samples:
            prev = self.samples[-1][1]
            if prev > 0 and abs(px / prev - 1) > 0.05:   # >5% between samples = bad tick
                self.samples.clear()
                return
        self.samples.append((time.time(), px))
        cutoff = time.time() - 20
        self.samples = [s for s in self.samples if s[0] >= cutoff]

    def momentum(self):
        if len(self.samples) < 8:
            return 0.0
        a, b = self.samples[0][1], self.samples[-1][1]
        if a <= 0:
            return 0.0
        m = (b - a) / a
        return m if abs(m) < 0.02 else 0.0      # >2% in 20s is noise, not a signal

    def wait_signal(self, max_wait=300):
        base_gate = self.base_momentum
        gate = (self.prof.gate(base_gate) if self.prof else base_gate) / 100.0
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
    def size_for(self, market, mark):
        """Same dollar risk on every market, and never below the venue minimum."""
        spec = self.specs.get(market, {})
        dec = int(spec.get("szDecimals", 5))
        min_notional = float(spec.get("minNotional", 10))
        if mark <= 0:
            return 0
        if getattr(self.a, "size_usd", 0):
            size = round(self.a.size_usd / mark, dec)
        else:
            size = round(self.cur_size, dec)
        if size * mark < min_notional:
            size = round((min_notional / mark) * 1.01, dec)
            print(f"  size raised to the {min_notional} min notional -> {size}")
        return size

    def open_side(self, side):
        slip = self.a.slip
        if self.prof:
            slip = self.prof.slip(self.a.slip)
            self.prof.pre_entry_jitter()
        mark = self.price()
        px = price5(mark * (1 + slip if side == "buy" else 1 - slip))
        size = self.size_for(self.cur_market, mark)
        if size <= 0:
            print("  size rounds to zero, skipping")
            return False
        self.last_size = size
        if self.a.dry_run:
            self.entry = mark
            self.side = side
            print(f"  DRY open {side} {size} @ {px} (entry {mark:.2f})")
            return True
        st, res = self.client.order(self.cur_market, side, size, px,
                                    tif=self.cur_tif, reduce_only=False)
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
            print(f"  DRY close {side} {getattr(self, 'last_size', self.cur_size)} @ {px} ({reason})")
            self.side = None
            return True
        size = getattr(self, "last_size", self.cur_size)
        if self.prof and self.prof.close_in_pieces() and size > 0.0002:
            half = round(size / 2, 6)
            st, res = self.client.order(self.cur_market, side, half, px, tif="ioc", reduce_only=True)
            if st not in (200, 201):
                return False
            time.sleep(random.uniform(1.0, 4.0))
            px = price5(self.price() * (1 - 0.0003 if side == "sell" else 1 + 0.0003))
            size = round(size - half, 6)
        st, res = self.client.order(self.cur_market, side, size, px,
                                    tif="ioc", reduce_only=True)
        print(f"  close {side} -> {st} {json.dumps(res)[:200]} ({reason})")
        if st in (200, 201):
            self.side = None
        return st in (200, 201)

    def warn(self, msg):
        import datetime
        ts = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=7))).strftime("%H:%M:%S WIB")
        line = f"!! [{getattr(self.a, 'name', 'account')}] {ts} {msg}"
        print(line, flush=True)
        try:
            with open("/root/titan/PROXY_ALERT", "a") as f:
                f.write(line + "\n")
        except Exception:
            pass

    def proxy_alive(self):
        """True when there is no proxy, or when the proxy answers."""
        if not self.proxy or not self.client:
            return True
        for _ in range(2):
            if self.client.alive():
                return True
            time.sleep(2)
        return False

    def guard_proxy(self, max_wait=300):
        """Warn the moment the proxy dies, and refuse to trade blind."""
        if self.proxy_alive():
            if self.proxy_dead_since:
                mins = (time.time() - self.proxy_dead_since) / 60
                self.warn(f"proxy back after {mins:.0f} min, resuming")
                self.proxy_dead_since = 0
            return True
        if not self.proxy_dead_since:
            self.proxy_dead_since = time.time()
            self.warn(f"PROXY DEAD {self.proxy.split('@')[-1]} - trading paused, retrying")
        waited = time.time() - self.proxy_dead_since
        if waited > max_wait:
            self.warn(f"proxy dead for {waited/60:.0f} min, stopping this account")
            return False
        time.sleep(15)
        return self.guard_proxy(max_wait)

    def flatten(self):
        """Leave no dust behind: any residual size is closed before a new entry."""
        if not self.client:
            return
        st, pos = self.client.positions()
        if st != 200:
            return
        for p in pos.get("items", []):
            sz = float(p.get("size", 0))
            if abs(sz) < 1e-9:
                continue
            side = "sell" if sz > 0 else "buy"
            market = p.get("market", self.cur_market)
            stm, mk = self.client.mark(market)
            ref = float(mk.get("markPrice", 0)) if stm == 200 else self.price()
            if ref <= 0:
                continue
            px = price5(ref * (0.999 if side == "sell" else 1.001))
            st2, res = self.client.order(market, side, abs(sz), px, tif="ioc", reduce_only=True)
            print(f"  flatten {market} {sz:+.6f} -> {st2} {json.dumps(res)[:120]}")

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
            if not self.guard_proxy():
                print("  stopped: proxy unreachable")
                return
            self.flatten()
            mom = self.wait_signal()
            if mom is None:
                print("  no signal in window, skipping")
                continue
            side = "buy" if mom > 0 else "sell"
            if self.prof:
                self.cur_market = self.prof.market(self.a.market)
                self.cur_size = self.prof.size(self.a.size)
                self.cur_tif = self.prof.tif()
                if self.prof.skip():
                    print(f"  signal {side} but passing this one (profile skip)")
                    time.sleep(self.prof.pause(self.a.pause))
                    continue
            print(f"  signal {side} momentum {mom*100:.3f}% "
                  f"{self.cur_market} size {self.cur_size} {self.cur_tif}")
            if not self.open_side(side):
                time.sleep(3)
                print("  entry failed, retrying once")
                if not self.open_side(side):
                    print("  entry failed twice, stopping this account")
                    break

            t0 = time.time()
            closed = "timeout"
            while True:
                if os.path.exists(KILL):
                    closed = "kill"
                    break
                r = self.roe()
                # carriage return only on a real terminal, otherwise logs glue lines together
                print(f"    roe {r:+.2f}%  ({time.time()-t0:.0f}s)",
                      end="\r" if sys.stdout.isatty() else "\n", flush=True)
                if r >= a.tp:
                    closed = "TP"
                    break
                if r <= -a.sl:
                    closed = "SL"
                    break
                hold = self.prof.hold(a.timeout) if self.prof else a.timeout
                if time.time() - t0 >= hold:
                    break
                time.sleep(1)
            final = self.roe()
            self.realized += final
            self.close_pos(closed)
            self.flatten()          # sweep whatever the close left behind
            print(f"\n  closed by {closed}, roe {final:+.2f}%, total {self.realized:+.2f}%")

            if self.realized <= -a.max_dd:
                print(f"  drawdown guard hit ({self.realized:.2f}% <= -{a.max_dd}%), stopping")
                break
            if self.prof:
                time.sleep(self.prof.pause(a.pause))
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
