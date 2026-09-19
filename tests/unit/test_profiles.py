"""Network profiles: separation guarantees between devnet, testnet, privnet and mainnet."""
from __future__ import annotations

import dataclasses

import pytest

from mineai import consensus as C
from mineai.config import DEVNET, MAINNET, PRIVNET, PROFILES, TESTNET, get_params

ALL = [DEVNET, TESTNET, PRIVNET, MAINNET]


def test_every_profile_is_registered_and_names_match():
    assert set(PROFILES) == {"devnet", "testnet", "privnet", "mainnet"}
    assert all(PROFILES[p.name] is p for p in ALL)


def test_profiles_are_pairwise_separated():
    for field in ("network_id", "address_prefix", "default_port", "default_p2p_port", "label"):
        values = [getattr(p, field) for p in ALL]
        assert len(set(values)) == len(values), field
    ports = [p.default_port for p in ALL] + [p.default_p2p_port for p in ALL]
    assert len(set(ports)) == len(ports)                                  # no two services share a default port


def test_devnet_and_privnet_genesis_hashes_are_pinned_and_distinct():
    assert C.genesis_block(PRIVNET)["hash"] == PRIVNET.genesis_hash and PRIVNET.genesis_hash != DEVNET.genesis_hash
    assert C.genesis_block(DEVNET)["hash"] == DEVNET.genesis_hash


def test_privnet_is_a_fast_private_test_profile_and_says_so():
    p = get_params("privnet")
    assert p.target_spacing == 5 and p.dynamic_difficulty and p.address_prefix == "PMAI"
    assert "PRIVATE" in p.label and "no monetary value" in p.label
    assert p.min_difficulty <= p.difficulty and p.coinbase_maturity == 3


def test_a_profile_cannot_be_disabled_by_accident():
    assert get_params("devnet") is DEVNET and get_params("privnet") is PRIVNET and get_params("testnet") is TESTNET
    with pytest.raises(ValueError, match="disabled"):
        get_params("mainnet")
    assert not MAINNET.enabled


def test_economics_are_identical_across_profiles():
    """Only the network-specific parameters differ; monetary policy must never depend on the profile."""
    for p in (TESTNET, PRIVNET, MAINNET):
        assert (p.block_reward, p.max_supply, p.min_fee, p.default_fee) == (
            DEVNET.block_reward, DEVNET.max_supply, DEVNET.min_fee, DEVNET.default_fee)


def test_addresses_do_not_validate_across_profiles():
    from mineai.crypto import address_from_public_key, validate_address
    pub = bytes(range(32))
    addresses = {p.name: address_from_public_key(pub, p.address_prefix) for p in ALL}
    for a in ALL:
        for b in ALL:
            assert validate_address(addresses[a.name], b.address_prefix) == (a is b), (a.name, b.name)
