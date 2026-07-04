# Case 9: Keylime 失信节点上的存量 VM 风险标记与恢复清理

实验日期：2026-07-04

## 目标

Case 8 已经证明：当计算节点失信后，OpenStack 可以阻断新的 trusted workload 调度。

Case 9 继续处理真实云平台中必须面对的问题：

```text
节点失信前已经运行在该节点上的虚拟机怎么办？
```

本案例实现第一版“存量 VM 风险标记与审计”：

```text
计算节点可信：
  清理该节点上 VM 的 keylime_trust_* metadata。

计算节点失信：
  给该节点上已有 VM 写入 keylime_trust_* metadata。

管理员审计：
  通过 OpenStack server properties 和审计 JSON 查询受影响 VM。
```

第一版不做自动迁移、不自动关机，避免把故障面扩大。它只做安全标记和审计。

## 实现文件

```text
deploy/scripts/keylime-vm-risk-marker.sh
deploy/scripts/keylime-sync-control-loop.sh
deploy/env/openstack-keylime-lab.env
```

部署到 csri10 后路径：

```text
/opt/keylime-openstack-sync/keylime-vm-risk-marker.sh
```

审计文件：

```text
/var/log/keylime-openstack-vm-risk-marker.json
```

## 关键配置

```bash
export KEYLIME_VM_RISK_MARKER_ENABLE="true"
export KEYLIME_VM_RISK_MARKER_INTERVAL_SECONDS="60"
export KEYLIME_VM_RISK_AUDIT_FILE="/var/log/keylime-openstack-vm-risk-marker.json"
```

`keylime-sync-control-loop.sh` 在完成每轮 Keylime/Placement/nova-compute 同步后，会根据 `KEYLIME_VM_RISK_MARKER_ENABLE` 调用 `keylime-vm-risk-marker.sh`。

因此该能力挂载到现有 systemd timer：

```text
keylime-openstack-sync.timer
  -> keylime-openstack-sync.service
  -> keylime-sync-control-loop.sh
  -> keylime-vm-risk-marker.sh
```

## VM Metadata 设计

失信 host 上的 VM 会被写入：

```yaml
keylime_trust_state: host_untrusted
keylime_trust_host: <compute-host>
keylime_trust_reason: <Keylime decision reason>
keylime_trust_checked_at: <UTC timestamp>
```

例如：

```yaml
keylime_trust_checked_at: '2026-07-04T13:50:47Z'
keylime_trust_host: csri8
keylime_trust_reason: ATTESTATION_STATUS_NOT_PASS
keylime_trust_state: host_untrusted
```

可信 host 上的 VM 会清理这些 metadata：

```text
keylime_trust_state
keylime_trust_host
keylime_trust_reason
keylime_trust_checked_at
```

## 为什么第一版使用 Metadata

实验中验证：

```text
openstack server set --property ... 可用
openstack server set --tag ... 需要 --os-compute-api-version 2.26 或更高版本
```

因此第一版使用 OpenStack server metadata，后续可在 API 版本确认后补充 tag。

## 基线验证

在 csri8/csri9 都可信时运行：

```bash
/opt/keylime-openstack-sync/keylime-vm-risk-marker.sh
```

实测输出：

```text
planned_actions=13
audit_file=/var/log/keylime-openstack-vm-risk-marker.json
marked=0
cleared=13
failed=0
```

代表当前全部 host 可信，所以所有 VM 都执行清理动作，没有风险标记。

测试 VM：

```text
VM id: 2e66f3cd-4c3e-47be-ad48-1a7380877bde
name: trusted-after-recovery-csri8-20260704034223
host: csri8
properties: {}
```

## 失信标记验证

停止 csri8 Keylime agent 并等待 freshness 过期：

```bash
ssh root@172.31.100.8 'docker rm -f keylime-agent || true'
sleep 150
systemctl start keylime-openstack-sync.service || true
```

Keylime/OpenStack 状态：

```text
summary: {'total': 2, 'trusted': 1, 'untrusted': 1, 'no_agent': 0, 'level': 'bad', 'text': '存在异常节点'}
csri8 trust= 不可信 decision= NOT_PASS reason= ATTESTATION_STATUS_NOT_PASS trait= False service= disabled / up vm_count= 5
csri9 trust= 可信 decision= PASS_FRESH reason= PASS_AND_FRESH trait= True service= enabled / up vm_count= 8
```

运行 VM risk marker：

```text
planned_actions=13
marked=5
cleared=8
failed=0
```

被标记的 5 台 VM：

```text
trusted-after-recovery-csri8-20260704034223
trusted-pool-auto-20260704031921
trusted-pool-csri8-20260704031921
tpm-ubuntu
fedora-demo
```

示例 VM metadata：

```yaml
OS-EXT-SRV-ATTR:host: csri8
name: trusted-after-recovery-csri8-20260704034223
properties:
  keylime_trust_checked_at: '2026-07-04T13:50:47Z'
  keylime_trust_host: csri8
  keylime_trust_reason: ATTESTATION_STATUS_NOT_PASS
  keylime_trust_state: host_untrusted
status: ACTIVE
```

结论：

```text
csri8 失信后，csri8 上已有 VM 被自动标记为运行在失信宿主机上。
csri9 上 VM 被清理或保持无风险标记。
```

## 恢复清理验证

恢复 csri8 Keylime agent 后，必须等待同步决策回到 `PASS_FRESH`。

实测恢复后的 csri8 decision：

```json
{
  "attestation_status": "PASS",
  "checked_at_utc": "2026-07-04T13:59:17.167314+00:00",
  "keylime_command_rc": 0,
  "last_event_id": "internal.verifier.not_reachable",
  "last_successful_attestation": "1783173553",
  "last_successful_attestation_age_seconds": 4,
  "max_attestation_age_seconds": 120,
  "operational_state": "Get Quote",
  "reason": "PASS_AND_FRESH",
  "result": "PASS_FRESH"
}
```

再次运行 VM risk marker：

```text
planned_actions=13
marked=0
cleared=13
failed=0
```

csri8 上 5 台 VM 的 metadata 被清理：

```text
trusted-after-recovery-csri8-20260704034223 properties: {}
trusted-pool-auto-20260704031921 properties: {}
trusted-pool-csri8-20260704031921 properties: {}
tpm-ubuntu properties: {}
fedora-demo properties: {}
```

结论：

```text
csri8 恢复 PASS_FRESH 后，VM 风险标记自动清理。
该能力能表达“风险随宿主机可信状态变化而变化”，不是一次性静态标签。
```

## 审计文件

`/var/log/keylime-openstack-vm-risk-marker.json` 记录：

```text
checked_at_utc
summary
hosts
actions
```

每个 action 包含：

```text
action: MARK / CLEAR
server_id
server_name
server_status
host
reason
checked_at
```

这使管理员可以独立于前端，通过日志文件审计某一轮标记动作。

## 手动验证命令

查看 marker 审计摘要：

```bash
python3 - <<'PY'
import json
d = json.load(open("/var/log/keylime-openstack-vm-risk-marker.json"))
mark = [a for a in d.get("actions", []) if a.get("action") == "MARK"]
clear = [a for a in d.get("actions", []) if a.get("action") == "CLEAR"]
print("summary:", d.get("summary"))
print("mark_count:", len(mark))
print("clear_count:", len(clear))
for a in mark:
    print(a.get("server_name"), "id=", a.get("server_id"), "host=", a.get("host"), "reason=", a.get("reason"))
PY
```

列出 csri8 VM 风险 metadata：

```bash
source /etc/kolla/admin-openrc.sh
for vm in $(openstack server list --all-projects --long -f value -c ID -c Host | awk '$2=="csri8"{print $1}'); do
  echo "--- $vm ---"
  openstack server show "$vm" \
    -c name \
    -c status \
    -c OS-EXT-SRV-ATTR:host \
    -c properties \
    -f yaml
done
```

## 安全边界

第一版只做标记与审计：

```text
不自动迁移 VM
不自动关机
不自动重启 workload
不修改租户网络或安全组
```

原因是：宿主机失信后的存量 workload 处置可能影响业务连续性，应先进入审计和人工确认流程。

后续可以扩展：

```text
1. 前端显示“运行在失信节点上的 VM”列表。
2. 给 marker 增加 dry-run 模式。
3. 使用 OpenStack tag 辅助查询。
4. 对 ACTIVE trusted VM 触发告警。
5. 引入人工确认后的 evacuate/live migration 流程。
```
