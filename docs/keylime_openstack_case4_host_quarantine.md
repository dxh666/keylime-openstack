# 案例 4：Keylime 驱动 OpenStack 计算节点自动隔离与恢复

日期：2026-07-02

## 1. 案例目标

前面的案例已经实现：

```text
Keylime PASS_FRESH
  -> csri9 拥有 CUSTOM_KEYLIME_ATTESTED
  -> trusted flavor 可以调度到 csri9

Keylime FAIL / freshness 过期
  -> csri9 移除 CUSTOM_KEYLIME_ATTESTED
  -> trusted flavor 无法调度
```

案例 4 继续增强：

```text
Keylime FAIL / freshness 过期
  -> 移除 CUSTOM_KEYLIME_ATTESTED
  -> 自动 disable csri9 的 nova-compute
  -> 阻止任何新 VM 调度到 csri9

Keylime 恢复 PASS_FRESH
  -> 恢复 CUSTOM_KEYLIME_ATTESTED
  -> 自动 enable csri9 的 nova-compute
  -> csri9 重新进入调度池
```

这个案例把 Keylime 从“可信 flavor 的调度约束”进一步升级为“云平台计算节点安全运营控制器”。

## 2. 安全语义

阶段 1 到阶段 3 的语义是：

```text
不可信节点不能承载 trusted workload。
```

案例 4 的语义升级为：

```text
不可信节点不能承载任何新 workload。
```

区别：

```text
移除 CUSTOM_KEYLIME_ATTESTED:
  只影响要求可信 trait 的 flavor。

disable nova-compute:
  影响所有新建 VM 的调度。
```

因此，这个案例更接近生产中的“失信节点隔离”。

## 3. 当前实验对象

```text
控制节点:
  csri10

被 Keylime 管控的计算节点:
  csri9

Nova compute service:
  host: csri9
  binary: nova-compute

Keylime trait:
  CUSTOM_KEYLIME_ATTESTED

Keylime 判定文件:
  /var/log/keylime-openstack-sync-decision.json
```

## 4. 行为设计

### 4.1 PASS_FRESH

当 freshness 脚本输出：

```json
{
  "result": "PASS_FRESH",
  "reason": "PASS_AND_FRESH"
}
```

执行：

```text
1. 保留或恢复 CUSTOM_KEYLIME_ATTESTED。
2. 如果 csri9 是被 Keylime 自动禁用的，则 enable nova-compute。
3. 如果 csri9 是管理员手动禁用的，不自动启用。
```

### 4.2 NOT_PASS

当 freshness 脚本输出：

```json
{
  "result": "NOT_PASS",
  "reason": "ATTESTATION_STATUS_NOT_PASS"
}
```

或：

```text
STALE_LAST_SUCCESSFUL_ATTESTATION
OPERATIONAL_STATE_UNSAFE
KEYLIME_STATUS_COMMAND_FAILED
```

执行：

```text
1. 移除 CUSTOM_KEYLIME_ATTESTED。
2. disable csri9 nova-compute。
3. 写入本地 marker，表示该禁用动作由 Keylime 控制器执行。
```

## 5. 为什么需要 marker 文件

不能简单地在 Keylime PASS 后无条件 enable nova-compute。

原因：

```text
管理员可能因为维护、故障、升级手动 disable csri9。
如果 Keylime 脚本无条件 enable，会覆盖人工运维意图。
```

因此本案例使用 marker 文件：

```text
/var/lib/keylime-openstack-sync/csri9.nova-compute.disabled-by-keylime
```

只有当这个 marker 存在时，Keylime 恢复 PASS_FRESH 才会自动 enable。

## 6. 部署 quarantine 脚本

以下命令在 `csri10` 执行。

创建状态目录：

```bash
install -d -m 0755 /var/lib/keylime-openstack-sync
```

创建 `keylime-nova-compute-quarantine.sh`：

```bash
cat >/opt/keylime-openstack-sync/keylime-nova-compute-quarantine.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

OPENRC=/etc/kolla/admin-openrc.sh
DECISION_FILE=/var/log/keylime-openstack-sync-decision.json
STATE_DIR=/var/lib/keylime-openstack-sync

COMPUTE_HOST="${COMPUTE_HOST:-csri9}"
COMPUTE_SERVICE="${COMPUTE_SERVICE:-nova-compute}"
MARKER_FILE="$STATE_DIR/${COMPUTE_HOST}.${COMPUTE_SERVICE}.disabled-by-keylime"

source "$OPENRC"

if [ ! -s "$DECISION_FILE" ]; then
  echo "ERROR: decision file not found or empty: $DECISION_FILE"
  exit 1
fi

read -r result reason attestation_status operational_state last_event_id < <(
  python3 - "$DECISION_FILE" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as f:
    d = json.load(f)

print(
    d.get("result", ""),
    d.get("reason", ""),
    d.get("attestation_status", ""),
    d.get("operational_state", ""),
    d.get("last_event_id", ""),
)
PY
)

disable_compute() {
  local disable_reason
  disable_reason="Keylime attestation not trusted: result=${result}, reason=${reason}, state=${operational_state}, event=${last_event_id}"

  echo "Keylime result is $result/$reason: disable ${COMPUTE_HOST} ${COMPUTE_SERVICE}"

  openstack compute service set \
    --disable \
    --disable-reason "$disable_reason" \
    "$COMPUTE_HOST" \
    "$COMPUTE_SERVICE"

  install -d -m 0755 "$STATE_DIR"
  {
    date -u +"%Y-%m-%dT%H:%M:%SZ"
    echo "$disable_reason"
  } > "$MARKER_FILE"
}

enable_compute_if_owned() {
  if [ -f "$MARKER_FILE" ]; then
    echo "Keylime result is PASS_FRESH and marker exists: enable ${COMPUTE_HOST} ${COMPUTE_SERVICE}"

    openstack compute service set \
      --enable \
      "$COMPUTE_HOST" \
      "$COMPUTE_SERVICE"

    rm -f "$MARKER_FILE"
  else
    echo "Keylime result is PASS_FRESH but no Keylime marker exists; do not override manual admin state."
  fi
}

if [ "$result" = "PASS_FRESH" ]; then
  enable_compute_if_owned
else
  disable_compute
fi

echo "Current compute service state:"
openstack compute service list | awk 'NR==1 || /nova-compute/ && /csri9/'
EOF

chmod +x /opt/keylime-openstack-sync/keylime-nova-compute-quarantine.sh
bash -n /opt/keylime-openstack-sync/keylime-nova-compute-quarantine.sh
```

## 7. 部署统一控制循环 wrapper

当前已有：

```text
/opt/keylime-openstack-sync/keylime-placement-sync-locked.sh
```

但案例 4 需要把两个动作放在同一个锁里：

```text
1. Keylime -> Placement trait 同步
2. Keylime -> nova-compute 隔离/恢复
```

创建新 wrapper：

```bash
cat >/opt/keylime-openstack-sync/keylime-sync-control-loop.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

LOCK_FILE=/run/keylime-openstack-control-loop.lock
PLACEMENT_SYNC=/opt/keylime-openstack-sync/keylime-placement-sync.sh
QUARANTINE_SYNC=/opt/keylime-openstack-sync/keylime-nova-compute-quarantine.sh

exec 9>"$LOCK_FILE"

if ! flock -n 9; then
  echo "Another keylime-openstack control loop is still active; skip this round."
  exit 0
fi

"$PLACEMENT_SYNC"
"$QUARANTINE_SYNC"
EOF

chmod +x /opt/keylime-openstack-sync/keylime-sync-control-loop.sh
bash -n /opt/keylime-openstack-sync/keylime-sync-control-loop.sh
```

## 8. 修改 systemd service

先备份：

```bash
cp -a /etc/systemd/system/keylime-openstack-sync.service \
  "/etc/systemd/system/keylime-openstack-sync.service.before-quarantine.$(date -u +%Y%m%dT%H%M%SZ).bak"
```

替换 service：

```bash
cat >/etc/systemd/system/keylime-openstack-sync.service <<'EOF'
[Unit]
Description=Sync Keylime attestation status to OpenStack Placement traits and quarantine nova-compute
Wants=docker.service
After=docker.service network-online.target

[Service]
Type=oneshot
ExecStart=/opt/keylime-openstack-sync/keylime-sync-control-loop.sh
TimeoutStartSec=120
EOF

systemctl daemon-reload
systemctl restart keylime-openstack-sync.timer
```

手动触发一次：

```bash
systemctl start keylime-openstack-sync.service
journalctl -u keylime-openstack-sync.service -n 100 --no-pager
```

## 9. 正向验证：Keylime PASS_FRESH 时 csri9 保持启用

确认 Keylime 当前 PASS_FRESH：

```bash
cat /var/log/keylime-openstack-sync-decision.json | python3 -m json.tool
```

手动运行：

```bash
/opt/keylime-openstack-sync/keylime-sync-control-loop.sh
```

查看 compute service：

```bash
source /etc/kolla/admin-openrc.sh
openstack compute service list | awk 'NR==1 || /nova-compute/ && /csri9/'
```

预期：

```text
Status: enabled
State: up
```

如果 marker 不存在，日志可能显示：

```text
Keylime result is PASS_FRESH but no Keylime marker exists; do not override manual admin state.
```

这是正确行为。

## 10. 反向验证：Keylime 失效后自动隔离 csri9

在 `csri9` 停止 agent：

```bash
docker stop keylime-agent
```

在 `csri10` 等待 freshness 过期：

```bash
sleep 150
```

触发控制循环：

```bash
/opt/keylime-openstack-sync/keylime-sync-control-loop.sh

cat /var/log/keylime-openstack-sync-decision.json | python3 -m json.tool
```

查看 compute service：

```bash
source /etc/kolla/admin-openrc.sh
openstack compute service list | awk 'NR==1 || /nova-compute/ && /csri9/'
```

预期：

```text
csri9 nova-compute disabled
```

查看 marker：

```bash
cat /var/lib/keylime-openstack-sync/csri9.nova-compute.disabled-by-keylime
```

此时的安全含义：

```text
csri9 不再接收任何新 VM 调度。
```

## 11. 验证 trusted VM 仍然失败

因为 trait 已被移除，trusted VM 应继续失败：

```bash
source /etc/kolla/admin-openrc.sh

export FAIL_VM="trusted-quarantine-fail-a"
export IMAGE="Fedora-Cloud-Base-AmazonEC2-44-1.7.x86_64.raw"
export PRIVATE_TRUSTED_FLAVOR="trusted.keylime.private.small"
export NET_A_ID="64cef53a-83c7-49ba-beeb-2d7ea395d029"

openstack \
  --os-auth-url "$OS_AUTH_URL" \
  --os-identity-api-version 3 \
  --os-username "alice-a" \
  --os-password "<LAB_USER_PASSWORD>" \
  --os-user-domain-name "Default" \
  --os-project-name "proj-boundary-a" \
  --os-project-domain-name "Default" \
  server create "$FAIL_VM" \
  --image "$IMAGE" \
  --flavor "$PRIVATE_TRUSTED_FLAVOR" \
  --nic net-id="$NET_A_ID"

sleep 20

openstack server list --all-projects --name "$FAIL_VM"
```

预期：

```text
Status: ERROR
```

进一步查详情时，先通过 `server list --all-projects` 拿到 ID，再：

```bash
openstack server show <server-id> \
  -c status \
  -c OS-EXT-SRV-ATTR:host \
  -c fault \
  -f yaml
```

预期：

```text
No valid host was found.
```

## 12. 恢复验证：Keylime PASS_FRESH 后自动解除隔离

在 `csri9` 重启 agent：

```bash
/root/restart-keylime-agent-csri9.sh
```

如果你没有放置脚本，就使用前面固定下来的完整 `docker run` 重启命令。

在 `csri10` reactivate：

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
```

触发控制循环：

```bash
/opt/keylime-openstack-sync/keylime-sync-control-loop.sh

cat /var/log/keylime-openstack-sync-decision.json | python3 -m json.tool
```

预期：

```text
result: PASS_FRESH
```

检查 compute service：

```bash
source /etc/kolla/admin-openrc.sh
openstack compute service list | awk 'NR==1 || /nova-compute/ && /csri9/'
```

预期：

```text
csri9 nova-compute enabled up
```

marker 应消失：

```bash
ls -l /var/lib/keylime-openstack-sync/csri9.nova-compute.disabled-by-keylime
```

预期：

```text
No such file or directory
```

## 13. 回滚命令

如果 quarantine 逻辑有问题，立即停止 timer：

```bash
systemctl stop keylime-openstack-sync.timer
```

手动恢复 csri9：

```bash
source /etc/kolla/admin-openrc.sh
openstack compute service set --enable csri9 nova-compute
rm -f /var/lib/keylime-openstack-sync/csri9.nova-compute.disabled-by-keylime
```

恢复旧 service：

```bash
ls -1 /etc/systemd/system/keylime-openstack-sync.service.before-quarantine.*.bak

cp -a /etc/systemd/system/keylime-openstack-sync.service.before-quarantine.<时间戳>.bak \
  /etc/systemd/system/keylime-openstack-sync.service

systemctl daemon-reload
systemctl start keylime-openstack-sync.timer
```

## 14. 案例 4 达成的结果

完成后，本案例证明：

```text
Keylime 不只是 trusted flavor 的调度条件。
Keylime 可以成为 OpenStack 计算节点隔离控制器。

宿主机失信:
  -> 移除可信 trait
  -> 禁用 nova-compute
  -> 阻止所有新 workload 调度

宿主机恢复可信:
  -> 恢复可信 trait
  -> 恢复 nova-compute
  -> 重新进入调度池
```

## 15. 生产化注意事项

生产环境不建议无脑自动 enable / disable，至少需要：

```text
1. 只 enable 由 Keylime 自动 disable 的节点，本案例已用 marker 实现。
2. 对多次抖动加冷却时间，避免节点频繁进出调度池。
3. 禁用前发送告警。
4. 对已运行 VM 做标记、告警或迁移策略。
5. 结合 host aggregate 或维护模式做更完整的隔离。
6. 使用最小权限 OpenStack 账号，而不是 admin-openrc.sh。
```


