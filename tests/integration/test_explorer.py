"""Explorer pages and the v1 API used by wallets and the explorer."""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from mineai.node import create_app
from tests.helpers import START, TEST, Acct, funded, mai, mine

LOCAL = frozenset({"testclient", "127.0.0.1"})


@pytest.fixture
def env(tmp_path):
    chain, alice = funded(tmp_path, blocks=4)
    bob = Acct()
    tx = alice.tx(bob, 5, nonce=1, timestamp=chain.now())
    chain.submit_transaction(tx)
    mine(chain, alice)                                   # block 5 confirms the transfer
    chain.clock.advance(60)
    client = TestClient(create_app(chain, local_hosts=LOCAL, rate_limit_per_minute=100_000), raise_server_exceptions=False)
    return chain, alice, bob, tx, client


# ====================================================================== versioning
def test_v1_endpoints_exist_and_legacy_aliases_answer_identically(env):
    chain, alice, bob, tx, client = env
    for path in ("/status", "/health", "/blocks?limit=3", f"/account/{alice.address}", "/mempool", "/fee", "/peers"):
        v1, legacy = client.get("/api/v1" + path), client.get("/api" + path)
        assert v1.status_code == legacy.status_code == 200, path
        if path not in ("/status",):                       # status has fields that can change between two calls
            assert v1.json() == legacy.json(), path
    paths = set(client.get("/openapi.json").json()["paths"])
    assert "/api/v1/status" in paths and "/api/v1/address/{address}/transactions" in paths
    assert not any(p.startswith("/api/") and not p.startswith("/api/v1/") for p in paths)      # aliases are hidden


def test_write_endpoints_only_accept_post(env):
    client = env[-1]
    for path in ("/api/v1/transactions", "/api/v1/mining/submit"):
        assert client.get(path).status_code in (405, 403)
    assert client.delete("/api/v1/status").status_code == 405


# ====================================================================== status / fee / search
def test_status_reports_everything_the_explorer_shows(env):
    chain, alice, bob, tx, client = env
    st = client.get("/api/v1/status").json()
    for key in ("network", "label", "version", "height", "difficulty", "estimated_hashrate", "circulating_supply_mai",
                "max_supply_mai", "mempool_size", "peer_count", "sync_status", "latest_hash", "total_work"):
        assert key in st, key
    assert st["height"] == 5 and st["circulating_supply_mai"] == "125" and st["max_supply_mai"] == "100000000"
    assert st["sync_status"] == "p2p_disabled" and st["peer_count"] == 0


def test_estimated_hashrate_is_work_over_time(tmp_path):
    chain, _ = funded(tmp_path, blocks=12)                   # 60 s apart at fixed difficulty 16
    assert chain.estimated_hashrate() == pytest.approx(16 / 60, rel=0.05)
    from tests.helpers import make_chain
    assert make_chain(tmp_path, TEST, name="fresh.db").estimated_hashrate() is None      # not enough blocks yet


def test_fee_endpoint(env):
    chain, alice, bob, tx, client = env
    body = client.get("/api/v1/fee").json()
    assert body["min_fee_atomic"] == TEST.min_fee and body["default_fee_atomic"] == TEST.default_fee
    assert body["mempool_median_fee_atomic"] is None and body["mempool_size"] == 0
    chain.submit_transaction(alice.tx(bob, 1, fee="0.02", nonce=2, timestamp=chain.now()))
    body = client.get("/api/v1/fee").json()
    assert body["mempool_median_fee_atomic"] == mai("0.02") and body["mempool_size"] == 1


def test_search_finds_blocks_transactions_and_addresses(env):
    chain, alice, bob, tx, client = env
    tip = chain.tip()
    assert client.get("/api/v1/search", params={"q": "3"}).json() == {"type": "block", "value": "3"}
    assert client.get("/api/v1/search", params={"q": tip["hash"]}).json() == {"type": "block", "value": tip["hash"]}
    assert client.get("/api/v1/search", params={"q": tx["txid"]}).json() == {"type": "tx", "value": tx["txid"]}
    assert client.get("/api/v1/search", params={"q": f"  {alice.address} "}).json() == {"type": "address", "value": alice.address}
    for miss in ("999", "0" * 64, "not-an-address", "' OR 1=1 --", "<script>alert(1)</script>", "\x00"):
        assert client.get("/api/v1/search", params={"q": miss}).status_code == 404, miss
    assert client.get("/api/v1/search", params={"q": ""}).status_code == 422
    assert client.get("/api/v1/search", params={"q": "a" * 101}).status_code == 422


# ====================================================================== address history
def test_address_history_lists_mined_sent_and_received_with_pagination(env):
    chain, alice, bob, tx, client = env
    h = client.get(f"/api/v1/address/{alice.address}/transactions").json()
    kinds = [t["direction"] for t in h["transactions"]]
    assert h["total"] == 6 and kinds.count("mined") == 5 and kinds.count("out") == 1
    assert h["transactions"][0]["height"] == 5 and h["transactions"][0]["confirmations"] == 1       # newest first
    sent = next(t for t in h["transactions"] if t["direction"] == "out")
    assert sent["amount"] == mai(5) and sent["fee"] == mai("0.01") and sent["counterparty"] == bob.address
    assert client.get(f"/api/v1/address/{bob.address}/transactions").json()["transactions"][0]["direction"] == "in"
    page2 = client.get(f"/api/v1/address/{alice.address}/transactions", params={"limit": 2, "offset": 2}).json()
    assert len(page2["transactions"]) == 2 and page2["total"] == 6
    assert page2["transactions"][0]["height"] == h["transactions"][2]["height"]
    assert client.get(f"/api/v1/address/{Acct().address}/transactions").json()["transactions"] == []


def test_pending_transactions_are_shown_for_both_parties(env):
    chain, alice, bob, tx, client = env
    pending = alice.tx(bob, 2, nonce=2, timestamp=chain.now())
    chain.submit_transaction(pending)
    a = client.get(f"/api/v1/address/{alice.address}/transactions").json()
    b = client.get(f"/api/v1/address/{bob.address}/transactions").json()
    assert [p["direction"] for p in a["pending"]] == ["out"] and [p["direction"] for p in b["pending"]] == ["in"]
    assert a["pending"][0]["status"] == "pending" and a["pending"][0]["confirmations"] == 0
    later = client.get(f"/api/v1/address/{alice.address}/transactions", params={"offset": 1}).json()
    assert later["pending"] == []                                  # only on the first page


def test_address_history_input_validation(env):
    client = env[-1]
    assert client.get("/api/v1/address/nope/transactions").status_code == 400
    assert client.get(f"/api/v1/address/{Acct().address}/transactions", params={"limit": 0}).status_code == 422
    assert client.get(f"/api/v1/address/{Acct().address}/transactions", params={"limit": 101}).status_code == 422
    assert client.get(f"/api/v1/address/{Acct().address}/transactions", params={"offset": -1}).status_code == 422


# ====================================================================== explorer pages
def test_index_shows_every_required_item(env):
    chain, alice, bob, tx, client = env
    page = client.get("/")
    assert page.status_code == 200
    html = page.text
    for needle in ("DEVNET", "node 0.2", "Block height", "Difficulty", "Est. network hashrate", "Circulating supply",
                   "Maximum supply", "100000000 MAI", "Mempool", "Peers", "Sync status", "Latest blocks"):
        assert needle in html, needle
    assert "/explorer/block/5" in html and alice.address[:16] in html
    assert 'action="/explorer/search"' in html


def test_index_pagination(tmp_path):
    chain, _ = funded(tmp_path, blocks=45)
    client = TestClient(create_app(chain, local_hosts=LOCAL, rate_limit_per_minute=100_000))
    p1, p2, p3 = (client.get("/", params={"page": n}).text for n in (1, 2, 3))
    assert "/explorer/block/45" in p1 and "/explorer/block/26" in p1 and "/explorer/block/25" not in p1
    assert "/explorer/block/25" in p2 and "older →" in p2 and "← newer" in p2
    assert "/explorer/block/0" in p3 and "older →" not in p3
    assert client.get("/", params={"page": 0}).status_code == 422
    assert client.get("/", params={"page": "x"}).status_code == 422


def test_block_page(env):
    chain, alice, bob, tx, client = env
    html = client.get("/explorer/block/5").text
    assert "Block #5" in html and "1 confirmation" not in html and "<div>Confirmations</div><div>1</div>" in html
    assert "/explorer/block/4" in html and "/explorer/block/6" not in html            # previous only (it is the tip)
    assert f"/explorer/tx/{tx['txid']}" in html and f"/explorer/address/{bob.address}" in html
    assert "coinbase" in html
    mid = client.get("/explorer/block/3").text
    assert "/explorer/block/2" in mid and "/explorer/block/4" in mid
    genesis = client.get("/explorer/block/0").text
    assert "genesis" in genesis and "no transactions" in genesis
    assert client.get("/explorer/block/999").status_code == 404
    assert client.get("/explorer/block/" + chain.tip()["hash"]).status_code == 200


def test_transaction_page_confirmed_pending_and_coinbase(env):
    chain, alice, bob, tx, client = env
    html = client.get(f"/explorer/tx/{tx['txid']}").text
    assert "confirmed" in html and "<div>Amount</div><div>5 MAI</div>" in html and "<div>Fee</div><div>0.01 MAI</div>" in html
    assert alice.address in html and bob.address in html
    pending = alice.tx(bob, 1, nonce=2, timestamp=chain.now())
    chain.submit_transaction(pending)
    assert "pending in mempool" in client.get(f"/explorer/tx/{pending['txid']}").text
    coinbase = chain.tip()["transactions"][0]
    cb = client.get(f"/explorer/tx/{coinbase['txid']}").text
    assert "coinbase (block reward)" in cb and "25 MAI" in cb
    assert client.get("/explorer/tx/" + "0" * 64).status_code == 404
    assert client.get("/explorer/tx/not-hex").status_code == 404


def test_address_page(env):
    chain, alice, bob, tx, client = env
    html = client.get(f"/explorer/address/{alice.address}").text
    assert "Confirmed balance" in html and "Available to spend" in html and "History (6 confirmed)" in html
    assert 'class="pill mined"' in html and 'class="pill out"' in html
    bob_html = client.get(f"/explorer/address/{bob.address}").text
    assert "<div>Confirmed balance</div><div>5 MAI</div>" in bob_html and 'class="pill in"' in bob_html
    chain.submit_transaction(alice.tx(bob, 2, nonce=2, timestamp=chain.now()))
    assert "pending out" in client.get(f"/explorer/address/{alice.address}").text
    assert client.get("/explorer/address/nope").status_code == 400


def test_search_redirects_to_the_right_page(env):
    chain, alice, bob, tx, client = env
    def go(q):
        return client.get("/explorer/search", params={"q": q}, follow_redirects=False)
    assert go("2").headers["location"] == "/explorer/block/2" and go("2").status_code == 303
    assert go(tx["txid"]).headers["location"] == f"/explorer/tx/{tx['txid']}"
    assert go(alice.address).headers["location"] == f"/explorer/address/{alice.address}"
    miss = go("<script>alert(1)</script>")
    assert miss.status_code == 404 and "<script>alert(1)</script>" not in miss.text and "&lt;script&gt;" in miss.text
    assert go("").status_code == 404


def test_explorer_is_strictly_read_only_and_leaks_nothing(env):
    chain, alice, bob, tx, client = env
    pages = ["/", "/explorer/block/5", f"/explorer/tx/{tx['txid']}", f"/explorer/address/{alice.address}"]
    for path in pages:
        html = client.get(path).text
        forms = re.findall(r"<form[^>]*>", html)
        assert forms == ['<form class="search" action="/explorer/search" method="get">'], path      # only the search box
        low = html.lower()
        for forbidden in ("password", "private key", "mnemonic", "recovery phrase", "wallet.json", "/admin", "shutdown"):
            assert forbidden not in low, (path, forbidden)
        assert "<script" not in low and "onclick" not in low                                           # no scripts at all
    for method in ("post", "put", "delete", "patch"):
        assert getattr(client, method)("/explorer/block/5").status_code == 405


def test_untrusted_data_is_html_escaped(tmp_path):
    """Everything shown comes from the chain; make sure template autoescaping is on for it."""
    chain, alice = funded(tmp_path, blocks=2)
    client = TestClient(create_app(chain, local_hosts=LOCAL, rate_limit_per_minute=100_000))
    for identifier in ("<img src=x onerror=alert(1)>", "%3Cscript%3E", "1;DROP TABLE blocks"):
        r = client.get(f"/explorer/block/{identifier}")
        assert r.status_code in (400, 404) and "<img" not in r.text and "<script" not in r.text.lower()


def test_explorer_pages_never_500_on_odd_input(env):
    client = env[-1]
    for path in ("/explorer/block/", "/explorer/block/%00", "/explorer/tx/" + "g" * 64, "/explorer/address/" + "A" * 5000,
                 "/explorer/block/99999999999999999999", "/explorer/search?q=" + "%" * 5, "/?page=999999"):
        assert client.get(path).status_code < 500, path
