"""Dynamic difficulty (PROTOCOL.md 5.10): unit tests, simulations, and real-chain integration tests."""
from __future__ import annotations

import dataclasses
import random
import statistics

import pytest

from mineai import consensus as C
from mineai.blockchain import Blockchain
from mineai.config import DEVNET
from mineai.consensus import ValidationError
from mineai.storage import WIRE_KEYS
from tests.difficulty_sim import Sim, run, sim_params
from tests.helpers import Acct, Clock, build, make_chain, mine

T = 60
H = 1000.0                                   # simulated hashrate (hashes/second); equilibrium D = H * T = 60 000
EQ = int(H * T)


def avg(values):
    return sum(values) / len(values)


def steps_ok(ds):
    """Per-block change limit, exactly as specified: parent//2 <= child <= 2*parent."""
    return all(a // 2 <= b <= 2 * a for a, b in zip(ds, ds[1:]))


def history(spacings, difficulty=60_000, start=1_800_000_000, first_gap=None):
    """Chain history (oldest first) starting from a genesis entry, with the given solve times."""
    ts, out = start, [(start, 0)]
    for gap in spacings:
        ts += gap
        out.append((ts, difficulty))
    return out


P = sim_params(60_000)
NEED = P.lwma_window + C.TS_FILTER              # history entries the algorithm looks at


# ====================================================================== pure function: boundaries
def test_the_first_blocks_use_the_initial_difficulty():
    assert C.next_difficulty(P, []) == 60_000
    assert C.next_difficulty(P, [(1_800_000_000, 0)]) == 60_000                       # child of genesis
    fast = history([1] * 10)
    for parent_height in range(0, C.TS_FILTER):                                       # heights 0..4: not enough history yet
        assert C.next_difficulty(P, fast[: parent_height + 1]) == 60_000, parent_height
    assert C.next_difficulty(P, fast[: C.TS_FILTER + 1]) > 60_000                       # from height 5 on it reacts


@pytest.mark.parametrize("blocks", [1, 4, 5, 6, 7, 10, 59, 60, 61, 100])
def test_constant_hashrate_is_an_exact_fixed_point(blocks):
    """Blocks exactly on target with equal difficulty: the next difficulty is unchanged (integer-exact)."""
    hist = history([T] * blocks)[-NEED:]
    assert C.next_difficulty(P, hist) == 60_000


def test_faster_blocks_raise_and_slower_blocks_lower_the_difficulty():
    faster = C.next_difficulty(P, history([30] * 100)[-NEED:])
    slower = C.next_difficulty(P, history([120] * 100)[-NEED:])
    assert faster > 60_000 > slower
    assert abs(faster - 120_000) <= 2 and abs(slower - 30_000) <= 2          # ~ proportional to the speed error


def test_change_per_block_is_limited_to_a_factor_of_two():
    assert C.next_difficulty(P, history([1] * 100)[-NEED:]) == 120_000          # absurdly fast: capped at 2x
    assert C.next_difficulty(P, history([10 ** 6] * 100)[-NEED:]) == 30_000     # absurdly slow: capped at /2
    sim = run(sim_params(60_000), lambda i, s: 1000 * H if i < 50 else H / 1000, 200, seed=5)
    ds = [d for _, d in sim.chain[1:]]
    assert steps_ok(ds)


def test_a_late_step_is_capped_at_six_targets_and_an_isolated_outlier_is_ignored():
    base = history([T] * 100)

    def with_delay(delay, blocks=3):
        h = list(base)
        for k in range(1, blocks + 1):                                       # the last `blocks` blocks all arrive `delay` late
            h[-k] = (h[-k][0] + delay, 60_000)
        return C.next_difficulty(P, h[-NEED:])
    ten_targets, a_million_seconds = with_delay(10 * T), with_delay(1_000_000)
    assert ten_targets == a_million_seconds                                   # the solve time is capped: no further effect
    assert ten_targets < 60_000                                               # but a real slowdown is noticed
    assert with_delay(10 * T, blocks=1) == C.next_difficulty(P, base[-NEED:])   # one isolated late block is filtered out


def test_difficulty_never_drops_below_the_minimum_or_below_one():
    floor = sim_params(1_000, min_difficulty=900)
    assert C.next_difficulty(floor, history([10 ** 6] * 100, difficulty=1_000)[-NEED:]) == 900
    tiny = sim_params(1, min_difficulty=1)
    assert C.next_difficulty(tiny, history([10 ** 6] * 100, difficulty=1)[-NEED:]) == 1
    assert C.next_difficulty(tiny, history([1] * 100, difficulty=1)[-NEED:]) == 2


def test_difficulty_never_exceeds_the_maximum():
    top = C.MAX_DIFFICULTY
    assert C.next_difficulty(P, history([1] * 100, difficulty=top)[-NEED:]) == top
    assert C.next_difficulty(P, history([1] * 100, difficulty=top - 1)[-NEED:]) == top


def test_rounding_is_integer_floor_and_stable_for_tiny_windows():
    hist = history([61] * 6, difficulty=1000)                                # 7 entries -> a window of 2 blocks, a second slow each
    assert C.next_difficulty(sim_params(1000, min_difficulty=1), hist) == (2 * 1000 * 3 * 60) // (2 * (61 + 2 * 61))
    assert C.next_difficulty(sim_params(1000, min_difficulty=1), hist) == 983
    for d in (1, 2, 3, 7, 999, 1001):
        out = C.next_difficulty(sim_params(d, min_difficulty=1), history([59, 61, 60, 58, 62, 60, 61], difficulty=d))
        assert isinstance(out, int) and out >= 1


def test_identical_and_backwards_timestamps_do_not_break_the_math():
    same = [(1_800_000_000, 0)] + [(1_800_000_100, 60_000)] * 70             # every block stamped identically
    out = C.next_difficulty(P, same[-NEED:])
    assert out == 120_000                                                    # treated as 1 s solve times: capped 2x
    backwards = [(1_800_000_000, 0)] + [(1_800_100_000 - 10 * i, 60_000) for i in range(70)]
    assert C.next_difficulty(P, backwards[-NEED:]) == 120_000                  # still finite, deterministic


def test_function_is_pure_and_deterministic():
    hist = history([random.Random(3).randint(1, 200) for _ in range(80)])[-NEED:]
    frozen = list(hist)
    results = {C.next_difficulty(P, hist) for _ in range(5)}
    assert len(results) == 1 and hist == frozen                              # no mutation, no hidden state


def test_extra_older_history_changes_nothing():
    rng = random.Random(9)
    full = history([rng.randint(20, 150) for _ in range(300)])
    need = NEED
    assert C.next_difficulty(P, full[-need:]) == C.next_difficulty(P, full[-need - 40:]) == C.next_difficulty(P, full[-need - 200:])


def test_median_filter_definition():
    assert C._filtered([5], 0) == 5
    assert C._filtered([5, 9], 1) == 9                                       # even count: upper median
    assert C._filtered([1, 100, 3, 4, 5], 4) == 4                            # outlier ignored
    assert C._filtered(list(range(100)), 50) == 48                           # window is the 5 stamps 46..50


def test_an_isolated_timestamp_outlier_barely_moves_the_result():
    base = history([T] * 100)
    for position in (-2, -5, -20, -40):
        hacked = list(base)
        ts, d = hacked[position]
        hacked[position] = (ts + 5_000, d)                                   # one block stamped 5000 s in the future
        out = C.next_difficulty(P, hacked[-NEED:])
        assert abs(out - 60_000) / 60_000 < 0.06, position


def test_profile_validation_rejects_bad_difficulty_settings():
    for bad in (dict(min_difficulty=0), dict(min_difficulty=10 ** 9), dict(lwma_window=1), dict(target_spacing=0)):
        with pytest.raises(ValueError):
            dataclasses.replace(DEVNET, **bad)


# ====================================================================== simulations
def test_stable_hashrate_holds_the_60_second_target():
    spacings = [run(P, lambda i, s: H, 700, seed).mean_spacing(100, 701) for seed in range(12)]
    assert 57 <= avg(spacings) <= 64 and all(52 <= x <= 70 for x in spacings)


def test_a_start_far_from_equilibrium_converges_quickly():
    for d0 in (6_000, 600_000):                                              # 10x too easy / 10x too hard
        s = run(sim_params(d0), lambda i, x: H, 400, seed=3)
        ds = [d for _, d in s.chain[1:]]
        assert 0.6 * EQ <= avg(ds[80:400]) <= 1.6 * EQ, d0
        assert 55 <= s.mean_spacing(150, 400) <= 68, d0


def test_ten_times_hashrate_increase():
    results = []
    for seed in range(10):
        s = run(P, lambda i, x: H if i < 200 else 10 * H, 520, seed)
        results.append((s.chain[520][1], s.mean_spacing(200, 231), s.mean_spacing(280, 521)))
    final_d, transient, settled = (avg([r[k] for r in results]) for k in range(3))
    assert 0.88 * 10 * EQ <= final_d <= 1.12 * 10 * EQ          # difficulty follows the hashrate
    assert 5 <= transient <= 30                                  # blocks speed up, but not unboundedly (10x would be 6 s)
    assert 55 <= settled <= 66                                   # and the target is restored within ~2 windows


def test_ninety_percent_hashrate_decrease():
    results = []
    for seed in range(10):
        s = run(P, lambda i, x: H if i < 200 else 0.1 * H, 520, seed)
        results.append((s.chain[520][1], s.mean_spacing(200, 231), s.mean_spacing(320, 521)))
    final_d, transient, settled = (avg([r[k] for r in results]) for k in range(3))
    assert 0.85 * 0.1 * EQ <= final_d <= 1.15 * 0.1 * EQ
    assert transient <= 400                                      # slow while adapting (unavoidable), but bounded
    assert 55 <= settled <= 68


def test_long_network_pause_causes_no_burst_and_recovers():
    rows = []
    for seed in range(10):
        s = Sim(P, random.Random(seed))
        for _ in range(299):
            s.step(H)
        before = s.chain[-1][1]
        s.step(H, extra_delay=86_400)                            # the network was down for a whole day
        resume = s.real[-1]
        for _ in range(400):
            s.step(H)
        dip = min(d for _, d in s.chain[301:316])                # the pause is noticed a couple of blocks later
        in_first_hour = sum(1 for t in s.real[301:] if t - resume <= 3600)
        rows.append((before, dip, in_first_hour, s.mean_spacing(340, 701)))
    before, dip, in_hour, later = (avg([r[k] for r in rows]) for k in range(4))
    assert 0.5 * before <= dip < 0.97 * before                   # difficulty falls, but only modestly (capped solve time)
    assert in_hour <= 90                                         # ~60 blocks in the following hour: no burst of free blocks
    assert 56 <= later <= 65                                     # back on target


def test_repeated_pauses_do_not_derail_the_chain():
    s = Sim(P, random.Random(4))
    for round_ in range(6):
        for _ in range(150):
            s.step(H)
        s.step(H, extra_delay=6 * 3600)
    for _ in range(300):
        s.step(H)
    assert 50 <= s.mean_spacing(len(s.chain) - 250, len(s.chain)) <= 70
    ds = [d for _, d in s.chain[1:]]
    assert steps_ok(ds)


def test_alternating_high_and_low_hashrate_does_not_oscillate_or_diverge():
    rows = []
    for seed in range(10):
        s = run(P, lambda i, x: (3 * H if (i // 30) % 2 == 0 else H / 3), 1200, seed)
        late = [d for _, d in s.chain[600:1201]]
        early = [d for _, d in s.chain[200:600]]
        rows.append((max(late) / min(late), statistics.pstdev([__import__("math").log(d) for d in late]),
                     statistics.pstdev([__import__("math").log(d) for d in early])))
    swing, late_sd, early_sd = (avg([r[k] for r in rows]) for k in range(3))
    assert swing < 6.5                                           # tracks less than the 9x input swing (damped, not amplified)
    assert late_sd < 1.3 * early_sd                              # variance does not grow over time (no divergence)


def test_faster_alternation_is_damped_further():
    slow = avg([statistics.pstdev([__import__("math").log(d) for _, d in
                run(P, lambda i, x, k=60: (3 * H if (i // k) % 2 == 0 else H / 3), 900, seed).chain[300:]])
                for seed in range(6)])
    fast = avg([statistics.pstdev([__import__("math").log(d) for _, d in
                run(P, lambda i, x, k=6: (3 * H if (i // k) % 2 == 0 else H / 3), 900, seed).chain[300:]])
                for seed in range(6)])
    assert fast < slow                                           # rapid flip-flops average out


# ---- timestamp manipulation --------------------------------------------------------------
def median_last(sim, n=11):
    return int(statistics.median([t for t, _ in sim.chain[-n:]]))


def attack(kind, share, seeds=8, blocks=700, offset=300):
    rows = []
    for seed in range(seeds):
        s = Sim(P, random.Random(seed))
        flip = 0
        for _ in range(1, blocks):
            if s.rng.random() >= share:
                s.step(H)
            elif kind == "forward":                                # stamp as far ahead as the network allows
                s.step(H, lambda now, sim: int(now) + offset)
            elif kind == "mtp_low":                                # stamp as early as the network allows
                s.step(H, lambda now, sim: max(median_last(sim) + 1, int(now) - 3600))
            elif kind == "alternate":
                flip ^= 1
                s.step(H, (lambda now, sim: int(now) + offset) if flip else (lambda now, sim: median_last(sim) + 1))
        rows.append((avg([d for _, d in s.chain[200:]]), s.mean_spacing(200, blocks)))
    return avg([r[0] for r in rows]), avg([r[1] for r in rows])


@pytest.mark.parametrize("kind", ["forward", "mtp_low", "alternate"])
@pytest.mark.parametrize("share", [0.1, 0.3, 0.45])
def test_timestamp_manipulation_barely_moves_the_block_time(kind, share):
    honest_d, honest_spacing = attack("none", 0.0)
    d, spacing = attack(kind, share)
    # Up to 30% of the hashrate: within ~13% of the 60 s target. An attacker near 50% can slow blocks by at most
    # ~1.5x (documented in PROTOCOL.md 5.10); it can never make them faster/cheaper.
    assert 55 <= spacing <= (68 if share <= 0.3 else 95)
    assert d >= 0.95 * honest_d                                  # the attacker cannot make blocks meaningfully cheaper


def test_manipulation_cannot_lower_difficulty_for_profit():
    """Whatever timestamp strategy is tried, the average difficulty never falls below the honest level."""
    honest_d, _ = attack("none", 0.0)
    for kind in ("forward", "mtp_low", "alternate"):
        for share in (0.1, 0.3, 0.45):
            assert attack(kind, share, seeds=4)[0] >= 0.95 * honest_d, (kind, share)


def test_a_constant_timestamp_offset_changes_nothing():
    """Only timestamp DIFFERENCES matter, so stamping every block a fixed amount ahead has no lasting effect."""
    plain = run(P, lambda i, s: H, 500, seed=2)
    shifted = Sim(P, random.Random(2))
    for _ in range(500):
        shifted.step(H, lambda now, sim: int(now) + 250)         # every block stamped 250 s ahead of the truth
    plain_d = avg([d for _, d in plain.chain[150:]])
    shifted_d = avg([d for _, d in shifted.chain[150:]])
    assert abs(shifted_d / plain_d - 1) < 0.05
    assert abs(shifted.mean_spacing(150, 501) - plain.mean_spacing(150, 501)) < 4


# ---- extremes and fuzz -------------------------------------------------------------------
def test_sub_second_blocks_are_handled_with_integer_timestamps():
    s = run(P, lambda i, x: 1_000_000 * H, 60, seed=1)           # a million times the hashrate: many blocks per second
    ds = [d for _, d in s.chain[1:]]
    assert ds[:C.TS_FILTER] == [60_000] * C.TS_FILTER            # the first blocks use the initial difficulty
    assert ds[C.TS_FILTER] > ds[0] and steps_ok(ds)              # then it ramps up as fast as allowed
    assert ds[-1] > 100 * ds[0]                                  # geometrically


def test_random_hashrate_walk_keeps_every_invariant():
    rng = random.Random(77)
    level = [H]

    def walk(i, s):
        if i % 25 == 0:
            level[0] = max(1.0, min(1e6, level[0] * rng.choice([0.1, 0.5, 1, 1, 2, 5, 10])))
        return level[0]
    s = run(sim_params(60_000, min_difficulty=50), walk, 3000, seed=8)
    ds = [d for _, d in s.chain[1:]]
    assert min(ds) >= 50 and max(ds) <= C.MAX_DIFFICULTY
    assert steps_ok(ds)
    replay = Sim(sim_params(60_000, min_difficulty=50), random.Random(0))           # recompute from stored history only
    replay.chain = list(s.chain[:1])
    for ts, d in s.chain[1:]:
        assert replay.expected() == d                             # every declared difficulty is reproducible
        replay.chain.append((ts, d))


# ====================================================================== real chain (integration)
DYN = dataclasses.replace(DEVNET, difficulty=64, min_difficulty=16, coinbase_maturity=2)
G0 = DEVNET.genesis_timestamp


def dyn_chain(tmp_path, name="d.db", start=G0 + 60):
    return make_chain(tmp_path, DYN, Clock(start), name=name)


def spaced(chain, miner, gap, n=1):
    out = []
    for _ in range(n):
        chain.clock.advance(gap)
        out.append({k: v for k, v in mine(chain, miner).items() if k in WIRE_KEYS})
    return out


def independent_expectation(chain):
    """Recompute the required difficulty straight from the stored blocks, without the chain's own helper."""
    tip = chain.tip()
    if tip["height"] == 0:
        return DYN.difficulty
    need = DYN.lwma_window + C.TS_FILTER - 1
    lo = max(0, tip["height"] - need + 1)
    rows = [chain.storage.get_block_by_height(h) for h in range(lo, tip["height"] + 1)]
    return C.next_difficulty(DYN, [(b["timestamp"], b["difficulty"]) for b in rows])


def test_template_difficulty_follows_the_algorithm_and_wrong_difficulty_is_rejected(tmp_path):
    chain = dyn_chain(tmp_path)
    alice = Acct(DYN)
    assert chain.mining_template(alice.address)["difficulty"] == 64            # child of genesis: initial difficulty
    spaced(chain, alice, 60, 20)
    for gap, n in ((60, 6), (2, 16)):                                          # on target, then far too fast
        for _ in range(n):
            expected = independent_expectation(chain)
            assert chain.mining_template(alice.address)["difficulty"] == expected == chain.expected_difficulty(chain.tip())
            spaced(chain, alice, gap)
    assert chain.tip()["difficulty"] > 64 * 1.3                                # fast blocks made it harder
    easy = build(chain, alice, lambda b: b.update(difficulty=64))              # mined at the old, too-easy difficulty
    with pytest.raises(ValidationError) as exc:
        chain.submit_mined_block(easy)
    assert exc.value.code == "bad_difficulty"
    hard = build(chain, alice, lambda b: b.update(difficulty=chain.expected_difficulty(chain.tip()) * 2))
    with pytest.raises(ValidationError) as exc:
        chain.submit_mined_block(hard)
    assert exc.value.code == "bad_difficulty"
    chain.verify_integrity()


def test_slow_blocks_lower_the_difficulty_on_a_real_chain(tmp_path):
    chain = dyn_chain(tmp_path)
    alice = Acct(DYN)
    spaced(chain, alice, 60, 20)
    start = chain.tip()["difficulty"]
    spaced(chain, alice, 400, 12)
    assert chain.expected_difficulty(chain.tip()) < start and chain.expected_difficulty(chain.tip()) >= DYN.min_difficulty
    chain.verify_integrity()


def test_each_branch_is_judged_by_its_own_history(tmp_path):
    from tests.consensus.test_forks import clone
    n = dyn_chain(tmp_path, "n.db")
    alice = Acct(DYN)
    spaced(n, alice, 60, 14)
    fast, slow = clone(n, tmp_path, "f.db"), clone(n, tmp_path, "s.db")
    fast_blocks = spaced(fast, Acct(DYN), 2, 5)                                # a branch of quickly stamped blocks
    slow_blocks = spaced(slow, Acct(DYN), 90, 5)                               # a branch of slowly stamped blocks
    assert fast.expected_difficulty(fast.tip()) > slow.expected_difficulty(slow.tip())
    for b in fast_blocks + slow_blocks:
        n.clock.t = max(n.clock.t, b["timestamp"])
        n.process_block(b)                                                     # each branch validates under its own ancestry
    assert n.storage.side_get(fast_blocks[-1]["hash"]) or n.storage.is_main(fast_blocks[-1]["hash"])
    assert n.storage.side_get(slow_blocks[-1]["hash"]) or n.storage.is_main(slow_blocks[-1]["hash"])
    # a child of the SLOW branch that declares the FAST branch's difficulty is invalid there
    wrong = build(slow, Acct(DYN), lambda b: b.update(difficulty=fast.expected_difficulty(fast.tip())))
    with pytest.raises(ValidationError) as exc:
        n.process_block({k: wrong[k] for k in WIRE_KEYS})
    assert exc.value.code == "bad_difficulty"
    n.verify_integrity()


def test_a_shorter_chain_with_more_real_work_wins_under_dynamic_difficulty(tmp_path):
    """Real retargeting, no test hooks: a slow branch gets EASIER blocks, a fast branch gets HARDER ones, so a
    branch with fewer blocks can carry more cumulative work and must win."""
    from tests.consensus.test_forks import clone
    n = dyn_chain(tmp_path, "n.db")
    alice = Acct(DYN)
    spaced(n, alice, 60, 14)
    base_height = n.tip()["height"]
    slow_branch = clone(n, tmp_path, "m.db")
    hard_branch = clone(n, tmp_path, "h.db")
    spaced(slow_branch, Acct(DYN), 150, 34)                                    # 34 blocks, each stamped 150 s after the last
    hard = spaced(hard_branch, Acct(DYN), 20, 30)                              # 30 blocks, each stamped 20 s after the last
    assert hard_branch.tip()["height"] < slow_branch.tip()["height"]           # the hard branch is SHORTER...
    assert hard_branch.tip_work() > slow_branch.tip_work() > n.tip_work()      # ...but carries more work
    assert hard_branch.tip()["difficulty"] > 64 > slow_branch.tip()["difficulty"]
    for b in slow_branch.storage.blocks_after(base_height, 40):
        n.clock.t = max(n.clock.t, b["timestamp"])
        n.process_block({k: b[k] for k in WIRE_KEYS})
    assert n.tip()["hash"] == slow_branch.tip()["hash"] and n.reorg_count == 0
    for b in hard:
        n.clock.t = max(n.clock.t, b["timestamp"])
        n.process_block(b)
    assert n.tip()["hash"] == hard_branch.tip()["hash"]                        # fewer blocks, more work: reorganized
    assert n.tip()["height"] == base_height + 30 < base_height + 34
    assert n.reorg_count == 1
    n.verify_integrity()
    assert n.storage.total_balances() == n.minted_supply()


def test_timestamp_beyond_the_future_limit_is_rejected(tmp_path):
    chain = dyn_chain(tmp_path)
    alice = Acct(DYN)
    limit = chain.now() + DYN.max_future_seconds
    assert DYN.max_future_seconds == 300
    with pytest.raises(ValidationError) as exc:
        chain.submit_mined_block(build(chain, alice, lambda b: b.update(timestamp=limit + 1)))
    assert exc.value.code == "bad_timestamp"
    chain.submit_mined_block(build(chain, alice, lambda b: b.update(timestamp=limit)))


def test_difficulty_survives_restart_and_tampering_is_detected(tmp_path):
    chain = dyn_chain(tmp_path)
    alice = Acct(DYN)
    spaced(chain, alice, 60, 15)
    spaced(chain, alice, 3, 4)
    expected = chain.expected_difficulty(chain.tip())
    chain.close()
    reopened = Blockchain(tmp_path / "d.db", DYN, clock=Clock(G0 + 10_000))
    assert reopened.expected_difficulty(reopened.tip()) == expected
    reopened.verify_integrity()
    reopened.close()
    import sqlite3
    raw = sqlite3.connect(tmp_path / "d.db")
    raw.execute("UPDATE blocks SET difficulty = difficulty + 1 WHERE height = 9")
    raw.commit()
    raw.close()
    from mineai.blockchain import ChainError
    with pytest.raises(ChainError):
        Blockchain(tmp_path / "d.db", DYN, clock=Clock(G0 + 10_000)).verify_integrity()


def test_status_api_reports_the_current_difficulty(tmp_path):
    from fastapi.testclient import TestClient
    from mineai.node import create_app
    chain = dyn_chain(tmp_path)
    spaced(chain, Acct(DYN), 60, 16)
    spaced(chain, Acct(DYN), 2, 3)
    client = TestClient(create_app(chain, local_hosts=frozenset({"testclient"}), rate_limit_per_minute=10_000))
    st = client.get("/api/status").json()
    assert st["dynamic_difficulty"] is True and st["target_spacing"] == 60
    assert st["difficulty"] == chain.expected_difficulty(chain.tip()) and st["difficulty"] > 64
    assert st["total_work"] == str(chain.tip_work())
