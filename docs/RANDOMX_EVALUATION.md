# RandomX evaluation (not implemented)

**Status: MineAI does NOT support RandomX.** The development proof of work is SHA-256 (PROTOCOL.md 5.4/5.5).
Nothing in the code, the documentation or the release packages may claim RandomX support until it has been
specified, implemented, and tested on the platforms listed below. This document is an evaluation only.

> **Honesty note.** This was written from general knowledge of the RandomX project and its ecosystem. It was **not**
> verified by building or running RandomX in this repository, and the numbers below are approximate. Every item marked
> *(verify)* must be checked against the upstream repository and against real measurements before any decision.

## What adopting it would mean

RandomX would replace the block-hash function itself, so it is a **consensus change**, not a miner plug-in. It must be
written into PROTOCOL.md first and then implemented and tested in the same order as every other consensus rule:

1. **Spec.** Hash input (the existing 8-field header encoding can be reused as the RandomX input), how the result is compared
   with the existing numeric target (a 32-byte output fits the current `int_be(hash) <= target(D)` rule), and the **key/seed**:
   RandomX needs a key that changes every epoch (Monero: every 2048 blocks with a 64-block lag), derived from a block hash. That
   requires defining the epoch length, the lag, and what happens across reorganizations that cross an epoch boundary.
2. **Activation.** A new network id / genesis (testnet only, no mainnet exists), never a silent change on a running network.
3. **Verification path.** Every node must verify every block's PoW with RandomX. Cheap checks (size, structure, difficulty,
   timestamp) must still run first so an attacker cannot make a node spend RandomX time on obviously invalid blocks.
4. **Implementation.** A native library binding (below), a `PowBackend` for miners (`mineai/pow.py` already provides the seam),
   dataset/cache lifecycle management, and an epoch-change test matrix (start, boundary, reorg across the boundary).
5. **Tests.** Known-answer vectors from upstream, verification fuzzing, malformed-input and memory-exhaustion cases,
   reorganizations across epochs, and multi-platform CI.

## Integration complexity (high)

* RandomX is a C++ library. There is no pure-Python implementation that would be usable for consensus (far too slow).
* Options: build the upstream library and call its C API with `ctypes`/`cffi`, or use a third-party Python wrapper
  *(verify: maintenance status, license, and whether prebuilt wheels exist for Windows and Linux)*. A wrapper is unaudited
  native code in the consensus path.
* Two operating modes: **light** (cache only; slow per hash, used for verification) and **fast** (full dataset; used by miners).

## Native build requirements

* A C++ toolchain and CMake: MSVC (Visual Studio Build Tools) or MinGW on Windows, GCC/Clang on Linux *(verify minimum versions)*.
* The build must be reproducible and its output verified, or the release becomes an opaque binary supply-chain risk.

## Platform compatibility

| | Windows | Linux |
|---|---|---|
| Upstream support | Yes (Monero ships Windows builds) | Yes |
| JIT | Needs executable (RWX) memory; can trigger antivirus heuristics; an interpreter fallback exists but is much slower | Same; may be restricted under hardened kernels or SELinux |
| Large/huge pages | Optional; needs the "Lock pages in memory" privilege | Optional; needs configured huge pages |
| Packaging | Bundle the DLL/static lib with the Python freezer (PyInstaller); unsigned binaries add SmartScreen/AV friction | Distribute build instructions or distro-specific packages |

Neither platform has been tested here.

## Licensing

Upstream RandomX is, to my knowledge, BSD-3-Clause *(verify against the LICENSE file of the exact release you would ship)*.
That is compatible with redistribution but requires keeping the notice. Any Python wrapper has its own license.

## Memory and time *(approximate; measure before deciding)*

* Verification (light mode): a cache of roughly 256 MiB per process, and on the order of tens of milliseconds per hash on a
  desktop CPU. SHA-256 verification is microseconds, so **block verification becomes thousands of times more expensive**, which
  matters for initial sync and for denial-of-service resistance.
* Mining (fast mode): a dataset of roughly 2 GiB plus the cache. Small VPS instances and low-RAM machines cannot mine
  efficiently and can be memory-starved by a node that also runs the explorer and P2P layer.
* Dataset initialization takes seconds to about a minute and is repeated at every epoch change.

## Verification behaviour and security risks

* **DoS surface:** peers can force expensive verifications. Keep cheap checks first, cap verifications per peer per second,
  and score peers that send blocks failing PoW.
* **Native-code risk:** a crash or memory-safety bug in the library is a consensus-node crash. It must be fuzzed and isolated.
* **Determinism:** the result must be bit-identical across compilers, CPUs and JIT versus interpreter mode; that needs
  cross-platform known-answer tests in CI.
* **Epoch/reorg edge cases:** a reorganization that crosses an epoch boundary changes the key; the state machine must handle it.
* **"ASIC resistance" is a design goal, not a guarantee**, and a small testnet hashrate is easy to dominate regardless of algorithm.
* **Supply chain:** pin the upstream commit, verify hashes, and prefer building from source in CI over fetching binaries.

## Recommendation

Keep SHA-256 through the private and public testnet (Milestones 7 and 8), which exercise networking, forks, difficulty and
wallets without adding a native dependency. Decide on RandomX afterwards, on a separate branch, only if the project actually needs
CPU-fairness and can staff the native-code review. Before claiming support: spec merged, implementation tested on Windows and
Linux, known-answer and epoch/reorg tests passing, verification cost measured and its DoS limits chosen, and licensing confirmed.
