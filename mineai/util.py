from __future__ import annotations

import hashlib
from decimal import Decimal, InvalidOperation

from .config import ATOMIC_UNITS, INT_MAX


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def mai_to_atomic(value: str | int | Decimal) -> int:
    """Parse a decimal MAI amount into atomic units. Floats are rejected on purpose."""
    if isinstance(value, float) or isinstance(value, bool):
        raise ValueError("amounts must be given as strings or integers, never floats")
    try:
        d = Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise ValueError("not a valid amount") from exc
    if not d.is_finite() or d <= 0:
        raise ValueError("amount must be greater than zero")
    scaled = d * ATOMIC_UNITS
    if scaled != scaled.to_integral_value():
        raise ValueError("MAI supports at most 6 decimal places")
    atomic = int(scaled)
    if atomic > INT_MAX:
        raise ValueError("amount too large")
    return atomic


def atomic_to_mai(value: int) -> str:
    sign = "-" if value < 0 else ""
    whole, frac = divmod(abs(int(value)), ATOMIC_UNITS)
    text = f"{whole}.{frac:06d}".rstrip("0").rstrip(".")
    return sign + text
