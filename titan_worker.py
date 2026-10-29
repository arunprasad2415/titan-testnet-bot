#!/usr/bin/env python3
"""Run one account's trading loop. Spawned by titan_multi.py.

  python3 titan_worker.py --session sessions/alice.json --config '{"market":"BTC-USDCX",...}'
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from titan_client import TitanClient      # noqa: E402
from titan_smart import Smart             # noqa: E402
from titan_behavior import Profile        # noqa: E402


class NS:
    def __init__(self, d, session):
        self.market = d.get("market", "BTC-USDCX")
        self.size = float(d.get("size", 0.002))
        self.leverage = int(d.get("leverage", 5))
        self.tp = float(d.get("tp", 1.2))
        self.sl = float(d.get("sl", 0.9))
        self.timeout = int(d.get("timeout", 90))
        self.max_dd = float(d.get("max_dd", 15))
        self.cycles = int(d.get("cycles", 0))          # 0 = forever
        self.pause = float(d.get("pause", 3))
        self.slip = float(d.get("slip", 0.0004))
        self.momentum = float(d.get("momentum", 0.01))
        self.stealth = bool(d.get("stealth", True))
        self.session = session
        self.dry_run = bool(d.get("dry_run", False))
        self.name = d.get("name", "default")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True)
    ap.add_argument("--config", required=True)
    a = ap.parse_args()

    d = json.loads(a.config)
    ns = NS(d, a.session)
    proxy = d.get("proxy") or None
    prof = Profile(ns.name, index=int(d.get("index", 0)))
    print(f"profile {ns.name}: {prof.impersonate}, size x{prof.size_mult:.2f}, "
          f"pace x{prof.pace_mult:.2f}, markets {prof.market_pool}", flush=True)
    client = None if ns.dry_run else TitanClient(a.session, proxy=proxy,
                                                 impersonate=prof.impersonate)

    if client:
        ready, info = client.account_ready()
        if not ready:
            print("account not ready:", json.dumps(info)[:200], flush=True)
            sys.exit(1)
        st, lev = client.set_leverage(ns.market, ns.leverage)
        print(f"leverage {ns.leverage}x -> {st}", flush=True)

    # cycles 0 means run until killed
    if ns.cycles == 0:
        ns.cycles = 10 ** 9
    Smart(ns, client=client).run()


if __name__ == "__main__":
    main()
