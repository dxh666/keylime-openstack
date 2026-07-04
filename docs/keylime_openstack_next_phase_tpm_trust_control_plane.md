# 下一阶段目标：TPM 驱动的 Keylime/OpenStack 可信控制面

更新时间：2026-07-04

## 1. 一句话目标

下一阶段不再围绕某个单点小功能推进，而是把 Keylime 的 TPM attestation 能力系统性接入 OpenStack，使 TPM 成为 OpenStack 计算节点可信状态、调度准入、失信处置和审计闭环的底层证据来源。

目标不是“OpenStack 页面上能看到 Keylime 状态”，而是：

```text
TPM 产生硬件可信证据
  -> Keylime 持续验证证据是否符合策略
  -> OpenStack 消费 Keylime 决策
  -> Nova/Placement/Flavor/Project 执行可信调度和隔离
  -> 失信节点上的存量 VM 被标记、审计、后续处置
```

## 2. 当前已经具备的基础能力

当前实验环境已经形成第一版可信云控制闭环：

```text
csri8 / csri9 TPM 2.0
  -> Keylime agent
  -> csri10 Keylime registrar / verifier / tenant
  -> keylime-openstack-sync.timer
  -> Placement trait: CUSTOM_KEYLIME_ATTESTED
  -> trusted flavor 调度约束
  -> nova-compute 自动隔离/恢复
  -> 存量 VM keylime_trust_* 风险标记
  -> 前端可信状态监控
```

已完成能力包括：

- 自动发现 OpenStack nova-compute 节点。
- 自动匹配已注册 Keylime agent。
- 多节点 csri8/csri9 attestation 状态同步。
- `PASS_FRESH` 才添加 `CUSTOM_KEYLIME_ATTESTED`。
- Keylime 失信或 freshness 过期时移除 trusted trait。
- Keylime 失信时禁用对应 `nova-compute`，阻止新 trusted workload 调度。
- Keylime 恢复后，在确认由控制器禁用的前提下自动恢复 compute service。
- trusted flavor 通过 Placement trait 只调度到可信节点。
- trusted private flavor 可限制给指定 project。
- 存量 VM 在宿主机失信时自动写入 `keylime_trust_*` metadata，恢复后自动清理。
- 前端可展示计算节点 IP、承载 VM 数量、可信状态和服务状态。

这些能力证明了 Keylime 已经可以影响 OpenStack 的资源表达和调度行为。

## 3. 当前最大缺口

当前系统已经把 Keylime 用进 OpenStack，但还没有把 TPM 的价值完全用出来。

主要缺口：

```text
TPM 证据层还不够完整
  当前主要消费 Keylime PASS/FAIL/freshness，没有系统沉淀 TPM quote、PCR bank、Secure Boot、measured boot、IMA 等证据。

Keylime 策略层还不够正式
  TPM PCR 策略已有管理雏形，但还没有形成节点基线、策略版本、批量下发、回滚、漂移审计和 measured boot / IMA 策略体系。

OpenStack 表达层仍然偏粗粒度
  当前主要用 CUSTOM_KEYLIME_ATTESTED 一个 trait，后续应拆分为不同安全等级和不同可信能力。

失信处置还停留在第一版
  已能隔离节点和标记 VM，但还没有告警、人工确认、迁移建议、审批恢复和完整审计流程。
```

因此，下一阶段应把 Keylime 从“可信状态同步脚本”提升为“OpenStack 可信控制面”。

## 4. 下一阶段总架构

```text
TPM Evidence Layer
  TPM quote
  EK / AK identity
  SHA256 PCR bank
  Secure Boot state
  UEFI measured boot event log
  IMA runtime measurement
        |
        v
Keylime Policy Layer
  PCR policy
  measured boot policy
  IMA runtime policy
  policy profile
  policy version
  rollout / rollback
        |
        v
Keylime Decision Layer
  agent registered
  agent reachable
  quote valid
  PCR policy valid
  measured boot valid
  IMA valid
  attestation fresh
        |
        v
OpenStack Trust Expression Layer
  Placement traits
  trusted flavors
  private trusted flavors
  host aggregates
  VM metadata markers
        |
        v
OpenStack Enforcement Layer
  scheduler placement
  nova-compute quarantine
  trusted project access
  existing VM risk marking
  audit and alert
```

## 5. Case 10 建议目标：TPM 证据基线与 Keylime 策略控制面

下一阶段建议命名为：

```text
Case 10: TPM 证据基线与 Keylime 策略驱动的 OpenStack 可信控制面
```

它不是一个前端功能，而是一组基础能力建设。

### 5.1 第一小步：沉淀 TPM 证据基线

目的：先看清楚 csri8/csri9 的 TPM 能提供哪些证据。

需要采集：

- TPM 固定属性和厂商信息。
- TPM owner / endorsement / lockout auth 状态。
- SHA256 PCR 0-7 当前值。
- 是否存在 SHA1 PCR bank。
- Secure Boot 状态。
- UEFI measured boot event log 是否存在。
- IMA measurement 文件是否存在。
- Keylime verifier 对每个 agent 的当前判断。
- OpenStack 当前 trait、compute service、VM 分布。

建议在 csri10 上执行的起始检查：

```bash
source /etc/keylime-openstack-sync/openstack-keylime-lab.env
source /etc/kolla/admin-openrc.sh
export OS_PLACEMENT_API_VERSION=1.17

echo "=== Current integrated capability ==="
/opt/keylime-openstack-sync/keylime-openstack-capability-check.sh

echo
echo "=== TPM evidence baseline on compute nodes ==="
for h in 172.31.100.8 172.31.100.9; do
  echo
  echo "=== $h ==="
  ssh root@$h '
    hostname
    echo "--- TPM fixed properties ---"
    tpm2_getcap properties-fixed | egrep -i "FAMILY|LEVEL|REVISION|MANUFACTURER|VENDOR|FIRMWARE|HR_TRANSIENT|PCR" || true

    echo "--- TPM variable properties ---"
    tpm2_getcap properties-variable | egrep -i "ownerAuthSet|endorsementAuthSet|lockoutAuthSet|disableClear|inLockout|LOCKOUT_COUNTER|MAX_AUTH_FAIL" || true

    echo "--- PCR sha256 0-7 ---"
    tpm2_pcrread sha256:0,1,2,3,4,5,6,7

    echo "--- PCR sha1 0-7, if available ---"
    tpm2_pcrread sha1:0,1,2,3,4,5,6,7 || true

    echo "--- measured boot event log ---"
    ls -l /sys/kernel/security/tpm0/binary_bios_measurements 2>/dev/null || echo "NO_TPM_EVENT_LOG"

    echo "--- IMA runtime measurements ---"
    ls -l /sys/kernel/security/ima/ascii_runtime_measurements 2>/dev/null || echo "NO_IMA_ASCII_LOG"
    ls -l /sys/kernel/security/ima/binary_runtime_measurements 2>/dev/null || echo "NO_IMA_BINARY_LOG"

    echo "--- Secure Boot state ---"
    mokutil --sb-state 2>/dev/null || bootctl status 2>/dev/null | egrep -i "Secure Boot|Boot Loader" || echo "SECURE_BOOT_STATE_UNKNOWN"
  '
done
```

第一小步的产物应该是一份证据清单，而不是马上改策略。只有先知道硬件、固件、启动链和内核支持状态，后续策略才不会盲目。

### 5.2 第二小步：建立节点策略 Profile

将节点可信策略从零散命令升级为 profile：

```text
profile: csri-lab-basic-pcr
scope: csri8, csri9
policy_type: tpm_pcr
pcr_bank: sha256
pcrs: 0,1,2,3,4,5,6,7
strictness: lab
rollout: manual approval
rollback: previous known-good policy
```

第一版可以继续用 JSON 文件存储，后续再接数据库。

建议目录：

```text
/var/lib/keylime-openstack-sync/policies/
  profiles/
  rendered/
  history/
```

仓库侧建议后续新增：

```text
deploy/policies/examples/
deploy/scripts/keylime-policy-profile-render.sh
deploy/scripts/keylime-policy-apply.sh
deploy/scripts/keylime-policy-audit.sh
```

### 5.3 第三小步：策略下发与 OpenStack 联动

策略下发不应只是调用 `keylime-tenant update`。

完整流程应是：

```text
生成策略
  -> 校验策略 JSON
  -> 保存策略版本
  -> 对目标 agent 执行 keylime-tenant update
  -> reactivate agent
  -> 等待 Keylime 新 attestation
  -> 读取 verifier 结果
  -> 同步 Placement trait
  -> 观察 Nova 调度结果
  -> 写入审计文件
```

这一步完成后，OpenStack 的可信节点状态才真正由 TPM 策略驱动。

### 5.4 第四小步：错误策略反向验证

必须保留反向验证，因为它能证明 TPM 策略不是摆设。

实验方式：

```text
给 csri8 或 csri9 下发错误 PCR policy
  -> Keylime attestation 失败
  -> 对应 Placement trait 被移除
  -> nova-compute 被隔离
  -> trusted flavor 无法调度到该节点
  -> 该节点存量 VM 被打 keylime_trust_* 风险标记
```

再恢复正确策略：

```text
恢复正确 PCR policy
  -> reactivate
  -> Keylime PASS_FRESH
  -> trait 恢复
  -> compute service 恢复
  -> VM 风险 metadata 清理
```

这才算“TPM 赋能 OpenStack”的可验证闭环。

## 6. 后续能力路线

Case 10 完成后，继续推进：

```text
Case 11: measured boot policy
  用 UEFI event log 解释 PCR，而不是只比较固定 PCR 值。

Case 12: IMA runtime integrity
  用 Keylime runtime policy 检测宿主机运行时关键文件变化。

Case 13: 分层 trusted traits
  把 CUSTOM_KEYLIME_ATTESTED 拆为 TPM quote、PCR policy、measured boot、IMA、production trusted 等多级能力。

Case 14: 可信 project / image / workload policy
  将可信需求从 flavor 扩展到 project 和 image 元数据。

Case 15: 生产化安全加固
  最小权限 OpenStack service account、固定镜像版本、TPM 设备权限收敛、EK/证书链策略、告警与审批。
```

## 7. 下一阶段验收标准

Case 10 的验收标准建议定为：

```text
1. 能一键采集 csri8/csri9 TPM 证据基线，并生成审计文件。
2. 能为 csri8/csri9 生成可追踪版本的 TPM PCR policy。
3. 能统一下发同一策略到所有可信计算节点。
4. 能单独下发策略到指定计算节点。
5. 正确策略下，Keylime PASS_FRESH，OpenStack trusted trait 保持存在。
6. 错误策略下，Keylime NOT_PASS，OpenStack trusted trait 自动移除。
7. 错误策略下，trusted flavor 无法调度到失信节点。
8. 错误策略下，失信节点上已有 VM 自动出现 keylime_trust_* metadata。
9. 恢复正确策略后，节点重新进入可信池，VM 风险 metadata 自动清理。
10. 所有关键动作都有日志和 JSON 审计文件。
```

## 8. 给新窗口的快速判断

如果新开 Codex 窗口继续这个仓库，优先阅读顺序是：

```text
1. README.md
2. docs/keylime_openstack_next_phase_tpm_trust_control_plane.md
3. docs/keylime_openstack_integrated_capability_audit.md
4. docs/keylime_openstack_case8_dynamic_trusted_pool.md
5. docs/keylime_openstack_case9_vm_risk_marker.md
6. docs/openstack_keylime_deep_integration_roadmap.md
```

然后从 Case 10 第一小步开始：采集 TPM 证据基线。

