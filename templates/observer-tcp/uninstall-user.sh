#!/usr/bin/env bash
# Cleanly remove a sudoless user-mode install.

set -euo pipefail

if [[ $EUID -eq 0 ]]; then
  echo "error: don't run this with sudo. Use uninstall.sh for the root install." >&2
  exit 1
fi

DEST="$HOME/.local/share/aus-observer"
UNIT_PATH="$HOME/.config/systemd/user/aus-observer.service"

systemctl --user stop aus-observer.service 2>/dev/null || true
systemctl --user disable aus-observer.service 2>/dev/null || true
rm -f "$UNIT_PATH"
systemctl --user daemon-reload || true

rm -rf "$DEST"

echo "Removed."
echo "(linger setting, if any, is left alone; if you enabled it,"
echo " you can disable it with: sudo loginctl disable-linger \$USER)"
