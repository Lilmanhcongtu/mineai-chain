#!/bin/sh
# MineAI TESTNET - Linux source install.  UNVERIFIED: this script has never been run on Linux.
# Installs into a private virtualenv under $PREFIX (default ~/.local/share/mineai). Needs python3 >= 3.10.
# It never touches wallets or chain data.
set -eu
PREFIX="${PREFIX:-$HOME/.local/share/mineai}"
BIN="${BIN:-$HOME/.local/bin}"
SRC="$(cd "$(dirname "$0")/../.." && pwd)"

command -v python3 >/dev/null 2>&1 || { echo "python3 not found" >&2; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || { echo "Python 3.10 or newer is required" >&2; exit 1; }

echo "Installing MineAI (TESTNET, no monetary value) into $PREFIX"
python3 -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/python" -m pip install --upgrade pip
"$PREFIX/venv/bin/python" -m pip install "$SRC"

mkdir -p "$BIN"
for tool in node wallet miner; do
    cat > "$BIN/mineai-$tool" <<WRAP
#!/bin/sh
exec "$PREFIX/venv/bin/python" -m mineai $tool "\$@"
WRAP
    chmod 755 "$BIN/mineai-$tool"
done
echo "Installed: mineai-node, mineai-wallet, mineai-miner in $BIN (add it to PATH if needed)."
echo "Default network is devnet; use MINEAI_NETWORK=testnet for the public testnet candidate."
echo "Uninstall: rm -rf \"$PREFIX\" \"$BIN\"/mineai-node \"$BIN\"/mineai-wallet \"$BIN\"/mineai-miner   (your data directory is not touched)"
