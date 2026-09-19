#!/bin/sh
# Builds a Linux source tarball check: installs into a temp venv and runs the tests.  UNVERIFIED on Linux.
set -eu
cd "$(dirname "$0")/../.."
python3 -m venv .build-venv
.build-venv/bin/python -m pip install --upgrade pip
.build-venv/bin/python -m pip install ".[dev]"
.build-venv/bin/python -m pytest -q -x -p no:cacheprovider
.build-venv/bin/python -m mineai version
