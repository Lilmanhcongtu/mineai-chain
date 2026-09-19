"""Wallet file security and the logging safety rail."""
from __future__ import annotations

import json
import logging
import os

import pytest

from mineai import log as mlog
from mineai import wallet
from mineai.crypto import (
    KDF_N, WALLET_FORMAT, WrongPasswordError, decrypt_private_key, encrypt_private_key, private_key_bytes,
    wallet_aad,
)
from tests.helpers import TEST

PASSWORD = "correct horse battery"
FAST = 2 ** 12          # fast KDF for tests; production default is 2**17


def make_wallet(tmp_path, name="w.wallet.json", password=PASSWORD):
    path = tmp_path / name
    address = wallet.create_wallet_file(path, password, TEST, kdf_n=FAST)
    return path, address


def test_create_and_unlock_roundtrip(tmp_path):
    path, address = make_wallet(tmp_path)
    data = wallet.load_wallet(path, TEST)
    assert data["address"] == address and data["format"] == WALLET_FORMAT and data["network"] == "devnet"
    key = wallet.unlock(data, TEST, PASSWORD)
    from mineai.crypto import address_from_public_key, public_key_bytes
    assert address_from_public_key(public_key_bytes(key), TEST.address_prefix) == address


def test_wallet_file_contains_no_plaintext_secret(tmp_path):
    path, address = make_wallet(tmp_path)
    data = wallet.load_wallet(path, TEST)
    key = wallet.unlock(data, TEST, PASSWORD)
    raw = private_key_bytes(key)
    text = path.read_bytes()
    import base64
    assert raw not in text and base64.urlsafe_b64encode(raw) not in text and raw.hex().encode() not in text
    assert PASSWORD.encode() not in text


def test_existing_wallet_is_never_overwritten(tmp_path):
    path, _ = make_wallet(tmp_path)
    original = path.read_bytes()
    with pytest.raises(SystemExit, match="Refusing to overwrite"):
        wallet.create_wallet_file(path, "another password!", TEST, kdf_n=FAST)
    assert path.read_bytes() == original


def test_wrong_password_is_a_clean_error(tmp_path):
    path, _ = make_wallet(tmp_path)
    with pytest.raises(SystemExit, match="wrong password"):
        wallet.unlock(wallet.load_wallet(path, TEST), TEST, "not the password")


def test_short_passwords_rejected(tmp_path):
    with pytest.raises(ValueError, match="at least"):
        wallet.create_wallet_file(tmp_path / "x.json", "short", TEST, kdf_n=FAST)
    assert not (tmp_path / "x.json").exists()


def test_each_wallet_gets_unique_salt_nonce_and_key(tmp_path):
    a, b = make_wallet(tmp_path, "a.json")[0], make_wallet(tmp_path, "b.json")[0]
    ea, eb = (json.loads(p.read_text())["encrypted_private_key"] for p in (a, b))
    assert ea["salt"] != eb["salt"] and ea["nonce"] != eb["nonce"] and ea["ciphertext"] != eb["ciphertext"]


def test_ciphertext_is_bound_to_address_and_network(tmp_path):
    path, address = make_wallet(tmp_path)
    data = json.loads(path.read_text())
    blob = data["encrypted_private_key"]
    with pytest.raises(WrongPasswordError):                      # swapped into another wallet identity
        decrypt_private_key(blob, PASSWORD, wallet_aad("DMAI" + "A" * 39, "devnet"))
    with pytest.raises(WrongPasswordError):                      # moved to another network
        decrypt_private_key(blob, PASSWORD, wallet_aad(address, "testnet"))


def test_tampered_ciphertext_detected(tmp_path):
    path, address = make_wallet(tmp_path)
    blob = json.loads(path.read_text())["encrypted_private_key"]
    ct = bytearray(__import__("base64").urlsafe_b64decode(blob["ciphertext"]))
    ct[0] ^= 1
    blob["ciphertext"] = __import__("base64").urlsafe_b64encode(bytes(ct)).decode()
    with pytest.raises(WrongPasswordError):
        decrypt_private_key(blob, PASSWORD, wallet_aad(address, "devnet"))


@pytest.mark.parametrize("n", [2 ** 30, 2 ** 21, 3 * 2 ** 12, 1, 0, -5])
def test_hostile_kdf_parameters_are_refused_before_any_work(tmp_path, n):
    path, address = make_wallet(tmp_path)
    blob = json.loads(path.read_text())["encrypted_private_key"]
    blob["n"] = n
    with pytest.raises(ValueError, match="malformed"):
        decrypt_private_key(blob, PASSWORD, wallet_aad(address, "devnet"))


def test_kdf_default_is_strong():
    assert KDF_N >= 2 ** 17


def test_malformed_wallet_files_give_clean_errors(tmp_path):
    for content in ("not json", "[]", "{}", json.dumps({"format": "MineAI-Wallet-V0.1"})):
        p = tmp_path / "bad.json"
        p.write_text(content)
        with pytest.raises(SystemExit):
            wallet.load_wallet(p, TEST)
    with pytest.raises(SystemExit, match="not found"):
        wallet.load_wallet(tmp_path / "missing.json", TEST)


def test_legacy_v01_wallet_is_refused(tmp_path):
    p = tmp_path / "old.json"
    p.write_text(json.dumps({"format": "MineAI-Wallet-V0.1", "address": "MAIXXXX"}))
    with pytest.raises(SystemExit, match="V0.1"):
        wallet.load_wallet(p, TEST)


def test_wallet_for_another_network_is_refused(tmp_path):
    import dataclasses
    path, _ = make_wallet(tmp_path)
    other = dataclasses.replace(TEST, name="testnet", address_prefix="TMAI")
    with pytest.raises(SystemExit, match="not a testnet wallet"):
        wallet.load_wallet(path, other)


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits are not meaningful on Windows")
def test_wallet_file_is_owner_only(tmp_path):
    path, _ = make_wallet(tmp_path)
    assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_transaction_signed_by_wallet_is_valid(tmp_path):
    from mineai import consensus as C
    from tests.helpers import Acct
    path, address = make_wallet(tmp_path)
    key = wallet.unlock(wallet.load_wallet(path, TEST), TEST, PASSWORD)
    from mineai.crypto import sign_transaction
    tx = sign_transaction(key, {"recipient": Acct().address, "amount": 1_000_000, "fee": 10_000,
                                "nonce": 1, "timestamp": 1_800_000_000}, TEST.network_id, TEST.address_prefix)
    assert tx["sender"] == address
    C.check_transaction(tx, TEST)


# ------------------------------------------------------------------ logging
def test_logger_refuses_sensitive_fields():
    logger = logging.getLogger("mineai.test")
    for name in ("password", "private_key", "secret", "mnemonic", "seed", "Password"):
        with pytest.raises(ValueError, match="sensitive"):
            mlog.event(logger, logging.INFO, "x", **{name: "hunter2"})


def test_log_lines_are_structured_json(capsys):
    mlog.configure("INFO")
    mlog.event(logging.getLogger("mineai.test"), logging.INFO, "hello", height=5, txid="ab")
    line = capsys.readouterr().err.strip().splitlines()[-1]
    entry = json.loads(line)
    assert entry["event"] == "hello" and entry["height"] == 5 and entry["level"] == "INFO" and entry["ts"].endswith("Z")


def test_chain_events_are_logged_without_secrets(tmp_path, caplog):
    from tests.helpers import Acct, funded
    with caplog.at_level(logging.INFO, logger="mineai"):
        chain, alice = funded(tmp_path)
        try:
            chain.submit_transaction(alice.tx(Acct(), 1, fee=0))
        except Exception:
            pass
    events = {r.getMessage() for r in caplog.records}
    assert {"chain_initialized", "block_accepted"} <= events
    joined = " ".join(str(getattr(r, "fields", "")) for r in caplog.records)
    assert "key" not in joined.lower().replace("public_key", "")
