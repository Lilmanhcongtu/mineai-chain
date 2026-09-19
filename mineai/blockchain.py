"""Blockchain state machine: applies consensus rules (consensus.py) to persistent state (storage.py).

Account model: state = {address: (balance, nonce)}. Balances are integer atomic units.
Best chain = the valid chain with the greatest cumulative work (PROTOCOL.md section 8).
Nothing here depends on a web framework.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import consensus as C
from .config import NetworkParams, get_params
from .consensus import ValidationError
from .crypto import validate_address
from .log import event
from .storage import WIRE_KEYS, Storage, StorageError

log = logging.getLogger("mineai.chain")


class ChainError(RuntimeError):
    """The local database is inconsistent with the selected network."""


@dataclass
class BlockResult:
    """Outcome of process_block. status: extended | reorg | side | orphan | duplicate."""
    status: str
    block: dict
    disconnected: int = 0
    connected: int = 0


class _Ctx:
    """Scratch state used while applying a sequence of transactions at a given height."""

    def __init__(self, chain: "Blockchain", height: int):
        self.chain, self.height = chain, height
        p = chain.params
        self.immature = chain.storage.coinbase_amounts(height - p.coinbase_maturity + 1, height - 1)
        self.overlay: dict[str, list[int]] = {}
        self.undo: dict[str, tuple[int, int]] = {}
        self.fees = 0

    def acct(self, address: str) -> list[int]:
        if address not in self.overlay:
            balance, nonce = self.chain.storage.get_account(address)
            self.undo[address] = (balance, nonce)
            self.overlay[address] = [balance, nonce]
        return self.overlay[address]

    def apply(self, tx: dict) -> None:
        """Validate `tx` against current scratch state and apply it. Raises before mutating."""
        sender = self.acct(tx["sender"])
        if tx["nonce"] != sender[1] + 1:
            raise ValidationError(f"invalid nonce: expected {sender[1] + 1}", "bad_nonce")
        spendable = sender[0] - self.immature.get(tx["sender"], 0)
        cost = tx["amount"] + tx["fee"]
        if cost > spendable:
            raise ValidationError("insufficient spendable balance", "insufficient_funds")
        recipient = self.acct(tx["recipient"])
        sender[0] -= cost
        sender[1] = tx["nonce"]
        recipient[0] += tx["amount"]
        self.fees += tx["fee"]

    def changes(self) -> dict[str, tuple[int, int]]:
        return {a: (v[0], v[1]) for a, v in self.overlay.items()}


class Blockchain:
    def __init__(self, db_path: Path, params: NetworkParams | None = None,
                 clock: Callable[[], float] = time.time):
        self.params = params or get_params()
        self.clock = clock
        self.lock = threading.RLock()
        self.orphans: OrderedDict[str, dict] = OrderedDict()     # in-memory, bounded (PROTOCOL 8.3)
        self.reorg_count = 0
        self.storage = Storage(db_path)
        try:
            self._open()
        except Exception:
            self.storage.close()
            raise

    def close(self) -> None:
        self.storage.close()

    def expected_difficulty(self, parent: dict) -> int:
        """Difficulty a child of `parent` must declare. A function of the parent's ancestry only.
        Constant for now; dynamic difficulty (Milestone 5) replaces this."""
        return self.params.difficulty

    # ------------------------------------------------------------------ startup checks
    def _open(self) -> None:
        p = self.params
        genesis = C.genesis_block(p)
        if p.genesis_hash and genesis["hash"] != p.genesis_hash:
            raise ChainError("computed genesis hash does not match the pinned value for "
                             f"{p.name}: the software and the network profile disagree")
        if self.storage.block_count() == 0:
            with self.storage.atomic():
                self.storage.insert_genesis(genesis)
                self.storage.meta_set("network_id", p.network_id)
                self.storage.meta_set("genesis_hash", genesis["hash"])
            event(log, logging.INFO, "chain_initialized", network=p.name, genesis=genesis["hash"])
            return
        stored_net = self.storage.meta_get("network_id")
        if stored_net != p.network_id:
            raise ChainError(f"database belongs to network {stored_net!r}, not {p.network_id!r}")
        stored = self.storage.get_block_by_height(0)
        if stored is None or stored["hash"] != genesis["hash"] or \
                self.storage.meta_get("genesis_hash") != genesis["hash"]:
            raise ChainError("stored genesis block does not match this network")
        tip = self.storage.latest_block()
        if C.block_hash(p, tip) != tip["hash"]:
            raise ChainError("tip block hash does not verify: database corrupted")
        if self.storage.total_balances() != self.minted_supply():
            raise ChainError("sum of balances differs from minted supply: database corrupted")
        event(log, logging.INFO, "chain_opened", network=p.name, height=tip["height"], tip=tip["hash"])

    def verify_integrity(self) -> None:
        """Full offline re-verification of the stored best chain and replay of balances. Slow: O(chain)."""
        try:
            self._verify_integrity()
        except ValidationError as exc:
            raise ChainError(f"stored chain violates consensus rules: {exc}") from exc

    def _verify_integrity(self) -> None:
        p, prev, minted, work = self.params, None, 0, 0
        balances: dict[str, int] = {}
        nonces: dict[str, int] = {}
        for block in self.storage.iter_blocks_ascending():
            if block["height"] == 0:
                if block["hash"] != C.genesis_block(p)["hash"]:
                    raise ChainError("genesis mismatch")
                prev = block
                continue
            C.check_block_structure({k: block[k] for k in C.BLOCK_FIELDS}, p, self.expected_difficulty(prev))
            if block["previous_hash"] != prev["hash"] or block["height"] != prev["height"] + 1:
                raise ChainError(f"broken link at height {block['height']}")
            work += C.block_work(block["difficulty"])
            if int(block["total_work"]) != work:
                raise ChainError(f"cumulative work mismatch at height {block['height']}")
            subsidy = C.subsidy_for(minted, p)
            fees = 0
            for tx in block["transactions"][1:]:
                if tx["nonce"] != nonces.get(tx["sender"], 0) + 1:
                    raise ChainError(f"bad nonce sequence at height {block['height']}")
                balances[tx["sender"]] = balances.get(tx["sender"], 0) - tx["amount"] - tx["fee"]
                if balances[tx["sender"]] < 0:
                    raise ChainError(f"negative balance at height {block['height']}")
                nonces[tx["sender"]] = tx["nonce"]
                balances[tx["recipient"]] = balances.get(tx["recipient"], 0) + tx["amount"]
                fees += tx["fee"]
            C.check_coinbase(block["transactions"][0], p, block["height"], subsidy, fees)
            cb = block["transactions"][0]
            balances[cb["recipient"]] = balances.get(cb["recipient"], 0) + cb["amount"]
            minted += subsidy
            prev = block
        if minted != self.minted_supply():
            raise ChainError("minted supply mismatch")
        for address, balance in balances.items():
            if self.storage.get_account(address) != (balance, nonces.get(address, 0)):
                raise ChainError(f"account state mismatch for {address}")

    # ------------------------------------------------------------------ read helpers
    def now(self) -> int:
        return int(self.clock())

    def tip(self) -> dict:
        return self.storage.latest_block()

    def tip_work(self) -> int:
        return self.storage.tip_work()

    def minted_supply(self) -> int:
        return int(self.storage.meta_get("minted_supply") or 0)

    def next_subsidy(self) -> int:
        return C.subsidy_for(self.minted_supply(), self.params)

    def has_block(self, block_hash: str) -> bool:
        return self.storage.is_main(block_hash) or self.storage.side_get(block_hash) is not None \
            or block_hash in self.orphans

    def get_block_any(self, block_hash: str) -> dict | None:
        """A block by hash from the best chain or a stored side branch, in wire form."""
        main = self.storage.get_block_by_hash(block_hash)
        if main:
            return {k: main[k] for k in WIRE_KEYS}
        side = self.storage.side_get(block_hash)
        return side["block"] if side and side["status"] == "ok" else None

    def locator(self) -> list[str]:
        """Hashes of the best chain, newest first: 10 consecutive, then exponentially spaced, ending at genesis."""
        tip_height = self.tip()["height"]
        heights, step, h = [], 1, tip_height
        while h > 0:
            heights.append(h)
            if len(heights) >= 10:
                step *= 2
            h -= step
        heights.append(0)
        return [self.storage.get_block_by_height(x)["hash"] for x in heights][:32]

    def blocks_after_locator(self, locator: list[str], count: int) -> list[dict]:
        """Blocks of OUR best chain after the first locator hash that is on it (from height 1 if none)."""
        start = 0
        for h in locator:
            block = self.storage.get_block_by_hash(h)
            if block:
                start = block["height"]
                break
        return [{k: b[k] for k in WIRE_KEYS} for b in self.storage.blocks_after(start, count)]

    def account(self, address: str) -> dict:
        if not validate_address(address, self.params.address_prefix):
            raise ValidationError("invalid MAI address", "bad_address")
        with self.lock:
            balance, nonce = self.storage.get_account(address)
            height = self.tip()["height"] + 1
            immature = self.storage.coinbase_amounts(
                height - self.params.coinbase_maturity + 1, height - 1).get(address, 0)
            pending = self.storage.mempool_for_sender(address)
            pending_out = sum(t["amount"] + t["fee"] for t in pending)
            return {
                "address": address,
                "balance": balance,
                "immature_balance": immature,
                "available_balance": balance - immature - pending_out,
                "confirmed_nonce": nonce,
                "next_nonce": nonce + len(pending) + 1,
                "pending_outgoing": pending_out,
            }

    def find_transaction(self, txid: str) -> dict | None:
        loc = self.storage.tx_location(txid)
        if loc:
            height, index = loc
            block = self.storage.get_block_by_height(height)
            return {"status": "confirmed", "height": height, "index": index,
                    "confirmations": self.tip()["height"] - height + 1,
                    "block_hash": block["hash"], "transaction": block["transactions"][index]}
        tx = self.storage.mempool_get(txid)
        return {"status": "mempool", "confirmations": 0, "transaction": tx} if tx else None

    # ------------------------------------------------------------------ mempool
    def submit_transaction(self, tx: dict, now: int | None = None) -> dict:
        p = self.params
        now = self.now() if now is None else now
        try:
            C.check_transaction(tx, p)
            with self.lock:
                self._admit(tx, now)
        except ValidationError as exc:
            event(log, logging.INFO, "tx_rejected", code=exc.code,
                  txid=tx.get("txid") if isinstance(tx, dict) else None)
            raise
        event(log, logging.INFO, "tx_accepted", txid=tx["txid"])
        return tx

    def _admit(self, tx: dict, now: int, check_window: bool = True) -> None:
        p, st = self.params, self.storage
        if check_window and abs(tx["timestamp"] - now) > p.mempool_timestamp_window:
            raise ValidationError("transaction timestamp is outside the acceptable window", "bad_timestamp")
        if st.tx_location(tx["txid"]) or st.mempool_contains(tx["txid"]):
            raise ValidationError("duplicate transaction", "duplicate_tx")
        if st.mempool_count() >= p.mempool_max_txs:
            raise ValidationError("mempool is full", "mempool_full")
        info = self.account(tx["sender"])
        if info["next_nonce"] - info["confirmed_nonce"] - 1 >= p.mempool_max_per_sender:
            raise ValidationError("too many pending transactions for this sender", "mempool_sender_limit")
        if tx["nonce"] != info["next_nonce"]:
            raise ValidationError(
                f"invalid nonce: expected {info['next_nonce']} (replacement of pending transactions "
                "is not supported)", "bad_nonce")
        if tx["amount"] + tx["fee"] > info["available_balance"]:
            raise ValidationError("insufficient available balance", "insufficient_funds")
        try:
            st.mempool_add(tx)
        except StorageError as exc:
            raise ValidationError(str(exc), "duplicate_tx") from exc

    def _prune_mempool(self) -> None:
        """Drop mempool transactions that can no longer be mined. Call inside storage.atomic()."""
        st, height = self.storage, self.tip()["height"] + 1
        immature = st.coinbase_amounts(height - self.params.coinbase_maturity + 1, height - 1)
        drop: list[str] = []
        for sender in st.mempool_senders():
            balance, nonce = st.get_account(sender)
            spendable = balance - immature.get(sender, 0)
            broken = False
            for tx in st.mempool_for_sender(sender):
                cost = tx["amount"] + tx["fee"]
                if broken or tx["nonce"] != nonce + 1 or cost > spendable:
                    drop.append(tx["txid"])
                    broken = broken or tx["nonce"] > nonce + 1 or cost > spendable
                    continue
                nonce += 1
                spendable -= cost
        if drop:
            st.mempool_remove(drop)
            event(log, logging.INFO, "mempool_pruned", removed=len(drop))

    def _restore_mempool(self, disconnected: list[dict], connected: list[dict], now: int) -> None:
        """After a reorganization: drop what the new branch confirmed, re-admit what only the old
        branch had (oldest first, preserving per-sender nonce order), then prune. Inside atomic()."""
        st = self.storage
        confirmed = {tx["txid"] for b in connected for tx in b["transactions"][1:]}
        st.mempool_remove(list(confirmed))
        readmitted = 0
        for block in reversed(disconnected):                 # disconnect_tip yields newest first
            for tx in block["transactions"][1:]:
                if tx["txid"] in confirmed:
                    continue
                try:
                    self._admit(tx, now, check_window=False)
                    readmitted += 1
                except ValidationError:
                    pass                                     # conflicts with the new branch: dropped
        self._prune_mempool()
        event(log, logging.INFO, "mempool_restored", readmitted=readmitted, confirmed_removed=len(confirmed))

    # ------------------------------------------------------------------ mining
    def mining_template(self, miner_address: str, now: int | None = None) -> dict:
        p = self.params
        if not validate_address(miner_address, p.address_prefix):
            raise ValidationError("invalid miner address", "bad_address")
        now = self.now() if now is None else now
        with self.lock:
            tip = self.tip()
            height = tip["height"] + 1
            mtp = C.median_time_past(self.storage.recent_timestamps(p.mtp_window))
            ctx = _Ctx(self, height)
            selected: list[dict] = []
            remaining = self.storage.mempool_list()
            progress = True
            while progress and remaining and len(selected) < p.max_regular_txs_per_block:
                progress, deferred = False, []
                for tx in remaining:
                    if len(selected) >= p.max_regular_txs_per_block:
                        break
                    try:
                        ctx.apply(tx)
                    except ValidationError:
                        deferred.append(tx)      # may become valid once an earlier nonce is selected
                    else:
                        selected.append(tx)
                        progress = True
                remaining = deferred
            subsidy = self.next_subsidy()
            cb = C.coinbase_tx(p, height, miner_address, subsidy, ctx.fees)
            txs = [cb] + selected
            return {
                "network_id": p.network_id,
                "height": height,
                "previous_hash": tip["hash"],
                "merkle_root": C.merkle_root([t["txid"] for t in txs]),
                "timestamp": max(now, mtp + 1),
                "difficulty": self.expected_difficulty(tip),
                "nonce": 0,
                "transactions": txs,
            }

    # ------------------------------------------------------------------ blocks: local mining API
    def submit_mined_block(self, block: dict, now: int | None = None) -> dict:
        """Strict path used by the local miner API: the block must extend the current best tip."""
        now = self.now() if now is None else now
        try:
            with self.lock:
                p, st = self.params, self.storage
                tip = self.tip()
                C.check_block_structure(block, p, self.expected_difficulty(tip))
                if block["height"] != tip["height"] + 1 or block["previous_hash"] != tip["hash"]:
                    raise ValidationError("stale block: does not extend the current tip", "stale")
                accepted = self._extend_tip(block, now)
        except ValidationError as exc:
            event(log, logging.INFO, "block_rejected", code=exc.code,
                  height=block.get("height") if isinstance(block, dict) else None)
            raise
        event(log, logging.INFO, "block_accepted", height=accepted["height"], hash=accepted["hash"],
              txs=len(accepted["transactions"]) - 1)
        return accepted

    # ------------------------------------------------------------------ blocks: network path
    def process_block(self, block: dict, now: int | None = None) -> BlockResult:
        """Handle a block from any source, including competing branches (PROTOCOL.md section 8.3)."""
        now = self.now() if now is None else now
        try:
            with self.lock:
                result = self._process(block, now)
                if result.status in ("extended", "reorg", "side"):
                    self._adopt_orphans(result.block["hash"], now)
        except ValidationError as exc:
            event(log, logging.INFO, "block_rejected", code=exc.code,
                  height=block.get("height") if isinstance(block, dict) else None)
            raise
        return result

    def _process(self, block: dict, now: int) -> BlockResult:
        p, st = self.params, self.storage
        h = block.get("hash") if isinstance(block, dict) else None
        if isinstance(h, str):
            side = st.side_get(h)
            if side and side["status"] == "invalid":
                raise ValidationError("block is known to be invalid", "invalid_block")
            if side and side["total_work"] > st.tip_work():
                # A stored branch that has more work than our tip but was never adopted (a reorganization
                # was interrupted, or the branch only just got heavier): retry it instead of ignoring it.
                return self._reorganize(h, now)
            if st.is_main(h) or side or h in self.orphans:
                return BlockResult("duplicate", block)
        # Stateless rules only (the difficulty rule needs the parent, checked below).
        C.check_block_structure(block, p, block.get("difficulty") if isinstance(block, dict) else None)
        parent_hash = block["previous_hash"]
        parent = st.get_block_by_hash(parent_hash)
        parent_side = None if parent else st.side_get(parent_hash)
        if parent_side and parent_side["status"] == "invalid":
            raise ValidationError("block descends from an invalid block", "invalid_parent")
        if parent is None and parent_side is None:
            self._add_orphan(block)
            return BlockResult("orphan", block)
        parent_block = parent if parent else parent_side["block"]
        parent_work = int(parent["total_work"]) if parent else parent_side["total_work"]
        if block["height"] != parent_block["height"] + 1:
            raise ValidationError("block height does not follow its parent", "bad_height")
        if block["difficulty"] != self.expected_difficulty(parent_block):
            raise ValidationError("incorrect difficulty for this branch", "bad_difficulty")
        total = parent_work + C.block_work(block["difficulty"])
        tip = self.tip()
        if parent_hash == tip["hash"]:
            stored = self._extend_tip(block, now)
            event(log, logging.INFO, "block_accepted", height=stored["height"], hash=stored["hash"],
                  txs=len(stored["transactions"]) - 1)
            return BlockResult("extended", stored)
        if parent and tip["height"] - parent["height"] > p.max_reorg_depth:
            raise ValidationError("fork is deeper than the reorganization limit", "reorg_too_deep")
        st.side_add(block, total, now)
        event(log, logging.INFO, "side_block_stored", height=block["height"], hash=block["hash"], work=total)
        if total > st.tip_work():
            return self._reorganize(block["hash"], now)
        self._housekeeping()
        return BlockResult("side", block)

    def _extend_tip(self, block: dict, now: int) -> dict:
        """Validate `block` against state and connect it; it must already extend the tip."""
        st = self.storage
        total = st.tip_work() + C.block_work(block["difficulty"])
        with st.atomic():                                        # block + state + mempool: all or nothing
            stored = self._connect(block, now, total)
            st.mempool_remove([t["txid"] for t in block["transactions"][1:]])
            self._prune_mempool()
        self._housekeeping()
        return stored

    def _connect(self, block: dict, now: int, total_work: int) -> dict:
        """Stateful validation + persistence of a block whose parent is the current tip.
        Caller provides the surrounding atomic() transaction."""
        p, st = self.params, self.storage
        tip = self.tip()
        if block["height"] != tip["height"] + 1 or block["previous_hash"] != tip["hash"]:
            raise ValidationError("stale block: does not extend the current tip", "stale")
        mtp = C.median_time_past(st.recent_timestamps(p.mtp_window))
        C.check_block_time(block["timestamp"], mtp, now, p)
        txs = block["transactions"]
        ctx = _Ctx(self, block["height"])
        for tx in txs[1:]:
            if st.tx_location(tx["txid"]):
                raise ValidationError("transaction already confirmed", "duplicate_tx")
            ctx.apply(tx)
        minted = self.minted_supply()
        subsidy = C.subsidy_for(minted, p)
        cb = C.check_coinbase(txs[0], p, block["height"], subsidy, ctx.fees)
        ctx.acct(cb["recipient"])[0] += cb["amount"]
        stored = dict(block)
        st.apply_block(stored, cb["recipient"], subsidy, ctx.fees, ctx.changes(), ctx.undo,
                       minted + subsidy, total_work)
        stored.update(miner=cb["recipient"], subsidy=subsidy, fees=ctx.fees, total_work=str(total_work))
        return stored

    # ------------------------------------------------------------------ reorganization
    def _reorganize(self, new_hash: str, now: int) -> BlockResult:
        p, st = self.params, self.storage
        path: list[dict] = []
        totals: list[int] = []
        cursor = new_hash
        while True:
            side = st.side_get(cursor)
            if side is None:
                break
            if side["status"] == "invalid":
                raise ValidationError("branch contains an invalid block", "invalid_parent")
            path.append(side["block"])
            totals.append(side["total_work"])
            cursor = side["previous_hash"]
        fork = st.get_block_by_hash(cursor)
        if fork is None:                                       # ancestry was pruned: cannot evaluate this branch
            return BlockResult("side", path[0])
        tip = self.tip()
        if tip["height"] - fork["height"] > p.max_reorg_depth:
            raise ValidationError("reorganization is deeper than the limit", "reorg_too_deep")
        path.reverse()
        totals.reverse()
        old_tip = tip["hash"]
        disconnected: list[dict] = []
        try:
            with st.atomic():                                  # the whole reorganization is ONE transaction
                while self.tip()["hash"] != fork["hash"]:
                    disconnected.append(st.disconnect_tip())
                for index, (block, total) in enumerate(zip(path, totals)):
                    try:
                        self._connect(block, now, total)
                    except ValidationError as exc:
                        exc.failed_index = index               # type: ignore[attr-defined]
                        raise
                    st.side_remove(block["hash"])
                self._restore_mempool(disconnected, path, now)
        except ValidationError as exc:
            index = getattr(exc, "failed_index", None)
            if index is not None and exc.code != "bad_timestamp":
                st.side_mark_invalid([b["hash"] for b in path[index:]])
                event(log, logging.WARNING, "branch_rejected", code=exc.code, height=path[index]["height"],
                      hash=path[index]["hash"])
            raise                                              # the transaction rolled back: old chain untouched
        self.reorg_count += 1
        event(log, logging.WARNING, "reorganization", old_tip=old_tip, new_tip=new_hash,
              fork_height=fork["height"], disconnected=len(disconnected), connected=len(path))
        self._housekeeping()
        return BlockResult("reorg", self.tip(), disconnected=len(disconnected), connected=len(path))

    def _housekeeping(self) -> None:
        p = self.params
        self.storage.side_prune(max(0, self.tip()["height"] - p.max_reorg_depth - 1), p.max_side_blocks)

    # ------------------------------------------------------------------ orphans
    def _add_orphan(self, block: dict) -> None:
        self.orphans[block["hash"]] = block
        while len(self.orphans) > self.params.max_orphans:
            self.orphans.popitem(last=False)

    def _adopt_orphans(self, parent_hash: str, now: int) -> None:
        queue = [parent_hash]
        while queue:
            ph = queue.pop()
            for oh in [h for h, b in self.orphans.items() if b["previous_hash"] == ph]:
                child = self.orphans.pop(oh)
                try:
                    self._process(child, now)
                except ValidationError:
                    continue                                   # an invalid orphan is simply dropped
                queue.append(child["hash"])
