"""Serialization, hashing, signing, address and amount rules (unit level)."""
from __future__ import annotations

import base64
import hashlib
import struct

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mineai import consensus as C
from mineai.config import DEVNET, TESTNET, get_params
from mineai.crypto import (
    address_from_public_key, b64d_strict, compute_txid, public_key_bytes, sign_transaction,
    validate_address, verify_transaction_signature,
)
from mineai.util import atomic_to_mai, mai_to_atomic

D = DEVNET
KEY_A = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
KEY_B = Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))
ADDR_A = "DMAIKZDVVJ2UMNDUYAUF35O36K6KW462MUJVA4GPVRA"
ADDR_B = "DMAIMW3AM46W5WEEX4A4FQRC3AVNUB2A6KNMQPVQFYA"


def golden_tx():
    return sign_transaction(KEY_A, {"recipient": ADDR_B, "amount": 5_000_000, "fee": 10_000,
                                    "nonce": 1, "timestamp": 1_800_000_000}, D.network_id, D.address_prefix)


# ---------------------------------------------------------------- golden vectors (regression guard)
def test_golden_addresses():
    assert address_from_public_key(public_key_bytes(KEY_A), "DMAI") == ADDR_A
    assert address_from_public_key(public_key_bytes(KEY_B), "DMAI") == ADDR_B


def test_golden_txid_and_signature_are_stable():
    tx = golden_tx()
    assert tx["txid"] == "716fd9667ac4fa2137b172c88e9e5f6497d5743389f58f9362d4d1094fa6381a"
    assert tx["signature"].startswith("atQdp0e973mpXyJDwuW4WNaLt-DI")     # Ed25519 is deterministic


def test_golden_coinbase_and_merkle():
    cb = C.coinbase_tx(D, 1, ADDR_A, 25_000_000, 10_000)
    assert cb["txid"] == "317b06ddb9983ac6a95aa8b49d834c43f4215912b9e47df1d42e644db7e54aaf"
    assert C.merkle_root([cb["txid"], golden_tx()["txid"]]) == \
        "1158acad1b649ae284191ba20919ab73164b7431b680afed08d740fbd0e31bba"


def test_devnet_genesis_hash_is_pinned():
    assert C.genesis_block(D)["hash"] == D.genesis_hash


# ---------------------------------------------------------------- independent re-implementation of PROTOCOL.md
def test_signing_payload_matches_the_written_spec():
    tx = golden_tx()
    net, snd, rcp = D.network_id.encode(), tx["sender"].encode(), tx["recipient"].encode()
    expected = (b"MineAI/tx/v1\x00" + bytes([len(net)]) + net + bytes([len(snd)]) + snd
                + bytes([len(rcp)]) + rcp + struct.pack(">QQQQ", 5_000_000, 10_000, 1, 1_800_000_000)
                + b64d_strict(tx["public_key"], 32))
    from mineai.crypto import transaction_signing_payload
    assert transaction_signing_payload(tx, D.network_id) == expected
    assert tx["txid"] == hashlib.sha256(expected + b64d_strict(tx["signature"], 64)).hexdigest()


def test_block_hash_matches_the_written_spec():
    g = C.genesis_block(D)
    net = D.network_id.encode()
    header = (b"MineAI/block/v1\x00" + bytes([len(net)]) + net + struct.pack(">Q", 0)
              + bytes(32) + bytes(32) + struct.pack(">Q", D.genesis_timestamp) + struct.pack(">Q", 0)
              + struct.pack(">Q", 0))
    assert g["hash"] == hashlib.sha256(header).hexdigest()


def test_merkle_root_rules():
    assert C.merkle_root([]) == "0" * 64
    a, b, c = (hashlib.sha256(bytes([i])).hexdigest() for i in range(3))
    leaf = lambda h: hashlib.sha256(b"\x00" + bytes.fromhex(h)).digest()
    node = lambda l, r: hashlib.sha256(b"\x01" + l + r).digest()
    assert C.merkle_root([a]) == leaf(a).hex()
    assert C.merkle_root([a, b]) == node(leaf(a), leaf(b)).hex()
    assert C.merkle_root([a, b, c]) == node(node(leaf(a), leaf(b)), leaf(c)).hex()   # odd node promoted
    assert C.merkle_root([a, b, c]) != C.merkle_root([a, b, c, c])                    # no duplication trick
    assert C.merkle_root([a, b]) != C.merkle_root([b, a])


def test_serialization_is_deterministic():
    assert golden_tx() == golden_tx()
    g1, g2 = C.genesis_block(D), C.genesis_block(D)
    assert g1 == g2


# ---------------------------------------------------------------- signatures
def test_signature_verifies_and_detects_every_field_change():
    tx = golden_tx()
    assert verify_transaction_signature(tx, D.network_id, D.address_prefix)
    for field, value in [("amount", 5_000_001), ("fee", 10_001), ("nonce", 2), ("timestamp", 1),
                         ("recipient", ADDR_A)]:
        assert not verify_transaction_signature({**tx, field: value}, D.network_id, D.address_prefix), field


def test_signature_is_bound_to_the_network():
    tx = golden_tx()
    assert not verify_transaction_signature(tx, "mineai-testnet-v1", D.address_prefix)


def test_txid_depends_on_signature_and_network():
    tx = golden_tx()
    assert compute_txid(tx, D.network_id) == tx["txid"]
    assert compute_txid(tx, "another-network") != tx["txid"]


def test_noncanonical_base64_is_rejected():
    tx = golden_tx()
    padded_variant = tx["public_key"].rstrip("=") + "="  # wrong length after decode or non-canonical
    with pytest.raises(ValueError):
        b64d_strict(padded_variant + "AA", 32)
    with pytest.raises(ValueError):
        b64d_strict(tx["signature"], 32)                 # wrong length
    with pytest.raises(ValueError):
        b64d_strict("é" * 44, 32)


# ---------------------------------------------------------------- addresses
def test_address_roundtrip_and_prefix_separation():
    assert validate_address(ADDR_A, "DMAI")
    assert not validate_address(ADDR_A, "TMAI")            # devnet address is not a testnet address
    assert not validate_address(ADDR_A, "MAI")


@pytest.mark.parametrize("bad", [
    "", "DMAI", ADDR_A.lower(), ADDR_A[:-1], ADDR_A + "A", ADDR_A[:-1] + "B", ADDR_A.replace("K", "1", 1),
    " " + ADDR_A, ADDR_A + " ", ADDR_A + "\n", None, 5, "DMAI" + "A" * 39, ADDR_A.replace("D", "Ｄ", 1),
])
def test_invalid_addresses_rejected(bad):
    assert not validate_address(bad, "DMAI")


def test_lowercase_alias_cannot_create_a_second_account():
    assert not validate_address(ADDR_A.lower(), "DMAI")
    assert not validate_address("dmai" + ADDR_A[4:], "DMAI")


# ---------------------------------------------------------------- amounts
def test_atomic_conversion_is_exact():
    assert mai_to_atomic("25") == 25_000_000
    assert mai_to_atomic("0.000001") == 1
    assert mai_to_atomic("0.001") == 1_000
    assert mai_to_atomic(5) == 5_000_000
    assert atomic_to_mai(25_000_000) == "25"
    assert atomic_to_mai(1) == "0.000001"
    assert atomic_to_mai(1_234_500) == "1.2345"
    assert atomic_to_mai(0) == "0"
    assert mai_to_atomic("0.1") + mai_to_atomic("0.2") == mai_to_atomic("0.3")     # floats would fail here


@pytest.mark.parametrize("bad", ["0", "-1", "0.0000001", "abc", "", "NaN", "Infinity", "1e400",
                                 "9999999999999999999999", 0.1, True, None,
                                 "1e3", "1E3", "1e-6", "1_0", "+5", ".5", "5.", "0x10", "5 0", "٥", "1,5", "--1", "1.2.3",
                                 "0.0", "00.000000"])
def test_bad_amounts_rejected(bad):
    with pytest.raises((ValueError, TypeError)):
        mai_to_atomic(bad)


def test_amount_parsing_accepts_only_plain_decimals():
    assert mai_to_atomic(" 5 ") == 5_000_000                        # surrounding whitespace is tolerated
    assert mai_to_atomic("007") == 7_000_000 and mai_to_atomic("0.5") == 500_000 and mai_to_atomic("12.345678") == 12_345_678
    assert mai_to_atomic("9223372036854.775807") == 9_223_372_036_854_775_807      # the largest representable amount
    with pytest.raises(ValueError):
        mai_to_atomic("9223372036854.775808")                       # one atomic unit too many


def test_max_supply_fits_in_consensus_integer_range():
    from mineai.config import INT_MAX
    assert D.max_supply == 100_000_000 * 10 ** 6 <= INT_MAX


# ---------------------------------------------------------------- economics / profiles
def test_subsidy_is_clipped_at_the_cap():
    assert C.subsidy_for(0, D) == 25_000_000
    assert C.subsidy_for(D.max_supply - 1, D) == 1
    assert C.subsidy_for(D.max_supply, D) == 0
    assert C.subsidy_for(D.max_supply + 5, D) == 0        # never negative


def test_parameters_match_the_documented_economics():
    assert (D.block_reward, D.max_supply, D.min_fee, D.default_fee) == (
        25_000_000, 100_000_000_000_000, 1_000, 10_000)


def test_profiles_are_separated():
    assert D.network_id != TESTNET.network_id and D.address_prefix != TESTNET.address_prefix
    assert D.default_port != TESTNET.default_port


def test_mainnet_is_disabled_and_unknown_profiles_rejected():
    with pytest.raises(ValueError, match="disabled"):
        get_params("mainnet")
    with pytest.raises(ValueError, match="unknown"):
        get_params("nope")


def test_work_equals_difficulty_and_target_shrinks_with_it():
    assert C.block_work(1) == 1 and C.block_work(65536) == 65536
    assert C.target_for(1) == 2 ** 256 - 1                        # difficulty 1 accepts every hash
    assert C.target_for(2) == (2 ** 256 - 1) // 2
    assert C.target_for(65536) < C.target_for(4096) < C.target_for(16)
    hi = "f" * 64                                                 # the largest possible hash
    assert C.meets_target(hi, 1) and not C.meets_target(hi, 2)
    assert C.meets_target("0" * 64, 2 ** 62)                      # the smallest hash always qualifies
    edge = format(C.target_for(1000), "064x")                     # a hash exactly at the target qualifies...
    assert C.meets_target(edge, 1000) and not C.meets_target(format(int(edge, 16) + 1, "064x"), 1000)
