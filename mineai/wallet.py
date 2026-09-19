"""Wallet: encrypted key file, locking, backup/restore, and a local command-line / interactive client.

Keys never leave this process and transactions are signed locally. Passwords come from a hidden prompt
(or, for automation only, MINEAI_WALLET_PASSWORD); there is deliberately no --password flag because command-line
arguments are visible to other processes. Nothing here ever prints or logs a password or a private key.

Security notes (also in SECURITY.md):
* Key generation uses the operating system's CSPRNG (cryptography's Ed25519 key generation).
* The key file is scrypt (N=2^17) + AES-256-GCM, bound to the address and network.
* An unlocked session keeps the key in process memory. Python cannot securely wipe memory, so "lock" drops every
  reference and asks the garbage collector to run; it is a best effort, not a guarantee.
"""
from __future__ import annotations

import argparse
import getpass
import gc
import hashlib
import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from . import config
from .crypto import (
    KDF_N_MAX, WALLET_FORMAT, WrongPasswordError, address_from_public_key, b64e, decrypt_private_key,
    encrypt_private_key, private_key_bytes, public_key_bytes, sign_transaction, validate_address, wallet_aad,
)
from .util import atomic_to_mai, mai_to_atomic

MIN_PASSWORD_LENGTH = 10
DEFAULT_LOCK_TIMEOUT = 120.0
COMMON_PASSWORDS = {
    "password", "password1", "password12", "password123", "1234567890", "12345678910", "qwertyuiop", "qwerty12345",
    "letmein123", "iloveyou123", "administrator", "welcome123", "abcdefghij", "0123456789", "1q2w3e4r5t",
}


class WalletLockedError(RuntimeError):
    pass


# ====================================================================== passwords and files
def check_password_strength(password: str) -> None:
    """Reject obviously weak passwords. This is a floor, not a guarantee of strength."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(set(password)) < 5:
        raise ValueError("password is too repetitive: use at least 5 different characters")
    if password.lower() in COMMON_PASSWORDS or password.isdigit() and len(password) < 14:
        raise ValueError("password is too common or too simple")


def restrict_permissions(path: Path) -> bool:
    """Best effort: make the file readable/writable by its owner only. Returns True if applied."""
    try:
        if os.name == "nt":
            user = getpass.getuser()
            result = subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:(F)"],
                                    capture_output=True, text=True, timeout=15)
            return result.returncode == 0
        os.chmod(path, 0o600)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def _write_new_file(path: Path, data: bytes) -> None:
    """Create `path` exclusively (never overwrites), owner-only, durably."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        raise SystemExit(f"Refusing to overwrite existing file: {path}")
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    restrict_permissions(path)


def _params_for(network: str):
    if network not in config.PROFILES:
        raise ValueError(f"unknown network {network!r}")
    return config.get_params(network)


def validate_wallet_document(doc: object) -> dict:
    """Structural validation of a wallet file. Raises ValueError with a clear message; reveals no secrets."""
    if not isinstance(doc, dict):
        raise ValueError("not a wallet file")
    if doc.get("format") != WALLET_FORMAT:
        raise ValueError("unsupported wallet format (V0.1 wallets belong to the retired local devnet)")
    params = _params_for(doc.get("network"))
    if not validate_address(doc.get("address"), params.address_prefix):
        raise ValueError("wallet address is invalid for its network")
    blob = doc.get("encrypted_private_key")
    if not isinstance(blob, dict) or not {"salt", "nonce", "ciphertext", "n", "r", "p"} <= set(blob):
        raise ValueError("wallet file has no valid encrypted key")
    if not isinstance(blob["n"], int) or not 2 ** 12 <= blob["n"] <= KDF_N_MAX:
        raise ValueError("wallet file has unacceptable key-derivation parameters")
    return doc


# ====================================================================== create / load / unlock
def create_wallet_file(path: Path, password: str, params, kdf_n: int | None = None) -> str:
    """Create a new wallet file. Refuses to overwrite. Returns the address."""
    check_password_strength(password)
    private = Ed25519PrivateKey.generate()                        # OS CSPRNG
    pub = public_key_bytes(private)
    address = address_from_public_key(pub, params.address_prefix)
    kwargs = {"n": kdf_n} if kdf_n else {}
    payload = {
        "format": WALLET_FORMAT,
        "network": params.name,
        "address": address,
        "public_key": b64e(pub),
        "encrypted_private_key": encrypt_private_key(
            private_key_bytes(private), password, wallet_aad(address, params.name), **kwargs),
    }
    _write_new_file(Path(path), json.dumps(payload, indent=2).encode("utf-8"))
    return address


def load_wallet(path: Path, params) -> dict:
    if not Path(path).exists():
        raise SystemExit(f"Wallet not found: {path}")
    try:
        wallet = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"Cannot read wallet file: {exc}")
    if not isinstance(wallet, dict):
        raise SystemExit("Cannot read wallet file: not a wallet.")
    if wallet.get("format") != WALLET_FORMAT:
        raise SystemExit("Unsupported wallet format (V0.1 wallets belong to the retired local devnet "
                         "and cannot be used here). Create a new wallet.")
    if wallet.get("network") != params.name or not validate_address(wallet.get("address"), params.address_prefix):
        raise SystemExit(f"This wallet is not a {params.name} wallet.")
    return wallet


def get_password(prompt: str) -> str:
    env = os.environ.get("MINEAI_WALLET_PASSWORD")
    return env if env is not None else getpass.getpass(prompt)


def unlock(wallet: dict, params, password: str) -> Ed25519PrivateKey:
    try:
        return decrypt_private_key(wallet["encrypted_private_key"], password,
                                   wallet_aad(wallet["address"], params.name))
    except (WrongPasswordError, ValueError) as exc:
        raise SystemExit(str(exc))


# ====================================================================== session with locking
class WalletSession:
    """An unlockable wallet. The key exists only while unlocked and is dropped on lock or inactivity."""

    def __init__(self, wallet: dict, params, *, timeout: float = DEFAULT_LOCK_TIMEOUT,
                 clock: Callable[[], float] = time.monotonic, poll: float | None = None):
        self.wallet, self.params, self.timeout, self.clock = wallet, params, timeout, clock
        self.poll = poll if poll is not None else max(0.05, min(1.0, timeout / 4))
        self._key: Ed25519PrivateKey | None = None
        self._last = 0.0
        self._mutex = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.auto_locked = False                      # set when the timeout (not the user) locked the wallet

    @property
    def address(self) -> str:
        return self.wallet["address"]

    @property
    def unlocked(self) -> bool:
        self._check_timeout()
        return self._key is not None

    def unlock(self, password: str) -> None:
        key = decrypt_private_key(self.wallet["encrypted_private_key"], password,
                                  wallet_aad(self.wallet["address"], self.params.name))   # raises WrongPasswordError
        with self._mutex:
            self._key, self._last, self.auto_locked = key, self.clock(), False
        self._start_watchdog()

    def lock(self) -> None:
        with self._mutex:
            self._key = None
        self._stop.set()
        gc.collect()

    def close(self) -> None:
        self.lock()

    def touch(self) -> None:
        """Record activity. An already-expired session is locked first: activity cannot revive it."""
        self._check_timeout()
        with self._mutex:
            if self._key is not None:
                self._last = self.clock()

    def _check_timeout(self) -> None:
        with self._mutex:
            if self._key is not None and self.clock() - self._last > self.timeout:
                self._key, self.auto_locked = None, True
                gc.collect()

    def _start_watchdog(self) -> None:
        self._stop.clear()
        if self._thread and self._thread.is_alive():
            return

        def watch():
            while not self._stop.wait(self.poll):
                self._check_timeout()
                with self._mutex:
                    if self._key is None:
                        return
        self._thread = threading.Thread(target=watch, name="wallet-autolock", daemon=True)
        self._thread.start()

    def sign_transfer(self, recipient: str, amount: int, fee: int, nonce: int, timestamp: int) -> dict:
        self._check_timeout()
        with self._mutex:
            key = self._key
        if key is None:
            raise WalletLockedError("wallet is locked")
        self.touch()
        return sign_transaction(key, {"recipient": recipient, "amount": amount, "fee": fee,
                                      "nonce": nonce, "timestamp": timestamp},
                                self.params.network_id, self.params.address_prefix)


# ====================================================================== backup / restore / password change
def _sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_wallet_password(path: Path, password: str) -> str:
    """Prove that `password` decrypts the wallet's key, without returning or printing the key."""
    doc = validate_wallet_document(json.loads(Path(path).read_text(encoding="utf-8")))
    params = _params_for(doc["network"])
    decrypt_private_key(doc["encrypted_private_key"], password, wallet_aad(doc["address"], params.name))
    return doc["address"]


def backup_wallet(src: Path, dest: Path, password: str | None = None) -> dict:
    """Copy the (encrypted) wallet file to `dest`, never overwriting. Verifies the copy byte-for-byte and,
    if a password is given, that it decrypts. Returns {address, network, sha256, password_verified}."""
    src, dest = Path(src), Path(dest)
    if not src.exists():
        raise SystemExit(f"Wallet not found: {src}")
    data = src.read_bytes()
    try:
        doc = validate_wallet_document(json.loads(data.decode("utf-8")))
    except (ValueError, UnicodeDecodeError) as exc:
        raise SystemExit(f"Refusing to back up: {exc}")
    if password is not None:
        try:
            verify_wallet_password(src, password)
        except (WrongPasswordError, ValueError) as exc:
            raise SystemExit(f"Refusing to back up: {exc}")
    if dest.resolve() == src.resolve():
        raise SystemExit("The backup destination is the wallet file itself.")
    _write_new_file(dest, data)
    if _sha256_file(dest) != hashlib.sha256(data).hexdigest():
        raise SystemExit("Backup verification failed: the copy does not match the original.")
    return {"address": doc["address"], "network": doc["network"], "sha256": hashlib.sha256(data).hexdigest(),
            "password_verified": password is not None}


def restore_wallet(src: Path, dest: Path, password: str | None = None) -> dict:
    """Restore a wallet from a backup into a NEW file (an existing wallet is never overwritten)."""
    return backup_wallet(src, dest, password) | {"restored_to": str(dest)}


def change_wallet_password(path: Path, old_password: str, new_password: str, kdf_n: int | None = None) -> Path:
    """Re-encrypt the key under a new password. The new file is written and verified first, then swapped in
    atomically; the previous file is kept next to it (encrypted with the OLD password) and its path returned."""
    path = Path(path)
    check_password_strength(new_password)
    doc = validate_wallet_document(json.loads(path.read_text(encoding="utf-8")))
    params = _params_for(doc["network"])
    aad = wallet_aad(doc["address"], params.name)
    try:
        key = decrypt_private_key(doc["encrypted_private_key"], old_password, aad)
    except (WrongPasswordError, ValueError) as exc:
        raise SystemExit(str(exc))
    kwargs = {"n": kdf_n} if kdf_n else {}
    new_doc = dict(doc, encrypted_private_key=encrypt_private_key(private_key_bytes(key), new_password, aad, **kwargs))
    staged = path.with_name(path.name + ".new")
    _write_new_file(staged, json.dumps(new_doc, indent=2).encode("utf-8"))
    try:
        decrypt_private_key(json.loads(staged.read_text(encoding="utf-8"))["encrypted_private_key"], new_password, aad)
    except Exception:
        staged.unlink(missing_ok=True)
        raise SystemExit("Password change aborted: the re-encrypted file did not verify. The wallet is unchanged.")
    old_copy = path.with_name(f"{path.name}.before-password-change-{int(time.time())}")
    os.replace(path, old_copy)
    os.replace(staged, path)
    return old_copy


# ====================================================================== node access
def api(method: str, node: str, path: str, payload: dict | None = None):
    try:
        r = httpx.request(method, node.rstrip("/") + path, json=payload, timeout=10)
    except httpx.HTTPError as exc:
        raise SystemExit(f"Cannot reach node at {node}: {exc}")
    if r.status_code >= 400:
        try:
            err = r.json()["error"]
            raise SystemExit(f"Node rejected the request [{err['code']}]: {err['message']}")
        except (ValueError, KeyError):
            raise SystemExit(f"Node error {r.status_code}")
    return r.json()


def show_balance(wallet: dict, node: str, out=print, api_fn=api) -> dict:
    data = api_fn("GET", node, "/api/v1/account/" + wallet["address"])
    out(f"Address:   {wallet['address']}")
    out(f"Confirmed: {atomic_to_mai(data['balance'])} MAI")
    out(f"Immature:  {atomic_to_mai(data['immature_balance'])} MAI (mining rewards not yet spendable)")
    out(f"Pending:   -{atomic_to_mai(data['pending_outgoing'])} MAI outgoing")
    out(f"Available: {atomic_to_mai(data['available_balance'])} MAI")
    return data


def show_history(wallet: dict, node: str, page: int = 1, per_page: int = 15, out=print, api_fn=api) -> dict:
    data = api_fn("GET", node, f"/api/v1/address/{wallet['address']}/transactions?limit={per_page}&offset={(page - 1) * per_page}")
    if not data["transactions"] and not data["pending"]:
        out("No transactions yet.")
        return data
    for t in data["pending"]:
        sign = "-" if t["direction"] == "out" else "+"
        out(f"  PENDING   {t['direction']:<5} {sign}{atomic_to_mai(t['amount']):>14} MAI   {t['txid'][:16]}…")
    for t in data["transactions"]:
        sign = "-" if t["direction"] == "out" else "+"
        when = time.strftime("%Y-%m-%d %H:%M", time.gmtime(t["timestamp"]))
        extra = f"  (+{atomic_to_mai(t['fee'])} fee)" if t["direction"] == "out" else ""
        out(f"  {when}  {t['direction']:<5} {sign}{atomic_to_mai(t['amount']):>14} MAI   "
            f"{t['confirmations']:>4} conf   {t['txid'][:16]}…{extra}")
    pages = max(1, -(-data["total"] // per_page))
    out(f"Page {page} of {pages} ({data['total']} confirmed transactions)")
    return data


def prepare_transfer(wallet: dict, params, node: str, to: str, amount_text: str, fee_text: str | None,
                     out=print, api_fn=api) -> tuple[dict, int, int]:
    """Validate a transfer, show the preview (amount, fee, total, warnings). Returns (account, amount, fee)."""
    if not validate_address(to, params.address_prefix):
        raise SystemExit(f"Recipient is not a valid {params.name} address.")
    if to == wallet["address"]:
        raise SystemExit("You cannot send to your own address.")
    try:
        amount = mai_to_atomic(amount_text)
        fee = mai_to_atomic(fee_text) if fee_text else params.default_fee
    except ValueError as exc:
        raise SystemExit(str(exc))
    if fee < params.min_fee:
        raise SystemExit(f"Fee is below the network minimum of {atomic_to_mai(params.min_fee)} MAI.")
    account = api_fn("GET", node, "/api/v1/account/" + wallet["address"])
    fees = api_fn("GET", node, "/api/v1/fee")
    total = amount + fee
    out(f"Network: {params.label}\nFrom:    {wallet['address']}\nTo:      {to}")
    out(f"Amount:  {atomic_to_mai(amount)} MAI\nFee:     {atomic_to_mai(fee)} MAI\nTotal:   {atomic_to_mai(total)} MAI")
    median = fees.get("mempool_median_fee_atomic")
    if median is not None and fee < median:
        out(f"Note: pending transactions currently pay {atomic_to_mai(median)} MAI; a lower fee may confirm slowly.")
    if total > account["available_balance"]:
        raise SystemExit(f"Insufficient available balance ({atomic_to_mai(account['available_balance'])} MAI; "
                         "mining rewards need to mature first, and pending sends are reserved).")
    out("This transfer cannot be reversed once it is mined.")
    return account, amount, fee


# ====================================================================== interactive shell
HELP = """commands:
  address                     show your address
  balance                     confirmed / immature / pending / available
  history [page]              transaction history (pending first)
  send <to> <amount> [fee]    preview, confirm, sign locally and send
  lock | unlock               drop / re-enter the key (auto-locks after inactivity)
  backup <file>               copy the ENCRYPTED wallet file (never overwrites)
  help | quit"""


def run_shell(session: WalletSession, node: str, wallet_path: Path, *, input_fn=input, out=print,
              password_fn=getpass.getpass, api_fn=api, now=time.time) -> None:
    params = session.params
    out(f"[{params.label}] wallet {session.address}\nAuto-lock after {int(session.timeout)} s of inactivity. Type 'help'.")
    while True:
        try:
            line = input_fn(f"{'unlocked' if session.unlocked else 'locked'}> ").strip()
        except (EOFError, KeyboardInterrupt):
            out("")
            break
        session.touch()                                   # locks first if the timeout already passed
        if session.auto_locked:
            out("(wallet auto-locked after inactivity: use 'unlock' or enter your password when sending)")
            session.auto_locked = False
        if not line:
            continue
        cmd, *args = line.split()
        try:
            if cmd in ("quit", "exit"):
                break
            elif cmd == "help":
                out(HELP)
            elif cmd == "address":
                out(session.address)
            elif cmd == "balance":
                show_balance(session.wallet, node, out, api_fn)
            elif cmd == "history":
                show_history(session.wallet, node, int(args[0]) if args else 1, out=out, api_fn=api_fn)
            elif cmd == "lock":
                session.lock()
                out("Wallet locked.")
            elif cmd == "unlock":
                session.unlock(password_fn("Wallet password: "))
                out("Wallet unlocked.")
            elif cmd == "backup":
                if len(args) != 1:
                    out("usage: backup <file>")
                    continue
                out("WARNING: the backup contains your ENCRYPTED private key. Anyone who has this file AND your "
                    "password controls your funds. Store it offline, and test the restore.")
                info = backup_wallet(wallet_path, Path(args[0]))
                out(f"Backup written. SHA-256: {info['sha256']}")
            elif cmd == "send":
                if len(args) not in (2, 3):
                    out("usage: send <to> <amount> [fee]")
                    continue
                account, amount, fee = prepare_transfer(session.wallet, params, node, args[0], args[1],
                                                        args[2] if len(args) == 3 else None, out, api_fn)
                if input_fn("Type 'yes' to sign and send: ").strip().lower() != "yes":
                    out("Cancelled.")
                    continue
                if not session.unlocked:
                    session.unlock(password_fn("Wallet password: "))
                tx = session.sign_transfer(args[0], amount, fee, int(account["next_nonce"]), int(now()))
                result = api_fn("POST", node, "/api/v1/transactions", tx)
                out(f"Transaction accepted into the mempool.\nTXID: {result['txid']}")
            else:
                out(f"Unknown command '{cmd}'. Type 'help'.")
        except SystemExit as exc:                     # user-facing errors raised by the helpers
            out(f"Error: {exc}")
        except WrongPasswordError as exc:
            out(f"Error: {exc}")
        except (ValueError, WalletLockedError) as exc:
            out(f"Error: {exc}")
    session.lock()


# ====================================================================== command line
def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="MineAI wallet")
    ap.add_argument("--network", default=None, help="devnet | testnet (default: MINEAI_NETWORK or devnet)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("create", "address", "balance", "history", "send", "shell", "verify", "change-password"):
        s = sub.add_parser(name)
        s.add_argument("--wallet", required=True, type=Path)
        if name in ("balance", "history", "send", "shell"):
            s.add_argument("--node", default=None)
    sub.choices["history"].add_argument("--page", type=int, default=1)
    sub.choices["shell"].add_argument("--lock-timeout", type=float, default=DEFAULT_LOCK_TIMEOUT,
                                      help="seconds of inactivity before the key is dropped (default 120)")
    send = sub.choices["send"]
    send.add_argument("--to", required=True)
    send.add_argument("--amount", required=True, help='decimal MAI as text, e.g. "5" or "0.25"')
    send.add_argument("--fee", default=None)
    send.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    b = sub.add_parser("backup")
    b.add_argument("--wallet", required=True, type=Path)
    b.add_argument("--to", required=True, type=Path)
    b.add_argument("--verify-password", action="store_true", help="also prove the password decrypts the wallet")
    r = sub.add_parser("restore")
    r.add_argument("--from", dest="src", required=True, type=Path)
    r.add_argument("--wallet", required=True, type=Path, help="NEW wallet file to create (must not exist)")
    r.add_argument("--verify-password", action="store_true")
    return ap


def main() -> None:
    args = _parser().parse_args()
    params = config.get_params(args.network)
    node = getattr(args, "node", None) or f"http://127.0.0.1:{params.default_port}"
    print(f"[{params.label}]")

    if args.cmd == "create":
        password = get_password("Create wallet password: ")
        if "MINEAI_WALLET_PASSWORD" not in os.environ and getpass.getpass("Repeat password: ") != password:
            raise SystemExit("Passwords do not match.")
        try:
            address = create_wallet_file(args.wallet, password, params)
        except ValueError as exc:
            raise SystemExit(str(exc))
        print(f"Created wallet: {args.wallet}\nAddress: {address}")
        print("BACK UP this file (use the 'backup' command) and remember the password: there is no recovery. "
              f"This is a {params.name} wallet with no monetary value; never reuse a real password.")
        return

    if args.cmd == "backup":
        password = get_password("Wallet password: ") if args.verify_password else None
        info = backup_wallet(args.wallet, args.to, password)
        print(f"Backup written to {args.to}\nAddress: {info['address']} ({info['network']})\nSHA-256: {info['sha256']}")
        print("WARNING: this file holds your ENCRYPTED private key. Anyone with the file AND the password controls "
              "your funds. Store it offline. Test it with the 'restore' command on a spare path.")
        return
    if args.cmd == "restore":
        password = get_password("Wallet password: ") if args.verify_password else None
        info = restore_wallet(args.src, args.wallet, password)
        print(f"Restored {info['address']} ({info['network']}) to {args.wallet}")
        return
    if args.cmd == "verify":
        try:
            address = verify_wallet_password(args.wallet, get_password("Wallet password: "))
        except (WrongPasswordError, ValueError, OSError) as exc:
            raise SystemExit(f"Verification failed: {exc}")
        print(f"OK: the password decrypts the wallet for {address}")
        return
    if args.cmd == "change-password":
        old = get_password("Current password: ")
        new = get_password("New password: ")
        if "MINEAI_WALLET_PASSWORD" not in os.environ and getpass.getpass("Repeat new password: ") != new:
            raise SystemExit("Passwords do not match.")
        try:
            old_copy = change_wallet_password(args.wallet, old, new)
        except ValueError as exc:
            raise SystemExit(str(exc))
        print(f"Password changed. The previous file (encrypted with the OLD password) was kept at:\n  {old_copy}\n"
              "Delete it once you have verified the new password and refreshed your backups.")
        return

    wallet = load_wallet(args.wallet, params)
    if args.cmd == "address":
        print(wallet["address"])
    elif args.cmd == "balance":
        show_balance(wallet, node)
    elif args.cmd == "history":
        show_history(wallet, node, args.page)
    elif args.cmd == "shell":
        session = WalletSession(wallet, params, timeout=args.lock_timeout)
        try:
            session.unlock(get_password("Wallet password: "))
        except WrongPasswordError as exc:
            raise SystemExit(str(exc))
        run_shell(session, node, args.wallet)
    elif args.cmd == "send":
        account, amount, fee = prepare_transfer(wallet, params, node, args.to, args.amount, args.fee)
        if not args.yes and input("Type 'yes' to sign and send: ").strip().lower() != "yes":
            raise SystemExit("Cancelled.")
        session = WalletSession(wallet, params, timeout=60)
        try:
            session.unlock(get_password("Wallet password: "))
        except WrongPasswordError as exc:
            raise SystemExit(str(exc))
        try:
            tx = session.sign_transfer(args.to, amount, fee, int(account["next_nonce"]), int(time.time()))
        finally:
            session.lock()
        result = api("POST", node, "/api/v1/transactions", tx)
        print(f"Transaction accepted into the mempool.\nTXID: {result['txid']}\nMine a block to confirm it.")


if __name__ == "__main__":
    main()
