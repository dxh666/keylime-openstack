# Case 10B: TPM PCR 策略管理控制面

更新时间：2026-07-04

## 1. 目标

Case 10B 的目标不是再手工复制 `keylime-tenant --tpm_policy` 命令，而是把 TPM PCR 策略管理做成 OpenStack/Keylime 可信控制面的一部分。

本阶段形成的能力：

```text
TPM evidence baseline 采集
  -> 从 baseline 自动生成 PCR policy
  -> 策略入库、版本化、按节点绑定
  -> 统一下发或单节点下发
  -> Keylime 决策联动 OpenStack trait / nova-compute / VM 风险标记
```

本阶段优先做完“启动过程度量可信”。管理系统中的策略管理已经按 Keylime 能力拆成“启动度量策略”和“运行时完整性策略”两个模块；运行时完整性后续进入 IMA runtime policy 阶段。

## 2. 实验结论

### 2.1 PCR7 是当前稳定准入策略

当前 csri8/csri9 的 SHA256 PCR7 一致：

```text
AD69DC884387BB14056F05ABC4AB0B8AA751824D909931618FB6365EE2C2938D
```

PCR7 策略闭环已经验证通过：

```text
正确 PCR7 policy
  -> Keylime PASS_FRESH
  -> Placement trusted trait 存在
  -> nova-compute enabled/up
  -> trusted flavor 可调度

错误 PCR7 policy
  -> Keylime NOT_PASS
  -> Placement trusted trait 移除
  -> nova-compute disabled/up
  -> trusted flavor 无法调度到失信节点
  -> 失信节点上的已有 VM 写入 keylime_trust_* metadata

恢复正确 PCR7 policy
  -> 节点重新进入可信池
  -> VM 风险 metadata 自动清理
```

### 2.2 PCR0-7 精确策略暂作为诊断项

TPM evidence baseline 显示：

```text
PCR0, PCR2, PCR3, PCR4, PCR6, PCR7 一致
PCR1, PCR5 不一致
```

按节点渲染精确 PCR0-7 policy 后，Keylime verifier 曾出现：

```text
operational_state: Invalid Quote
last_event_id: measured_boot.parser.tpm2_eventlog.warning
```

因此第一版控制面不把 PCR0-7 exact policy 作为生产准入策略，而是保留为 Case 10B/Case 11 的 measured boot 诊断入口。

## 3. 新增脚本

```text
deploy/scripts/keylime-tpm-evidence-audit.sh
  采集 OpenStack compute 节点 TPM 证据基线。

deploy/scripts/keylime-tpm-pcr-policy-render-from-baseline.sh
  从 baseline 自动生成策略：
    <host>-sha256-pcr7-baseline
    <host>-sha256-pcr0-7-exact
  默认把每个 host 绑定到自己的 PCR7 baseline。

deploy/scripts/keylime-tpm-pcr-policy-apply.sh
  从策略库读取策略并下发：
    keylime-tpm-pcr-policy-apply.sh all bound
    keylime-tpm-pcr-policy-apply.sh csri8 csri8-sha256-pcr7-baseline
```

## 4. 策略文件与审计文件

```text
/var/log/keylime-openstack-tpm-evidence-baseline.json
  TPM evidence baseline。

/var/lib/keylime-openstack-sync/tpm-pcr-policies.json
  前端/API 使用的 PCR 策略库。

/var/lib/keylime-openstack-sync/policies/
  profile、rendered policy、history。

/var/log/keylime-openstack-policy-render.json
  从 baseline 渲染策略的审计文件。

/var/log/keylime-openstack-policy-apply.json
  策略下发审计文件。
```

## 5. 管理系统 API

```text
GET  /api/policies
  查看策略库、节点绑定、baseline 摘要和审计文件。

GET  /api/policies/baseline
  查看 TPM evidence baseline 摘要。

POST /api/policies/import-baseline
  从 TPM evidence baseline 导入 PCR7/PCR0-7 策略并绑定节点。

POST /api/policies/apply
  手工选择一个策略，下发到选中节点或所有节点。

POST /api/policies/apply-bound
  按每个节点当前绑定的策略批量下发。
```

## 6. 管理系统前端

策略管理页面不再把所有 Keylime 策略揉在一起，而是拆成：

```text
启动度量策略
  当前已实现。默认页面只保留 TPM evidence baseline、策略导入和绑定策略下发。

运行时完整性策略
  已预留独立入口。后续基于 Keylime IMA runtime policy / PCR10 / runtime measurements 实现。
```

启动度量策略页面提供：

```text
刷新基线
从基线导入策略
按绑定策略下发
```

策略 JSON 编辑、模板和手动下发被收进“高级策略编辑与手动下发”折叠区，避免日常操作界面过重。管理系统不提供破坏性策略下发或负向验证入口；生产运维只通过标准策略导入、绑定和下发流程操作。

## 7. 推荐实验流程

在 csri10 上执行：

```bash
source /etc/keylime-openstack-sync/openstack-keylime-lab.env

/opt/keylime-openstack-sync/keylime-tpm-evidence-audit.sh

/opt/keylime-openstack-sync/keylime-tpm-pcr-policy-render-from-baseline.sh

/opt/keylime-openstack-sync/keylime-tpm-pcr-policy-apply.sh all bound
```

需要重新下发某个节点绑定策略时：

```bash
/opt/keylime-openstack-sync/keylime-tpm-pcr-policy-apply.sh csri8 csri8-sha256-pcr7-baseline
sleep 40
systemctl start keylime-openstack-sync.service || true
KEYLIME_VM_RISK_MARKER_FORCE=true /opt/keylime-openstack-sync/keylime-vm-risk-marker.sh
```

## 8. 下一步

Case 10B 后续应继续诊断 PCR0-7 exact policy 触发的：

```text
measured_boot.parser.tpm2_eventlog.warning
```

这会自然进入 Case 11：measured boot policy。届时不再直接依赖 PCR0-7 固定值，而是使用 UEFI event log 和 reference state 解释 PCR。

运行时完整性会进入后续 IMA case：采集宿主机运行时文件度量基线，生成 Keylime runtime policy，并让该决策继续联动 OpenStack trait、调度、隔离和 VM 风险标记。
