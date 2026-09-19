"""Single entry point: `mineai node|wallet|miner ...` (also `python -m mineai ...`).

The packaged Windows build ships one executable, mineai.exe, plus small wrapper scripts for each tool.
"""
from __future__ import annotations

import multiprocessing
import sys

USAGE = """MineAI (development software: devnet/testnet only, no monetary value)

usage: mineai <command> [options]

commands:
  node     run a node with the REST API, explorer and peer-to-peer networking
  wallet   create/backup/restore wallets, check balances, send, interactive shell
  miner    mine blocks (runs only while the command runs; Ctrl+C stops it)
  version  print the version

Run 'mineai <command> --help' for the options of each command."""


def main(argv: list[str] | None = None) -> None:
    multiprocessing.freeze_support()          # required by the miner's worker processes in a frozen build
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        return
    command, rest = argv[0], argv[1:]
    sys.argv = [f"mineai {command}"] + rest
    if command == "node":
        from .node import main as run
    elif command == "wallet":
        from .wallet import main as run
    elif command == "miner":
        from .miner import main as run
    elif command in ("version", "--version"):
        from . import __version__, PROTOCOL_VERSION
        print(f"mineai {__version__} (protocol {PROTOCOL_VERSION})")
        return
    else:
        print(f"unknown command {command!r}\n\n{USAGE}", file=sys.stderr)
        raise SystemExit(2)
    run()


if __name__ == "__main__":
    main()
