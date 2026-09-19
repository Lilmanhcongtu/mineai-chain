"""Proof-of-work search backends (mining side only).

Consensus verification is fixed by the protocol: the block hash is SHA-256 of the header and must satisfy the
numeric target (PROTOCOL.md 5.4 / 5.5). A backend here only decides HOW a miner searches for a nonce, so a
different search implementation (a faster SHA-256, a GPU kernel, ...) can be plugged in without touching consensus.

RandomX is NOT implemented. Adopting it would change the block-hash function itself (and add an epoch/seed
mechanism), which is a consensus change that must be specified in PROTOCOL.md and put through the full test
program first. See docs/RANDOMX_EVALUATION.md.
"""
from __future__ import annotations

import hashlib
import struct
from abc import ABC, abstractmethod
from typing import Callable

from .consensus import meets_target, target_for

CHECK_EVERY = 512          # how many hashes between checks of the stop flag / progress reports


class PowBackend(ABC):
    name: str

    @abstractmethod
    def search(self, prefix: bytes, difficulty: int, start: int, step: int,
               should_stop: Callable[[], bool], on_progress: Callable[[int], None]) -> tuple[int, str] | None:
        """Try nonces start, start+step, ... until one meets `difficulty` (return (nonce, hash_hex)) or
        `should_stop()` becomes true (return None). `on_progress(n)` reports n newly tried hashes."""

    @abstractmethod
    def verify(self, prefix: bytes, nonce: int, difficulty: int) -> bool:
        """Does this nonce satisfy the difficulty? (Must agree with consensus verification.)"""


class Sha256Backend(PowBackend):
    name = "sha256"

    def search(self, prefix, difficulty, start, step, should_stop, on_progress):
        target = target_for(difficulty)
        sha256, pack, from_bytes = hashlib.sha256, struct.Struct(">Q").pack, int.from_bytes
        nonce, tried = start, 0
        while True:
            for _ in range(CHECK_EVERY):
                digest = sha256(prefix + pack(nonce)).digest()
                if from_bytes(digest, "big") <= target:
                    on_progress(tried + 1)
                    return nonce, digest.hex()
                nonce += step
                tried += 1
            on_progress(tried)
            tried = 0
            if should_stop():
                return None

    def verify(self, prefix, nonce, difficulty):
        return meets_target(hashlib.sha256(prefix + struct.pack(">Q", nonce)).hexdigest(), difficulty)


BACKENDS: dict[str, type[PowBackend]] = {"sha256": Sha256Backend}


def get_backend(name: str) -> PowBackend:
    try:
        return BACKENDS[name]()
    except KeyError:
        raise ValueError(f"unknown proof-of-work backend {name!r}; available: {', '.join(BACKENDS)}") from None
