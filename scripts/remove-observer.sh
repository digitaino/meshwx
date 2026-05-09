#!/usr/bin/env bash
# Revoke an observer's credential from the Mosquitto password file and
# reload the broker. Existing connections from that user will be allowed
# to finish in-flight publishes but new connections will fail.
#
# Usage:
#   ./scripts/remove-observer.sh <username>
#
# Run from the repo root.

set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <username>" >&2
  exit 1
fi

USERNAME="$1"
PASSWORD_FILE="mosquitto/passwords"

if [[ ! -f "$PASSWORD_FILE" ]]; then
  echo "error: $PASSWORD_FILE not found — are you in the repo root?" >&2
  exit 1
fi

if ! cut -d: -f1 "$PASSWORD_FILE" | grep -qx "$USERNAME"; then
  echo "no such user: $USERNAME" >&2
  echo "current users:" >&2
  cut -d: -f1 "$PASSWORD_FILE" | sed 's/^/  /' >&2
  exit 1
fi

docker run --rm \
  -v "$(pwd)/$PASSWORD_FILE:/passwords" \
  eclipse-mosquitto:2 \
  mosquitto_passwd -D /passwords "$USERNAME" >/dev/null

if docker ps --format '{{.Names}}' | grep -q '^mosquitto$'; then
  docker kill --signal=HUP mosquitto >/dev/null
  RELOAD_NOTE="(broker reloaded)"
else
  RELOAD_NOTE="(mosquitto container not running)"
fi

echo "Removed user $USERNAME $RELOAD_NOTE"
