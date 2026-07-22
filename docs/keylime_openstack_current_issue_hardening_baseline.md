# Keylime 与 OpenStack：当前问题优化基线

日期：2026-07-03

## 1. 本阶段目标

本阶段暂不开展新的实验能力验证，而是先把已经遇到的问题和临时处理方式沉淀为更稳定的实验基线。

目标是：

```text
1. 减少脚本中的硬编码环境信息。
2. 统一 Keylime/OpenStack 变量来源。
3. 保留当前 lab 行为，不改变实验结论。
4. 让 systemd timer 默认运行完整控制循环。
5. 为下一阶段 verifier API、Placement API、最小权限账号改造打基础。
```

## 2. 当前优化内容

### 2.1 统一 env 文件

所有主要脚本现在都会尝试加载：

```bash
/etc/keylime-openstack-sync/openstack-keylime-lab.env
```

也可以用环境变量覆盖：

```bash
export KEYLIME_OPENSTACK_ENV_FILE=/path/to/openstack-keylime-lab.env
```

仓库模板位于：

```text
deploy/env/openstack-keylime-lab.env
```

建议部署时复制到控制节点：

```bash
install -d -m 0755 /etc/keylime-openstack-sync
cp deploy/env/openstack-keylime-lab.env \
  /etc/keylime-openstack-sync/openstack-keylime-lab.env
```

### 2.2 配置项收敛

以下内容从脚本硬编码升级为 env 可配置：

```text
OPENRC
KEYLIME_DIR
KEYLIME_OPENSTACK_SYNC_DIR
KEYLIME_OPENSTACK_LOG_DIR
KEYLIME_OPENSTACK_STATE_DIR
KEYLIME_AGENT_UUID_FIXED
KEYLIME_VERIFIER_IP / PORT
KEYLIME_REGISTRAR_IP / PORT
KEYLIME_AGENT_REGISTRAR_PORT
KEYLIME_AGENT_IP / PORT
RP_NAME
TRUSTED_TRAIT
COMPUTE_HOST
COMPUTE_SERVICE
MAX_ATTESTATION_AGE_SECONDS
```

这样后续接入 `csri8` 或调整部署路径时，优先改 env，而不是复制多份脚本。

### 2.3 registrar 端口语义拆分

实验中有两个 registrar 端口，容易混淆：

```text
KEYLIME_REGISTRAR_PORT=8891
  keylime-tenant 查询/操作 registrar 时使用

KEYLIME_AGENT_REGISTRAR_PORT=8890
  keylime-agent 注册时使用
```

这两个变量已经在 env 模板中显式拆开。

### 2.4 同步脚本运行前检查

`keylime-placement-sync.sh` 增加了运行前检查：

```text
OPENRC 必须可读
KEYLIME_DIR 必须存在
日志目录会自动创建
```

如果配置错误，脚本会在进入 OpenStack 或 Keylime 操作前失败并输出明确错误。

### 2.5 systemd 默认进入完整控制循环

`deploy/systemd/keylime-openstack-sync.service` 现在默认运行：

```text
/opt/keylime-openstack-sync/keylime-sync-control-loop.sh
```

控制循环包含：

```text
1. Keylime -> Placement trait 同步
2. Keylime -> nova-compute quarantine / recovery
```

同时 systemd 会加载：

```text
EnvironmentFile=-/etc/keylime-openstack-sync/openstack-keylime-lab.env
```

### 2.6 quarantine 脚本保留人工运维边界

`keylime-nova-compute-quarantine.sh` 继续使用 marker 文件：

```text
/var/lib/keylime-openstack-sync/<host>.<service>.disabled-by-keylime
```

只有 Keylime 控制器自己禁用过的 compute service，才会在 `PASS_FRESH` 后自动启用。

如果管理员手动禁用了 compute service，Keylime 控制器不会无条件覆盖人工状态。

### 2.7 清理 deploy 文件的 UTF-8 BOM

`deploy/` 下的脚本、env 文件和 systemd unit 已清理开头的 UTF-8 BOM。

原因：

```text
带 BOM 的 shell 脚本用 bash script.sh 运行通常没问题，
但由 systemd 或内核按 shebang 直接执行时，
#! 可能不是文件第一个字节，存在执行失败风险。
```

后续新增脚本建议保持：

```text
UTF-8 without BOM
LF line endings
```

## 3. 当前仍然保留的 lab 妥协

以下问题本阶段只显式化，不立即生产化改造：

```text
1. 同步脚本仍使用 /etc/kolla/admin-openrc.sh。
2. Keylime 状态仍通过 keylime-tenant CLI 获取。
3. keylime-agent 容器仍默认使用 latest 镜像。
4. agent restart 脚本仍默认使用 --privileged。
5. agent restart 脚本默认会放宽 /dev/tpm* 权限。
6. 暂未启用 EK certificate、measured boot、IMA runtime policy。
```

其中 TPM 权限放宽已经变成显式变量：

```bash
export KEYLIME_AGENT_RELAX_TPM_PERMISSIONS="true"
```

下一阶段生产化时，应改为：

```text
udev 规则 + tss 组 + 宿主机 systemd keylime-agent
```

## 4. 部署更新建议

在 `csri10` 控制节点：

```bash
install -d -m 0755 /opt/keylime-openstack-sync
install -d -m 0755 /etc/keylime-openstack-sync

cp deploy/scripts/keylime-placement-sync.sh /opt/keylime-openstack-sync/
cp deploy/scripts/keylime-placement-sync-locked.sh /opt/keylime-openstack-sync/
cp deploy/scripts/keylime-nova-compute-quarantine.sh /opt/keylime-openstack-sync/
cp deploy/scripts/keylime-sync-control-loop.sh /opt/keylime-openstack-sync/
cp deploy/examples/legacy/keylime-reactivate-csri9.sh /opt/keylime-openstack-sync/

chmod +x /opt/keylime-openstack-sync/*.sh

cp deploy/env/openstack-keylime-lab.env \
  /etc/keylime-openstack-sync/openstack-keylime-lab.env

cp deploy/systemd/keylime-openstack-sync.service /etc/systemd/system/
cp deploy/systemd/keylime-openstack-sync.timer /etc/systemd/system/

systemctl daemon-reload
systemctl restart keylime-openstack-sync.timer
```

在 `csri9` 计算节点，如果仍使用容器版 agent：

```bash
cp deploy/examples/legacy/restart-keylime-agent-csri9.sh /root/
chmod +x /root/restart-keylime-agent-csri9.sh
```

如需复用统一 env，可以把 env 文件也复制到 `csri9` 的同一路径。

## 5. 回归验证清单

更新后建议先做不引入新实验的回归：

```text
1. bash -n 检查所有 deploy/scripts/*.sh。
2. systemctl start keylime-openstack-sync.service 手动触发一次。
3. 查看 /var/log/keylime-openstack-sync-decision.json。
4. Keylime PASS_FRESH 时确认 csri9 有 CUSTOM_KEYLIME_ATTESTED。
5. Keylime FAIL 或 freshness 过期时确认 trait 被移除。
6. marker 存在时 PASS_FRESH 会恢复 nova-compute。
7. marker 不存在时 PASS_FRESH 不覆盖管理员手动禁用状态。
```

关键日志：

```bash
journalctl -u keylime-openstack-sync.service -n 100 --no-pager
cat /var/log/keylime-openstack-sync-last.log
cat /var/log/keylime-openstack-sync-decision.json | python3 -m json.tool
```

## 6. 下一阶段前置收益

完成这轮优化后，下一阶段可以更自然地推进：

```text
1. 用 verifier REST API 替代 keylime-tenant CLI。
2. 用 Placement REST API 替代 openstack CLI。
3. 用最小权限 service account 替代 admin-openrc.sh。
4. 加入多节点映射表，接入 csri8。
5. 引入 measured boot / IMA / 多级可信 trait。
```

这一轮优化不改变可信云实验的安全语义，只把已经验证过的链路整理成更可靠、更可复用的实验基线。

## 7. 实测验收记录

验收时间：2026-07-03

实验节点：

```text
controller: csri10
compute:    csri9
agent uuid: 11111111-1111-4111-8111-000000000009
trait:      CUSTOM_KEYLIME_ATTESTED
service:    csri9 nova-compute
```

### 7.1 env 与脚本部署验证

`csri10` 上统一 env 文件加载成功：

```text
OPENRC readable: /etc/kolla/admin-openrc.sh
KEYLIME_DIR exists: /opt/keylime-docker
KEYLIME_VERIFIER_IP:PORT = 172.31.100.10:8881
KEYLIME_REGISTRAR_IP:PORT = 172.31.100.10:8891
RP_NAME / TRUSTED_TRAIT = csri9 / CUSTOM_KEYLIME_ATTESTED
```

`keylime-placement-sync.sh`、`keylime-nova-compute-quarantine.sh`、`keylime-sync-control-loop.sh` 与 systemd service 均通过手动触发验证。

### 7.2 健康态验证

Keylime 正常时，控制循环输出：

```json
{
  "attestation_status": "PASS",
  "operational_state": "Get Quote",
  "last_successful_attestation_age_seconds": 4,
  "max_attestation_age_seconds": 120,
  "reason": "PASS_AND_FRESH",
  "result": "PASS_FRESH"
}
```

OpenStack 侧结果：

```text
CUSTOM_KEYLIME_ATTESTED exists on csri9 resource provider
csri9 nova-compute enabled / up
no Keylime marker exists, so manual admin state is not overridden
```

### 7.3 失信隔离验证

在 `csri9` 停止 `keylime-agent`，等待 freshness 过期后，timer 自动触发控制循环。

Keylime 判定结果：

```json
{
  "attestation_status": "FAIL",
  "operational_state": "Failed",
  "last_event_id": "internal.verifier.not_reachable",
  "last_successful_attestation_age_seconds": 141,
  "max_attestation_age_seconds": 120,
  "reason": "ATTESTATION_STATUS_NOT_PASS",
  "result": "NOT_PASS"
}
```

OpenStack 侧结果：

```text
CUSTOM_KEYLIME_ATTESTED removed from csri9 resource provider
csri9 nova-compute disabled / up
marker created:
  /var/lib/keylime-openstack-sync/csri9.nova-compute.disabled-by-keylime
```

marker 内容：

```text
Keylime attestation not trusted: result=NOT_PASS, reason=ATTESTATION_STATUS_NOT_PASS, state=Failed, event=internal.verifier.not_reachable
```

### 7.4 恢复解除隔离验证

重启 `csri9` 的 `keylime-agent` 并执行 reactivate 后，timer 自动恢复可信状态。

Keylime 判定结果：

```json
{
  "attestation_status": "PASS",
  "operational_state": "Get Quote",
  "last_successful_attestation_age_seconds": 2,
  "max_attestation_age_seconds": 120,
  "reason": "PASS_AND_FRESH",
  "result": "PASS_FRESH"
}
```

OpenStack 侧结果：

```text
CUSTOM_KEYLIME_ATTESTED restored on csri9 resource provider
csri9 nova-compute enabled / up
marker removed
```

后续 timer 再次运行时输出：

```text
Keylime result is PASS_FRESH but no Keylime marker exists; do not override manual admin state.
```

这是预期行为，表示 Keylime 控制器不会覆盖管理员手动维护状态。

### 7.5 最终状态

最终收尾确认：

```text
keylime-openstack-sync.timer active
keylime-openstack-sync.timer enabled
result = PASS_FRESH
CUSTOM_KEYLIME_ATTESTED exists
csri9 nova-compute enabled / up
/var/lib/keylime-openstack-sync/ is empty
```

本次优化验收结论：

```text
健康态、失信隔离、恢复解除隔离三段闭环均通过。
优化后的 env、脚本、systemd timer、freshness 判定、trait 同步、nova-compute quarantine 与 marker 保护机制可作为下一阶段实验基线。
```
