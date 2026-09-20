"""SANDBOX profile: the only network with a pre-allocated genesis. Every other profile must stay premine-free."""
from __future__ import annotations

import dataclasses

import pytest

from mineai import consensus as C
from mineai.blockchain import Blockchain, ChainError
from mineai.config import DEVNET, MAINNET, PRIVNET, PROFILES, SANDBOX, TESTNET, _mai, consensus_fingerprint
from tests.helpers import Acct, Clock, mine_n

ALLOC_ADDRESS, ALLOC_AMOUNT = SANDBOX.genesis_allocations[0]
# Same rules, trivial proof of work, no retargeting: only for mining a few blocks in a test.
FAST = dataclasses.replace(SANDBOX, difficulty=16, min_difficulty=1, dynamic_difficulty=False, genesis_hash="")


def test_only_the_sandbox_has_a_genesis_allocation():
    assert [p.name for p in PROFILES.values() if p.genesis_allocations] == ["sandbox"]
    for p in (DEVNET, TESTNET, PRIVNET, MAINNET):
        assert C.genesis_supply(p) == 0 and C.genesis_block(p)["merkle_root"] == C.ZERO_HASH   # tokenomics: no premine


def test_the_sandbox_allocates_twenty_million_mai_to_one_sandbox_address():
    assert SANDBOX.genesis_allocations == ((ALLOC_ADDRESS, _mai(20_000_000)),)
    assert ALLOC_ADDRESS.startswith("SMAI") and C.genesis_supply(SANDBOX) == _mai(20_000_000) < SANDBOX.max_supply


def test_the_sandbox_genesis_hash_is_pinned_and_committed_to_the_allocation():
    assert C.genesis_block(SANDBOX)["hash"] == SANDBOX.genesis_hash
    other = dataclasses.replace(SANDBOX, genesis_allocations=((ALLOC_ADDRESS, _mai(20_000_001)),))
    assert C.genesis_block(other)["hash"] != SANDBOX.genesis_hash                 # a different balance = a different chain
    assert consensus_fingerprint(other) != consensus_fingerprint(SANDBOX)
    assert SANDBOX.genesis_hash not in {p.genesis_hash for p in (DEVNET, TESTNET, PRIVNET)}


def test_the_chain_starts_with_the_allocation_as_minted_supply(tmp_path):
    chain = Blockchain(tmp_path / "c.db", SANDBOX, clock=Clock())
    assert chain.storage.get_account(ALLOC_ADDRESS) == (ALLOC_AMOUNT, 0)
    assert chain.minted_supply() == ALLOC_AMOUNT == chain.storage.total_balances()
    chain.close()
    chain = Blockchain(tmp_path / "c.db", SANDBOX, clock=Clock())                # reopening re-checks the invariant
    assert chain.minted_supply() == ALLOC_AMOUNT
    chain.close()


def test_mining_on_top_of_the_allocation_keeps_every_invariant(tmp_path):
    chain = Blockchain(tmp_path / "c.db", FAST, clock=Clock())
    miner = Acct(FAST)
    mine_n(chain, miner, 4)
    assert chain.minted_supply() == ALLOC_AMOUNT + 4 * FAST.block_reward
    assert chain.storage.total_balances() == chain.minted_supply()
    chain.verify_integrity()                                                      # full replay, including genesis balances
    chain.close()


def test_the_supply_cap_counts_the_allocation(tmp_path):
    tight = dataclasses.replace(FAST, max_supply=ALLOC_AMOUNT + _mai(30))        # room for one full and one partial reward
    chain = Blockchain(tmp_path / "c.db", tight, clock=Clock())
    miner = Acct(tight)
    mine_n(chain, miner, 4)
    assert chain.minted_supply() == tight.max_supply                              # capped, never above
    assert chain.next_subsidy() == 0
    chain.verify_integrity()
    chain.close()


def test_a_database_with_a_different_genesis_is_refused(tmp_path):
    Blockchain(tmp_path / "c.db", FAST, clock=Clock()).close()
    other = dataclasses.replace(FAST, genesis_allocations=((ALLOC_ADDRESS, _mai(1)),))
    with pytest.raises(ChainError, match="genesis"):
        Blockchain(tmp_path / "c.db", other, clock=Clock())


@pytest.mark.parametrize("allocations, message", [
    (((ALLOC_ADDRESS, 0),), "positive"),
    (((ALLOC_ADDRESS, 1), (ALLOC_ADDRESS, 2)), "unique"),
    ((("DMAI" + "A" * 20, 1),), "prefix"),
    (((ALLOC_ADDRESS, SANDBOX.max_supply + 1),), "exceed"),
])
def test_invalid_allocations_are_rejected_at_definition(allocations, message):
    with pytest.raises(ValueError, match=message):
        dataclasses.replace(SANDBOX, genesis_allocations=allocations)
