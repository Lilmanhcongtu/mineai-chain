from __future__ import annotations

import hashlib
import re
from decimal import Decimal

from .config import ATOMIC_UNITS, INT_MAX


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


_PLAIN_DECIMAL = re.compile(r"^[0-9]+(\.[0-9]{1,6})?$")
_TOO_PRECISE = re.compile(r"^[0-9]+\.[0-9]{7,}$")


def mai_to_atomic(value: str | int | Decimal) -> int:
    """Parse a MAI amount into atomic units. Only plain decimals are accepted ("5", "0.25", "12.345678"):
    no floats, no exponents ("1e3"), no digit separators ("1_0"), no signs, no spaces inside."""
    if isinstance(value, float) or isinstance(value, bool):
        raise ValueError("amounts must be given as strings or integers, never floats")
    text = str(value).strip()
    if text.startswith("-"):
        raise ValueError("amount must be greater than zero")
    if _TOO_PRECISE.match(text):
        raise ValueError("MAI supports at most 6 decimal places")
    if not _PLAIN_DECIMAL.match(text):
        raise ValueError("not a valid amount (use a plain decimal such as 5 or 0.25)")
    d = Decimal(text)
    if d <= 0:
        raise ValueError("amount must be greater than zero")
    scaled = d * ATOMIC_UNITS
    atomic = int(scaled)
    if atomic > INT_MAX:
        raise ValueError("amount too large")
    return atomic


def atomic_to_mai(value: int) -> str:
    sign = "-" if value < 0 else ""
    whole, frac = divmod(abs(int(value)), ATOMIC_UNITS)
    text = f"{whole}.{frac:06d}".rstrip("0").rstrip(".")
    return sign + text
