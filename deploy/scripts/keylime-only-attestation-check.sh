#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  keylime-only-attestation-check.sh [--strict] [--hosts host1,host2]
  keylime-only-attestation-check.sh --strict --count 10 --interval 30
  keylime-only-attestation-check.sh --failures-only

Check Keylime-only attestation state before enabling OpenStack enforcement.
This reads Keylime verifier state through the FastAPI trust-plane container and
does not update Placement traits, nova-compute state, or VM metadata.

Options are passed to keylime-openstackctl keylime-check.
EOF
}

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  usage
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$SCRIPT_DIR/keylime-openstackctl" keylime-check "$@"
