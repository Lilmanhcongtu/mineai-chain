"""Mining: proof-of-work backend, the real multiprocess miner, and user-facing accept/reject reporting."""
from __future__ import annotations

import ast
import pathlib
import sys

import pytest

from mineai import consensus as C
from mineai import miner as M
from mineai.pow import BACKENDS, PowBackend, Sha256Backend, get_backend
from tests.helpers import TEST, Acct, funded, make_chain


def prefix_for(template, params=TEST):
    return C.header_prefix(params, template["height"], template["previous_hash"], template["merkle_root"],
                           template["timestamp"], template["difficulty"])


# ====================================================================== backend interface
def test_backend_registry_and_interface():
    assert set(BACKENDS) == {"sha256"} and isinstance(get_backend("sha256"), PowBackend)
    with pytest.raises(ValueError, match="unknown proof-of-work backend"):
        get_backend("randomx")                                        # not implemented, and says so
    assert "randomx" not in BACKENDS


def test_sha256_backend_finds_nonces_that_consensus_accepts(tmp_path):
    chain = make_chain(tmp_path)
    template = chain.mining_template(Acct().address)
    prefix, backend = prefix_for(template), Sha256Backend()
    progress = []
    nonce, digest = backend.search(prefix, template["difficulty"], 0, 1, lambda: False, progress.append)
    assert backend.verify(prefix, nonce, template["difficulty"])
    assert digest == C.hash_header(prefix, nonce) and C.meets_target(digest, template["difficulty"])
    assert sum(progress) >= 1


def test_search_is_striped_across_workers_without_overlap(tmp_path):
    prefix = b"x" * 40
    found = []
    for worker in range(4):
        nonce, _ = Sha256Backend().search(prefix, 200, worker, 4, lambda: False, lambda n: None)
        found.append(nonce)
        assert nonce % 4 == worker                                    # each worker only tries its own residue class


def test_search_stops_promptly_when_asked_and_reports_progress():
    calls = {"n": 0, "tried": 0}

    def stop():
        calls["n"] += 1
        return calls["n"] >= 3

    result = Sha256Backend().search(b"p" * 40, 2 ** 62, 0, 1, stop, lambda n: calls.__setitem__("tried", calls["tried"] + n))
    assert result is None and calls["tried"] >= 2 * 512               # gave up after a few chunks, having reported its work


def test_verify_rejects_a_wrong_nonce():
    prefix = b"q" * 40
    nonce, _ = Sha256Backend().search(prefix, 4096, 0, 1, lambda: False, lambda n: None)
    assert Sha256Backend().verify(prefix, nonce, 4096)
    bad = next(n for n in range(nonce + 1, nonce + 100000) if not Sha256Backend().verify(prefix, n, 4096))
    assert not Sha256Backend().verify(prefix, bad, 4096)


# ====================================================================== the real multiprocess miner
def test_multiprocess_miner_mines_a_block_the_node_accepts(tmp_path):
    chain, _ = funded(tmp_path, blocks=1)
    alice = Acct()
    template = chain.mining_template(alice.address)
    outcome = M.mine_template(TEST, template, threads=2)
    assert outcome is not None
    block, elapsed, rate = outcome
    assert elapsed > 0 and rate > 0                                   # a measured, aggregate hashrate
    accepted = chain.submit_mined_block(block)
    assert accepted["height"] == 2 and chain.account(alice.address)["balance"] == 25_000_000


def test_miner_aborts_stale_work_and_stops_its_workers(tmp_path):
    chain = make_chain(tmp_path)
    template = chain.mining_template(Acct().address)
    template["difficulty"] = 2 ** 60                                  # impossible: only an abort can end this
    calls = {"n": 0}

    def abort():
        calls["n"] += 1
        return calls["n"] >= 2
    import time
    started = time.time()
    assert M.mine_template(TEST, template, threads=2, should_abort=abort) is None
    assert time.time() - started < 15
    import multiprocessing
    time.sleep(0.3)
    assert not [p for p in multiprocessing.active_children() if p.is_alive()]          # nothing keeps running


# ====================================================================== accept / reject reporting
class Resp:
    def __init__(self, status, body=None, text=""):
        self.status_code, self._body, self.text = status, body, text

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class FakeHttp:
    def __init__(self, template, submits):
        self.template, self.submits, self.posted = template, list(submits), []

    def get(self, url, **kw):
        if url.endswith("/mining/template"):
            return self.template() if callable(self.template) else Resp(200, self.template)
        return Resp(200, {"latest_hash": "irrelevant"})

    def post(self, url, json=None, **kw):
        self.posted.append(json)
        return self.submits.pop(0)


def canned_template(height=1):
    return {"network_id": TEST.network_id, "height": height, "previous_hash": "0" * 64, "merkle_root": "1" * 64,
            "timestamp": 1, "difficulty": 16, "nonce": 0, "transactions": [{"txid": "c"}]}


def canned_miner(params, template, threads, stale, backend):
    return ({"height": template["height"], "hash": "ab" * 32}, 0.5, 1234.0)


def test_miner_reports_accepted_blocks_and_stops_at_the_requested_count():
    http = FakeHttp(canned_template(), [Resp(200, {"accepted": True, "height": 1, "hash": "ab" * 32})] * 2)
    out = []
    n = M.run_miner(TEST, "http://node", "addr", 2, 1, out=out.append, http=http, miner=canned_miner)
    assert n == 2 and len(http.posted) == 2
    text = "\n".join(out)
    assert text.count("ACCEPTED block") == 2 and "1,234 H/s" in text and "difficulty 16" in text


def test_miner_retries_after_a_stale_rejection_and_says_why():
    http = FakeHttp(canned_template(), [Resp(400, {"error": {"code": "stale", "message": "does not extend the tip"}}),
                                        Resp(200, {"accepted": True, "height": 1, "hash": "ab" * 32})])
    out = []
    assert M.run_miner(TEST, "http://node", "addr", 1, 1, out=out.append, http=http, miner=canned_miner) == 1
    text = "\n".join(out)
    assert "Rejected [stale]" in text and "another block was accepted first" in text and "ACCEPTED" in text


@pytest.mark.parametrize("code", ["bad_pow", "bad_difficulty", "bad_coinbase", "bad_timestamp", "insufficient_funds"])
def test_miner_stops_with_the_reason_on_a_real_rejection(code):
    http = FakeHttp(canned_template(), [Resp(400, {"error": {"code": code, "message": "because reasons"}})])
    with pytest.raises(SystemExit) as exc:
        M.run_miner(TEST, "http://node", "addr", 1, 1, out=lambda *_: None, http=http, miner=canned_miner)
    assert f"REJECTED [{code}]" in str(exc.value) and "because reasons" in str(exc.value)


def test_miner_drops_stale_work_without_submitting():
    calls = {"n": 0}

    def miner(params, template, threads, stale, backend):
        calls["n"] += 1
        return None if calls["n"] == 1 else canned_miner(params, template, threads, stale, backend)
    http = FakeHttp(canned_template(), [Resp(200, {"accepted": True, "height": 1, "hash": "ab" * 32})])
    out = []
    M.run_miner(TEST, "http://node", "addr", 1, 1, out=out.append, http=http, miner=miner)
    assert len(http.posted) == 1 and any("Stale work" in l for l in out)


def test_miner_refuses_a_node_on_another_network_and_node_errors():
    wrong = canned_template()
    wrong["network_id"] = "some-other-network"
    with pytest.raises(SystemExit, match="different network"):
        M.run_miner(TEST, "http://node", "addr", 1, 1, out=lambda *_: None, http=FakeHttp(wrong, []), miner=canned_miner)
    refusing = FakeHttp(lambda: Resp(403, {"error": {"code": "http_error", "message": "only from the local machine"}}), [])
    with pytest.raises(SystemExit, match="refused to give a mining template"):
        M.run_miner(TEST, "http://node", "addr", 1, 1, out=lambda *_: None, http=refusing, miner=canned_miner)
    garbled = FakeHttp(lambda: Resp(500, None, "<html>oops</html>"), [])
    with pytest.raises(SystemExit, match="500"):
        M.run_miner(TEST, "http://node", "addr", 1, 1, out=lambda *_: None, http=garbled, miner=canned_miner)


# ====================================================================== command line: consent and limits
def cli(monkeypatch, capsys, argv):
    monkeypatch.setattr(sys, "argv", ["mineai-miner"] + argv)
    try:
        M.main()
    except SystemExit as exc:
        return str(exc.code), capsys.readouterr().out
    return "", capsys.readouterr().out


def test_cli_validates_before_doing_anything(monkeypatch, capsys):
    ok = Acct().address
    for argv, message in [
        (["--address", "nope"], "invalid devnet address"),
        (["--address", ok, "--blocks", "0"], "--blocks must be"),
        (["--address", ok, "--threads", "0"], "--threads"),
        (["--address", ok, "--threads", "9999"], "--threads"),
        (["--address", ok, "--backend", "randomx"], "unknown proof-of-work backend"),
        ([], "the following arguments are required|2"),
    ]:
        err, _ = cli(monkeypatch, capsys, argv)
        assert err != "", argv


def test_cli_announces_that_it_stops_when_the_command_exits(monkeypatch, capsys):
    monkeypatch.setattr(M, "run_miner", lambda *a, **k: 1)
    _, out = cli(monkeypatch, capsys, ["--address", Acct().address, "--blocks", "1", "--threads", "1"])
    assert "Ctrl+C" in out and "nothing keeps mining after this command exits" in out


def test_ctrl_c_is_a_clean_stop(monkeypatch, capsys):
    def interrupted(*a, **k):
        raise KeyboardInterrupt
    monkeypatch.setattr(M, "run_miner", interrupted)
    err, out = cli(monkeypatch, capsys, ["--address", Acct().address, "--threads", "1"])
    assert err == "" and "Stopped by user" in out


# ====================================================================== no hidden mining anywhere
def _imports_miner(node) -> bool:
    if isinstance(node, ast.ImportFrom):
        return bool(node.module) and node.module.split(".")[-1] == "miner" or any(a.name == "miner" for a in node.names)
    if isinstance(node, ast.Import):
        return any(a.name.split(".")[-1] == "miner" for a in node.names)
    return False


def test_nothing_starts_mining_as_a_side_effect():
    """The node, wallet, API and P2P code never import the miner. The only importer is the `mineai miner`
    command dispatcher, and it does so lazily inside that command's branch (never at module import time)."""
    root = pathlib.Path(__file__).resolve().parents[2] / "mineai"
    offenders = []
    for path in root.rglob("*.py"):
        if path.name in ("miner.py", "pow.py"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if path.name == "__main__.py":
            assert not any(_imports_miner(n) for n in tree.body), "miner must not be imported when mineai starts"
            lazy = [n for n in ast.walk(tree) if _imports_miner(n)]
            assert len(lazy) == 1                                      # exactly one lazy import, in the miner branch
            continue
        offenders += [str(path) for n in ast.walk(tree) if _imports_miner(n)]
    assert offenders == []
    src = (root / "miner.py").read_text()
    assert "daemon=True" in src and "atexit" not in src and "Scheduler" not in src           # workers die with the command
