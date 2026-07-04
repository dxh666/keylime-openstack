#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

ENV_DIR="${KEYLIME_OPENSTACK_ENV_DIR:-/etc/keylime-openstack-sync}"
ENV_FILE="${KEYLIME_OPENSTACK_ENV_FILE:-$ENV_DIR/openstack-keylime-lab.env}"
SYNC_DIR="${KEYLIME_OPENSTACK_SYNC_DIR:-/opt/keylime-openstack-sync}"
CONSOLE_DIR="${KEYLIME_OPENSTACK_CONSOLE_DIR:-/opt/keylime-openstack-console}"
SYSTEMD_DIR="${KEYLIME_OPENSTACK_SYSTEMD_DIR:-/etc/systemd/system}"
START_SYNC="${KEYLIME_INSTALL_START_SYNC:-true}"

install -d -m 0755 "$ENV_DIR" "$SYNC_DIR" "$CONSOLE_DIR"

if [ -f "$ENV_FILE" ]; then
  install -m 0644 "$REPO_ROOT/deploy/env/openstack-keylime-lab.env" \
    "$ENV_FILE.repo-template"
else
  install -m 0644 "$REPO_ROOT/deploy/env/openstack-keylime-lab.env" "$ENV_FILE"
fi

append_env_default() {
  local key="$1"
  local line="$2"

  if ! grep -qE "^[[:space:]]*(export[[:space:]]+)?${key}=" "$ENV_FILE" 2>/dev/null; then
    printf '\n%s\n' "$line" >> "$ENV_FILE"
  fi
}

append_env_default "KEYLIME_OPENSTACK_SYNC_DIR" "export KEYLIME_OPENSTACK_SYNC_DIR=\"$SYNC_DIR\""
append_env_default "KEYLIME_OPENSTACK_LOG_DIR" "export KEYLIME_OPENSTACK_LOG_DIR=\"/var/log\""
append_env_default "KEYLIME_OPENSTACK_STATE_DIR" "export KEYLIME_OPENSTACK_STATE_DIR=\"/var/lib/keylime-openstack-sync\""
append_env_default "KEYLIME_MONITOR_URL" "export KEYLIME_MONITOR_URL=\"http://172.31.100.10:8088/api/status?force=1\""
append_env_default "KEYLIME_VM_RISK_MARKER_ENABLE" "export KEYLIME_VM_RISK_MARKER_ENABLE=\"true\""
append_env_default "KEYLIME_VM_RISK_MARKER_INTERVAL_SECONDS" "export KEYLIME_VM_RISK_MARKER_INTERVAL_SECONDS=\"60\""
append_env_default "KEYLIME_VM_RISK_AUDIT_FILE" "export KEYLIME_VM_RISK_AUDIT_FILE=\"/var/log/keylime-openstack-vm-risk-marker.json\""

install -d -m 0755 /var/lib/keylime-openstack-sync

find "$REPO_ROOT/deploy/scripts" -maxdepth 1 -type f -name "keylime-*.sh" -print0 | \
  while IFS= read -r -d '' script; do
    install -m 0755 "$script" "$SYNC_DIR/$(basename "$script")"
  done

install -m 0644 "$REPO_ROOT/deploy/frontend/index.html" "$CONSOLE_DIR/index.html"
install -m 0644 "$REPO_ROOT/deploy/frontend/trust_monitor_server.py" "$CONSOLE_DIR/trust_monitor_server.py"
install -m 0644 "$REPO_ROOT/deploy/frontend/README.md" "$CONSOLE_DIR/README.md"

install -m 0644 "$REPO_ROOT/deploy/systemd/keylime-openstack-sync.service" \
  "$SYSTEMD_DIR/keylime-openstack-sync.service"
install -m 0644 "$REPO_ROOT/deploy/systemd/keylime-openstack-sync.timer" \
  "$SYSTEMD_DIR/keylime-openstack-sync.timer"
install -m 0644 "$REPO_ROOT/deploy/systemd/keylime-openstack-monitor.service" \
  "$SYSTEMD_DIR/keylime-openstack-monitor.service"

systemctl daemon-reload
systemctl enable --now keylime-openstack-monitor.service
systemctl enable --now keylime-openstack-sync.timer

if [ "$START_SYNC" = "true" ]; then
  systemctl start keylime-openstack-sync.service || true
fi

echo "Installed Keylime/OpenStack control plane."
echo "env_file=$ENV_FILE"
echo "sync_dir=$SYNC_DIR"
echo "console_dir=$CONSOLE_DIR"
echo "monitor_service=$(systemctl is-active keylime-openstack-monitor.service || true)"
echo "sync_timer=$(systemctl is-active keylime-openstack-sync.timer || true)"
