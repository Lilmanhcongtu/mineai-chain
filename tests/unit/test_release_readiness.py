"""Public-testnet release candidate: frozen rules, pinned genesis, labelling, isolation, version consistency."""
from __future__ import annotations

import dataclasses
import pathlib
import re
import sys

import pytest
from fastapi.testclient import TestClient

from mineai import __version__, config, consensus as C
from mineai.blockchain import Blockchain
from mineai.config import DEVNET, MAINNET, PRIVNET, TESTNET, banner_lines, consensus_fingerprint
from mineai.node import create_app

ROOT = pathlib.Path(__file__).resolve().parents[2]

# ---- frozen consensus rules: changing ANY consensus parameter changes these values. If you meant to change the rules,
# you must give the network a NEW network id (and genesis) and update these pins deliberately.
PINNED_FINGERPRINTS = {
    "devnet": "4e2df364c7b7859c50dfa86ba2d067bfd304a39a56099b8984a733cb7e7407da",
    "testnet": "3a5f5223c12364c1363a117df0cbbbd87379ce65cdba1db5822d8e0da6217ed0",
    "privnet": "5077a2c856b34413f464ee6e3bb3cfd690099052f76ef12fbb69927bb82e75b8",
}
TESTNET_GENESIS = "2ddd2b532e7d313ab4fed3c7ddbba84297d9f35fd040292e2046da471d29d335"


@pytest.mark.parametrize("profile", [DEVNET, TESTNET, PRIVNET], ids=lambda p: p.name)
def test_consensus_rules_are_frozen_per_profile(profile):
    assert consensus_fingerprint(profile) == PINNED_FINGERPRINTS[profile.name], (
        f"{profile.name}: a consensus parameter changed. Rules of a published network must not change: "
        "issue a new network id and genesis, then update the pin on purpose.")


def test_the_fingerprint_reacts_to_every_consensus_field_and_ignores_policy():
    base = consensus_fingerprint(TESTNET)
    for name in config.CONSENSUS_FIELDS:
        value = getattr(TESTNET, name)
        changed = {"network_id": "x-net", "address_prefix": "QMAI", "dynamic_difficulty": not value, "genesis_hash": "0" * 64}.get(
            name, value + 1 if isinstance(value, int) else value)
        alt = dataclasses.replace(TESTNET, **{name: changed})
        assert consensus_fingerprint(alt) != base, name
    for name, value in (("mempool_max_txs", 1), ("max_reorg_depth", 5), ("mempool_max_per_sender", 3), ("max_orphans", 7)):
        assert consensus_fingerprint(dataclasses.replace(TESTNET, **{name: value})) == base, name    # policy, not consensus


def test_the_testnet_genesis_is_fixed_pinned_and_unique():
    assert TESTNET.genesis_hash == TESTNET_GENESIS == C.genesis_block(TESTNET)["hash"]
    hashes = {p.name: p.genesis_hash for p in (DEVNET, TESTNET, PRIVNET)}
    assert len(set(hashes.values())) == 3                                  # no two networks share a genesis
    g = C.genesis_block(TESTNET)
    assert g["height"] == 0 and g["transactions"] == [] and g["difficulty"] == 0 and g["nonce"] == 0   # no premine, no coinbase


def test_a_fresh_testnet_node_opens_on_the_pinned_genesis_and_refuses_the_others(tmp_path):
    chain = Blockchain(tmp_path / "t.db", TESTNET)
    assert chain.tip()["hash"] == TESTNET_GENESIS and chain.minted_supply() == 0
    assert chain.storage.total_balances() == 0                              # nothing exists at genesis: no premine
    chain.close()
    from mineai.blockchain import ChainError
    with pytest.raises(ChainError, match="belongs to network"):
        Blockchain(tmp_path / "t.db", PRIVNET)                              # a testnet database cannot be opened as another network


def test_testnet_parameters_match_the_documented_release():
    assert (TESTNET.network_id, TESTNET.address_prefix, TESTNET.default_port, TESTNET.default_p2p_port) == (
        "mineai-testnet-v1", "TMAI", 18080, 18081)
    assert TESTNET.target_spacing == 60 and TESTNET.coinbase_maturity == 10 and TESTNET.dynamic_difficulty
    assert (TESTNET.block_reward, TESTNET.max_supply, TESTNET.min_fee) == (25_000_000, 100_000_000 * 10 ** 6, 1_000)
    assert "TESTNET" in TESTNET.label and "no monetary value" in TESTNET.label
    assert TESTNET.enabled and not MAINNET.enabled


def test_testnet_data_lives_in_its_own_directory(tmp_path):
    a, b, c = (config.db_path_for(p, tmp_path) for p in (DEVNET, TESTNET, PRIVNET))
    assert len({a, b, c}) == 3 and "testnet" in str(b) and str(b).endswith("chain.db")
    assert a.parent != b.parent                                             # separate database directories


# ====================================================================== labelling
@pytest.mark.parametrize("profile", [DEVNET, TESTNET, PRIVNET], ids=lambda p: p.name)
def test_every_test_network_banner_says_it_is_a_test_network(profile):
    text = "\n".join(banner_lines(profile, "1.2.3"))
    assert "TEST network" in text and "NO monetary value" in text and "reset" in text and "1.2.3" in text
    assert "real password" in text


def test_the_explorer_and_status_api_say_testnet(tmp_path):
    chain = Blockchain(tmp_path / "t.db", TESTNET)
    client = TestClient(create_app(chain, local_hosts=frozenset({"testclient"}), rate_limit_per_minute=100_000))
    page = client.get("/").text
    assert "TESTNET" in page and "no monetary value" in page and "network testnet" in page
    st = client.get("/api/v1/status").json()
    assert st["network"] == "testnet" and "TESTNET" in st["label"] and st["genesis_hash"] == TESTNET_GENESIS
    assert "TESTNET" in client.get(f"/explorer/block/0").text


def test_wallet_creation_on_testnet_warns_explicitly(tmp_path, monkeypatch, capsys):
    from mineai import wallet as W
    monkeypatch.setattr(sys, "argv", ["mineai-wallet", "--network", "testnet", "create", "--wallet", str(tmp_path / "t.wallet.json")])
    monkeypatch.setenv("MINEAI_WALLET_PASSWORD", "a test passphrase for testnet 1")
    W.main()
    out = capsys.readouterr().out
    assert "TESTNET" in out and "TEST network" in out and "NO monetary value" in out and "reset" in out
    assert "TMAI" in out                                                    # a testnet address, not a devnet one
    assert "a test passphrase for testnet 1" not in out


def test_a_testnet_wallet_cannot_be_used_on_other_networks(tmp_path, monkeypatch, capsys):
    from mineai import wallet as W
    path = tmp_path / "t.wallet.json"
    address = W.create_wallet_file(path, "another testnet passphrase 2", TESTNET, kdf_n=2 ** 12)
    assert address.startswith("TMAI")
    for other in (DEVNET, PRIVNET):
        with pytest.raises(SystemExit, match="not a"):
            W.load_wallet(path, other)


# ====================================================================== versioning
def test_version_is_consistent_everywhere():
    assert __version__ == "0.2.0rc1"
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert f'version = "{__version__}"' in pyproject
    notes = ROOT / "docs" / "RELEASE_NOTES.md"
    if notes.exists():
        assert __version__ in notes.read_text(encoding="utf-8").splitlines()[0]
    assert re.fullmatch(r"\d+\.\d+\.\d+(rc\d+)?", __version__)               # PEP 440 release candidate


def test_v01_balances_cannot_carry_over():
    """No transfer of V0.1 / devnet balances: different network id, address prefix and genesis make it impossible."""
    assert TESTNET.network_id != DEVNET.network_id and TESTNET.address_prefix != DEVNET.address_prefix
    assert TESTNET.genesis_hash != DEVNET.genesis_hash
