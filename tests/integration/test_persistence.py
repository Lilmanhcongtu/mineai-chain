"""Restart recovery, migrations, corruption detection, atomicity of block acceptance."""
from __future__ import annotations

import dataclasses
import sqlite3

import pytest

from mineai.blockchain import Blockchain, ChainError
from mineai.config import TESTNET
from mineai.consensus import ValidationError
from mineai.storage import MIGRATIONS, Storage, StorageError
from tests.helpers import START, TEST, Acct, Clock, build, funded, make_chain, mai, mine


def test_fresh_database_is_migrated_and_has_pinned_genesis(tmp_path):
    chain = make_chain(tmp_path)
    assert chain.storage.conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
    assert chain.tip()["height"] == 0 and chain.tip()["hash"] == TEST.genesis_hash
    assert chain.storage.meta_get("network_id") == TEST.network_id


def test_restart_recovers_full_state(tmp_path):
    chain, alice = funded(tmp_path, blocks=4)
    bob = Acct()
    chain.submit_transaction(alice.tx(bob, 3))
    before = (chain.tip(), chain.minted_supply(), chain.account(alice.address), chain.storage.mempool_list())
    chain.close()
    reopened = Blockchain(tmp_path / "chain.db", TEST, clock=Clock())
    after = (reopened.tip(), reopened.minted_supply(), reopened.account(alice.address), reopened.storage.mempool_list())
    assert before == after
    mine(reopened, alice)                                          # and it keeps working
    assert reopened.account(bob.address)["balance"] == mai(3)
    reopened.verify_integrity()


def test_reopening_is_idempotent(tmp_path):
    chain, _ = funded(tmp_path, blocks=2)
    chain.close()
    for _ in range(3):
        Blockchain(tmp_path / "chain.db", TEST, clock=Clock()).close()
    assert Blockchain(tmp_path / "chain.db", TEST, clock=Clock()).tip()["height"] == 2


def test_interrupted_block_write_leaves_no_partial_state(tmp_path, monkeypatch):
    chain, alice = funded(tmp_path)
    chain.submit_transaction(alice.tx(Acct(), 2))
    block = build(chain, alice)
    before = (chain.tip()["hash"], chain.minted_supply(), chain.storage.total_balances(),
              chain.storage.mempool_count(), chain.account(alice.address))

    def crash(txids):                       # dies after the block rows were written, inside the transaction
        raise RuntimeError("simulated crash / power loss")
    monkeypatch.setattr(chain.storage, "mempool_remove", crash)
    with pytest.raises(RuntimeError):
        chain.submit_mined_block(block)
    monkeypatch.undo()

    after = (chain.tip()["hash"], chain.minted_supply(), chain.storage.total_balances(),
             chain.storage.mempool_count(), chain.account(alice.address))
    assert before == after                                          # nothing half-applied
    assert chain.storage.get_block_by_hash(block["hash"]) is None
    assert chain.storage.tx_location(block["transactions"][1]["txid"]) is None
    chain.submit_mined_block(block)                                 # the same block is still acceptable
    chain.verify_integrity()


def test_crash_between_processes_is_atomic(tmp_path):
    """A transaction that is never committed disappears on reopen (WAL rollback)."""
    chain, alice = funded(tmp_path)
    conn = chain.storage.conn
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("UPDATE accounts SET balance = balance + 1000000000")
    chain.storage.close()                                          # connection dies without COMMIT
    reopened = Blockchain(tmp_path / "chain.db", TEST, clock=Clock())
    assert reopened.storage.total_balances() == reopened.minted_supply()


def test_garbage_file_is_detected(tmp_path):
    path = tmp_path / "chain.db"
    path.write_bytes(b"this is not a sqlite database" * 100)
    with pytest.raises(StorageError):
        Blockchain(path, TEST)


def test_truncated_database_is_detected(tmp_path):
    chain, _ = funded(tmp_path, blocks=5)
    chain.storage.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    chain.close()
    path = tmp_path / "chain.db"
    data = path.read_bytes()
    path.write_bytes(data[: len(data) // 2])
    with pytest.raises((StorageError, ChainError)):
        Blockchain(path, TEST)


def test_tampered_balance_is_detected_on_open(tmp_path):
    chain, alice = funded(tmp_path)
    chain.close()
    raw = sqlite3.connect(tmp_path / "chain.db")
    raw.execute("UPDATE accounts SET balance = balance + 1 WHERE address = ?", (alice.address,))
    raw.commit()
    raw.close()
    with pytest.raises(ChainError, match="minted supply"):
        Blockchain(tmp_path / "chain.db", TEST)


def test_tampered_history_is_caught_by_full_verification(tmp_path):
    chain, alice = funded(tmp_path, blocks=4)
    chain.submit_transaction(alice.tx(Acct(), 3))
    mine(chain, alice)
    chain.close()
    raw = sqlite3.connect(tmp_path / "chain.db")
    body = raw.execute("SELECT body_json FROM blocks WHERE height = 5").fetchone()[0]
    raw.execute("UPDATE blocks SET body_json = ? WHERE height = 5", (body.replace('"amount":3000000', '"amount":9000000'),))
    raw.commit()
    raw.close()
    reopened = Blockchain(tmp_path / "chain.db", TEST)             # cheap open checks may pass...
    with pytest.raises(ChainError):
        reopened.verify_integrity()                                # ...the full audit must not


def test_tampered_tip_hash_is_detected_on_open(tmp_path):
    chain, _ = funded(tmp_path)
    chain.close()
    raw = sqlite3.connect(tmp_path / "chain.db")
    raw.execute("UPDATE blocks SET nonce = nonce + 1 WHERE height = 3")
    raw.commit()
    raw.close()
    with pytest.raises(ChainError, match="tip block hash"):
        Blockchain(tmp_path / "chain.db", TEST)


def test_database_from_another_network_is_refused(tmp_path):
    make_chain(tmp_path).close()
    with pytest.raises(ChainError, match="belongs to network"):
        Blockchain(tmp_path / "chain.db", dataclasses.replace(TESTNET, genesis_hash=""))


def test_legacy_v01_database_is_refused_and_untouched(tmp_path):
    path = tmp_path / "mineai.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE blocks (height INTEGER PRIMARY KEY, hash TEXT)")
    con.execute("INSERT INTO blocks VALUES (0, 'legacy')")
    con.commit()
    con.close()
    with pytest.raises(StorageError, match="legacy"):
        Storage(path).close()
    con = sqlite3.connect(path)
    assert con.execute("SELECT hash FROM blocks").fetchall() == [("legacy",)]
    assert con.execute("PRAGMA user_version").fetchone()[0] == 0
    con.close()


def test_database_with_newer_schema_is_refused(tmp_path):
    path = tmp_path / "chain.db"
    make_chain(tmp_path).close()
    con = sqlite3.connect(path)
    con.execute(f"PRAGMA user_version = {len(MIGRATIONS) + 5}")
    con.commit()
    con.close()
    with pytest.raises(StorageError, match="newer"):
        Blockchain(path, TEST)


def test_failed_migration_rolls_back_completely(tmp_path, monkeypatch):
    import mineai.storage as storage_mod

    def broken(conn):
        conn.execute("CREATE TABLE half_done (x INTEGER)")
        raise RuntimeError("migration crashed")
    monkeypatch.setattr(storage_mod, "MIGRATIONS", [broken])
    with pytest.raises(RuntimeError):
        Storage(tmp_path / "m.db")
    con = sqlite3.connect(tmp_path / "m.db")
    assert con.execute("SELECT name FROM sqlite_master WHERE name='half_done'").fetchall() == []
    assert con.execute("PRAGMA user_version").fetchone()[0] == 0
    con.close()


def test_state_diffs_are_recorded_for_future_rollback(tmp_path):
    chain, alice = funded(tmp_path, blocks=3)
    rows = chain.storage.conn.execute("SELECT height, diff_json FROM state_diffs ORDER BY height").fetchall()
    assert [r[0] for r in rows] == [1, 2, 3]
    assert alice.address in rows[1][1]                            # block 2 records alice's prior balance


def test_unique_constraints_prevent_duplicate_rows(tmp_path):
    chain, alice = funded(tmp_path)
    tx = alice.tx(Acct(), 1)
    chain.storage.mempool_add(tx)
    with pytest.raises(StorageError):
        chain.storage.mempool_add(tx)
    with pytest.raises(sqlite3.IntegrityError):
        chain.storage.conn.execute("INSERT INTO accounts(address,balance,nonce) VALUES('x',-1,0)")


def test_reorg_ready_undo_data_restores_previous_state(tmp_path):
    """The per-block undo record is sufficient to reconstruct the pre-block balances."""
    import json
    chain, alice = funded(tmp_path, blocks=3)
    before = chain.storage.get_account(alice.address)
    bob = Acct()
    chain.submit_transaction(alice.tx(bob, 4))
    mine(chain, alice)
    undo = json.loads(chain.storage.conn.execute("SELECT diff_json FROM state_diffs WHERE height=4").fetchone()[0])
    assert tuple(undo[alice.address]) == before
    assert tuple(undo[bob.address]) == (0, 0)


def test_upgrade_from_older_schema_preserves_data_and_backfills_work(tmp_path):
    """Strip a real chain back to the schema-v1 layout, then open it with the current software:
    the real migrations (peers, total_work backfill, side_blocks, address index backfill) run over existing data."""
    chain, alice = funded(tmp_path, blocks=4)
    chain.submit_transaction(alice.tx(Acct(), 2))
    mine(chain, alice)
    history_before = chain.address_history(alice.address, limit=50)
    chain.close()
    path = tmp_path / "chain.db"
    con = sqlite3.connect(path, isolation_level=None)
    for table in ("side_blocks", "peers", "tx_addresses"):
        con.execute(f"DROP TABLE {table}")
    con.execute("ALTER TABLE blocks DROP COLUMN total_work")
    con.execute("PRAGMA user_version=1")
    con.close()
    reopened = Blockchain(path, TEST, clock=Clock())
    assert reopened.storage.conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
    assert reopened.tip()["height"] == 5 and reopened.storage.mempool_count() == 0
    assert reopened.tip()["total_work"] == str(5 * 16)                 # backfilled: 5 blocks of difficulty 16
    assert reopened.storage.peers_count() == 0 and reopened.storage.side_count() == 0
    history_after = reopened.address_history(alice.address, limit=50)
    assert history_after["total"] == history_before["total"] == 6      # 5 mined blocks + 1 outgoing transfer
    assert history_after["transactions"] == history_before["transactions"]      # the backfill is exact
    reopened.verify_integrity()                                        # checks cumulative work block by block
    mine(reopened, alice)                                              # and the chain keeps working
    assert reopened.tip()["height"] == 6


def test_peer_address_storage(tmp_path):
    chain = make_chain(tmp_path)
    st = chain.storage
    st.peers_upsert("127.0.0.1:9001", 100)
    st.peers_upsert("127.0.0.1:9002", 200)
    st.peers_upsert("127.0.0.1:9001", 300)                 # refresh, no duplicate
    assert st.peers_list() == ["127.0.0.1:9001", "127.0.0.1:9002"] and st.peers_count() == 2
    for _ in range(3):
        st.peers_failure("127.0.0.1:9002", max_failures=3)
    assert st.peers_list() == ["127.0.0.1:9001"]           # dropped after repeated failures
    st.peers_failure("127.0.0.1:9001", max_failures=3)
    st.peers_upsert("127.0.0.1:9001", 400)                 # success resets the failure counter
    for _ in range(2):
        st.peers_failure("127.0.0.1:9001", max_failures=3)
    assert st.peers_list() == ["127.0.0.1:9001"]
