#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  opentcsm-evidence-collect.sh <hostname>

Collect OpenTCSM/Hygon TPCM evidence through the management system. The target
node must be registered as trust_agent_type=opentcsm_tpcm.
EOF
}

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  usage
  exit 0
fi

host="${1:-}"
if [ -z "$host" ]; then
  usage >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$SCRIPT_DIR/keylime-openstackctl" opentcsm-collect "$host"
