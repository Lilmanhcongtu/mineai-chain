# Deploying three independent testnet nodes (VPS guide)

Goal: satisfy the launch criterion "at least three independently hosted nodes" in `LAUNCH_CRITERIA.md`.
This is **testnet software, coins have no monetary value, and the network may be reset**. Nothing here has been run on a
real host yet: `packaging/linux/install.sh` and `mineai-node.service` are unverified until CI (`.github/workflows/ci.yml`)
and a first deployment have passed. Treat the first deployment as the test.

## What you need

* Three small Linux machines (1 vCPU / 1 GB RAM / 10 GB disk is plenty), ideally at different providers or regions so they
  are really independent. Ubuntu 22.04+ or Debian 12+ with `python3` >= 3.10.
* A static public IP for each. Call them `A`, `B`, `C`.
* The ability to open **one TCP port (18081, P2P)** in each machine's firewall. Do **not** open 18080.

## Security posture (read first)

* P2P is **unencrypted and unauthenticated** (`SECURITY.md`, `KNOWN_LIMITATIONS.md`). Anyone can connect and read traffic.
  Acceptable for a valueless testnet; not for anything else.
* Keep the HTTP API/explorer on `127.0.0.1` (`MINEAI_HOST` default). It has no authentication. To expose the explorer,
  put a reverse proxy in front that serves **read-only GET requests only**; never proxy the mining endpoints.
* Run the node as an unprivileged user. Never put wallet files or passwords on a seed node: a seed only relays.

## 1. Per-machine setup (repeat on A, B, C)

```bash
sudo adduser --system --group --home /home/mineai --shell /bin/bash mineai
sudo mkdir -p /var/lib/mineai /etc/mineai && sudo chown mineai:mineai /var/lib/mineai

sudo -u mineai -H sh -c '
  git clone <YOUR-REPO-URL> ~/mineai-src && cd ~/mineai-src && sh packaging/linux/install.sh'
```

`install.sh` creates a virtualenv in `~/.local/share/mineai` and the wrapper `~/.local/bin/mineai-node`.

## 2. Configure each node

`/etc/mineai/node.env` (mode 0640, owner root:mineai). Replace `A`, `B`, `C` with the real IPs; each node seeds the **other two**:

```ini
MINEAI_NETWORK=testnet
MINEAI_DATA_DIR=/var/lib/mineai
MINEAI_P2P_HOST=0.0.0.0
# node A lists B and C; node B lists A and C; node C lists A and B:
MINEAI_SEEDS=B:18081,C:18081
```

Leave `MINEAI_HOST` unset so the API stays on 127.0.0.1.

## 3. Firewall

```bash
sudo ufw allow 22/tcp
sudo ufw allow 18081/tcp
sudo ufw enable
```

## 4. Start

```bash
sudo cp ~mineai/mineai-src/packaging/linux/mineai-node.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mineai-node
journalctl -u mineai-node -f            # JSON log lines
```

## 5. Verify

On each machine:

```bash
curl -s http://127.0.0.1:18080/api/v1/status
```

Expect `"network":"testnet"`, genesis `2ddd2b532e7d313ab4fed3c7ddbba84297d9f35fd040292e2046da471d29d335`, and `peer_count` of 2.
If the genesis differs, the software version is wrong; the nodes will refuse each other.

Then mine a few blocks from one machine (miners only talk to a loopback node) and confirm the others follow:

```bash
mineai wallet --network testnet create --wallet ~/me.testnet.wallet.json     # on a machine you trust, not necessarily a seed
mineai miner  --network testnet --address <TMAI...> --blocks 5
```

Check that `height` and `latest_hash` match on all three via `/api/v1/status`. Then run the checks in `docs/PRIVATE_TESTNET.md`
against the real network: restart nodes one at a time, stop one for an hour and start it again, and confirm each recovers.

## 6. Monitoring

Point Prometheus at `http://127.0.0.1:18080/metrics` on each host (through an SSH tunnel or a local agent). Alerts worth having
are listed in `PRIVATE_TESTNET.md` (peers == 0, height stalled for 10 block times, reorg rate).

## 7. Publishing the seed list

Only after all three are synchronized for a few days: publish `A:18081,B:18081,C:18081` in the README and `INSTALL.md`, keep the
"no monetary value / may be reset" statement next to it, and record the date in `LAUNCH_CRITERIA.md`.

## Known gaps this guide does not close

Time skew (run `chrony`/`systemd-timesyncd`: blocks with timestamps over 5 minutes ahead are rejected), no eclipse-attack
protection, no incident process yet. See `KNOWN_LIMITATIONS.md` and `SECURITY_CHECKLIST.md`.
