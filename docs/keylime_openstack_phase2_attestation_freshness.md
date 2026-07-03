# Keylime 与 OpenStack 深度结合：阶段 2 引入 Attestation Freshness

日期：2026-07-02

## 1. 阶段目标

阶段 1 已经完成：

```text
PCR 策略符合基线
  -> Keylime attestation_status = PASS
  -> systemd timer 保留 CUSTOM_KEYLIME_ATTESTED
  -> trusted.keylime.small 可以调度到 csri9
```

阶段 2 要解决的问题是：

```text
Keylime 曾经 PASS，不等于现在仍然可信。
```

因此同步脚本不能只判断：

```text
attestation_status == PASS
```

而要升级为：

```text
attestation_status == PASS
last_successful_attestation 存在
last_successful_attestation 没有超过 freshness 阈值
operational_state 没有进入 Failed / Terminated
```

建议本阶段 freshness 阈值先设为 120 秒。你当前 systemd timer 是 30 秒周期，120 秒能容忍短暂抖动，同时又不会让陈旧 PASS 长时间保留 trait。

## 2. 在 csri10 上先确认当前状态字段

以下命令在 `csri10` 执行。

```bash
cd /opt/keylime-docker
export KEYLIME_AGENT_UUID_FIXED="11111111-1111-4111-8111-000000000009"

docker compose run --rm keylime-tenant \
  -c status \
  -u "$KEYLIME_AGENT_UUID_FIXED" \
  -v 172.31.100.10 \
  -vp 8881 \
  -r 172.31.100.10 \
  -rp 8891
```

重点观察输出里是否有类似字段：

```text
"attestation_status": "PASS"
"operational_state": "Get Quote"
"last_successful_attestation": "..."
```

如果没有 `last_successful_attestation`，不要进入下一步，先把完整输出发回来。

## 3. 备份现有同步脚本

```bash
install -d -m 0755 /opt/keylime-openstack-sync/backups

cp -a /opt/keylime-openstack-sync/keylime-placement-sync.sh \
  "/opt/keylime-openstack-sync/backups/keylime-placement-sync.sh.before-freshness.$(date -u +%Y%m%dT%H%M%SZ).bak"

ls -l /opt/keylime-openstack-sync/backups | tail
```

## 4. 替换为 freshness 感知版本

这个版本会生成两个日志：

```text
/var/log/keylime-openstack-sync-status.raw.log
/var/log/keylime-openstack-sync-decision.json
```

第一个保存 `keylime-tenant status` 原始输出；第二个保存本轮判定原因。

```bash
cat >/opt/keylime-openstack-sync/keylime-placement-sync.sh <<'EOF'
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
EOF

chmod +x /opt/keylime-openstack-sync/keylime-placement-sync.sh
bash -n /opt/keylime-openstack-sync/keylime-placement-sync.sh
```

## 5. 手动验证 PASS_FRESH 场景

```bash
/opt/keylime-openstack-sync/keylime-placement-sync-locked.sh

cat /var/log/keylime-openstack-sync-last.log
cat /var/log/keylime-openstack-sync-decision.json | python3 -m json.tool
```

预期看到：

```text
decision=PASS_FRESH
reason=PASS_AND_FRESH
Keylime status PASS_FRESH: ensure CUSTOM_KEYLIME_ATTESTED on csri9
```

确认 trait：

```bash
source /etc/kolla/admin-openrc.sh
export OS_PLACEMENT_API_VERSION=1.17
export RP_CSRI9="$(openstack resource provider list --name csri9 -f value -c uuid)"

openstack resource provider trait list "$RP_CSRI9" | grep CUSTOM_KEYLIME_ATTESTED
```

## 6. 验证 systemd timer 仍然正常

```bash
systemctl status keylime-openstack-sync.timer --no-pager
systemctl status keylime-openstack-sync.service --no-pager
systemctl list-timers --all | grep keylime-openstack-sync

journalctl -u keylime-openstack-sync.service -n 80 --no-pager
```

如果刚替换脚本后想立即触发一次：

```bash
systemctl start keylime-openstack-sync.service
journalctl -u keylime-openstack-sync.service -n 80 --no-pager
```

## 7. 反向验证：agent 停止后 freshness 失效

在 `csri9` 停止 agent：

```bash
docker stop keylime-agent
```

在 `csri10` 等待超过 freshness 阈值。当前阈值是 120 秒，建议等 150 秒：

```bash
sleep 150
```

手动触发同步，或等待 timer 自动触发：

```bash
/opt/keylime-openstack-sync/keylime-placement-sync-locked.sh

cat /var/log/keylime-openstack-sync-last.log
cat /var/log/keylime-openstack-sync-decision.json | python3 -m json.tool
```

预期结果可能是以下之一，任意一种都应移除 trait：

```text
reason=ATTESTATION_STATUS_NOT_PASS
reason=OPERATIONAL_STATE_UNSAFE
reason=STALE_LAST_SUCCESSFUL_ATTESTATION
reason=KEYLIME_STATUS_COMMAND_FAILED
```

确认 trait 被移除：

```bash
source /etc/kolla/admin-openrc.sh
export OS_PLACEMENT_API_VERSION=1.17
export RP_CSRI9="$(openstack resource provider list --name csri9 -f value -c uuid)"

openstack resource provider trait list "$RP_CSRI9" | grep CUSTOM_KEYLIME_ATTESTED || \
  echo "OK: CUSTOM_KEYLIME_ATTESTED removed because attestation is not fresh"
```

创建 trusted VM，预期失败：

```bash
source /etc/kolla/admin-openrc.sh

export IMAGE="Fedora-Cloud-Base-AmazonEC2-44-1.7.x86_64.raw"
export TRUSTED_FLAVOR="trusted.keylime.small"
export NET_A_ID="64cef53a-83c7-49ba-beeb-2d7ea395d029"
export FAIL_VM="keylime-trusted-freshness-fail"

openstack server create "$FAIL_VM" \
  --image "$IMAGE" \
  --flavor "$TRUSTED_FLAVOR" \
  --nic net-id="$NET_A_ID"

sleep 20

openstack server show "$FAIL_VM" \
  -c status \
  -c OS-EXT-SRV-ATTR:host \
  -c fault \
  -f yaml
```

预期：

```text
status: ERROR
OS-EXT-SRV-ATTR:host: null
fault.message: No valid host was found.
```

## 8. 恢复 agent 和可信 trait

在 `csri9` 使用你之前固定下来的完整重启命令启动 `keylime-agent`。

在 `csri10` 等待 verifier 重新通过：

```bash
cd /opt/keylime-docker
export KEYLIME_AGENT_UUID_FIXED="11111111-1111-4111-8111-000000000009"

docker compose run --rm keylime-tenant \
  -c reactivate \
  -u "$KEYLIME_AGENT_UUID_FIXED" \
  -v 172.31.100.10 \
  -vp 8881 \
  -r 172.31.100.10 \
  -rp 8891

sleep 30

docker compose run --rm keylime-tenant \
  -c status \
  -u "$KEYLIME_AGENT_UUID_FIXED" \
  -v 172.31.100.10 \
  -vp 8881 \
  -r 172.31.100.10 \
  -rp 8891
```

触发同步：

```bash
/opt/keylime-openstack-sync/keylime-placement-sync-locked.sh

cat /var/log/keylime-openstack-sync-last.log
cat /var/log/keylime-openstack-sync-decision.json | python3 -m json.tool
```

确认 trait 恢复：

```bash
source /etc/kolla/admin-openrc.sh
export OS_PLACEMENT_API_VERSION=1.17
export RP_CSRI9="$(openstack resource provider list --name csri9 -f value -c uuid)"

openstack resource provider trait list "$RP_CSRI9" | grep CUSTOM_KEYLIME_ATTESTED
```

## 9. 快速调试命令

查看最近 timer 执行：

```bash
journalctl -u keylime-openstack-sync.service -n 120 --no-pager
```

查看最近同步判定：

```bash
cat /var/log/keylime-openstack-sync-decision.json | python3 -m json.tool
```

查看 Keylime 原始 status 输出：

```bash
cat /var/log/keylime-openstack-sync-status.raw.log
```

临时缩短 freshness 阈值做快速测试：

```bash
MAX_ATTESTATION_AGE_SECONDS=5 /opt/keylime-openstack-sync/keylime-placement-sync-locked.sh
cat /var/log/keylime-openstack-sync-decision.json | python3 -m json.tool
```

恢复默认阈值不需要改文件，因为脚本默认是 120 秒；systemd timer 也会继续用默认值。

## 10. 阶段 2 验收标准

阶段 2 成功的标准：

```text
1. Keylime PASS 且 last_successful_attestation 未过期时：
   csri9 保留 CUSTOM_KEYLIME_ATTESTED。

2. Keylime 非 PASS、agent 不可达、verifier 状态失败、或 last_successful_attestation 过期时：
   csri9 自动移除 CUSTOM_KEYLIME_ATTESTED。

3. trait 移除后：
   trusted.keylime.small 创建 VM 失败，Nova 返回 No valid host。

4. agent 恢复且 Keylime 重新 PASS_FRESH 后：
   trait 自动恢复，trusted VM 可以重新调度到 csri9。
```

完成这一阶段后，`CUSTOM_KEYLIME_ATTESTED` 的语义会升级为：

```text
该计算节点最近一次 Keylime 验证成功，且结果仍在有效时间窗口内。
```

这比“曾经 PASS”更接近生产环境里的持续可信。

