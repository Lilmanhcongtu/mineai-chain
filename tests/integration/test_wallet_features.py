"""Wallet hardening: locking, auto-lock, backup/restore, password change, transfer preview, interactive shell."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time

import pytest
from fastapi.testclient import TestClient

from mineai import consensus as C
from mineai import wallet as W
from mineai.crypto import WrongPasswordError
from mineai.node import create_app
from tests.helpers import START, TEST, Acct, funded, mai, mine

PASSWORD = "correct horse battery staple"
FAST = 2 ** 12


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def new_wallet(tmp_path, name="w.wallet.json", password=PASSWORD):
    path = tmp_path / name
    address = W.create_wallet_file(path, password, TEST, kdf_n=FAST)
    return path, address, W.load_wallet(path, TEST)


def session_for(wallet, **kw):
    return W.WalletSession(wallet, TEST, **kw)


# ====================================================================== password policy
@pytest.mark.parametrize("bad", ["short", "123456789", "aaaaaaaaaaaa", "abababababab", "password123", "PASSWORD123",
                                 "1234567890", "qwertyuiop", "        ", "12345678910"])
def test_weak_passwords_are_rejected(bad, tmp_path):
    with pytest.raises(ValueError):
        W.check_password_strength(bad)
    with pytest.raises(ValueError):
        W.create_wallet_file(tmp_path / "x.json", bad, TEST, kdf_n=FAST)
    assert not (tmp_path / "x.json").exists()


@pytest.mark.parametrize("good", ["correct horse battery staple", "x9!Kq2#vLm8Z", "a long passphrase is fine 1", "Ünïcödé-pässwörd-ok"])
def test_reasonable_passwords_are_accepted(good):
    W.check_password_strength(good)


# ====================================================================== session: lock / unlock
def test_session_starts_locked_and_refuses_to_sign(tmp_path):
    _, address, wallet = new_wallet(tmp_path)
    s = session_for(wallet)
    assert not s.unlocked and s.address == address
    with pytest.raises(W.WalletLockedError):
        s.sign_transfer(Acct().address, 1_000_000, 10_000, 1, START)


def test_wrong_password_keeps_it_locked(tmp_path):
    _, _, wallet = new_wallet(tmp_path)
    s = session_for(wallet)
    with pytest.raises(WrongPasswordError):
        s.unlock("definitely not it")
    assert not s.unlocked and s._key is None


def test_unlock_sign_lock_cycle_produces_valid_transactions(tmp_path):
    _, address, wallet = new_wallet(tmp_path)
    s = session_for(wallet)
    s.unlock(PASSWORD)
    tx = s.sign_transfer(Acct().address, 5_000_000, 10_000, 1, START)
    assert tx["sender"] == address
    C.check_transaction(tx, TEST)                                     # a fully valid signed transaction
    s.lock()
    assert not s.unlocked and s._key is None                          # the key reference is gone
    with pytest.raises(W.WalletLockedError):
        s.sign_transfer(Acct().address, 1_000_000, 10_000, 2, START)
    s.unlock(PASSWORD)                                                # and it can be unlocked again
    assert s.unlocked


def test_the_key_is_never_present_in_the_session_repr_or_wallet_document(tmp_path):
    from mineai.crypto import private_key_bytes
    path, _, wallet = new_wallet(tmp_path)
    s = session_for(wallet)
    s.unlock(PASSWORD)
    raw = private_key_bytes(s._key)
    assert raw not in json.dumps(wallet).encode() and raw.hex() not in repr(s) and raw.hex() not in repr(wallet)
    s.lock()


# ====================================================================== auto-lock
def test_auto_lock_after_inactivity_with_a_fake_clock(tmp_path):
    _, _, wallet = new_wallet(tmp_path)
    clock = FakeClock()
    s = session_for(wallet, timeout=60, clock=clock, poll=3600)       # watchdog effectively off: test the lazy check
    s.unlock(PASSWORD)
    clock.t += 59
    assert s.unlocked
    clock.t += 2                                                       # 61 s idle
    assert not s.unlocked and s.auto_locked and s._key is None
    with pytest.raises(W.WalletLockedError):
        s.sign_transfer(Acct().address, 1, 10_000, 1, START)
    s.unlock(PASSWORD)
    assert s.unlocked and not s.auto_locked


def test_activity_resets_the_inactivity_timer(tmp_path):
    _, _, wallet = new_wallet(tmp_path)
    clock = FakeClock()
    s = session_for(wallet, timeout=60, clock=clock, poll=3600)
    s.unlock(PASSWORD)
    for _ in range(5):                                                 # 5 x 50 s: far beyond 60 s in total
        clock.t += 50
        s.sign_transfer(Acct().address, 1_000_000, 10_000, 1, START)   # signing counts as activity
        assert s.unlocked
    clock.t += 50
    s.touch()
    clock.t += 50
    assert s.unlocked
    clock.t += 61
    assert not s.unlocked


def test_activity_cannot_revive_an_expired_session(tmp_path):
    """Regression: touch() used to refresh the timer BEFORE noticing that the timeout had already passed."""
    _, _, wallet = new_wallet(tmp_path)
    clock = FakeClock()
    s = session_for(wallet, timeout=60, clock=clock, poll=3600)
    s.unlock(PASSWORD)
    clock.t += 500                                                     # long idle, nobody has looked yet
    s.touch()                                                          # the user "does something"
    assert not s.unlocked and s.auto_locked and s._key is None
    with pytest.raises(W.WalletLockedError):
        s.sign_transfer(Acct().address, 1_000_000, 10_000, 1, START)


def test_background_watchdog_locks_an_untouched_session_in_real_time(tmp_path):
    """The key must disappear even if nobody calls the wallet again (the user walked away)."""
    _, _, wallet = new_wallet(tmp_path)
    s = session_for(wallet, timeout=0.3, poll=0.05)
    s.unlock(PASSWORD)
    assert s._key is not None
    time.sleep(0.9)
    assert s._key is None and s.auto_locked                            # no property access happened in between
    assert s._thread is not None
    s._thread.join(timeout=2)
    assert not s._thread.is_alive()                                    # the watchdog stops once locked


def test_explicit_lock_stops_the_watchdog(tmp_path):
    _, _, wallet = new_wallet(tmp_path)
    s = session_for(wallet, timeout=30, poll=0.05)
    s.unlock(PASSWORD)
    s.lock()
    s._thread.join(timeout=2)
    assert not s._thread.is_alive() and not s.auto_locked


# ====================================================================== wallet document validation
def base_doc(tmp_path):
    return json.loads(new_wallet(tmp_path)[0].read_text())


@pytest.mark.parametrize("mutate,message", [
    (lambda d: d.update(format="MineAI-Wallet-V0.1"), "unsupported"),
    (lambda d: d.update(network="nowhere"), "unknown network"),
    (lambda d: d.update(network="mainnet"), "disabled"),
    (lambda d: d.update(address="DMAI" + "A" * 39), "address"),
    (lambda d: d.pop("encrypted_private_key"), "encrypted key"),
    (lambda d: d["encrypted_private_key"].pop("salt"), "encrypted key"),
    (lambda d: d["encrypted_private_key"].update(n=2 ** 30), "key-derivation"),
    (lambda d: d["encrypted_private_key"].update(n=100), "key-derivation"),
])
def test_wallet_document_validation(tmp_path, mutate, message):
    doc = base_doc(tmp_path)
    mutate(doc)
    with pytest.raises(ValueError, match=message):
        W.validate_wallet_document(doc)


@pytest.mark.parametrize("junk", [None, [], "x", 5, {}])
def test_wallet_document_validation_rejects_non_wallets(junk):
    with pytest.raises(ValueError):
        W.validate_wallet_document(junk)


# ====================================================================== backup / restore
def test_backup_is_an_exact_verified_copy(tmp_path):
    path, address, _ = new_wallet(tmp_path)
    info = W.backup_wallet(path, tmp_path / "backup.json")
    assert (tmp_path / "backup.json").read_bytes() == path.read_bytes()
    assert info["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest() and info["address"] == address
    assert info["password_verified"] is False


def test_backup_never_overwrites_and_never_targets_itself(tmp_path):
    path, _, _ = new_wallet(tmp_path)
    (tmp_path / "exists.json").write_text("precious")
    with pytest.raises(SystemExit, match="Refusing to overwrite"):
        W.backup_wallet(path, tmp_path / "exists.json")
    assert (tmp_path / "exists.json").read_text() == "precious"
    with pytest.raises(SystemExit):
        W.backup_wallet(path, path)


def test_backup_with_password_verification(tmp_path):
    path, _, _ = new_wallet(tmp_path)
    with pytest.raises(SystemExit, match="Refusing to back up"):
        W.backup_wallet(path, tmp_path / "b.json", "the wrong password")
    assert not (tmp_path / "b.json").exists()                          # a failed verification leaves no file
    info = W.backup_wallet(path, tmp_path / "b.json", PASSWORD)
    assert info["password_verified"] is True


def test_backup_refuses_garbage_and_missing_sources(tmp_path):
    (tmp_path / "junk.json").write_text("this is not a wallet")
    with pytest.raises(SystemExit, match="Refusing to back up"):
        W.backup_wallet(tmp_path / "junk.json", tmp_path / "o.json")
    with pytest.raises(SystemExit, match="not found"):
        W.backup_wallet(tmp_path / "nope.json", tmp_path / "o.json")
    assert not (tmp_path / "o.json").exists()


def test_restore_creates_a_working_wallet_and_never_overwrites(tmp_path):
    path, address, _ = new_wallet(tmp_path)
    W.backup_wallet(path, tmp_path / "backup.json")
    restored = W.restore_wallet(tmp_path / "backup.json", tmp_path / "restored.json", PASSWORD)
    assert restored["address"] == address and restored["restored_to"].endswith("restored.json")
    s = session_for(W.load_wallet(tmp_path / "restored.json", TEST))
    s.unlock(PASSWORD)
    assert s.unlocked
    with pytest.raises(SystemExit, match="Refusing to overwrite"):
        W.restore_wallet(tmp_path / "backup.json", path)               # would clobber the live wallet
    assert W.verify_wallet_password(path, PASSWORD) == address


def test_restore_rejects_tampered_backups(tmp_path):
    path, _, _ = new_wallet(tmp_path)
    doc = json.loads(path.read_text())
    doc["encrypted_private_key"]["n"] = 2 ** 30
    (tmp_path / "evil.json").write_text(json.dumps(doc))
    with pytest.raises(SystemExit, match="Refusing to back up"):
        W.restore_wallet(tmp_path / "evil.json", tmp_path / "r.json")
    doc = json.loads(path.read_text())
    doc["address"] = Acct().address                                    # a different identity swapped in
    (tmp_path / "swap.json").write_text(json.dumps(doc))
    W.restore_wallet(tmp_path / "swap.json", tmp_path / "r2.json")     # structurally valid...
    with pytest.raises(WrongPasswordError):                            # ...but the ciphertext is bound to the real address
        session_for(W.load_wallet(tmp_path / "r2.json", TEST)).unlock(PASSWORD)


@pytest.mark.skipif(os.name != "nt", reason="ACL check is Windows-specific")
def test_wallet_files_are_restricted_to_the_current_user_on_windows(tmp_path):
    import subprocess
    path, _, _ = new_wallet(tmp_path)
    out = subprocess.run(["icacls", str(path)], capture_output=True, text=True).stdout
    for broad in ("Everyone", "BUILTIN\\Users", "Authenticated Users"):
        assert broad not in out, out


# ====================================================================== password change
def test_change_password_swaps_atomically_and_keeps_the_old_file(tmp_path):
    path, address, _ = new_wallet(tmp_path)
    old_bytes = path.read_bytes()
    NEW = "an entirely different passphrase 42"
    old_copy = W.change_wallet_password(path, PASSWORD, NEW, kdf_n=FAST)
    assert old_copy.read_bytes() == old_bytes and old_copy != path
    assert not (tmp_path / "w.wallet.json.new").exists()               # nothing left staged
    assert W.verify_wallet_password(path, NEW) == address
    with pytest.raises(WrongPasswordError):
        W.verify_wallet_password(path, PASSWORD)                       # the old password no longer opens the live file
    assert W.verify_wallet_password(old_copy, PASSWORD) == address     # the kept copy still opens with the OLD one
    from mineai.crypto import private_key_bytes
    a, b = session_for(W.load_wallet(path, TEST)), session_for(W.load_wallet(old_copy, TEST))
    a.unlock(NEW); b.unlock(PASSWORD)
    assert private_key_bytes(a._key) == private_key_bytes(b._key)      # same key, re-encrypted


def test_change_password_rejects_wrong_old_and_weak_new_without_touching_the_wallet(tmp_path):
    path, _, _ = new_wallet(tmp_path)
    before = path.read_bytes()
    with pytest.raises(SystemExit):
        W.change_wallet_password(path, "wrong old password", "a perfectly fine new one", kdf_n=FAST)
    with pytest.raises(ValueError):
        W.change_wallet_password(path, PASSWORD, "short", kdf_n=FAST)
    assert path.read_bytes() == before and sorted(p.name for p in tmp_path.iterdir()) == ["w.wallet.json"]


def test_change_password_aborts_cleanly_if_verification_fails(tmp_path, monkeypatch):
    path, _, _ = new_wallet(tmp_path)
    before = path.read_bytes()
    real = W.decrypt_private_key
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 2:                                            # the verification of the staged file
            raise ValueError("simulated corruption")
        return real(*a, **k)
    monkeypatch.setattr(W, "decrypt_private_key", flaky)
    with pytest.raises(SystemExit, match="unchanged"):
        W.change_wallet_password(path, PASSWORD, "another good passphrase 7", kdf_n=FAST)
    assert path.read_bytes() == before                                 # the live wallet is untouched
    assert sorted(p.name for p in tmp_path.iterdir()) == ["w.wallet.json"]    # and the staged file was cleaned up


# ====================================================================== transfer preview
def fake_api(account=None, fee=None):
    account = account or {"balance": mai(50), "immature_balance": 0, "available_balance": mai(50),
                          "pending_outgoing": 0, "confirmed_nonce": 0, "next_nonce": 1, "address": "x"}
    fee = fee or {"min_fee_atomic": TEST.min_fee, "default_fee_atomic": TEST.default_fee,
                  "mempool_median_fee_atomic": None, "mempool_size": 0}
    calls = []

    def call(method, node, path, payload=None):
        calls.append((method, path, payload))
        if path.endswith("/fee"):
            return fee
        if "/account/" in path:
            return account
        if path.endswith("/transactions") and method == "POST":
            return {"accepted": True, "txid": payload["txid"]}
        raise AssertionError(path)
    call.calls = calls
    return call


def test_preview_shows_amount_fee_total_and_network(tmp_path):
    _, address, wallet = new_wallet(tmp_path)
    lines = []
    _, amount, fee = W.prepare_transfer(wallet, TEST, "node", Acct().address, "5", None, lines.append, fake_api())
    text = "\n".join(lines)
    assert amount == mai(5) and fee == TEST.default_fee
    assert "DEVNET" in text and "Amount:  5 MAI" in text and "Fee:     0.01 MAI" in text and "Total:   5.01 MAI" in text
    assert "cannot be reversed" in text


def test_preview_warns_when_the_fee_is_below_what_the_mempool_pays(tmp_path):
    _, _, wallet = new_wallet(tmp_path)
    lines = []
    api = fake_api(fee={"min_fee_atomic": TEST.min_fee, "default_fee_atomic": TEST.default_fee,
                        "mempool_median_fee_atomic": mai("0.05"), "mempool_size": 9})
    W.prepare_transfer(wallet, TEST, "node", Acct().address, "1", "0.002", lines.append, api)
    assert any("may confirm slowly" in l for l in lines)


@pytest.mark.parametrize("to,amount,fee,message", [
    ("not-an-address", "1", None, "not a valid"),
    ("SELF", "1", None, "your own address"),
    (Acct().address, "0", None, "greater than zero"),
    (Acct().address, "-1", None, "greater than zero"),
    (Acct().address, "1.0000001", None, "6 decimal"),
    (Acct().address, "abc", None, "valid amount"),
    (Acct().address, "1e3", None, "not a valid amount"),
    (Acct().address, "1_0", None, "not a valid amount"),
    (Acct().address, "+5", None, "not a valid amount"),
    (Acct().address, ".5", None, "not a valid amount"),
    (Acct().address, "5.", None, "not a valid amount"),
    (Acct().address, "0x10", None, "not a valid amount"),
    (Acct().address, "5 0", None, "not a valid amount"),
    (Acct().address, "١٢", None, "not a valid amount"),
    (Acct().address, "1", "0.0009", "below the network minimum"),
    (Acct().address, "999", None, "Insufficient"),
    (Acct().address, "49.995", "0.01", "Insufficient"),
])
def test_preview_rejects_bad_transfers_before_anything_is_signed(tmp_path, to, amount, fee, message):
    _, address, wallet = new_wallet(tmp_path)
    to = address if to == "SELF" else to
    with pytest.raises(SystemExit, match=message):
        W.prepare_transfer(wallet, TEST, "node", to, amount, fee, lambda *_: None, fake_api())


def test_an_address_from_another_network_is_rejected(tmp_path):
    import dataclasses
    other = Acct(dataclasses.replace(TEST, address_prefix="TMAI"))
    _, _, wallet = new_wallet(tmp_path)
    with pytest.raises(SystemExit, match="not a valid devnet address"):
        W.prepare_transfer(wallet, TEST, "node", other.address, "1", None, lambda *_: None, fake_api())


# ====================================================================== interactive shell
def scripted(session, wallet_path, commands, api, password_fn=lambda _: PASSWORD, clock=None, advance=None):
    out, feed = [], iter(commands)

    def input_fn(prompt):
        if advance and clock is not None:
            clock.t += advance.pop(0) if advance else 0
        try:
            return next(feed)
        except StopIteration:
            raise EOFError

    W.run_shell(session, "node", wallet_path, input_fn=input_fn, out=out.append, password_fn=password_fn,
                api_fn=api, now=lambda: START)
    return "\n".join(out)


def test_shell_send_flow_asks_for_the_password_only_when_locked(tmp_path):
    path, address, wallet = new_wallet(tmp_path)
    s = session_for(wallet, timeout=600)
    asked, api, to = [], fake_api(), Acct().address

    def pw(prompt):
        asked.append(prompt)
        return PASSWORD
    text = scripted(s, path, [f"send {to} 2", "yes", "balance", "lock", f"send {to} 1 0.02", "yes"], api, pw)
    assert len(asked) == 2                                             # first send: locked at start -> asks; then unlocked
    posts = [c for c in api.calls if c[0] == "POST"]
    assert len(posts) == 2 and posts[0][2]["amount"] == mai(2) and posts[1][2]["fee"] == mai("0.02")
    assert "Transaction accepted" in text and "Wallet locked." in text
    assert PASSWORD not in text


def test_shell_wrong_password_and_cancel_send_nothing(tmp_path):
    path, _, wallet = new_wallet(tmp_path)
    s = session_for(wallet)
    api, to = fake_api(), Acct().address
    text = scripted(s, path, [f"send {to} 1", "yes", f"send {to} 1", "no"], api, lambda _: "wrong password!!")
    assert "wrong password" in text.lower() and "Cancelled." in text
    assert not [c for c in api.calls if c[0] == "POST"]                # nothing was signed or sent
    assert not s.unlocked


def test_shell_reports_the_auto_lock(tmp_path):
    path, _, wallet = new_wallet(tmp_path)
    clock = FakeClock()
    s = session_for(wallet, timeout=60, clock=clock, poll=3600)
    s.unlock(PASSWORD)
    text = scripted(s, path, ["address", "address", "address"], fake_api(), clock=clock, advance=[0, 90, 0])
    assert text.count("auto-locked after inactivity") == 1              # reported once, when it happened
    assert not s.unlocked                                               # and the next command did NOT revive it


def test_shell_commands_and_errors(tmp_path):
    path, address, wallet = new_wallet(tmp_path)
    s = session_for(wallet)
    s.unlock(PASSWORD)
    api = fake_api()
    text = scripted(s, path, ["help", "address", "frobnicate", "send", "send onlyone", "backup", "history abc"], api)
    assert "commands:" in text and address in text and "Unknown command 'frobnicate'" in text
    assert text.count("usage:") >= 2 and "Error:" in text


def test_shell_backup_command_warns_and_never_overwrites(tmp_path):
    path, _, wallet = new_wallet(tmp_path)
    s = session_for(wallet)
    target = tmp_path / "shell-backup.json"
    text = scripted(s, path, [f"backup {target}", f"backup {target}"], fake_api())
    assert target.read_bytes() == path.read_bytes() and "ENCRYPTED private key" in text and "SHA-256" in text
    assert "Refusing to overwrite" in text


# ====================================================================== against a real chain
def test_history_balance_and_send_against_a_real_node(tmp_path):
    chain, miner = funded(tmp_path, blocks=4)
    client = TestClient(create_app(chain, local_hosts=frozenset({"testclient"}), rate_limit_per_minute=100_000))

    def api(method, node, path, payload=None):
        r = client.request(method, path, json=payload)
        if r.status_code >= 400:
            raise SystemExit(f"Node rejected the request [{r.json()['error']['code']}]: {r.json()['error']['message']}")
        return r.json()

    wpath = tmp_path / "me.json"
    address = W.create_wallet_file(wpath, PASSWORD, TEST, kdf_n=FAST)
    wallet = W.load_wallet(wpath, TEST)
    tx = miner.tx(address, 20, nonce=1, timestamp=chain.now())
    chain.submit_transaction(tx)
    mine(chain, miner); chain.clock.advance(60)

    out = []
    W.show_balance(wallet, "node", out.append, api)
    assert any("Confirmed: 20 MAI" in l for l in out)
    out.clear()
    W.show_history(wallet, "node", out=out.append, api_fn=api)
    assert any("in " in l and "+" in l and "20" in l for l in out) and any("Page 1 of 1" in l for l in out)

    s = session_for(wallet)
    text = scripted(s, wpath, [f"send {miner.address} 3", "yes", "history"], api)
    assert "Transaction accepted" in text and "PENDING" in text          # the fresh send shows as pending in the history
    mine(chain, miner); chain.clock.advance(60)
    out.clear()
    W.show_history(wallet, "node", out=out.append, api_fn=api)
    assert not any("PENDING" in l for l in out) and any("out" in l and "-" in l for l in out)
    assert chain.account(address)["balance"] == mai(20) - mai(3) - mai("0.01")
    chain.verify_integrity()


# ====================================================================== command line
def run_cli(monkeypatch, capsys, argv, password=PASSWORD):
    monkeypatch.setattr(sys, "argv", ["mineai-wallet"] + argv)
    monkeypatch.setenv("MINEAI_WALLET_PASSWORD", password)
    try:
        W.main()
    except SystemExit as exc:
        return str(exc.code) if exc.code not in (None, 0) else "", capsys.readouterr().out
    return "", capsys.readouterr().out


def test_cli_create_backup_verify_restore_change_password(tmp_path, monkeypatch, capsys):
    w = tmp_path / "cli.wallet.json"
    err, out = run_cli(monkeypatch, capsys, ["create", "--wallet", str(w)])
    assert err == "" and "Created wallet" in out and "backup" in out.lower() and PASSWORD not in out
    err, out = run_cli(monkeypatch, capsys, ["create", "--wallet", str(w)])
    assert "Refusing to overwrite" in err                                # never clobbers
    b = tmp_path / "cli.backup.json"
    err, out = run_cli(monkeypatch, capsys, ["backup", "--wallet", str(w), "--to", str(b), "--verify-password"])
    assert err == "" and "SHA-256" in out and "WARNING" in out and PASSWORD not in out
    err, out = run_cli(monkeypatch, capsys, ["verify", "--wallet", str(w)])
    assert "OK: the password decrypts the wallet" in out
    err, out = run_cli(monkeypatch, capsys, ["verify", "--wallet", str(w)], password="not the password!!")
    assert "Verification failed" in err
    r = tmp_path / "cli.restored.json"
    err, out = run_cli(monkeypatch, capsys, ["restore", "--from", str(b), "--wallet", str(r)])
    assert err == "" and "Restored" in out and r.read_bytes() == w.read_bytes()
    err, out = run_cli(monkeypatch, capsys, ["address", "--wallet", str(r)])
    assert out.strip().splitlines()[-1].startswith("DMAI")
    err, out = run_cli(monkeypatch, capsys, ["change-password", "--wallet", str(w)])   # old == new here -> allowed, still atomic
    assert err == "" and "Password changed" in out and "OLD password" in out
    assert len(list(tmp_path.glob("cli.wallet.json.before-password-change-*"))) == 1


def test_no_command_prints_a_password(tmp_path, monkeypatch, capsys):
    secret = "Sup3r-Secret-Passphrase-Do-Not-Print"
    w = tmp_path / "s.wallet.json"
    for argv in (["create", "--wallet", str(w)], ["verify", "--wallet", str(w)], ["address", "--wallet", str(w)],
                 ["backup", "--wallet", str(w), "--to", str(tmp_path / "b.json"), "--verify-password"]):
        _, out = run_cli(monkeypatch, capsys, argv, password=secret)
        assert secret not in out
    assert secret.encode() not in w.read_bytes()
