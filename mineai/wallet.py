"""Local wallet CLI. Keys never leave this process; transactions are signed locally.

Passwords are read from a hidden prompt (or, for automation only, MINEAI_WALLET_PASSWORD).
There is deliberately no --password flag: command-line arguments are visible to other processes.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import time
from pathlib import Path

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from . import config
from .crypto import (
    WALLET_FORMAT, WrongPasswordError, address_from_public_key, b64e, decrypt_private_key,
    encrypt_private_key, private_key_bytes, public_key_bytes, sign_transaction, validate_address,
    wallet_aad,
)
from .util import atomic_to_mai, mai_to_atomic

MIN_PASSWORD_LENGTH = 10


def create_wallet_file(path: Path, password: str, params, kdf_n: int | None = None) -> str:
    """Create a new wallet file. Refuses to overwrite. Returns the address."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    private = Ed25519PrivateKey.generate()
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
    data = json.dumps(payload, indent=2).encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)   # fail if it exists
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        raise SystemExit(f"Refusing to overwrite existing file: {path}")
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    return address


def load_wallet(path: Path, params) -> dict:
    if not path.exists():
        raise SystemExit(f"Wallet not found: {path}")
    try:
        wallet = json.loads(path.read_text(encoding="utf-8"))
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
    except WrongPasswordError as exc:
        raise SystemExit(str(exc))
    except ValueError as exc:
        raise SystemExit(str(exc))


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


def main() -> None:
    ap = argparse.ArgumentParser(description="MineAI wallet")
    ap.add_argument("--network", default=None, help="devnet | testnet (default: MINEAI_NETWORK or devnet)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("create", "address", "balance", "send"):
        s = sub.add_parser(name)
        s.add_argument("--wallet", required=True, type=Path)
        if name in ("balance", "send"):
            s.add_argument("--node", default=None)
    send = sub.choices["send"]
    send.add_argument("--to", required=True)
    send.add_argument("--amount", required=True, help='decimal MAI as text, e.g. "5" or "0.25"')
    send.add_argument("--fee", default=None)
    send.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = ap.parse_args()

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
        print("BACK UP this file and remember the password: there is no recovery. "
              f"This is a {params.name} wallet with no monetary value; never reuse a real password.")
        return

    wallet = load_wallet(args.wallet, params)
    if args.cmd == "address":
        print(wallet["address"])
    elif args.cmd == "balance":
        data = api("GET", node, "/api/account/" + wallet["address"])
        print(f"Address:   {wallet['address']}")
        print(f"Confirmed: {atomic_to_mai(data['balance'])} MAI")
        print(f"Immature:  {atomic_to_mai(data['immature_balance'])} MAI (mining rewards not yet spendable)")
        print(f"Pending:   -{atomic_to_mai(data['pending_outgoing'])} MAI outgoing")
        print(f"Available: {atomic_to_mai(data['available_balance'])} MAI")
    elif args.cmd == "send":
        if not validate_address(args.to, params.address_prefix):
            raise SystemExit(f"Recipient is not a valid {params.name} address.")
        try:
            amount = mai_to_atomic(args.amount)
            fee = mai_to_atomic(args.fee) if args.fee else params.default_fee
        except ValueError as exc:
            raise SystemExit(str(exc))
        account = api("GET", node, "/api/account/" + wallet["address"])
        total = amount + fee
        print(f"Network: {params.label}\nFrom:    {wallet['address']}\nTo:      {args.to}")
        print(f"Amount:  {atomic_to_mai(amount)} MAI\nFee:     {atomic_to_mai(fee)} MAI\nTotal:   {atomic_to_mai(total)} MAI")
        if total > account["available_balance"]:
            raise SystemExit(f"Insufficient available balance ({atomic_to_mai(account['available_balance'])} MAI).")
        if not args.yes and input("Type 'yes' to sign and send: ").strip().lower() != "yes":
            raise SystemExit("Cancelled.")
        private = unlock(wallet, params, get_password("Wallet password: "))
        tx = sign_transaction(private, {
            "recipient": args.to, "amount": amount, "fee": fee,
            "nonce": int(account["next_nonce"]), "timestamp": int(time.time()),
        }, params.network_id, params.address_prefix)
        result = api("POST", node, "/api/transactions", tx)
        print(f"Transaction accepted into the mempool.\nTXID: {result['txid']}\nMine a block to confirm it.")


if __name__ == "__main__":
    main()
