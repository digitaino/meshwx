#!/usr/bin/env bash
# Manually pin a node's lat/lon in CoreScope's database and protect the
# value from being clobbered when the node next adverts (0,0).
#
# Why the protection: CoreScope's ingestor upserts nodes with
# `lat = COALESCE(?, lat)`. That only preserves manual values when the
# advert has NO location flag at all. Many "infra" repeaters whose
# operators never set GPS still broadcast a location flag with 0,0,
# and those would otherwise overwrite our manual fix on every advert.
#
# This script installs a SQLite AFTER-UPDATE trigger (idempotent — it's
# created once and reused) that detects the "previous lat/lon was
# non-zero, new lat/lon is (0,0)" pattern and reverses the update. So
# once you've manually set a node's location, it stays.
#
# Usage:
#   corescope/scripts/set-node-location.sh <name-or-pubkey-prefix> <lat> <lon>
#
# Examples:
#   corescope/scripts/set-node-location.sh "Lil Frank" 30.2672 -97.7431
#   corescope/scripts/set-node-location.sh ab12cd34 30.2672 -97.7431

set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "usage: $(basename "$0") <name-or-pubkey-prefix> <lat> <lon>" >&2
  echo "  e.g. set-node-location.sh \"Lil Frank\" 30.2672 -97.7431" >&2
  exit 1
fi

NAME_OR_KEY="$1"
LAT="$2"
LON="$3"

if ! [[ "$LAT" =~ ^-?[0-9]+\.?[0-9]*$ ]] || ! [[ "$LON" =~ ^-?[0-9]+\.?[0-9]*$ ]]; then
  echo "error: lat/lon must be numeric (e.g. 30.2672 -97.7431)" >&2
  exit 1
fi

if ! docker ps --format '{{.Names}}' | grep -q '^corescope$'; then
  echo "error: corescope container is not running" >&2
  exit 1
fi

# Make sure sqlite3 is available inside the container (Alpine base).
docker exec corescope sh -c 'command -v sqlite3 >/dev/null 2>&1 || apk add --no-cache sqlite >/dev/null'

# Identify the node. Match by exact name first, then case-insensitive
# substring on name, then by pubkey prefix. Refuse if ambiguous.
SQL_FIND=$(cat <<EOF
SELECT public_key, name, IFNULL(lat,'NULL'), IFNULL(lon,'NULL'), IFNULL(role,'?')
FROM nodes
WHERE name = '${NAME_OR_KEY//\'/\'\'}'
   OR LOWER(name) LIKE LOWER('%${NAME_OR_KEY//\'/\'\'}%')
   OR public_key LIKE '${NAME_OR_KEY//\'/\'\'}%';
EOF
)

MATCHES=$(docker exec corescope sqlite3 -cmd "PRAGMA busy_timeout=5000;" -separator $'\t' /app/data/meshcore.db "$SQL_FIND")
COUNT=$(printf '%s\n' "$MATCHES" | grep -c . || true)

if [[ "$COUNT" -eq 0 ]]; then
  echo "no node matched '$NAME_OR_KEY'" >&2
  exit 1
fi
if [[ "$COUNT" -gt 1 ]]; then
  echo "ambiguous '$NAME_OR_KEY' — matched $COUNT nodes:" >&2
  printf '  %s\n' "$MATCHES" >&2
  echo "use a longer pubkey prefix to disambiguate." >&2
  exit 1
fi

PUBKEY=$(printf '%s' "$MATCHES" | cut -f1)
NAME=$(printf '%s' "$MATCHES" | cut -f2)
OLD_LAT=$(printf '%s' "$MATCHES" | cut -f3)
OLD_LON=$(printf '%s' "$MATCHES" | cut -f4)

# Install (or refresh) the protection trigger. Per-component: if a new
# advert sets lat=0 (or NULL) but the old value was real, keep the old
# lat — and likewise for lon — independently. This handles both the
# all-zero "no GPS" case and the partial-GPS case where a node broadcasts
# e.g. (lat, 0). We DROP and re-create so the latest version always wins
# if we tweak the logic in a later release.
docker exec -i corescope sqlite3 /app/data/meshcore.db <<'TRIGGER'
PRAGMA busy_timeout = 5000;
DROP TRIGGER IF EXISTS preserve_manual_location;
CREATE TRIGGER preserve_manual_location
AFTER UPDATE OF lat, lon ON nodes
FOR EACH ROW
WHEN ((NEW.lat IS NULL OR NEW.lat = 0) AND OLD.lat IS NOT NULL AND OLD.lat <> 0)
  OR ((NEW.lon IS NULL OR NEW.lon = 0) AND OLD.lon IS NOT NULL AND OLD.lon <> 0)
BEGIN
    UPDATE nodes
    SET lat = CASE WHEN NEW.lat IS NULL OR NEW.lat = 0 THEN OLD.lat ELSE NEW.lat END,
        lon = CASE WHEN NEW.lon IS NULL OR NEW.lon = 0 THEN OLD.lon ELSE NEW.lon END
    WHERE public_key = NEW.public_key;
END;
TRIGGER

# Apply the manual override (with busy_timeout so we wait out any
# in-flight writes from the ingestor instead of failing).
docker exec corescope sqlite3 -cmd "PRAGMA busy_timeout=5000;" \
  /app/data/meshcore.db \
  "UPDATE nodes SET lat = $LAT, lon = $LON WHERE public_key = '$PUBKEY';"

cat <<EOF
Updated:
  name:    $NAME
  pubkey:  ${PUBKEY:0:16}...
  before:  lat=$OLD_LAT lon=$OLD_LON
  after:   lat=$LAT lon=$LON

Future (0,0) adverts from this node will be auto-reverted to the manual
coordinates by the 'preserve_manual_location' trigger (installed once,
applies to all nodes).

To clear the override and let CoreScope manage this node again:
  docker exec corescope sqlite3 /app/data/meshcore.db \\
    "UPDATE nodes SET lat = NULL, lon = NULL WHERE public_key = '$PUBKEY';"
EOF
