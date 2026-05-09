#!/usr/bin/env bash
# Production install for the AUS Meshcore proxy observer.
#
# Idempotent. Re-running upgrades the venv and unit file in place.
#
# Layout (matches Cisien/meshcoretomqtt's pattern):
#   /opt/aus-observer/        — code + venv + .env
#   /etc/systemd/system/aus-observer.service
#   user/group:  aus-observer (system user, no shell, no home)
#
# Run from inside the extracted bundle:
#   sudo ./install.sh
#
# To uninstall:  sudo ./uninstall.sh

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "error: must run as root (try: sudo $0)" >&2
  exit 1
fi

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="/opt/aus-observer"
UNIT_PATH="/etc/systemd/system/aus-observer.service"
SVC_USER="aus-observer"

# 1. System user (no shell, no home).
if ! id "$SVC_USER" >/dev/null 2>&1; then
  useradd --system --no-create-home --shell /usr/sbin/nologin "$SVC_USER"
  echo "==> Created system user $SVC_USER"
fi

# 2. Layout.
mkdir -p "$DEST"
install -m 0644 "$SRC/observer.py"        "$DEST/observer.py"
# .env contains a password — owner read-only, root + service user.
install -m 0640 -o root -g "$SVC_USER" "$SRC/.env" "$DEST/.env"
chown -R root:"$SVC_USER" "$DEST"

# 3. Python venv (idempotent).
if ! command -v python3 >/dev/null 2>&1; then
  echo "error: python3 not found. Install with: sudo apt install python3 python3-venv" >&2
  exit 1
fi
if [[ ! -d "$DEST/.venv" ]]; then
  python3 -m venv "$DEST/.venv"
  echo "==> Created venv at $DEST/.venv"
fi
"$DEST/.venv/bin/pip" install --quiet --upgrade pip
"$DEST/.venv/bin/pip" install --quiet --upgrade "meshcore>=2.3" "paho-mqtt>=2.0"
chown -R "$SVC_USER":"$SVC_USER" "$DEST/.venv"
echo "==> Dependencies installed"

# 4. Systemd unit. Hardened along the lines of Cisien/meshcoretomqtt.
cat > "$UNIT_PATH" <<'UNIT'
[Unit]
Description=AUS Meshcore observer (meshcore-proxy → MQTT)
Documentation=https://github.com/digitaino/meshwx
After=time-sync.target network-online.target
Wants=time-sync.target network-online.target

[Service]
Type=exec
User=aus-observer
Group=aus-observer
WorkingDirectory=/opt/aus-observer
EnvironmentFile=/opt/aus-observer/.env
ExecStart=/opt/aus-observer/.venv/bin/python /opt/aus-observer/observer.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

# Hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/aus-observer
LockPersonality=true
RestrictRealtime=true
RestrictSUIDSGID=true
SystemCallArchitectures=native

[Install]
WantedBy=multi-user.target
UNIT
echo "==> Wrote $UNIT_PATH"

# 5. Reload, enable, restart.
systemctl daemon-reload
systemctl enable aus-observer.service >/dev/null
systemctl restart aus-observer.service
sleep 1

cat <<EOF

==> Installed. Service running as system user '$SVC_USER'.

Status:
  sudo systemctl status aus-observer
Live logs:
  sudo journalctl -u aus-observer -f
Stop / start:
  sudo systemctl stop aus-observer
  sudo systemctl start aus-observer

To rotate the password later, replace /opt/aus-observer/.env with the
contents of a new .env from the operator's bundle, then:
  sudo systemctl restart aus-observer

To uninstall:
  sudo $SRC/uninstall.sh
EOF
