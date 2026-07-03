#!/usr/bin/env bash
set -euo pipefail

OPENRC=/etc/kolla/admin-openrc.sh
KEYLIME_DIR=/opt/keylime-docker

AGENT_UUID="11111111-1111-4111-8111-000000000009"
RP_NAME="csri9"
TRUSTED_TRAIT="CUSTOM_KEYLIME_ATTESTED"

VERIFIER_IP="172.31.100.10"
VERIFIER_PORT="8881"
REGISTRAR_IP="172.31.100.10"
REGISTRAR_PORT="8891"

MAX_ATTESTATION_AGE_SECONDS="${MAX_ATTESTATION_AGE_SECONDS:-120}"

RAW_STATUS_FILE="/var/log/keylime-openstack-sync-status.raw.log"
DECISION_FILE="/var/log/keylime-openstack-sync-decision.json"
LAST_LOG_FILE="/var/log/keylime-openstack-sync-last.log"

source "$OPENRC"
export OS_PLACEMENT_API_VERSION="${OS_PLACEMENT_API_VERSION:-1.17}"

RP_UUID="$(openstack resource provider list --name "$RP_NAME" -f value -c uuid | awk 'NF {print; exit}')"

if [ -z "$RP_UUID" ]; then
  echo "ERROR: resource provider '$RP_NAME' not found"
  exit 1
fi

get_keylime_status_raw() {
  local out rc

  set +e
  out="$(
    cd "$KEYLIME_DIR" && \
    docker compose run --rm keylime-tenant \
      -c status \
      -u "$AGENT_UUID" \
      -v "$VERIFIER_IP" \
      -vp "$VERIFIER_PORT" \
      -r "$REGISTRAR_IP" \
      -rp "$REGISTRAR_PORT" \
      2>&1
  )"
  rc=$?
  set -e

  printf '%s\n' "$out" > "$RAW_STATUS_FILE"
  return "$rc"
}

evaluate_keylime_status() {
  local keylime_rc="$1"

  python3 - "$RAW_STATUS_FILE" "$MAX_ATTESTATION_AGE_SECONDS" "$keylime_rc" "$DECISION_FILE" <<'PY'
import datetime as dt
import json
import re
import sys

raw_path, max_age_s, keylime_rc, decision_path = sys.argv[1:5]
max_age_s = int(max_age_s)
keylime_rc = int(keylime_rc)

with open(raw_path, "r", encoding="utf-8", errors="replace") as f:
    raw = f.read()

def pick(patterns):
    for pattern in patterns:
        matches = list(re.finditer(pattern, raw, flags=re.MULTILINE))
        if matches:
            value = matches[-1].group(1)
            if value is None:
                continue
            value = value.strip().strip('"').strip("'")
            if value.lower() in ("none", "null"):
                return ""
            return value
    return ""

attestation_status = pick([
    r'"attestation_status"\s*:\s*"([^"]*)"',
    r"'attestation_status'\s*:\s*'([^']*)'",
    r"attestation_status\s*:\s*([A-Za-z_]+)",
])

operational_state = pick([
    r'"operational_state"\s*:\s*"([^"]*)"',
    r"'operational_state'\s*:\s*'([^']*)'",
    r"operational_state\s*:\s*([^\n,}]+)",
])

last_success = pick([
    r'"last_successful_attestation"\s*:\s*"([^"]*)"',
    r'"last_successful_attestation"\s*:\s*([^,\n}]+)',
    r"'last_successful_attestation'\s*:\s*'([^']*)'",
    r"last_successful_attestation\s*:\s*([^\n,}]+)",
])

last_event_id = pick([
    r'"last_event_id"\s*:\s*"([^"]*)"',
    r'"last_event_id"\s*:\s*([^,\n}]+)',
    r"'last_event_id'\s*:\s*'([^']*)'",
    r"last_event_id\s*:\s*([^\n,}]+)",
])

def parse_timestamp(value):
    value = (value or "").strip()
    if not value:
        return None

    if re.fullmatch(r"\d+(\.\d+)?", value):
        return dt.datetime.fromtimestamp(float(value), tz=dt.timezone.utc)

    normalized = value.replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    except ValueError:
        pass

    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            parsed = dt.datetime.strptime(value, fmt)
            return parsed.replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue

    return None

now = dt.datetime.now(dt.timezone.utc)
last_dt = parse_timestamp(last_success)
age = None if last_dt is None else int((now - last_dt).total_seconds())

decision = {
    "result": "NOT_PASS",
    "reason": "",
    "keylime_command_rc": keylime_rc,
    "attestation_status": attestation_status,
    "operational_state": operational_state,
    "last_successful_attestation": last_success,
    "last_successful_attestation_age_seconds": age,
    "max_attestation_age_seconds": max_age_s,
    "last_event_id": last_event_id,
    "checked_at_utc": now.isoformat(),
}

bad_states = ("failed", "terminated")
state_lower = operational_state.lower()

if keylime_rc != 0:
    decision["reason"] = "KEYLIME_STATUS_COMMAND_FAILED"
elif attestation_status != "PASS":
    decision["reason"] = "ATTESTATION_STATUS_NOT_PASS"
elif any(s in state_lower for s in bad_states):
    decision["reason"] = "OPERATIONAL_STATE_UNSAFE"
elif last_dt is None:
    decision["reason"] = "MISSING_OR_UNPARSEABLE_LAST_SUCCESSFUL_ATTESTATION"
elif age is not None and age < -30:
    decision["reason"] = "LAST_SUCCESSFUL_ATTESTATION_IN_FUTURE_CLOCK_SKEW"
elif age is not None and age > max_age_s:
    decision["reason"] = "STALE_LAST_SUCCESSFUL_ATTESTATION"
else:
    decision["result"] = "PASS_FRESH"
    decision["reason"] = "PASS_AND_FRESH"

with open(decision_path, "w", encoding="utf-8") as f:
    json.dump(decision, f, indent=2, sort_keys=True)
    f.write("\n")

print(decision["result"])
print(decision["reason"])
PY
}

rp_has_trait() {
  openstack resource provider trait list "$RP_UUID" -f value -c name | grep -Fxq "$TRUSTED_TRAIT"
}

rp_add_trait() {
  local traits
  local cmd

  if rp_has_trait; then
    return 0
  fi

  traits="$(openstack resource provider trait list "$RP_UUID" -f value -c name || true)"

  cmd=(openstack resource provider trait set)
  while IFS= read -r t; do
    [ -n "$t" ] && cmd+=(--trait "$t")
  done < <(printf '%s\n' "$traits")

  cmd+=(--trait "$TRUSTED_TRAIT")
  cmd+=("$RP_UUID")
  "${cmd[@]}"
}

rp_remove_trait() {
  local traits
  local cmd

  if ! rp_has_trait; then
    return 0
  fi

  traits="$(openstack resource provider trait list "$RP_UUID" -f value -c name || true)"

  cmd=(openstack resource provider trait set)
  while IFS= read -r t; do
    [ -z "$t" ] && continue
    [ "$t" = "$TRUSTED_TRAIT" ] && continue
    cmd+=(--trait "$t")
  done < <(printf '%s\n' "$traits")

  cmd+=("$RP_UUID")
  "${cmd[@]}"
}

set +e
get_keylime_status_raw
keylime_rc=$?
set -e

mapfile -t decision_lines < <(evaluate_keylime_status "$keylime_rc")
decision="${decision_lines[0]:-NOT_PASS}"
reason="${decision_lines[1]:-UNKNOWN_REASON}"

{
  echo "decision=$decision"
  echo "reason=$reason"
  echo "decision_file=$DECISION_FILE"
  echo "raw_status_file=$RAW_STATUS_FILE"
  cat "$DECISION_FILE"
} | tee "$LAST_LOG_FILE"

if [ "$decision" = "PASS_FRESH" ]; then
  echo "Keylime status PASS_FRESH: ensure $TRUSTED_TRAIT on $RP_NAME"
  rp_add_trait
else
  echo "Keylime status $decision/$reason: remove $TRUSTED_TRAIT from $RP_NAME"
  rp_remove_trait
fi

echo "Current $RP_NAME traits containing Keylime:"
openstack resource provider trait list "$RP_UUID" | grep "$TRUSTED_TRAIT" || true


