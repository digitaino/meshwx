#!/usr/bin/env bash
# Add (or rotate) an observer credential and produce a hand-off the
# operator can send to the observer.
#
# Three flavors:
#   (default)  — observer connects directly to a USB radio
#                (templates/observer/, uses Cisien/meshcoretomqtt). Tarball.
#   --proxy    — observer connects to an existing meshcore-proxy TCP
#                endpoint (templates/observer-tcp/). Tarball.
#   --firmware — observer is a meshcore radio running the agessaman
#                MQTT-bridge firmware fork (Heltec V3/V4, Station G2,
#                LilyGo). No bundle — prints a paste-ready `set` block
#                for the radio's serial console (see
#                Observer_Setup_Firmware.md).
#
# Usage:
#   corescope/scripts/add-observer.sh [--proxy|--firmware] <username> [iata] [password]
#
# Runnable from anywhere — paths are derived from the script's location.

set -euo pipefail

# Locate corescope/ relative to this script.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COREDIR="$(cd "$SCRIPT_DIR/.." && pwd)"

VARIANT="usb"
TEMPLATE_DIR="$COREDIR/templates/observer"
if [[ "${1:-}" == "--proxy" ]]; then
  VARIANT="proxy"
  TEMPLATE_DIR="$COREDIR/templates/observer-tcp"
  shift
elif [[ "${1:-}" == "--firmware" ]]; then
  VARIANT="firmware"
  TEMPLATE_DIR=""   # firmware variant has no bundle
  shift
fi

if [[ $# -lt 1 || $# -gt 3 ]]; then
  echo "usage: $(basename "$0") [--proxy|--firmware] <username> [iata] [password]" >&2
  exit 1
fi

USERNAME="$1"
IATA="${2:-AUS}"
PASSWORD="${3:-$(openssl rand -hex 16)}"

PASSWORD_FILE="$COREDIR/mosquitto/passwords"
BUNDLE_DIR="$COREDIR/out/observer-bundles/$USERNAME"

if [[ ! -f "$PASSWORD_FILE" ]]; then
  echo "error: $PASSWORD_FILE not found — broker may not be initialized yet." >&2
  exit 1
fi
if [[ "$VARIANT" != "firmware" && ! -d "$TEMPLATE_DIR" ]]; then
  echo "error: $TEMPLATE_DIR not found." >&2
  exit 1
fi

# Add (or update) the user. mosquitto_passwd updates in place if the user
# already exists, so this doubles as a rotate-password command.
docker run --rm \
  -v "$PASSWORD_FILE:/passwords" \
  eclipse-mosquitto:2 \
  mosquitto_passwd -b /passwords "$USERNAME" "$PASSWORD" >/dev/null

# Reload without dropping existing client connections.
if docker ps --format '{{.Names}}' | grep -q '^mosquitto$'; then
  docker kill --signal=HUP mosquitto >/dev/null
  RELOAD_NOTE="(broker reloaded)"
else
  RELOAD_NOTE="(mosquitto container not running — start the stack to apply)"
fi

if [[ "$VARIANT" == "firmware" ]]; then
  # No bundle. Print a paste-ready block for the radio's serial console.
  cat <<EOF

Observer added: $USERNAME (iata=$IATA, variant=firmware) $RELOAD_NOTE

  username: $USERNAME
  password: $PASSWORD

Send the observer this setup guide:
  https://github.com/digitaino/meshwx/blob/main/corescope/Observer_Setup_Firmware.md

And this paste-ready block — they'll paste it into their radio's
serial console at 115200 baud (after replacing the WiFi placeholders):

  ───────────────────────  paste from here  ───────────────────────
  set wifi.ssid YOUR_WIFI_SSID
  set wifi.pwd  YOUR_WIFI_PASSWORD
  set mqtt3.preset custom
  set mqtt3.server mqtt.digitaino.com
  set mqtt3.port 443
  set mqtt3.username $USERNAME
  set mqtt3.password $PASSWORD
  set mqtt.iata $IATA
  save
  reboot
  ────────────────────────  to here  ──────────────────────────────

Done.
EOF
  exit 0
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
TARBALL="$COREDIR/out/observer-bundles/$USERNAME.tar.gz"
tar -czf "$TARBALL" -C "$COREDIR/out/observer-bundles" "$USERNAME"

if [[ "$VARIANT" == "proxy" ]]; then
  RUN_HINT='./run-native.sh          # foreground test first
  # then: sudo ./install.sh    # production install (systemd-managed)'
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
