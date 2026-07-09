# Case 11: PCR10 / IMA 运行时完整性控制面

更新时间：2026-07-07

## 1. 目标

Case 10B 已经把 PCR7 启动可信策略接入 Keylime/OpenStack 控制面。Case 11 继续推进运行时完整性：

```text
csri8/csri9 IMA runtime measurements / PCR10
  -> Keylime runtime policy
  -> Keylime verifier attestation decision
  -> Placement trait / trusted flavor / nova-compute quarantine / VM risk marker
```

本仓库当前补齐的是控制面能力：采集 IMA/PCR10 证据、注册现场生成的 Keylime runtime policy JSON、绑定到计算节点、下发到 Keylime verifier，并复用既有同步循环完成 OpenStack 联动。

注意：本次 Codex 会话不能直接 SSH 访问远端 csri10，所以以下命令需要在 csri10 上执行。

## 2. 设计原则

PCR7 仍是当前稳定启动准入策略。运行时完整性作为第二层策略叠加，不替代 PCR7：

```text
PCR7 boot policy PASS_FRESH
IMA runtime policy PASS_FRESH
  -> CUSTOM_KEYLIME_ATTESTED 保留

IMA runtime policy FAIL / stale / agent unreachable
  -> Keylime decision 变为 NOT_PASS
  -> CUSTOM_KEYLIME_ATTESTED 移除
  -> nova-compute 隔离
  -> 存量 VM 写入 keylime_trust_* metadata
```

仓库不硬编码 Keylime runtime policy JSON 结构。该 JSON 由与当前 Keylime 版本匹配的官方 runtime policy 工具或现场已验证流程生成；仓库只负责把它纳入策略库、审计、绑定和下发。

## 3. 新增脚本

```text
deploy/scripts/keylime-ima-runtime-evidence-audit.sh
  采集 csri8/csri9 的 IMA 日志、PCR10、securityfs、runtime guard 文件状态。

deploy/scripts/keylime-ima-runtime-policy-register.sh
  将已生成的 Keylime runtime policy JSON 注册为 ima_runtime 策略，并绑定到 host。

deploy/scripts/keylime-ima-runtime-policy-apply.sh
  按绑定策略或指定策略调用 keylime-tenant update --runtime-policy-name/--runtime-policy。

deploy/examples/case11-ima-runtime-integrity-commands.sh
  Case 11 命令封装。
```

## 4. 文件路径

```text
/var/log/keylime-openstack-ima-runtime-baseline.json
  IMA/PCR10 runtime evidence baseline。

/var/log/keylime-openstack-ima-runtime-evidence/<timestamp>/
  每个 compute host 的原始采集输出。

/var/lib/keylime-openstack-sync/policies/runtime/
  持久保存 runtime policy JSON。

/var/lib/keylime-openstack-sync/tpm-pcr-policies.json
  继续作为统一策略库，新增 ima_runtime policy 与 runtime binding。

/var/log/keylime-openstack-runtime-policy-register.json
  runtime policy 注册审计。

/var/log/keylime-openstack-runtime-policy-apply.json
  runtime policy 下发审计。
```

## 5. 推荐实验流程

在 csri10 上重新安装控制面脚本：

```bash
cd /path/to/keylime-openstack
deploy/scripts/keylime-openstack-control-plane-install.sh
```

先采集 IMA/PCR10 证据：

```bash
/opt/keylime-openstack-sync/keylime-ima-runtime-evidence-audit.sh
python3 -m json.tool /var/log/keylime-openstack-ima-runtime-baseline.json
```

重点检查：

```text
has_ima_ascii_log
ima_ascii_count
pcr10_sha256
runtime_guard_exists
runtime_guard_sha256
```

如果 `has_ima_ascii_log=false` 或 `ima_ascii_count=0`，说明当前 compute 节点尚未产生可供 Keylime runtime policy 使用的 IMA measurement，需要先在 csri8/csri9 启用 IMA measurement。

## 6. 注册并绑定 runtime policy

先用当前 Keylime 版本匹配的 runtime policy 工具生成 JSON，例如：

```text
/var/lib/keylime-openstack-sync/policies/runtime/cloud-runtime-policy.json
```

然后注册并绑定：

```bash
/opt/keylime-openstack-sync/keylime-ima-runtime-policy-register.sh \
  all \
  /var/lib/keylime-openstack-sync/policies/runtime/cloud-runtime-policy.json \
  cloud-runtime-guard \
  "cloud runtime guard"
```

也可以只绑定单节点：

```bash
/opt/keylime-openstack-sync/keylime-ima-runtime-policy-register.sh \
  csri8 \
  /var/lib/keylime-openstack-sync/policies/runtime/csri8-runtime-policy.json \
  csri8-runtime-guard \
  "csri8 runtime guard"
```

策略会写入：

```text
type: ima_runtime
module: runtime_integrity
runtime_policy_name: <policy id or configured name>
runtime_policy_path: /var/lib/keylime-openstack-sync/policies/runtime/<policy-id>.json
bindings.<host>.runtime.policy_id: <policy id>
```

## 7. 下发策略并联动 OpenStack

按绑定策略下发到全部已绑定节点：

```bash
/opt/keylime-openstack-sync/keylime-ima-runtime-policy-apply.sh all bound
sleep 40
systemctl start keylime-openstack-sync.service || true
KEYLIME_VM_RISK_MARKER_FORCE=true /opt/keylime-openstack-sync/keylime-vm-risk-marker.sh || true
```

查看管理系统聚合状态：

```bash
curl -s "http://172.31.100.10:8088/api/status?force=1" | python3 -m json.tool
```

关键字段：

```text
nodes[].trust_layers.runtime
nodes[].trust_layers.policy.has_runtime_policy
nodes[].trust_layers.policy.runtime_pcr10_enforced
nodes[].decision.result
nodes[].placement.trait_present
nodes[].service.status
nodes[].marker
```

## 8. 验证闭环

正向预期：

```text
Keylime status PASS_FRESH
has_runtime_policy=true
runtime layer 显示 IMA 通过
CUSTOM_KEYLIME_ATTESTED 保留
trusted flavor 可继续调度到 csri8/csri9
```

Note: `tpm_policy_mask` may remain `0x80` after runtime policy apply. Keylime
reports IMA runtime policy state separately with `has_runtime_policy=true`;
PCR10 / IMA enforcement should not be inferred only from the TPM PCR mask.
When applying runtime policy, the control-plane scripts also pass the host's
bound PCR7 boot policy to `keylime_tenant update` so verifier state does not
fall back to a runtime-only `0x400` TPM policy.

反向验证应只在受控窗口中做，并使用专用 guard 文件，例如：

```text
/opt/keylime-cloud-integrity/cloud-runtime-guard.sh
```

以下文件修改应在目标 compute host 上执行，例如 csri8 或 csri9，而不是在 csri10 控制节点上直接执行。建议流程：

```bash
cp -a /opt/keylime-cloud-integrity/cloud-runtime-guard.sh \
  /opt/keylime-cloud-integrity/cloud-runtime-guard.sh.before-case11

printf '\n# case11 negative test %s\n' "$(date -u +%Y%m%dT%H%M%SZ)" \
  >> /opt/keylime-cloud-integrity/cloud-runtime-guard.sh

sleep 40
systemctl start keylime-openstack-sync.service || true
KEYLIME_VM_RISK_MARKER_FORCE=true /opt/keylime-openstack-sync/keylime-vm-risk-marker.sh || true
```

预期：

```text
Keylime verifier 进入 FAIL / NOT_PASS
CUSTOM_KEYLIME_ATTESTED 被移除
nova-compute 被控制器禁用
trusted flavor 不再调度到失信节点
该 host 上已有 VM 出现 keylime_trust_* metadata
```

恢复：

```bash
cp -a /opt/keylime-cloud-integrity/cloud-runtime-guard.sh.before-case11 \
  /opt/keylime-cloud-integrity/cloud-runtime-guard.sh

/opt/keylime-openstack-sync/keylime-ima-runtime-policy-apply.sh all bound
sleep 40
systemctl start keylime-openstack-sync.service || true
KEYLIME_VM_RISK_MARKER_FORCE=true /opt/keylime-openstack-sync/keylime-vm-risk-marker.sh || true
```

## 9. 当前边界

```text
1. PCR7 是当前稳定启动准入；IMA runtime 是新增运行时层。
2. runtime policy JSON 的生成仍依赖 Keylime 版本对应工具，本仓库不伪造格式。
3. 如果 compute 节点未启用 IMA measurement，Case 11 只能完成证据审计，不能进入 enforcement。
4. IMA 失败会通过现有 Keylime decision 进入统一 NOT_PASS 处置路径。
5. 管理系统不暴露破坏性负向验证入口；负向实验只在文档和命令示例中保留。
```

## 10. 验收标准

```text
1. csri8/csri9 能生成 IMA/PCR10 runtime evidence baseline JSON。
2. runtime policy JSON 能注册进统一策略库，且不会覆盖 PCR7 boot binding。
3. runtime policy 能按节点绑定并通过 keylime-tenant 下发。
4. Keylime status 能显示 runtime policy 已绑定或 IMA/PCR10 状态。
5. IMA 正常时 trusted trait 保留，trusted flavor 调度不受影响。
6. IMA 异常时 trait 移除、nova-compute 隔离、VM metadata 风险标记生效。
7. 恢复 runtime policy 后节点重新进入可信池，VM 风险 metadata 清理。
```
