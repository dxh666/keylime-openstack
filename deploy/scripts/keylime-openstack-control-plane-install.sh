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
append_env_default "KEYLIME_PCR_POLICY_FILE" "export KEYLIME_PCR_POLICY_FILE=\"/var/lib/keylime-openstack-sync/tpm-pcr-policies.json\""
append_env_default "KEYLIME_TPM_EVIDENCE_BASELINE_JSON" "export KEYLIME_TPM_EVIDENCE_BASELINE_JSON=\"/var/log/keylime-openstack-tpm-evidence-baseline.json\""
append_env_default "KEYLIME_POLICY_BASE_DIR" "export KEYLIME_POLICY_BASE_DIR=\"/var/lib/keylime-openstack-sync/policies\""
append_env_default "KEYLIME_POLICY_RENDER_AUDIT_FILE" "export KEYLIME_POLICY_RENDER_AUDIT_FILE=\"/var/log/keylime-openstack-policy-render.json\""
append_env_default "KEYLIME_POLICY_APPLY_AUDIT_FILE" "export KEYLIME_POLICY_APPLY_AUDIT_FILE=\"/var/log/keylime-openstack-policy-apply.json\""
append_env_default "KEYLIME_POLICY_RENDER_BIND_MODE" "export KEYLIME_POLICY_RENDER_BIND_MODE=\"pcr7\""
append_env_default "KEYLIME_POLICY_RENDER_CREATE_BAD_PCR7" "export KEYLIME_POLICY_RENDER_CREATE_BAD_PCR7=\"false\""
append_env_default "KEYLIME_REQUIRE_BOOT_PCR7" "export KEYLIME_REQUIRE_BOOT_PCR7=\"true\""
append_env_default "KEYLIME_IMA_RUNTIME_BASELINE_JSON" "export KEYLIME_IMA_RUNTIME_BASELINE_JSON=\"/var/log/keylime-openstack-ima-runtime-baseline.json\""
append_env_default "KEYLIME_IMA_RUNTIME_EVIDENCE_DIR" "export KEYLIME_IMA_RUNTIME_EVIDENCE_DIR=\"/var/log/keylime-openstack-ima-runtime-evidence\""
append_env_default "KEYLIME_RUNTIME_POLICY_REGISTER_AUDIT_FILE" "export KEYLIME_RUNTIME_POLICY_REGISTER_AUDIT_FILE=\"/var/log/keylime-openstack-runtime-policy-register.json\""
append_env_default "KEYLIME_RUNTIME_POLICY_APPLY_AUDIT_FILE" "export KEYLIME_RUNTIME_POLICY_APPLY_AUDIT_FILE=\"/var/log/keylime-openstack-runtime-policy-apply.json\""
append_env_default "KEYLIME_RUNTIME_GUARD_PATH" "export KEYLIME_RUNTIME_GUARD_PATH=\"/opt/keylime-cloud-integrity/cloud-runtime-guard.sh\""
append_env_default "KEYLIME_RUNTIME_PROTECTED_PATHS" "export KEYLIME_RUNTIME_PROTECTED_PATHS=\"/opt/keylime-cloud-integrity/cloud-runtime-guard.sh\""
append_env_default "KEYLIME_RUNTIME_EXCLUDES" "export KEYLIME_RUNTIME_EXCLUDES='^(?!(boot_aggregate|/opt/keylime-cloud-integrity/cloud-runtime-guard.sh)$).*'"
append_env_default "KEYLIME_RUNTIME_POLICY_COPY" "export KEYLIME_RUNTIME_POLICY_COPY=\"true\""
append_env_default "KEYLIME_RUNTIME_POLICY_INCLUDE_BOUND_BOOT" "export KEYLIME_RUNTIME_POLICY_INCLUDE_BOUND_BOOT=\"true\""

install -d -m 0755 /var/lib/keylime-openstack-sync
install -d -m 0755 /var/lib/keylime-openstack-sync/policies
install -d -m 0755 /var/lib/keylime-openstack-sync/policies/runtime

find "$REPO_ROOT/deploy/scripts" -maxdepth 1 -type f -name "keylime-*.sh" -print0 | \
  while IFS= read -r -d '' script; do
    install -m 0755 "$script" "$SYNC_DIR/$(basename "$script")"
  done

find "$REPO_ROOT/deploy/frontend" -type f \
  ! -path "*/__pycache__/*" \
  ! -name "*.pyc" \
  -print0 | while IFS= read -r -d '' frontend_file; do
    rel_path="${frontend_file#"$REPO_ROOT/deploy/frontend/"}"
    install -D -m 0644 "$frontend_file" "$CONSOLE_DIR/$rel_path"
  done

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
