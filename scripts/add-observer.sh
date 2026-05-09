#!/usr/bin/env bash
# Add a new observer credential to the Mosquitto password file and reload
# the broker without dropping existing connections. Pass the username as
# the first argument; password is generated and printed at the end.
#
# Usage:
#   ./scripts/add-observer.sh <username>
#   ./scripts/add-observer.sh <username> <password>   # use an explicit password
#
# Run from the repo root.

set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "usage: $0 <username> [password]" >&2
  exit 1
fi

USERNAME="$1"
PASSWORD="${2:-}"

PASSWORD_FILE="mosquitto/passwords"
if [[ ! -f "$PASSWORD_FILE" ]]; then
  echo "error: $PASSWORD_FILE not found — are you in the repo root?" >&2
  exit 1
fi

if [[ -z "$PASSWORD" ]]; then
  PASSWORD=$(openssl rand -hex 16)
fi

# Add (or update) the user. mosquitto_passwd updates in place if the user
# already exists, so this doubles as a "rotate password" command.
docker run --rm \
  -v "$(pwd)/$PASSWORD_FILE:/passwords" \
  eclipse-mosquitto:2 \
  mosquitto_passwd -b /passwords "$USERNAME" "$PASSWORD" >/dev/null

# Reload without dropping existing client connections.
if docker ps --format '{{.Names}}' | grep -q '^mosquitto$'; then
  docker kill --signal=HUP mosquitto >/dev/null
  RELOAD_NOTE="(broker reloaded)"
else
  RELOAD_NOTE="(mosquitto container not running — start the stack to apply)"
fi

cat <<EOF

Observer credential added $RELOAD_NOTE

  username = "$USERNAME"
  password = "$PASSWORD"

Send these to the observer along with docs/Observer_Onboarding.md.
EOF
