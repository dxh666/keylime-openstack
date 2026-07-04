# Case 8: Keylime 驱动的 OpenStack 可信计算池动态收缩与恢复

实验日期：2026-07-04

## 目标

本案例验证 OpenStack 可信计算池不再是静态主机分组，而是由 Keylime attestation 实时驱动的动态资源池。

实验要证明：

```text
csri8/csri9 都可信时：
  trusted flavor 可以调度到两个节点。

csri8 Keylime agent 停止后：
  Keylime verifier 判定 csri8 NOT_PASS。
  同步控制器移除 csri8 的 CUSTOM_KEYLIME_ATTESTED。
  同步控制器禁用 csri8 nova-compute。
  trusted workload 不再调度到 csri8。
  csri9 继续承载 trusted workload。

csri8 Keylime agent 恢复后：
  csri8 回到 PASS_FRESH。
  csri8 恢复 CUSTOM_KEYLIME_ATTESTED。
  csri8 nova-compute 恢复 enabled。
  trusted workload 再次可以调度到 csri8。
```

## 实验环境

```text
controller: csri10 / 172.31.100.10
compute:    csri8  / 172.31.100.8
compute:    csri9  / 172.31.100.9

Keylime verifier/registrar/tenant: csri10
Keylime agent:
  csri8 uuid=22222222-2222-4222-8222-000000000008
  csri9 uuid=11111111-1111-4111-8111-000000000009

OpenStack Placement trait:
  CUSTOM_KEYLIME_ATTESTED

trusted flavor:
  trusted.keylime.small
  trait:CUSTOM_KEYLIME_ATTESTED=required
```

## 控制链路

```text
Keylime verifier
  -> keylime-openstack-sync.timer
  -> keylime-sync-control-loop.sh
  -> per-host decision JSON
  -> Placement trait add/remove
  -> nova-compute enable/disable
  -> Nova Scheduler trusted flavor placement
```

## 1. 基线确认

执行：

```bash
source /etc/keylime-openstack-sync/openstack-keylime-lab.env
source /etc/kolla/admin-openrc.sh
export OS_PLACEMENT_API_VERSION=1.17

curl -s "http://172.31.100.10:8088/api/status?force=1" > /tmp/keylime-status.json

python3 - <<'PY'
import json
d = json.load(open("/tmp/keylime-status.json"))
print("summary:", d.get("summary"))
for n in d.get("nodes", []):
    print(
        n.get("host"),
        "ip=", n.get("ip"),
        "vm_count=", n.get("vm_count"),
        "trust=", n.get("conclusion", {}).get("text"),
        "decision=", n.get("decision", {}).get("result"),
        "trait=", n.get("placement", {}).get("trait_present"),
        "service=", n.get("service", {}).get("status"), "/", n.get("service", {}).get("state"),
    )
PY
```

实测结果：

```text
summary: {'total': 2, 'trusted': 2, 'untrusted': 0, 'no_agent': 0, 'level': 'ok', 'text': '全部可信'}
csri8 ip= 172.31.100.8 vm_count= 2 trust= 可信 decision= PASS_FRESH trait= True service= enabled / up
csri9 ip= 172.31.100.9 vm_count= 5 trust= 可信 decision= PASS_FRESH trait= True service= enabled / up
```

Placement traits：

```text
csri8: CUSTOM_KEYLIME_ATTESTED
csri9: CUSTOM_KEYLIME_ATTESTED
```

nova-compute services：

```text
csri8: enabled / up
csri9: enabled / up
```

## 2. 可信池正向调度验证

分别强制调度 trusted VM 到 csri8、csri9，再创建一个普通自动调度的 trusted VM。

实测结果：

```text
trusted-pool-csri8-20260704031921:
  status=ACTIVE
  host=csri8
  flavor extra_specs: trait:CUSTOM_KEYLIME_ATTESTED=required

trusted-pool-csri9-20260704031921:
  status=ACTIVE
  host=csri9
  flavor extra_specs: trait:CUSTOM_KEYLIME_ATTESTED=required

trusted-pool-auto-20260704031921:
  status=ACTIVE
  host=csri8
  flavor extra_specs: trait:CUSTOM_KEYLIME_ATTESTED=required
```

此时可信池状态：

```text
summary: {'total': 2, 'trusted': 2, 'untrusted': 0, 'no_agent': 0, 'level': 'ok', 'text': '全部可信'}
csri8 trust=可信 decision=PASS_FRESH trait=True service=enabled/up vm_count=4
csri9 trust=可信 decision=PASS_FRESH trait=True service=enabled/up vm_count=6
```

结论：

```text
csri8 和 csri9 都能作为可信计算池成员承载 trusted workload。
```

## 3. csri8 失信触发可信池收缩

停止 csri8 Keylime agent：

```bash
ssh root@172.31.100.8 'docker rm -f keylime-agent || true'
sleep 150
systemctl start keylime-openstack-sync.service || true
```

实测 monitor API：

```text
summary: {'total': 2, 'trusted': 1, 'untrusted': 1, 'no_agent': 0, 'level': 'bad', 'text': '存在异常节点'}
csri8 ip= 172.31.100.8 vm_count= 4 trust= 不可信 decision= NOT_PASS reason= ATTESTATION_STATUS_NOT_PASS trait= False service= disabled / up
csri9 ip= 172.31.100.9 vm_count= 6 trust= 可信 decision= PASS_FRESH reason= PASS_AND_FRESH trait= True service= enabled / up
```

csri8 decision 文件：

```json
{
  "attestation_status": "FAIL",
  "checked_at_utc": "2026-07-04T03:26:38.673913+00:00",
  "keylime_command_rc": 0,
  "last_event_id": "internal.verifier.not_reachable",
  "last_successful_attestation": "1783135436",
  "last_successful_attestation_age_seconds": 162,
  "max_attestation_age_seconds": 120,
  "operational_state": "Failed",
  "reason": "ATTESTATION_STATUS_NOT_PASS",
  "result": "NOT_PASS"
}
```

OpenStack 侧结果：

```text
csri8:
  CUSTOM_KEYLIME_ATTESTED 已移除
  nova-compute disabled / up

csri9:
  CUSTOM_KEYLIME_ATTESTED 保留
  nova-compute enabled / up
```

结论：

```text
Keylime agent 不可达后，csri8 自动从 OpenStack 可信计算池中移除。
```

## 4. 失信后的调度反向验证

强制调度 trusted VM 到失信 csri8：

```text
name: trusted-fail-csri8-20260704032834
status: ERROR
host: null
fault message: No valid host was found.
```

强制调度 trusted VM 到健康 csri9：

```text
name: trusted-ok-csri9-20260704032834
status: ACTIVE
host: csri9
```

普通自动调度 trusted VM：

```text
name: trusted-auto-after-csri8-fail-20260704032834
status: ACTIVE
host: csri9
```

可信池状态：

```text
summary: {'total': 2, 'trusted': 1, 'untrusted': 1, 'no_agent': 0, 'level': 'bad', 'text': '存在异常节点'}
csri8 trust= 不可信 decision= NOT_PASS trait= False service= disabled / up vm_count= 4
csri9 trust= 可信 decision= PASS_FRESH trait= True service= enabled / up vm_count= 8
```

结论：

```text
Nova Scheduler 正确避开失信节点。
可信池从两个节点收缩为一个节点后，trusted workload 仍可继续落到 csri9。
```

## 5. csri8 恢复触发可信池扩容

恢复 csri8 Keylime agent，并重新同步后，OpenStack 状态恢复：

```text
csri8 nova-compute: enabled / up
csri9 nova-compute: enabled / up

csri8: CUSTOM_KEYLIME_ATTESTED
csri9: CUSTOM_KEYLIME_ATTESTED
```

恢复后的调度验证：

```text
name: trusted-after-recovery-csri8-20260704034223
status: ACTIVE
host: csri8
flavor extra_specs:
  trait:CUSTOM_KEYLIME_ATTESTED: required
```

结论：

```text
csri8 恢复可信后重新进入可信计算池，trusted workload 可以再次调度到 csri8。
```

## 6. 实验结论

本案例完整证明：

```text
OpenStack 的可信计算池可以由 Keylime attestation 动态驱动。

可信节点：
  保留 CUSTOM_KEYLIME_ATTESTED
  nova-compute enabled
  可承载 trusted workload

失信节点：
  移除 CUSTOM_KEYLIME_ATTESTED
  nova-compute disabled
  trusted workload 无法调度

恢复可信节点：
  重新添加 CUSTOM_KEYLIME_ATTESTED
  nova-compute enabled
  重新承载 trusted workload
```

可信云控制链已经形成：

```text
Keylime 证明宿主机可信状态
  -> 同步控制器更新 OpenStack 资源表达
  -> Placement trait 表达可信能力
  -> Nova Scheduler 强制执行 trusted flavor 约束
  -> 失信节点自动退出可信池
  -> 恢复节点自动重新加入可信池
```

## 7. 后续优化

后续阶段建议继续推进：

```text
1. 将单一 CUSTOM_KEYLIME_ATTESTED 拆成多级可信 trait。
2. 用直接 verifier API 替代 keylime-tenant CLI 状态查询。
3. 增加失信节点上的已运行 VM 标记、告警和人工处置流程。
4. 引入 measured boot policy，验证启动链可信。
5. 引入 IMA runtime policy，验证运行时文件完整性。
6. 使用最小权限 OpenStack service account 替代 admin-openrc.sh。
```
