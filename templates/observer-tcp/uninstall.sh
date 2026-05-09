#!/usr/bin/env bash
# Cleanly remove the AUS Meshcore observer install:
#   - stop & disable the systemd service
#   - delete /etc/systemd/system/aus-observer.service
#   - delete /opt/aus-observer
#   - delete the aus-observer system user
#
# Run as root.  Idempotent.

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "error: must run as root (try: sudo $0)" >&2
  exit 1
fi

DEST="/opt/aus-observer"
UNIT_PATH="/etc/systemd/system/aus-observer.service"
SVC_USER="aus-observer"

if systemctl list-unit-files | grep -q '^aus-observer\.service'; then
  systemctl stop aus-observer.service 2>/dev/null || true
  systemctl disable aus-observer.service 2>/dev/null || true
fi

rm -f "$UNIT_PATH"
systemctl daemon-reload || true

rm -rf "$DEST"

if id "$SVC_USER" >/dev/null 2>&1; then
  userdel "$SVC_USER" 2>/dev/null || true
fi

echo "Removed."
