#!/usr/bin/env bash
# Sudoless install: registers the observer as a user-level systemd unit
# under your own account. No root, no /opt, no system user, no /etc.
#
#   Layout:
#     ~/.local/share/aus-observer/       code + venv + .env
#     ~/.config/systemd/user/aus-observer.service
#
# Trade-off vs the root install.sh: by default user services only run
# while you're logged in. To keep it running across reboots without
# staying logged in, ask your operator (or yourself) for one sudo:
#
#   sudo loginctl enable-linger $USER
#
# That's the only sudo in the whole flow, and only if you want
# survive-reboot behavior. Run-while-logged-in works with no sudo at all.
#
# Re-run to upgrade in place.

set -euo pipefail

if [[ $EUID -eq 0 ]]; then
  echo "error: don't run this with sudo. Use install.sh for the root install." >&2
  exit 1
fi

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.local/share/aus-observer"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT_PATH="$UNIT_DIR/aus-observer.service"

if ! command -v python3 >/dev/null 2>&1; then
  echo "error: python3 not found. On Debian/Ubuntu/Pi:" >&2
  echo "       sudo apt install python3 python3-venv" >&2
  exit 1
fi

# 1. Layout.
mkdir -p "$DEST" "$UNIT_DIR"
install -m 0644 "$SRC/observer.py" "$DEST/observer.py"
install -m 0600 "$SRC/.env"        "$DEST/.env"

# 2. Venv (idempotent).
if [[ ! -d "$DEST/.venv" ]]; then
  python3 -m venv "$DEST/.venv"
  echo "==> Created venv at $DEST/.venv"
fi
"$DEST/.venv/bin/pip" install --quiet --upgrade pip
"$DEST/.venv/bin/pip" install --quiet --upgrade "meshcore>=2.3" "paho-mqtt>=2.0"
echo "==> Dependencies installed"

# 3. User-mode systemd unit. ProtectHome is omitted because the unit
# itself lives under $HOME; the rest of the standard hardening applies.
cat > "$UNIT_PATH" <<UNIT
[Unit]
Description=AUS Meshcore observer (user-mode)
Documentation=https://github.com/digitaino/meshwx
After=default.target

[Service]
Type=exec
WorkingDirectory=$DEST
EnvironmentFile=$DEST/.env
ExecStart=$DEST/.venv/bin/python $DEST/observer.py
Restart=always
RestartSec=10

# Hardening (subset that works in user mode)
NoNewPrivileges=true
PrivateTmp=true
LockPersonality=true
RestrictRealtime=true
RestrictSUIDSGID=true
SystemCallArchitectures=native

[Install]
WantedBy=default.target
UNIT
echo "==> Wrote $UNIT_PATH"

# 4. Enable and start.
systemctl --user daemon-reload
systemctl --user enable aus-observer.service >/dev/null
systemctl --user restart aus-observer.service
sleep 1

LINGER=$(loginctl show-user "$USER" -p Linger --value 2>/dev/null || echo "no")

cat <<EOF

==> Installed as user service. No sudo used.

Status:        systemctl --user status aus-observer
Live logs:     journalctl --user -u aus-observer -f
Stop / start:  systemctl --user stop aus-observer
               systemctl --user start aus-observer

EOF

if [[ "$LINGER" != "yes" ]]; then
  cat <<EOF
NOTE: linger is OFF for $USER, which means this service stops when you
log out and won't auto-start at boot. If you want survive-reboot
behavior (recommended for a Pi observer), one sudo enables it forever:

  sudo loginctl enable-linger $USER

Without that, just keep the SSH session open or expect to manually
\`systemctl --user start aus-observer\` after each reboot.

EOF
fi

cat <<EOF
To rotate the password later, replace ~/.local/share/aus-observer/.env
with the new contents from the operator, then:
  systemctl --user restart aus-observer

To uninstall:
  $SRC/uninstall-user.sh
EOF
