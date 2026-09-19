"""HTTP API behaviour: happy path, uniform errors, limits, loopback-only mining."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from mineai.node import create_app
from tests.helpers import START, Acct, build, funded, mai, mine

LOCAL = frozenset({"testclient", "127.0.0.1"})


@pytest.fixture
def env(tmp_path):
    chain, alice = funded(tmp_path)
    app = create_app(chain, local_hosts=LOCAL, rate_limit_per_minute=10_000)
    return chain, alice, TestClient(app, raise_server_exceptions=False)


def err(resp):
    body = resp.json()
    assert set(body) == {"error"} and set(body["error"]) == {"code", "message"}
    return body["error"]["code"]


def test_full_flow_over_http(env):
    chain, alice, client = env
    bob = Acct()
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/api/ready").json()["status"] == "ready"
    st = client.get("/api/status").json()
    assert st["network"] == "devnet" and st["height"] == 3 and st["label"] == "DEVNET"
    assert st["max_supply_mai"] == "100000000" and st["genesis_hash"] == chain.params.genesis_hash

    acct = client.get(f"/api/account/{alice.address}").json()
    tx = alice.tx(bob, 5, nonce=acct["next_nonce"])
    r = client.post("/api/transactions", json=tx)
    assert r.status_code == 200 and r.json() == {"accepted": True, "txid": tx["txid"]}
    assert client.get(f"/api/tx/{tx['txid']}").json()["status"] == "mempool"

    template = client.get("/api/mining/template", params={"address": alice.address}).json()
    assert len(template["transactions"]) == 2
    from tests.helpers import solve
    block = solve(chain.params, {k: template[k] for k in
                                 ("height", "previous_hash", "merkle_root", "timestamp", "difficulty", "nonce", "transactions")})
    r = client.post("/api/mining/submit", json=block)
    assert r.status_code == 200 and r.json()["height"] == 4
    found = client.get(f"/api/tx/{tx['txid']}").json()
    assert found["status"] == "confirmed" and found["confirmations"] == 1 and found["height"] == 4
    assert client.get(f"/api/account/{bob.address}").json()["balance"] == mai(5)
    assert client.get("/api/block/4").json()["hash"] == block["hash"]
    assert client.get(f"/api/block/{block['hash']}").json()["height"] == 4
    assert len(client.get("/api/blocks?limit=2&offset=1").json()) == 2


def test_explorer_pages_render_with_network_label(env):
    chain, alice, client = env
    page = client.get("/")
    assert page.status_code == 200 and "DEVNET" in page.text and "no monetary value" in page.text
    assert client.get("/explorer/block/1").status_code == 200
    assert client.get("/explorer/block/999").status_code == 404
    assert "private" not in page.text.lower()


@pytest.mark.parametrize("identifier", ["²", "９", "9" * 30, "-1", "1.5", "../etc/passwd", "%00", "A" * 64,
                                        "g" * 64, "", " 1", "1" * 65, "0x10", "١"])
def test_block_identifier_fuzzing_never_500s(env, identifier):
    _, _, client = env
    for path in (f"/api/block/{identifier}", f"/explorer/block/{identifier}", f"/api/tx/{identifier}"):
        assert client.get(path).status_code in (400, 404, 405, 422), path


def test_unknown_block_and_tx_are_404(env):
    _, _, client = env
    assert client.get("/api/block/999999").status_code == 404
    assert client.get("/api/block/" + "0" * 64).status_code == 404
    assert client.get("/api/tx/" + "0" * 64).status_code == 404


def test_pagination_bounds(env):
    _, _, client = env
    assert client.get("/api/blocks?limit=0").status_code == 422
    assert client.get("/api/blocks?limit=101").status_code == 422
    assert client.get("/api/blocks?offset=-1").status_code == 422
    assert client.get("/api/blocks?limit=abc").status_code == 422
    assert client.get("/api/mempool?limit=201").status_code == 422
    assert err(client.get("/api/blocks?limit=0")) == "invalid_request"


def test_bad_account_address(env):
    _, _, client = env
    r = client.get("/api/account/not-an-address")
    assert r.status_code == 400 and err(r) == "bad_address"
    assert client.get("/api/account/" + "A" * 5000).status_code == 400


@pytest.mark.parametrize("payload", [
    [], "string", 5, None, {}, {"txid": "x"}, {"amount": 2 ** 70}, {"nonce": True},
])
def test_malformed_transaction_bodies_are_4xx_never_500(env, payload):
    _, _, client = env
    r = client.post("/api/transactions", json=payload)
    assert r.status_code in (400, 422)
    assert r.json()["error"]["code"]


def test_transaction_with_huge_integers_is_rejected_cleanly(env):
    chain, alice, client = env
    tx = alice.tx(Acct(), 1)
    for field in ("amount", "fee", "nonce", "timestamp"):
        r = client.post("/api/transactions", json={**tx, field: 2 ** 70})
        assert r.status_code == 400 and err(r) == "out_of_range", field


def test_type_confusion_is_rejected_not_coerced(env):
    chain, alice, client = env
    tx = alice.tx(Acct(), 1)
    for value in (True, 5.0, "5000000", None, [1], {"a": 1}):
        r = client.post("/api/transactions", json={**tx, "amount": value})
        assert r.status_code == 400 and err(r) == "bad_type", value


def test_rejected_transactions_return_reason_codes(env):
    chain, alice, client = env
    tx = alice.tx(Acct(), 1)
    assert client.post("/api/transactions", json=tx).status_code == 200
    assert err(client.post("/api/transactions", json=tx)) == "duplicate_tx"
    assert err(client.post("/api/transactions", json={**tx, "signature": "A" * 86 + "=="})) == "bad_signature"


def test_invalid_block_submission_is_rejected_with_reason(env):
    chain, alice, client = env
    block = build(chain, alice, lambda b: b.update(previous_hash="1" * 64))
    r = client.post("/api/mining/submit", json=block)
    assert r.status_code == 400 and err(r) == "stale"
    assert chain.tip()["height"] == 3


def test_mining_endpoints_are_loopback_only(tmp_path):
    chain, alice = funded(tmp_path)
    app = create_app(chain, local_hosts=frozenset({"127.0.0.1"}), rate_limit_per_minute=10_000)
    remote = TestClient(app, raise_server_exceptions=False)         # client host = "testclient" => not local
    assert remote.get("/api/mining/template", params={"address": alice.address}).status_code == 403
    assert remote.post("/api/mining/submit", json={}).status_code == 403
    assert remote.get("/api/status").status_code == 200              # public read API unaffected


def test_no_admin_or_secret_endpoints_exist(env):
    _, _, client = env
    paths = set(client.get("/openapi.json").json()["paths"])
    assert not any(w in p for p in paths for w in ("admin", "wallet", "key", "shutdown", "export", "debug"))


def test_body_size_limit_by_content_length_and_by_stream(tmp_path):
    chain, alice = funded(tmp_path)
    client = TestClient(create_app(chain, max_body_bytes=2048, local_hosts=LOCAL, rate_limit_per_minute=10_000),
                        raise_server_exceptions=False)
    big = b'{"x":"' + b"a" * 5000 + b'"}'
    r = client.post("/api/transactions", content=big, headers={"content-type": "application/json"})
    assert r.status_code == 413 and err(r) == "too_large"

    def chunks():                                                    # chunked upload: no Content-Length
        for _ in range(10):
            yield b"a" * 1000
    r = client.post("/api/transactions", content=chunks(), headers={"content-type": "application/json"})
    assert r.status_code == 413
    assert client.get("/api/health").status_code == 200              # server still healthy


def test_default_body_limit_stops_a_50mb_upload(env):
    _, _, client = env
    r = client.post("/api/transactions", content=b"x" * 50_000_000, headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_invalid_content_length_header(env):
    _, _, client = env
    r = client.post("/api/transactions", content=b"{}", headers={"content-length": "abc", "content-type": "application/json"})
    assert r.status_code in (400, 413, 422)          # never a 5xx, never accepted


def test_rate_limiting(tmp_path):
    chain, _ = funded(tmp_path)
    client = TestClient(create_app(chain, rate_limit_per_minute=5, local_hosts=LOCAL), raise_server_exceptions=False)
    codes = [client.get("/api/health").status_code for _ in range(8)]
    assert codes[:5] == [200] * 5 and codes[5:] == [429] * 3
    assert err(client.get("/api/health")) == "rate_limited"


def test_unhandled_errors_do_not_leak_internals(tmp_path, monkeypatch):
    chain, _ = funded(tmp_path)
    client = TestClient(create_app(chain, local_hosts=LOCAL, rate_limit_per_minute=10_000), raise_server_exceptions=False)
    monkeypatch.setattr(chain, "tip", lambda: (_ for _ in ()).throw(RuntimeError("secret internal detail")))
    r = client.get("/api/status")
    assert r.status_code == 500 and err(r) == "internal_error"
    assert "secret internal detail" not in r.text and "Traceback" not in r.text


def test_app_factory_has_no_import_time_side_effects(tmp_path, monkeypatch):
    import importlib
    import mineai.node as node
    home = tmp_path / "home"
    monkeypatch.setenv("MINEAI_DATA_DIR", str(home))
    importlib.reload(node)
    assert not home.exists()


def test_peers_endpoint_is_read_only_and_hides_scores(tmp_path):
    chain, _ = funded(tmp_path)

    class Stub:
        peers = {"n1": object()}
        announced: list = []

        def peer_infos(self):
            return [{"node_id": "n1", "addr": "127.0.0.1:5", "advertised": "127.0.0.1:8081", "inbound": True,
                     "height": 7, "version": 1, "score": 40}]

        def announce_tx(self, tx):
            self.announced.append(("tx", tx["txid"]))

        def announce_block(self, block):
            self.announced.append(("block", block["hash"]))

        def sync_status(self):
            return "synced"

    stub = Stub()
    client = TestClient(create_app(chain, p2p=stub, local_hosts=LOCAL, rate_limit_per_minute=10_000),
                        raise_server_exceptions=False)
    body = client.get("/api/peers").json()
    assert body["count"] == 1 and body["peers"][0]["height"] == 7 and "score" not in body["peers"][0]
    assert client.get("/api/status").json()["peer_count"] == 1
    assert client.post("/api/peers", json={}).status_code == 405       # nothing writable
    alice = chain.storage  # accepted API transactions and blocks are announced to peers
    from tests.helpers import Acct as A
    acct = A()
    a2, bob = A(), A()
    # mine to a fresh funded account through the API path, then send a tx
    from tests.helpers import mine as mine_block
    a3 = A()
    for _ in range(3):
        mine_block(chain, a3)
    tx = a3.tx(bob, 1, nonce=1, timestamp=chain.now())
    assert client.post("/api/transactions", json=tx).status_code == 200
    assert ("tx", tx["txid"]) in stub.announced
    template = client.get("/api/mining/template", params={"address": a3.address}).json()
    from tests.helpers import solve
    block = solve(chain.params, {k: template[k] for k in
                                 ("height", "previous_hash", "merkle_root", "timestamp", "difficulty", "nonce", "transactions")})
    assert client.post("/api/mining/submit", json=block).status_code == 200
    assert ("block", block["hash"]) in stub.announced


def test_status_without_p2p_reports_zero_peers(env):
    _, _, client = env
    st = client.get("/api/status").json()
    assert st["peer_count"] == 0 and st["p2p_enabled"] is False
    assert client.get("/api/peers").json() == {"count": 0, "peers": []}


# ====================================================================== regression: full-size blocks must be submittable
def _full_mempool_chain(tmp_path, senders=8, per_sender=20):
    from tests.helpers import make_chain, mine_n
    chain = make_chain(tmp_path)
    accounts = [Acct() for _ in range(senders)]
    for who in accounts:
        mine_n(chain, who, 3)                                        # every sender owns matured coins
    sink = Acct()
    for who in accounts:
        for nonce in range(1, per_sender + 1):
            chain.submit_transaction(who.tx(sink, "0.01", nonce=nonce, timestamp=chain.now()))
    return chain, accounts[0]


def test_a_block_larger_than_the_default_body_limit_can_be_submitted(tmp_path):
    """Found by the private testnet: >~150 pending transactions made blocks the API refused (413), so miners stopped
    and the mempool could never drain."""
    import json
    from tests.helpers import solve
    chain, miner = _full_mempool_chain(tmp_path)
    assert chain.storage.mempool_count() == 160
    client = TestClient(create_app(chain, local_hosts=LOCAL, rate_limit_per_minute=100_000), raise_server_exceptions=False)
    template = client.get("/api/v1/mining/template", params={"address": miner.address}).json()
    block = solve(chain.params, {k: template[k] for k in ("height", "previous_hash", "merkle_root", "timestamp",
                                                           "difficulty", "nonce", "transactions")})
    size = len(json.dumps(block))
    assert size > 65536, "the test must exercise a block bigger than the default body limit"
    assert len(block["transactions"]) == 161 and size < chain.params.max_block_bytes
    r = client.post("/api/v1/mining/submit", json=block)
    assert r.status_code == 200, r.text
    assert chain.storage.mempool_count() == 0                          # the whole backlog was mined in one block
    chain.verify_integrity()


def test_only_block_submission_gets_the_larger_limit(tmp_path):
    chain, _ = funded(tmp_path)
    client = TestClient(create_app(chain, local_hosts=LOCAL, rate_limit_per_minute=100_000), raise_server_exceptions=False)
    filler = b'{"x":"' + b"a" * 70_000 + b'"}'                            # 70 KB: above 64 KiB, well below a block
    headers = {"content-type": "application/json"}
    assert client.post("/api/v1/transactions", content=filler, headers=headers).status_code == 413
    assert client.post("/api/transactions", content=filler, headers=headers).status_code == 413
    assert client.post("/api/v1/mining/submit", content=filler, headers=headers).status_code in (400, 422)   # parsed, then refused
    huge = b'{"x":"' + b"a" * (2 * chain.params.max_block_bytes + 10) + b'"}'
    assert client.post("/api/v1/mining/submit", content=huge, headers=headers).status_code == 413       # still bounded
    assert client.get("/api/v1/health").status_code == 200


def test_a_custom_body_limit_still_governs_everything_else_but_blocks_stay_submittable(tmp_path):
    chain, _ = funded(tmp_path)
    tiny = TestClient(create_app(chain, max_body_bytes=1024, local_hosts=LOCAL, rate_limit_per_minute=100_000),
                      raise_server_exceptions=False)
    assert tiny.post("/api/v1/transactions", content=b"x" * 2000, headers={"content-type": "application/json"}).status_code == 413
    assert tiny.post("/api/v1/mining/submit", content=b"{}", headers={"content-type": "application/json"}).status_code in (400, 422)
