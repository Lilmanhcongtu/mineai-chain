"""Deterministic difficulty simulator: pure-Python model of a chain under a hashrate/timestamp scenario.

Block solve time is exponential with mean D / hashrate (a memoryless proof-of-work search).
No real hashing happens, so thousands of blocks simulate in milliseconds.
"""
from __future__ import annotations

import dataclasses
import random
from dataclasses import dataclass, field

from mineai import consensus as C
from mineai.config import DEVNET

T = 60


def sim_params(d0: int = 60_000, **kw):
    return dataclasses.replace(DEVNET, difficulty=d0, min_difficulty=kw.pop("min_difficulty", 1), **kw)


@dataclass
class Sim:
    params: object
    rng: random.Random
    chain: list = field(default_factory=list)       # (timestamp, difficulty), genesis first
    real: list = field(default_factory=list)        # true (unmanipulated) time each block was found
    now: float = 0.0

    def __post_init__(self):
        g = self.params.genesis_timestamp
        self.chain = [(g, 0)]
        self.real = [float(g)]
        self.now = float(g)

    def expected(self) -> int:
        p = self.params
        if len(self.chain) == 1:
            return p.difficulty
        return C.next_difficulty(p, self.chain[-(p.lwma_window + C.TS_FILTER):])

    def step(self, hashrate: float, ts_fn=None, extra_delay: float = 0.0) -> float:
        """Mine one block at `hashrate` (H/s). Returns the true solve time."""
        d = self.expected()
        solve = self.rng.expovariate(hashrate / d) + extra_delay
        self.now += solve
        ts = int(self.now) if ts_fn is None else ts_fn(self.now, self)
        self.chain.append((ts, d))
        self.real.append(self.now)
        return solve

    # ---- measurements
    def difficulties(self, a: int, b: int):
        return [d for _, d in self.chain[a:b]]

    def mean_spacing(self, a: int, b: int) -> float:
        """Average TRUE seconds per block over blocks a..b-1 (indexes into chain)."""
        return (self.real[b - 1] - self.real[a - 1]) / (b - a)


def run(params, hashrate_fn, n: int, seed: int = 1, ts_fn=None) -> Sim:
    sim = Sim(params, random.Random(seed))
    for i in range(1, n + 1):
        sim.step(hashrate_fn(i, sim), ts_fn)
    return sim
