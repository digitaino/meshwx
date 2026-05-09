#!/usr/bin/env bash
# Native runner — no Docker needed. Creates a Python venv on first run,
# installs meshcore + paho-mqtt, then runs observer.py with the env
# values from .env. Re-run after editing .env to apply changes.
#
# To run as a service after first verifying it works, see the systemd
# unit example at the bottom of README.md.

set -euo pipefail

cd "$(dirname "$0")"

if [[ ! -f .env ]]; then
  echo "error: .env not found next to run-native.sh" >&2
  exit 1
fi

if [[ ! -d .venv ]]; then
  echo "Creating venv (one-time, ~30s)..."
  python3 -m venv .venv
  .venv/bin/pip install --quiet --upgrade pip
  .venv/bin/pip install --quiet "meshcore>=2.3" "paho-mqtt>=2.0"
fi

set -a
# shellcheck disable=SC1091
source .env
set +a

exec .venv/bin/python observer.py
