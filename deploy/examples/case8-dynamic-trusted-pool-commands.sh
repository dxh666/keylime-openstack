#!/usr/bin/env bash
set -euo pipefail

# Case 8: Keylime-driven OpenStack trusted compute pool shrink/recovery.
# Run on csri10 in the lab environment.

source /etc/keylime-openstack-sync/openstack-keylime-lab.env
source /etc/kolla/admin-openrc.sh
export OS_PLACEMENT_API_VERSION="${OS_PLACEMENT_API_VERSION:-1.17}"

show_pool_status() {
  local output="${1:-/tmp/keylime-status.json}"

  curl -s "http://172.31.100.10:8088/api/status?force=1" > "$output"
  python3 - "$output" <<'PY'
import json
import sys

d = json.load(open(sys.argv[1]))
print("summary:", d.get("summary"))
for n in d.get("nodes", []):
    print(
        n.get("host"),
        "ip=", n.get("ip"),
        "vm_count=", n.get("vm_count"),
        "trust=", n.get("conclusion", {}).get("text"),
        "decision=", n.get("decision", {}).get("result"),
        "reason=", n.get("decision", {}).get("reason"),
        "trait=", n.get("placement", {}).get("trait_present"),
        "service=", n.get("service", {}).get("status"), "/", n.get("service", {}).get("state"),
    )
PY
}

show_traits() {
  for host in csri8 csri9; do
    echo "--- $host ---"
    rp_uuid="$(openstack resource provider list --name "$host" -f value -c uuid)"
    echo "rp_uuid=$rp_uuid"
    openstack resource provider trait list "$rp_uuid" | grep CUSTOM_KEYLIME_ATTESTED || \
      echo "MISSING: CUSTOM_KEYLIME_ATTESTED"
  done
}

show_compute_services() {
  openstack compute service list | awk 'NR==1 || /nova-compute/'
}

create_trusted_vm() {
  local name="$1"
  shift

  openstack server create \
    --wait \
    --flavor trusted.keylime.small \
    --image "$IMAGE" \
    --network "$NET_A_ID" \
    "$@" \
    "$name"
}

show_vm() {
  local name="$1"
  openstack server show "$name" \
    -c name \
    -c status \
    -c fault \
    -c OS-EXT-SRV-ATTR:host \
    -c flavor \
    -f yaml 2>/dev/null || echo "server not found: $name"
}

baseline() {
  echo "=== Trusted pool baseline ==="
  show_pool_status /tmp/keylime-status-case8-baseline.json
  echo
  echo "=== Placement traits ==="
  show_traits
  echo
  echo "=== Nova compute services ==="
  show_compute_services
}

positive_scheduling() {
  local ts vm_csri8 vm_csri9 vm_auto
  ts="$(date +%Y%m%d%H%M%S)"
  vm_csri8="trusted-pool-csri8-${ts}"
  vm_csri9="trusted-pool-csri9-${ts}"
  vm_auto="trusted-pool-auto-${ts}"

  echo "=== Create trusted VM pinned to csri8 ==="
  create_trusted_vm "$vm_csri8" --availability-zone nova:csri8
  echo
  echo "=== Create trusted VM pinned to csri9 ==="
  create_trusted_vm "$vm_csri9" --availability-zone nova:csri9
  echo
  echo "=== Create trusted VM with normal scheduler ==="
  create_trusted_vm "$vm_auto"

  echo
  echo "=== Verify trusted VM placement ==="
  for vm in "$vm_csri8" "$vm_csri9" "$vm_auto"; do
    echo "--- $vm ---"
    show_vm "$vm"
  done
}

fail_csri8() {
  echo "=== Stop csri8 Keylime agent ==="
  ssh root@172.31.100.8 'docker rm -f keylime-agent || true'

  echo
  echo "=== Wait for freshness timeout and sync loop ==="
  date -u
  sleep 150

  echo
  echo "=== Force one sync run ==="
  systemctl start keylime-openstack-sync.service || true
  sleep 5

  echo
  echo "=== Trusted pool after csri8 failure ==="
  show_pool_status /tmp/keylime-status-case8-csri8-fail.json
  echo
  echo "=== Placement traits ==="
  show_traits
  echo
  echo "=== Nova compute services ==="
  show_compute_services
  echo
  echo "=== csri8 decision file ==="
  python3 -m json.tool /var/log/keylime-openstack-sync-decision-csri8.json
}

negative_scheduling() {
  local ts vm_bad vm_good vm_auto bad_rc
  ts="$(date +%Y%m%d%H%M%S)"
  vm_bad="trusted-fail-csri8-${ts}"
  vm_good="trusted-ok-csri9-${ts}"
  vm_auto="trusted-auto-after-csri8-fail-${ts}"

  echo "=== Create trusted VM pinned to failed csri8, expected to fail ==="
  set +e
  create_trusted_vm "$vm_bad" --availability-zone nova:csri8
  bad_rc=$?
  set -e
  echo "csri8_create_rc=$bad_rc"

  echo
  echo "=== Create trusted VM pinned to healthy csri9, expected to succeed ==="
  create_trusted_vm "$vm_good" --availability-zone nova:csri9

  echo
  echo "=== Create trusted VM with normal scheduler, expected to avoid csri8 ==="
  create_trusted_vm "$vm_auto"

  echo
  echo "=== Verify VM results ==="
  for vm in "$vm_bad" "$vm_good" "$vm_auto"; do
    echo "--- $vm ---"
    show_vm "$vm"
  done
}

recover_csri8() {
  echo "=== Restart csri8 Keylime agent ==="
  ssh root@172.31.100.8 '
cd /opt/keylime-agent-docker
docker rm -f keylime-agent 2>/dev/null || true
docker run -d \
  --name keylime-agent \
  --restart unless-stopped \
  --privileged \
  --network host \
  -v /dev:/dev \
  -v /sys/kernel/security:/sys/kernel/security \
  -v /opt/keylime-agent-docker/config/cacert.crt:/etc/keylime/cacert.crt:ro \
  -v /opt/keylime-agent-docker/varlib:/var/lib/keylime \
  -e KEYLIME_AGENT_UUID=22222222-2222-4222-8222-000000000008 \
  -e KEYLIME_AGENT_IP=0.0.0.0 \
  -e KEYLIME_AGENT_PORT=9002 \
  -e KEYLIME_AGENT_CONTACT_IP=172.31.100.8 \
  -e KEYLIME_AGENT_CONTACT_PORT=9002 \
  -e KEYLIME_AGENT_REGISTRAR_IP=172.31.100.10 \
  -e KEYLIME_AGENT_REGISTRAR_PORT=8890 \
  -e KEYLIME_AGENT_REGISTRAR_TLS_ENABLED=false \
  -e KEYLIME_AGENT_ENABLE_AGENT_MTLS=true \
  -e KEYLIME_AGENT_TRUSTED_CLIENT_CA=/etc/keylime/cacert.crt \
  -e KEYLIME_AGENT_REVOCATION_ACTIONS_DIR=/var/lib/keylime/actions \
  quay.io/keylime/keylime_agent:latest
sleep 5
docker ps --filter name=keylime-agent --format "table {{.Names}}\t{{.Status}}\t{{.Image}}"
'

  echo
  echo "=== Reactivate csri8 in verifier ==="
  (
    cd /opt/keylime-docker
    docker compose run --rm keylime-tenant \
      -c reactivate \
      -u 22222222-2222-4222-8222-000000000008 \
      -v 172.31.100.10 \
      -vp 8881 \
      -r 172.31.100.10 \
      -rp 8891
  ) || true

  echo
  echo "=== Wait and force sync ==="
  sleep 20
  systemctl start keylime-openstack-sync.service || true
  sleep 5

  echo
  echo "=== Trusted pool after csri8 recovery ==="
  show_pool_status /tmp/keylime-status-case8-csri8-recovery.json
  echo
  echo "=== Placement traits ==="
  show_traits
  echo
  echo "=== Nova compute services ==="
  show_compute_services
}

recovery_scheduling() {
  local ts vm_recovered
  ts="$(date +%Y%m%d%H%M%S)"
  vm_recovered="trusted-after-recovery-csri8-${ts}"

  echo "=== Create trusted VM pinned to recovered csri8 ==="
  create_trusted_vm "$vm_recovered" --availability-zone nova:csri8

  echo
  echo "=== Verify recovered csri8 scheduling ==="
  show_vm "$vm_recovered"
}

usage() {
  cat <<'EOF'
Usage:
  case8-dynamic-trusted-pool-commands.sh <step>

Steps:
  baseline             show current trust pool state
  positive             create trusted VMs on csri8/csri9/auto
  fail-csri8           stop csri8 agent and verify pool shrink
  negative             verify trusted scheduling avoids failed csri8
  recover-csri8        restart csri8 agent and verify pool recovery
  recovery-schedule    create trusted VM on recovered csri8
  all                  run all steps in order
EOF
}

step="${1:-}"
case "$step" in
  baseline) baseline ;;
  positive) positive_scheduling ;;
  fail-csri8) fail_csri8 ;;
  negative) negative_scheduling ;;
  recover-csri8) recover_csri8 ;;
  recovery-schedule) recovery_scheduling ;;
  all)
    baseline
    positive_scheduling
    fail_csri8
    negative_scheduling
    recover_csri8
    recovery_scheduling
    ;;
  *)
    usage
    exit 2
    ;;
esac
