#!/usr/bin/env python3
"""Behavioural layer: every account gets its own personality, and every decision
inside a cycle is drawn from that personality rather than from a fixed script.

The point is not secrecy, it is that no two accounts and no two cycles look the
same in the venue's logs: different base sizes, different hold times, different
order types, different markets, different pauses, sometimes no trade at all.
"""
import random
import time

IMPERSONATIONS = ["chrome", "chrome124", "chrome120", "safari17_0", "edge101"]
MARKETS = ["BTC-USDCX", "ETH-USDCX", "SOL-USDCX"]


class Profile:
    """A stable personality per account name, plus per-cycle noise on top."""

    def __init__(self, name, seed=None, index=None, skip_chance=None):
        self.name = name
        rnd = random.Random(seed if seed is not None else name)
        # stable traits; index spreads the TLS fingerprints so two accounts never match
        self.impersonate = (IMPERSONATIONS[index % len(IMPERSONATIONS)]
                            if index is not None else rnd.choice(IMPERSONATIONS))
        self.size_mult = rnd.uniform(0.6, 1.6)
        self.pace_mult = rnd.uniform(0.7, 1.5)
        self.hold_mult = rnd.uniform(0.7, 1.4)
        self.taker_bias = rnd.uniform(0.25, 0.75)     # chance of crossing (taker)
        self.skip_chance = rnd.uniform(0.05, 0.20)    # chance to pass on a signal
        if skip_chance is not None:
            self.skip_chance = float(skip_chance)
        self.market_pool = rnd.sample(MARKETS, rnd.choice([1, 1, 2, 3]))
        self.partial_bias = rnd.uniform(0.1, 0.4)     # chance to close in two pieces
        self.rnd = random.Random()

    # ---------- per-cycle decisions ----------
    def size(self, base):
        return round(base * self.size_mult * self.rnd.uniform(0.85, 1.15), 6)

    def slip(self, base):
        return base * self.rnd.uniform(0.4, 2.6)

    def gate(self, base_pct):
        return max(0.002, base_pct * self.rnd.uniform(0.6, 1.5))

    def pause(self, base):
        p = base * self.pace_mult * self.rnd.uniform(0.5, 2.0)
        if self.rnd.random() < 0.12:
            p = self.rnd.uniform(20, 120)          # occasional long idle
        return max(1.0, p)

    def hold(self, base_seconds):
        return max(8, base_seconds * self.hold_mult * self.rnd.uniform(0.6, 1.4))

    def skip(self):
        return self.rnd.random() < self.skip_chance

    def tif(self):
        return "ioc" if self.rnd.random() < self.taker_bias else "gtc"

    def market(self, fallback):
        return self.rnd.choice(self.market_pool) if self.market_pool else fallback

    def close_in_pieces(self):
        return self.rnd.random() < self.partial_bias

    def pre_entry_jitter(self):
        """Human-ish hesitation before clicking buy/sell."""
        time.sleep(self.rnd.uniform(0.3, 3.5))
