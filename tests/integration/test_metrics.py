"""Metrics registry, Prometheus output, endpoints, and the `mineai check` command."""
from __future__ import annotations

import re
import sys
import threading

import pytest
from fastapi.testclient import TestClient

from mineai import config, consensus as C
from mineai.blockchain import Blockchain
from mineai.consensus import ValidationError
from mineai.metrics import Metrics, reject_bucket, render_prometheus
from mineai.node import check_main, create_app
from tests.helpers import START, TEST, Acct, build, funded, mine

LOCAL = frozenset({"testclient"})
LABEL_VALUE = r'"(?:[^"\\\n]|\\.)*"'            # quoted string, \" \\ \n escapes allowed
PROM_LINE = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*(\{([a-z_]+=" + LABEL_VALUE + r",?)+\})? -?[0-9]+(\.[0-9]+)?(e[+-]?[0-9]+)?$")


def assert_valid_prometheus(text: str):
    assert text.endswith("\n")
    for line in text.splitlines():
        if line.startswith("# TYPE ") or line.startswith("# HELP "):
            continue
        assert PROM_LINE.match(line), f"not valid exposition format: {line!r}"


# ====================================================================== registry
def test_counters_labels_and_totals():
    m = Metrics()
    m.inc("blocks_rejected", code="bad_pow")
    m.inc("blocks_rejected", 2, code="bad_pow")
    m.inc("blocks_rejected", code="stale")
    m.inc("reorgs")
    assert m.get("blocks_rejected", code="bad_pow") == 3 and m.get("blocks_rejected", code="stale") == 1
    assert m.total("blocks_rejected") == 4 and m.get("never_seen") == 0
    snap = m.snapshot()
    assert snap["blocks_rejected"] == {"code=bad_pow": 3, "code=stale": 1} and snap["reorgs"] == {"": 1}


@pytest.mark.parametrize("bad", ["", "Bad", "has space", "1abc", "a-b", "x{y}", 'q"uote'])
def test_metric_names_are_validated(bad):
    with pytest.raises(ValueError):
        Metrics().inc(bad)


def test_counters_are_thread_safe():
    m = Metrics()

    def work():
        for _ in range(2000):
            m.inc("hits", code="a")
            m.inc("hits", code="b")
    threads = [threading.Thread(target=work) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert m.get("hits", code="a") == m.get("hits", code="b") == 16000


def test_prometheus_output_is_valid_and_escapes_label_values():
    m = Metrics()
    m.inc("odd", value='we"ird\\val\nue')
    m.inc("p2p_messages_received", 5, type="tx")
    text = render_prometheus(m, {"chain_height": 7, "estimated_hashrate": None, "difficulty": 12345.5},
                             {"version": "0.2", "network": "devnet"})
    assert_valid_prometheus(text)
    assert 'mineai_build_info{network="devnet",version="0.2"} 1' in text
    assert "mineai_chain_height 7" in text and "estimated_hashrate" not in text          # None gauges are omitted
    assert 'mineai_p2p_messages_received_total{type="tx"} 5' in text
    assert r'we\"ird\\val\nue' in text and "\nue" not in text


@pytest.mark.parametrize("reason,bucket", [
    ("different network", "network"), ("different genesis block", "genesis"), ("no common protocol version", "version"),
    ("connected to self", "self"), ("banned peer", "banned"), ("duplicate connection", "duplicate"),
    ("peer table full", "table_full"), ("frame length 999999 outside 1..4194304", "bad_frame"),
    ("malformed JSON", "malformed"), ("first message must be hello", "no_hello"), ("something else entirely", "other"),
    ("", "other"),
])
def test_rejection_reasons_map_to_a_fixed_bucket(reason, bucket):
    assert reject_bucket(reason) == bucket


def test_hostile_input_cannot_inflate_label_cardinality():
    m = Metrics()
    for i in range(5000):
        m.inc("p2p_protocol_errors", reason=reject_bucket(f"frame length {i * 7919} outside 1..{i}"))
        m.inc("p2p_protocol_errors", reason=reject_bucket(f"weird-{i}-reason"))
    assert len(m.snapshot()["p2p_protocol_errors"]) == 2


# ====================================================================== chain counters
def test_chain_counts_transactions_blocks_and_rejections_by_code(tmp_path):
    chain, alice = funded(tmp_path, blocks=3)
    m = chain.metrics
    assert m.get("blocks_accepted") == 3
    bob = Acct()
    chain.submit_transaction(alice.tx(bob, 1))
    with pytest.raises(ValidationError):
        chain.submit_transaction(alice.tx(bob, 1))                       # same nonce, different tx? -> bad_nonce/duplicate
    with pytest.raises(ValidationError):
        chain.submit_transaction({**alice.tx(bob, 2, nonce=2), "signature": "A" * 86 + "=="})
    assert m.get("transactions_accepted") == 1 and m.total("transactions_rejected") == 2
    assert m.get("transactions_rejected", code="bad_signature") == 1
    with pytest.raises(ValidationError):
        chain.submit_mined_block(build(chain, alice, lambda b: b.update(previous_hash="1" * 64)))
    assert m.get("blocks_rejected", code="stale") == 1


def test_reorganizations_and_block_statuses_are_counted(tmp_path):
    from tests.consensus.test_forks import clone, extend, new_node
    n, alice = new_node(tmp_path, blocks=3)
    y = clone(n, tmp_path, "y.db", upto=1)
    for b in extend(y, Acct(), 3):
        n.process_block(b)
    m = n.metrics
    assert m.get("reorgs") == 1 and m.get("reorg_blocks_disconnected") == 2 and m.get("reorg_blocks_connected") == 3
    assert m.get("blocks_received", status="side") == 2 and m.get("blocks_received", status="reorg") == 1
    assert m.get("blocks_received", status="extended") == 0        # nothing extended the tip directly yet
    n.process_block(extend(y, Acct(), 1)[0])
    assert m.get("blocks_received", status="extended") == 1


# ====================================================================== endpoints
@pytest.fixture
def client_env(tmp_path):
    chain, alice = funded(tmp_path, blocks=4)
    chain.submit_transaction(alice.tx(Acct(), 1))
    return chain, TestClient(create_app(chain, local_hosts=LOCAL, rate_limit_per_minute=100_000), raise_server_exceptions=False)


def test_prometheus_endpoint(client_env):
    chain, client = client_env
    r = client.get("/metrics")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    assert_valid_prometheus(r.text)
    for needle in ("mineai_chain_height 4", "mineai_mempool_transactions 1", "mineai_peers 0", "mineai_blocks_accepted_total 4",
                   "mineai_transactions_accepted_total 1", "mineai_uptime_seconds", "mineai_difficulty", "mineai_build_info"):
        assert needle in r.text, needle


def test_json_metrics_endpoint_and_alias(client_env):
    chain, client = client_env
    body = client.get("/api/v1/metrics").json()
    assert body["gauges"]["chain_height"] == 4 and body["counters"]["blocks_accepted"] == {"": 4}
    assert body["network"] == "devnet" and body["uptime_seconds"] >= 0
    assert client.get("/api/metrics").json()["gauges"] == body["gauges"]


def test_metrics_are_read_only_and_reveal_nothing_sensitive(client_env):
    chain, client = client_env
    for path in ("/metrics", "/api/v1/metrics"):
        assert client.post(path, json={}).status_code == 405 and client.delete(path).status_code == 405
    text = client.get("/metrics").text.lower() + client.get("/api/v1/metrics").text.lower()
    for forbidden in ("password", "private", "secret", "wallet", "sender", "recipient", "signature"):
        assert forbidden not in text


# ====================================================================== offline check command
def run_check(monkeypatch, capsys, data_dir, network="privnet"):
    monkeypatch.setattr(sys, "argv", ["mineai check", "--network", network, "--data-dir", str(data_dir)])
    try:
        check_main()
    except SystemExit as exc:
        return str(exc.code), capsys.readouterr().out
    return "", capsys.readouterr().out


def make_privnet_db(tmp_path, blocks=3):
    params = config.get_params("privnet")
    chain = Blockchain(config.db_path_for(params, tmp_path), params)
    miner = Acct(params)
    for _ in range(blocks):
        block = chain.mining_template(miner.address)
        from tests.helpers import solve
        chain.submit_mined_block(solve(params, {k: block[k] for k in ("height", "previous_hash", "merkle_root", "timestamp",
                                                                        "difficulty", "nonce", "transactions")}))
    chain.close()
    return params


def test_check_command_accepts_a_healthy_database(tmp_path, monkeypatch, capsys):
    make_privnet_db(tmp_path)
    err, out = run_check(monkeypatch, capsys, tmp_path)
    assert err == "" and "OK: privnet database" in out and "height 3" in out


def test_check_command_reports_problems_clearly(tmp_path, monkeypatch, capsys):
    err, _ = run_check(monkeypatch, capsys, tmp_path)
    assert "no database at" in err                                           # missing
    params = make_privnet_db(tmp_path)
    import sqlite3
    path = config.db_path_for(params, tmp_path)
    con = sqlite3.connect(path)
    con.execute("UPDATE blocks SET difficulty = difficulty + 1 WHERE height = 2")     # tamper with history
    con.commit()
    con.close()
    err, _ = run_check(monkeypatch, capsys, tmp_path)
    assert err.startswith("CHECK FAILED")
    path.write_bytes(b"not a database" * 50)                                  # destroyed file
    err, _ = run_check(monkeypatch, capsys, tmp_path)
    assert err.startswith("CHECK FAILED")
    err, _ = run_check(monkeypatch, capsys, tmp_path, network="devnet")       # wrong network: nothing there
    assert "no database" in err
