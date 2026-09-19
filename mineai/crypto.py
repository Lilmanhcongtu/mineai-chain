"""Addresses, canonical binary encodings, transaction signing and wallet-file encryption.

Every encoding here is normative and is documented in PROTOCOL.md. Do not change it
without bumping the protocol version.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import os
import struct
import re

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from .config import INT_MAX

HEX64 = re.compile(r"^[0-9a-f]{64}$")
_B32 = re.compile(r"^[A-Z2-7]+$")


# ---------------------------------------------------------------- primitive encodings
def b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii")


def b64d_strict(text: str, length: int) -> bytes:
    """Decode canonical urlsafe base64 of exactly `length` bytes, else ValueError."""
    if not isinstance(text, str) or not text.isascii():
        raise ValueError("bad base64")
    try:
        raw = base64.urlsafe_b64decode(text.encode("ascii"))
    except (binascii.Error, ValueError) as exc:
        raise ValueError("bad base64") from exc
    if len(raw) != length or b64e(raw) != text:
        raise ValueError("non-canonical or wrong-length base64")
    return raw


def u64(n: int) -> bytes:
    if type(n) is not int or n < 0 or n > INT_MAX:
        raise ValueError("integer out of range")
    return struct.pack(">Q", n)


def lp(text: str) -> bytes:
    """Length-prefixed (1 byte) ASCII string."""
    raw = text.encode("ascii")
    if len(raw) > 255:
        raise ValueError("string too long")
    return bytes([len(raw)]) + raw


# ---------------------------------------------------------------- keys and addresses
def public_key_bytes(private_key: Ed25519PrivateKey) -> bytes:
    return private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)


def private_key_bytes(private_key: Ed25519PrivateKey) -> bytes:
    return private_key.private_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption())


def address_from_public_key(pubkey: bytes, prefix: str) -> str:
    """address = PREFIX + base32(hash160 || checksum), upper-case, no padding.

    hash160 = SHA-256(pubkey)[:20]; checksum = SHA-256(PREFIX || hash160)[:4].
    """
    payload = hashlib.sha256(pubkey).digest()[:20]
    checksum = hashlib.sha256(prefix.encode("ascii") + payload).digest()[:4]
    return prefix + base64.b32encode(payload + checksum).decode("ascii").rstrip("=")


def validate_address(address: object, prefix: str) -> bool:
    """Strict: canonical upper-case form only (a lower-case alias would create a second
    account key for the same owner)."""
    if not isinstance(address, str) or not address.isascii() or not address.startswith(prefix):
        return False
    encoded = address[len(prefix):]
    if len(encoded) != 39 or not _B32.match(encoded):     # 24 bytes -> 39 base32 chars unpadded
        return False
    try:
        raw = base64.b32decode(encoded + "=", casefold=False)
    except (binascii.Error, ValueError):
        return False
    if len(raw) != 24:
        return False
    # 39 base32 chars carry 195 bits for 192 bits of data: the 3 spare bits must be zero,
    # otherwise several strings would decode to the same bytes (address aliasing).
    if base64.b32encode(raw).decode("ascii").rstrip("=") != encoded:
        return False
    payload, checksum = raw[:20], raw[20:]
    return checksum == hashlib.sha256(prefix.encode("ascii") + payload).digest()[:4]


# ---------------------------------------------------------------- transactions
TX_DOMAIN = b"MineAI/tx/v1\x00"
COINBASE_DOMAIN = b"MineAI/coinbase/v1\x00"


def transaction_signing_payload(tx: dict, network_id: str) -> bytes:
    return (TX_DOMAIN + lp(network_id) + lp(tx["sender"]) + lp(tx["recipient"])
            + u64(tx["amount"]) + u64(tx["fee"]) + u64(tx["nonce"]) + u64(tx["timestamp"])
            + b64d_strict(tx["public_key"], 32))


def compute_txid(tx: dict, network_id: str) -> str:
    """txid = SHA-256(signing_payload || signature) as lower-case hex."""
    return hashlib.sha256(
        transaction_signing_payload(tx, network_id) + b64d_strict(tx["signature"], 64)).hexdigest()


def sign_transaction(private_key: Ed25519PrivateKey, tx: dict, network_id: str, prefix: str) -> dict:
    """`tx` needs recipient, amount, fee, nonce, timestamp. Returns the complete signed tx."""
    pub = public_key_bytes(private_key)
    body = {
        "sender": address_from_public_key(pub, prefix),
        "recipient": tx["recipient"], "amount": tx["amount"], "fee": tx["fee"],
        "nonce": tx["nonce"], "timestamp": tx["timestamp"], "public_key": b64e(pub),
    }
    body["signature"] = b64e(private_key.sign(transaction_signing_payload(body, network_id)))
    body["txid"] = compute_txid(body, network_id)
    return body


def verify_transaction_signature(tx: dict, network_id: str, prefix: str) -> bool:
    try:
        pub = b64d_strict(tx["public_key"], 32)
        if address_from_public_key(pub, prefix) != tx["sender"]:
            return False
        Ed25519PublicKey.from_public_bytes(pub).verify(
            b64d_strict(tx["signature"], 64), transaction_signing_payload(tx, network_id))
        return compute_txid(tx, network_id) == tx["txid"]
    except (ValueError, KeyError, TypeError, InvalidSignature):
        return False


def coinbase_txid(network_id: str, height: int, recipient: str, subsidy: int, fees: int) -> str:
    return hashlib.sha256(COINBASE_DOMAIN + lp(network_id) + u64(height) + lp(recipient)
                          + u64(subsidy) + u64(fees)).hexdigest()


# ---------------------------------------------------------------- wallet file encryption
WALLET_FORMAT = "MineAI-Wallet-V0.2"
KDF_N = 2 ** 17          # ~128 MiB, ~0.3 s: OWASP-minimum-class scrypt cost
KDF_R = 8
KDF_P = 1
KDF_N_MAX = 2 ** 20      # refuse absurd cost parameters from an untrusted wallet file


def _kdf(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    if not (2 ** 12 <= n <= KDF_N_MAX) or n & (n - 1) or not (1 <= r <= 16) or not (1 <= p <= 4):
        raise ValueError("unacceptable KDF parameters")
    return Scrypt(salt=salt, length=32, n=n, r=r, p=p).derive(password.encode("utf-8"))


def wallet_aad(address: str, network_name: str) -> bytes:
    return f"{WALLET_FORMAT}|{network_name}|{address}".encode("ascii")


def encrypt_private_key(private_raw: bytes, password: str, aad: bytes, n: int = KDF_N) -> dict:
    salt, nonce = os.urandom(16), os.urandom(12)
    key = _kdf(password, salt, n, KDF_R, KDF_P)
    ct = AESGCM(key).encrypt(nonce, private_raw, aad)
    return {"kdf": "scrypt", "n": n, "r": KDF_R, "p": KDF_P,
            "salt": b64e(salt), "nonce": b64e(nonce), "ciphertext": b64e(ct)}


class WrongPasswordError(Exception):
    pass


def decrypt_private_key(blob: dict, password: str, aad: bytes) -> Ed25519PrivateKey:
    try:
        salt = b64d_strict(blob["salt"], 16)
        nonce = b64d_strict(blob["nonce"], 12)
        ct = base64.urlsafe_b64decode(blob["ciphertext"].encode("ascii"))
        key = _kdf(password, salt, int(blob["n"]), int(blob["r"]), int(blob["p"]))
        raw = AESGCM(key).decrypt(nonce, ct, aad)
    except InvalidTag as exc:
        raise WrongPasswordError("wrong password or corrupted wallet file") from exc
    except (KeyError, TypeError, ValueError, binascii.Error) as exc:
        raise ValueError("malformed wallet file") from exc
    return Ed25519PrivateKey.from_private_bytes(raw)
