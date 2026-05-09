#!/usr/bin/env bash
# Add (or rotate) an observer credential and produce a turnkey bundle the
# operator can hand off. The bundle contains a docker-compose.yml, the
# pre-filled config (TOML or env), and a README — the observer extracts
# and runs `docker compose up -d`.
#
# Two bundle flavors:
#   (default)  — observer connects directly to a USB radio
#                (templates/observer/, uses Cisien/meshcoretomqtt)
#   --proxy    — observer connects to an existing meshcore-proxy TCP
#                endpoint (templates/observer-tcp/). Use when the friend
#                already runs something like rgregg/meshcore-proxy
#                against their radio for the Meshcore app or HA.
#
# Usage:
#   ./scripts/add-observer.sh [--proxy] <username> [iata] [password]
#
# Defaults: iata=AUS, password=$(openssl rand -hex 16).
# Run from the repo root.

set -euo pipefail

VARIANT="usb"
TEMPLATE_DIR="templates/observer"
if [[ "${1:-}" == "--proxy" ]]; then
  VARIANT="proxy"
  TEMPLATE_DIR="templates/observer-tcp"
  shift
fi

if [[ $# -lt 1 || $# -gt 3 ]]; then
  echo "usage: $0 [--proxy] <username> [iata] [password]" >&2
  exit 1
fi

USERNAME="$1"
IATA="${2:-AUS}"
PASSWORD="${3:-$(openssl rand -hex 16)}"

PASSWORD_FILE="mosquitto/passwords"
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

# Generate the bundle. We copy the whole template tree so any extra files
# (e.g. observer.py and Dockerfile in the proxy variant) come along.
rm -rf "$BUNDLE_DIR"
mkdir -p "$BUNDLE_DIR"
cp -R "$TEMPLATE_DIR"/. "$BUNDLE_DIR"/

# Substitute placeholders in every file that might carry credentials.
find "$BUNDLE_DIR" -type f \( -name "*.toml" -o -name "docker-compose.yml" -o -name ".env.template" \) -print0 |
  while IFS= read -r -d '' f; do
    sed -i.bak \
      -e "s|__USERNAME__|$USERNAME|g" \
      -e "s|__PASSWORD__|$PASSWORD|g" \
      -e "s|__IATA__|$IATA|g" \
      "$f"
    rm -f "$f.bak"
  done

# If the bundle ships a .env.template, materialize it as .env so the
# observer can run-native.sh immediately without an extra cp step.
if [[ -f "$BUNDLE_DIR/.env.template" ]]; then
  mv "$BUNDLE_DIR/.env.template" "$BUNDLE_DIR/.env"
fi

# Tarball for easy hand-off.
TARBALL="out/observer-bundles/$USERNAME.tar.gz"
tar -czf "$TARBALL" -C "out/observer-bundles" "$USERNAME"

if [[ "$VARIANT" == "proxy" ]]; then
  RUN_HINT='./install-user.sh        # no sudo — user-mode systemd (recommended)
  # or: sudo ./install.sh      # system-wide install with hardening
  # or: docker compose up -d   # if they prefer Docker
  # or: ./run-native.sh        # foreground test'
else
  RUN_HINT='docker compose up -d'
fi

cat <<EOF

Observer added: $USERNAME (iata=$IATA, variant=$VARIANT) $RELOAD_NOTE

  bundle:   $BUNDLE_DIR/
  tarball:  $TARBALL
  username: $USERNAME
  password: $PASSWORD

Send the tarball to the observer. They run:

  tar xzf $USERNAME.tar.gz
  cd $USERNAME
  $RUN_HINT

Done.
EOF
