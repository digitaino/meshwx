#!/usr/bin/env bash
# Add (or rotate) an observer credential and produce a turnkey bundle the
# operator can hand over. The bundle contains a docker-compose.yml, a
# pre-filled config.toml, and a README — the observer extracts and runs
# `docker compose up -d`.
#
# Usage:
#   ./scripts/add-observer.sh <username> [iata]
#   ./scripts/add-observer.sh <username> <iata> <password>   # explicit pwd
#
# Defaults: iata=AUS, password=$(openssl rand -hex 16).
# Run from the repo root.

set -euo pipefail

if [[ $# -lt 1 || $# -gt 3 ]]; then
  echo "usage: $0 <username> [iata] [password]" >&2
  exit 1
fi

USERNAME="$1"
IATA="${2:-AUS}"
PASSWORD="${3:-$(openssl rand -hex 16)}"

PASSWORD_FILE="mosquitto/passwords"
TEMPLATE_DIR="templates/observer"
BUNDLE_DIR="out/observer-bundles/$USERNAME"

if [[ ! -f "$PASSWORD_FILE" ]]; then
  echo "error: $PASSWORD_FILE not found — are you in the repo root?" >&2
  exit 1
fi
if [[ ! -d "$TEMPLATE_DIR" ]]; then
  echo "error: $TEMPLATE_DIR not found — are you in the repo root?" >&2
  exit 1
fi

# Add (or update) the user. mosquitto_passwd updates in place if the user
# already exists, so this doubles as a rotate-password command.
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

# Generate the bundle.
rm -rf "$BUNDLE_DIR"
mkdir -p "$BUNDLE_DIR"
cp "$TEMPLATE_DIR/docker-compose.yml" "$BUNDLE_DIR/docker-compose.yml"
cp "$TEMPLATE_DIR/README.md"          "$BUNDLE_DIR/README.md"

# Fill in the config.toml placeholders.
sed \
  -e "s|__USERNAME__|$USERNAME|g" \
  -e "s|__PASSWORD__|$PASSWORD|g" \
  -e "s|__IATA__|$IATA|g" \
  "$TEMPLATE_DIR/config.toml" > "$BUNDLE_DIR/config.toml"

# Tarball for easy hand-off.
TARBALL="out/observer-bundles/$USERNAME.tar.gz"
tar -czf "$TARBALL" -C "out/observer-bundles" "$USERNAME"

cat <<EOF

Observer added: $USERNAME (iata=$IATA) $RELOAD_NOTE

  bundle:   $BUNDLE_DIR/   ($BUNDLE_DIR/{docker-compose.yml,config.toml,README.md})
  tarball:  $TARBALL
  username: $USERNAME
  password: $PASSWORD

Send the tarball to the observer. They run:

  tar xzf $USERNAME.tar.gz
  cd $USERNAME
  docker compose up -d

Done.
EOF
