#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  keylime-ima-runtime-policy-refresh.sh <host> [policy_id] [display_name]

Examples:
  keylime-ima-runtime-policy-refresh.sh hygon22
  keylime-ima-runtime-policy-refresh.sh hygon22 hygon22-ima-current

Collect the current IMA measurement list, generate/register a Keylime runtime
policy, apply it with the bound boot PCR policy, and run the configured sync
path. The generated policy_id defaults to <host>-ima-current-<utc timestamp>.
EOF
}

DEFAULT_ENV_FILE="/etc/keylime-openstack/keylime-openstack.env"
LEGACY_ENV_FILE="/etc/keylime-openstack-sync/openstack-keylime-lab.env"
ENV_FILE="${KEYLIME_OPENSTACK_ENV_FILE:-}"
if [ -z "$ENV_FILE" ]; then
  if [ -r "$DEFAULT_ENV_FILE" ]; then
    ENV_FILE="$DEFAULT_ENV_FILE"
  else
    ENV_FILE="$LEGACY_ENV_FILE"
  fi
fi
[ -r "$ENV_FILE" ] && source "$ENV_FILE"

HOST="${1:-}"
POLICY_ID="${2:-}"
DISPLAY_NAME="${3:-}"

if [ "$HOST" = "-h" ] || [ "$HOST" = "--help" ] || [ -z "$HOST" ]; then
  usage
  if [ -z "$HOST" ]; then
    exit 1
  fi
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GENERATE_SCRIPT="${KEYLIME_RUNTIME_POLICY_GENERATE_SCRIPT:-$SCRIPT_DIR/keylime-ima-runtime-policy-generate.sh}"
APPLY_SCRIPT="${KEYLIME_RUNTIME_POLICY_APPLY_SCRIPT:-$SCRIPT_DIR/keylime-ima-runtime-policy-apply.sh}"

if [ -z "$POLICY_ID" ]; then
  POLICY_ID="${HOST}-ima-current-$(date -u +%Y%m%dT%H%M%SZ)"
fi
if [ -z "$DISPLAY_NAME" ]; then
  DISPLAY_NAME="$HOST current IMA baseline $POLICY_ID"
fi

echo "host=$HOST"
echo "policy_id=$POLICY_ID"
echo "display_name=$DISPLAY_NAME"

KEYLIME_OPENSTACK_ENV_FILE="$ENV_FILE" \
  KEYLIME_RUNTIME_POLICY_GENERATE_REGISTER="${KEYLIME_RUNTIME_POLICY_GENERATE_REGISTER:-true}" \
  bash "$GENERATE_SCRIPT" "$HOST" "$POLICY_ID" "$DISPLAY_NAME"

KEYLIME_OPENSTACK_ENV_FILE="$ENV_FILE" \
  KEYLIME_RUNTIME_POLICY_INCLUDE_BOUND_BOOT="${KEYLIME_RUNTIME_POLICY_INCLUDE_BOUND_BOOT:-true}" \
  bash "$APPLY_SCRIPT" "$HOST" "$POLICY_ID"
