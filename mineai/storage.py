"""SQLite persistence with versioned migrations and atomic multi-statement transactions."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path


class StorageError(RuntimeError):
    pass


def _run_statements(conn: sqlite3.Connection, script: str) -> None:
    # Not executescript(): it implicitly COMMITs any open transaction, which would break
    # the atomicity of "migrate + bump user_version".
    for statement in script.split(";"):
        if statement.strip():
            conn.execute(statement)


def _migration_1(conn: sqlite3.Connection) -> None:
    # state_diffs holds the previous (balance, nonce) of every account a block touched,
    # so blocks can be rolled back during reorganizations (Milestone 4).
    _run_statements(conn,
        """
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE blocks (
            height INTEGER PRIMARY KEY,
            hash TEXT NOT NULL UNIQUE,
            previous_hash TEXT NOT NULL,
            merkle_root TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            difficulty INTEGER NOT NULL,
            nonce INTEGER NOT NULL,
            miner TEXT NOT NULL,
            subsidy INTEGER NOT NULL,
            fees INTEGER NOT NULL,
            body_json TEXT NOT NULL
        );
        CREATE TABLE accounts (
            address TEXT PRIMARY KEY,
            balance INTEGER NOT NULL CHECK (balance >= 0),
            nonce INTEGER NOT NULL CHECK (nonce >= 0)
        );
        CREATE TABLE tx_index (
            txid TEXT PRIMARY KEY,
            height INTEGER NOT NULL,
            position INTEGER NOT NULL
        );
        CREATE INDEX idx_tx_height ON tx_index(height);
        CREATE TABLE state_diffs (height INTEGER PRIMARY KEY, diff_json TEXT NOT NULL);
        CREATE TABLE mempool (
            txid TEXT PRIMARY KEY,
            sender TEXT NOT NULL,
            nonce INTEGER NOT NULL,
            fee INTEGER NOT NULL,
            timestamp INTEGER NOT NULL,
            tx_json TEXT NOT NULL,
            UNIQUE (sender, nonce)
        );
        """)


def _migration_2(conn: sqlite3.Connection) -> None:
    # Known peer addresses ("host:port"), persisted across restarts. Not consensus data.
    _run_statements(conn,
        """
        CREATE TABLE peers (
            addr TEXT PRIMARY KEY,
            last_seen INTEGER NOT NULL,
            failures INTEGER NOT NULL DEFAULT 0
        )
        """)


def _migration_3(conn: sqlite3.Connection) -> None:
    # Cumulative chain work per block, and storage for blocks that are not on the best chain
    # (competing branches and branches abandoned by a reorganization).
    conn.execute("ALTER TABLE blocks ADD COLUMN total_work TEXT NOT NULL DEFAULT '0'")
    total = 0
    for height, difficulty in conn.execute("SELECT height, difficulty FROM blocks ORDER BY height").fetchall():
        if height > 0:
            total += difficulty                          # work(block) = difficulty, work(genesis) = 0
        conn.execute("UPDATE blocks SET total_work=? WHERE height=?", (str(total), height))
    _run_statements(conn,
        """
        CREATE TABLE side_blocks (
            hash TEXT PRIMARY KEY,
            height INTEGER NOT NULL,
            previous_hash TEXT NOT NULL,
            total_work TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'ok',
            received INTEGER NOT NULL,
            block_json TEXT NOT NULL
        );
        CREATE INDEX idx_side_prev ON side_blocks(previous_hash);
        CREATE INDEX idx_side_height ON side_blocks(height)
        """)


MIGRATIONS = [_migration_1, _migration_2, _migration_3]      # index i migrates schema version i -> i+1

WIRE_KEYS = ("height", "previous_hash", "merkle_root", "timestamp", "difficulty", "nonce", "hash", "transactions")


class Storage:
    def __init__(self, path: Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.lock = threading.RLock()
        self._depth = 0
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        try:
            self._init()
        except sqlite3.DatabaseError as exc:       # e.g. "file is not a database", malformed image
            self.conn.close()
            raise StorageError(f"database is corrupted or not a MineAI database: {exc}") from exc
        except Exception:
            self.conn.close()
            raise

    # -------------------------------------------------------------- setup
    def _init(self) -> None:
        with self.lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=FULL")       # consensus data: durability first
            self.conn.execute("PRAGMA foreign_keys=ON")
            version = self.conn.execute("PRAGMA user_version").fetchone()[0]
            if version > len(MIGRATIONS):
                raise StorageError(f"database schema v{version} is newer than this software "
                                   f"(supports up to v{len(MIGRATIONS)}); refusing to open")
            if version == 0:
                legacy = self.conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name IN ('blocks','mempool')"
                ).fetchone()
                if legacy:
                    raise StorageError("this looks like a legacy V0.1 database (or a foreign one); "
                                       "refusing to modify it. Use a fresh data directory.")
            for v in range(version, len(MIGRATIONS)):
                with self.atomic():
                    MIGRATIONS[v](self.conn)
                    self.conn.execute(f"PRAGMA user_version={v + 1}")
            check = self.conn.execute("PRAGMA quick_check").fetchone()[0]
            if check != "ok":
                raise StorageError(f"database corruption detected: {check}")

    def close(self) -> None:
        with self.lock:
            self.conn.close()

    @contextmanager
    def atomic(self):
        """All-or-nothing transaction. Re-entrant: nested use joins the outer transaction."""
        with self.lock:
            outer = self._depth == 0
            if outer:
                self.conn.execute("BEGIN IMMEDIATE")
            self._depth += 1
            try:
                yield
            except BaseException:
                self._depth -= 1
                if outer:
                    self.conn.execute("ROLLBACK")
                raise
            else:
                self._depth -= 1
                if outer:
                    self.conn.execute("COMMIT")

    # -------------------------------------------------------------- meta
    def meta_get(self, key: str) -> str | None:
        with self.lock:
            row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return row[0] if row else None

    def meta_set(self, key: str, value: str) -> None:
        with self.lock:
            self.conn.execute("INSERT INTO meta(key,value) VALUES(?,?) "
                              "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    # -------------------------------------------------------------- blocks
    @staticmethod
    def _row_to_block(row) -> dict | None:
        if row is None:
            return None
        return {
            "height": row["height"], "hash": row["hash"], "previous_hash": row["previous_hash"],
            "merkle_root": row["merkle_root"], "timestamp": row["timestamp"],
            "difficulty": row["difficulty"], "nonce": row["nonce"],
            "transactions": json.loads(row["body_json"]),
            # derived, for explorers (not part of the hashed header)
            "miner": row["miner"], "subsidy": row["subsidy"], "fees": row["fees"],
            "total_work": row["total_work"],
        }

    def block_count(self) -> int:
        with self.lock:
            return int(self.conn.execute("SELECT COUNT(*) FROM blocks").fetchone()[0])

    def latest_block(self) -> dict | None:
        with self.lock:
            return self._row_to_block(
                self.conn.execute("SELECT * FROM blocks ORDER BY height DESC LIMIT 1").fetchone())

    def get_block_by_height(self, height: int) -> dict | None:
        with self.lock:
            return self._row_to_block(
                self.conn.execute("SELECT * FROM blocks WHERE height=?", (height,)).fetchone())

    def get_block_by_hash(self, block_hash: str) -> dict | None:
        with self.lock:
            return self._row_to_block(
                self.conn.execute("SELECT * FROM blocks WHERE hash=?", (block_hash,)).fetchone())

    def list_blocks(self, limit: int = 25, offset: int = 0) -> list[dict]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM blocks ORDER BY height DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
            return [self._row_to_block(r) for r in rows]

    def iter_blocks_ascending(self, batch: int = 500):
        height = 0
        while True:
            with self.lock:
                rows = self.conn.execute(
                    "SELECT * FROM blocks WHERE height>=? ORDER BY height LIMIT ?", (height, batch)).fetchall()
            if not rows:
                return
            for r in rows:
                yield self._row_to_block(r)
            height = rows[-1]["height"] + 1

    def recent_timestamps(self, count: int) -> list[int]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT timestamp FROM blocks ORDER BY height DESC LIMIT ?", (count,)).fetchall()
            return [r[0] for r in rows]

    def coinbase_amounts(self, low: int, high: int) -> dict[str, int]:
        """Sum of coinbase (subsidy+fees) per miner for blocks with low <= height <= high."""
        if high < low:
            return {}
        with self.lock:
            rows = self.conn.execute(
                "SELECT miner, SUM(subsidy+fees) FROM blocks WHERE height BETWEEN ? AND ? "
                "AND miner != 'GENESIS' GROUP BY miner", (low, high)).fetchall()
            return {r[0]: int(r[1]) for r in rows}

    def insert_genesis(self, block: dict) -> None:
        with self.atomic():
            self.conn.execute(
                "INSERT INTO blocks(height,hash,previous_hash,merkle_root,timestamp,difficulty,nonce,"
                "miner,subsidy,fees,body_json,total_work) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (0, block["hash"], block["previous_hash"], block["merkle_root"], block["timestamp"],
                 block["difficulty"], block["nonce"], "GENESIS", 0, 0, "[]", "0"))
            self.meta_set("minted_supply", "0")

    def apply_block(self, block: dict, miner: str, subsidy: int, fees: int,
                    changes: dict[str, tuple[int, int]], undo: dict[str, tuple[int, int] | None],
                    minted_supply: int, total_work: int) -> None:
        """Atomically persist a validated block plus all resulting state changes.
        Must be called inside `atomic()` together with mempool pruning by the caller."""
        with self.atomic():
            self.conn.execute(
                "INSERT INTO blocks(height,hash,previous_hash,merkle_root,timestamp,difficulty,nonce,"
                "miner,subsidy,fees,body_json,total_work) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (block["height"], block["hash"], block["previous_hash"], block["merkle_root"],
                 block["timestamp"], block["difficulty"], block["nonce"], miner, subsidy, fees,
                 json.dumps(block["transactions"], sort_keys=True, separators=(",", ":")), str(total_work)))
            for position, tx in enumerate(block["transactions"]):
                self.conn.execute("INSERT INTO tx_index(txid,height,position) VALUES(?,?,?)",
                                  (tx["txid"], block["height"], position))
            for address, (balance, nonce) in changes.items():
                self.conn.execute(
                    "INSERT INTO accounts(address,balance,nonce) VALUES(?,?,?) "
                    "ON CONFLICT(address) DO UPDATE SET balance=excluded.balance, nonce=excluded.nonce",
                    (address, balance, nonce))
            self.conn.execute("INSERT INTO state_diffs(height,diff_json) VALUES(?,?)",
                              (block["height"], json.dumps(undo, sort_keys=True, separators=(",", ":"))))
            self.meta_set("minted_supply", str(minted_supply))

    # -------------------------------------------------------------- best chain helpers
    def tip_work(self) -> int:
        with self.lock:
            row = self.conn.execute("SELECT total_work FROM blocks ORDER BY height DESC LIMIT 1").fetchone()
            return int(row[0]) if row else 0

    def is_main(self, block_hash: str) -> bool:
        with self.lock:
            return self.conn.execute("SELECT 1 FROM blocks WHERE hash=?", (block_hash,)).fetchone() is not None

    def header_chain(self, block_hash: str, count: int) -> list[tuple[int, int, int]]:
        """(height, timestamp, difficulty) of `block_hash` and up to count-1 ancestors, NEWEST first.
        Walks side branches (by hash) until it reaches the best chain, then reads the rest by height."""
        out: list[tuple[int, int, int]] = []
        cursor = block_hash
        with self.lock:
            while len(out) < count:
                row = self.conn.execute("SELECT height FROM blocks WHERE hash=?", (cursor,)).fetchone()
                if row is not None:
                    rows = self.conn.execute(
                        "SELECT height, timestamp, difficulty FROM blocks WHERE height BETWEEN ? AND ? "
                        "ORDER BY height DESC", (max(0, row[0] - (count - len(out)) + 1), row[0])).fetchall()
                    out.extend((r[0], r[1], r[2]) for r in rows)
                    break
                side = self.conn.execute("SELECT block_json, previous_hash FROM side_blocks WHERE hash=?",
                                         (cursor,)).fetchone()
                if side is None:
                    break                                                 # ancestry incomplete
                block = json.loads(side[0])
                out.append((block["height"], block["timestamp"], block["difficulty"]))
                cursor = side[1]
        return out

    def blocks_after(self, height: int, count: int) -> list[dict]:
        with self.lock:
            rows = self.conn.execute("SELECT * FROM blocks WHERE height>? ORDER BY height LIMIT ?",
                                     (height, count)).fetchall()
            return [self._row_to_block(r) for r in rows]

    def disconnect_tip(self) -> dict:
        """Remove the best tip, restoring the pre-block state recorded when it was connected.
        The block is kept as a side block. Must run inside the caller's atomic() together
        with everything that follows (a reorganization is one transaction)."""
        with self.atomic():
            tip = self.latest_block()
            if tip is None or tip["height"] == 0:
                raise StorageError("cannot disconnect the genesis block")
            row = self.conn.execute("SELECT diff_json FROM state_diffs WHERE height=?", (tip["height"],)).fetchone()
            if row is None:
                raise StorageError(f"no undo data for height {tip['height']}: database corrupted")
            for address, (balance, nonce) in json.loads(row[0]).items():
                self.conn.execute(
                    "INSERT INTO accounts(address,balance,nonce) VALUES(?,?,?) "
                    "ON CONFLICT(address) DO UPDATE SET balance=excluded.balance, nonce=excluded.nonce",
                    (address, balance, nonce))
            self.conn.execute("DELETE FROM tx_index WHERE height=?", (tip["height"],))
            self.conn.execute("DELETE FROM state_diffs WHERE height=?", (tip["height"],))
            self.conn.execute("DELETE FROM blocks WHERE height=?", (tip["height"],))
            self.meta_set("minted_supply", str(int(self.meta_get("minted_supply") or 0) - tip["subsidy"]))
            self.side_add(tip, int(tip["total_work"]), 0)
            return tip

    # -------------------------------------------------------------- side blocks
    def side_add(self, block: dict, total_work: int, received: int) -> None:
        wire = {k: block[k] for k in WIRE_KEYS}
        with self.lock:
            self.conn.execute(
                "INSERT OR IGNORE INTO side_blocks(hash,height,previous_hash,total_work,status,received,block_json) "
                "VALUES(?,?,?,?, 'ok', ?, ?)",
                (block["hash"], block["height"], block["previous_hash"], str(total_work), received,
                 json.dumps(wire, sort_keys=True, separators=(",", ":"))))

    def side_get(self, block_hash: str) -> dict | None:
        with self.lock:
            row = self.conn.execute("SELECT * FROM side_blocks WHERE hash=?", (block_hash,)).fetchone()
            if row is None:
                return None
            return {"block": json.loads(row["block_json"]), "height": row["height"],
                    "previous_hash": row["previous_hash"], "total_work": int(row["total_work"]),
                    "status": row["status"]}

    def side_remove(self, block_hash: str) -> None:
        with self.lock:
            self.conn.execute("DELETE FROM side_blocks WHERE hash=?", (block_hash,))

    def side_mark_invalid(self, hashes: list[str]) -> None:
        with self.lock:
            self.conn.executemany("UPDATE side_blocks SET status='invalid' WHERE hash=?", [(h,) for h in hashes])

    def side_count(self) -> int:
        with self.lock:
            return int(self.conn.execute("SELECT COUNT(*) FROM side_blocks").fetchone()[0])

    def side_prune(self, min_height: int, max_count: int) -> int:
        """Drop side blocks below `min_height`, then the oldest beyond `max_count`."""
        with self.lock:
            n = self.conn.execute("DELETE FROM side_blocks WHERE height < ?", (min_height,)).rowcount
            excess = self.side_count() - max_count
            if excess > 0:
                n += self.conn.execute(
                    "DELETE FROM side_blocks WHERE hash IN (SELECT hash FROM side_blocks "
                    "ORDER BY height ASC, received ASC LIMIT ?)", (excess,)).rowcount
            return n

    # -------------------------------------------------------------- accounts / index
    def get_account(self, address: str) -> tuple[int, int]:
        with self.lock:
            row = self.conn.execute("SELECT balance, nonce FROM accounts WHERE address=?", (address,)).fetchone()
            return (row[0], row[1]) if row else (0, 0)

    def total_balances(self) -> int:
        with self.lock:
            return int(self.conn.execute("SELECT COALESCE(SUM(balance),0) FROM accounts").fetchone()[0])

    def tx_location(self, txid: str) -> tuple[int, int] | None:
        with self.lock:
            row = self.conn.execute("SELECT height, position FROM tx_index WHERE txid=?", (txid,)).fetchone()
            return (row[0], row[1]) if row else None

    # -------------------------------------------------------------- peers
    def peers_upsert(self, addr: str, last_seen: int) -> None:
        with self.lock:
            self.conn.execute(
                "INSERT INTO peers(addr,last_seen,failures) VALUES(?,?,0) "
                "ON CONFLICT(addr) DO UPDATE SET last_seen=excluded.last_seen, failures=0", (addr, last_seen))

    def peers_failure(self, addr: str, max_failures: int) -> None:
        with self.lock:
            self.conn.execute("UPDATE peers SET failures=failures+1 WHERE addr=?", (addr,))
            self.conn.execute("DELETE FROM peers WHERE addr=? AND failures>=?", (addr, max_failures))

    def peers_list(self, limit: int = 1000) -> list[str]:
        with self.lock:
            return [r[0] for r in self.conn.execute(
                "SELECT addr FROM peers ORDER BY last_seen DESC LIMIT ?", (limit,)).fetchall()]

    def peers_count(self) -> int:
        with self.lock:
            return int(self.conn.execute("SELECT COUNT(*) FROM peers").fetchone()[0])

    # -------------------------------------------------------------- mempool
    def mempool_add(self, tx: dict) -> None:
        with self.lock:
            try:
                self.conn.execute(
                    "INSERT INTO mempool(txid,sender,nonce,fee,timestamp,tx_json) VALUES(?,?,?,?,?,?)",
                    (tx["txid"], tx["sender"], tx["nonce"], tx["fee"], tx["timestamp"],
                     json.dumps(tx, sort_keys=True, separators=(",", ":"))))
            except sqlite3.IntegrityError as exc:
                raise StorageError("transaction or (sender, nonce) already in mempool") from exc

    def mempool_remove(self, txids: list[str]) -> None:
        with self.lock:
            self.conn.executemany("DELETE FROM mempool WHERE txid=?", [(t,) for t in txids])

    def mempool_list(self, limit: int | None = None, offset: int = 0) -> list[dict]:
        with self.lock:
            sql = "SELECT tx_json FROM mempool ORDER BY fee DESC, timestamp, txid"
            args: tuple = ()
            if limit is not None:
                sql += " LIMIT ? OFFSET ?"
                args = (limit, offset)
            return [json.loads(r[0]) for r in self.conn.execute(sql, args).fetchall()]

    def mempool_for_sender(self, sender: str) -> list[dict]:
        with self.lock:
            rows = self.conn.execute("SELECT tx_json FROM mempool WHERE sender=? ORDER BY nonce", (sender,)).fetchall()
            return [json.loads(r[0]) for r in rows]

    def mempool_get(self, txid: str) -> dict | None:
        with self.lock:
            row = self.conn.execute("SELECT tx_json FROM mempool WHERE txid=?", (txid,)).fetchone()
            return json.loads(row[0]) if row else None

    def mempool_contains(self, txid: str) -> bool:
        with self.lock:
            return self.conn.execute("SELECT 1 FROM mempool WHERE txid=?", (txid,)).fetchone() is not None

    def mempool_count(self) -> int:
        with self.lock:
            return int(self.conn.execute("SELECT COUNT(*) FROM mempool").fetchone()[0])

    def mempool_senders(self) -> list[str]:
        with self.lock:
            return [r[0] for r in self.conn.execute("SELECT DISTINCT sender FROM mempool").fetchall()]
