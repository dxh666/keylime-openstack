# OpenStack 与 Keylime 深度结合路线图

## 1. 当前已经完成的基础闭环

当前实验已经完成第一层闭环：

```text
csri9 TPM/Keylime Agent
        |
        v
Keylime Registrar / Verifier on csri10
        |
        v
systemd timer 同步脚本
        |
        v
OpenStack Placement trait: CUSTOM_KEYLIME_ATTESTED
        |
        v
Nova trusted flavor 调度到 csri9
```

已验证结果：

- `csri9` 上 Keylime agent 可注册、可 attestation。
- `CUSTOM_KEYLIME_ATTESTED` trait 存在时，`trusted.keylime.small` 可调度到 `csri9`。
- 移除 trait 后，trusted flavor 创建虚拟机失败，Nova 返回 `No valid host was found`。
- systemd timer 已经部署成功，可以定期把 Keylime 状态同步到 Placement。

这说明链路已经打通，但还不等于“生产级可信云”。目前最大缺口是：Keylime 的验证策略还不够严格。

## 2. 总体目标

最终目标不是简单地让 Keylime 和 OpenStack “互通”，而是让 Keylime 的可信计算能力成为云平台的安全控制面之一。

目标状态：

```text
TPM / Secure Boot / Measured Boot / IMA
        |
        v
Keylime Verifier 持续验证宿主机可信状态
        |
        v
可信状态控制器同步到 OpenStack
        |
        +--> Placement traits
        +--> Host aggregates
        +--> Trusted flavors
        +--> Project / image / workload policy
        +--> 审计与告警
        |
        v
Nova 只把敏感虚拟机调度到可信宿主机
```

## 3. 深度结合的分阶段路线

### 阶段 0：链路打通，已完成

目的：证明 Keylime 的验证结果可以影响 Nova 调度。

当前状态：

- `csri9` 是可信计算节点。
- `csri8` 暂不接入，因为 SHA256 PCR bank 未启用。
- trusted flavor 使用：

```bash
openstack flavor show trusted.keylime.small
```

核心 extra spec：

```text
trait:CUSTOM_KEYLIME_ATTESTED=required
```

### 阶段 1：把空策略升级为 PCR 策略

目的：让 `CUSTOM_KEYLIME_ATTESTED` 不再表示“agent 活着”，而表示“节点 PCR 符合可信基线”。

这是下一步要做的实验。

在 `csri9` 上读取 PCR7：

```bash
tpm2_pcrread sha256:7
```

如果 PCR7 仍然是此前观测到的值：

```text
AD69DC884387BB14056F05ABC4AB0B8AA751824D909931618FB6365EE2C2938D
```

则在 `csri10` 上更新 Keylime 策略：

```bash
cd /opt/keylime-docker

export KEYLIME_AGENT_UUID_FIXED="11111111-1111-4111-8111-000000000009"
export PCR7_SHA256="AD69DC884387BB14056F05ABC4AB0B8AA751824D909931618FB6365EE2C2938D"
export TPM_POLICY_CSRI9="{\"7\":[\"${PCR7_SHA256}\"]}"

docker compose run --rm keylime-tenant \
  -c update \
  -t 172.31.100.9 \
  -tp 9002 \
  -u "$KEYLIME_AGENT_UUID_FIXED" \
  -v 172.31.100.10 \
  -vp 8881 \
  -r 172.31.100.10 \
  -rp 8891 \
  --agent-api-version 2.5 \
  --tpm_policy "$TPM_POLICY_CSRI9"
```

检查 Keylime 状态：

```bash
docker compose run --rm keylime-tenant \
  -c status \
  -u "$KEYLIME_AGENT_UUID_FIXED" \
  -v 172.31.100.10 \
  -vp 8881 \
  -r 172.31.100.10 \
  -rp 8891
```

手动运行同步脚本确认 trait 保持存在：

```bash
/opt/keylime-openstack-sync/keylime-placement-sync-locked.sh
cat /var/log/keylime-openstack-sync-last.log

source /etc/kolla/admin-openrc.sh
export OS_PLACEMENT_API_VERSION=1.17
export RP_CSRI9="$(openstack resource provider list --name csri9 -f value -c uuid)"
openstack resource provider trait list "$RP_CSRI9" | grep CUSTOM_KEYLIME_ATTESTED
```

反向验证：故意设置错误 PCR7，让 Keylime FAIL。

```bash
cd /opt/keylime-docker

export KEYLIME_AGENT_UUID_FIXED="11111111-1111-4111-8111-000000000009"
export BAD_TPM_POLICY='{"7":["0000000000000000000000000000000000000000000000000000000000000000"]}'

docker compose run --rm keylime-tenant \
  -c update \
  -t 172.31.100.9 \
  -tp 9002 \
  -u "$KEYLIME_AGENT_UUID_FIXED" \
  -v 172.31.100.10 \
  -vp 8881 \
  -r 172.31.100.10 \
  -rp 8891 \
  --agent-api-version 2.5 \
  --tpm_policy "$BAD_TPM_POLICY"
```

等待 systemd timer 自动同步，或手动触发：

```bash
/opt/keylime-openstack-sync/keylime-placement-sync-locked.sh
cat /var/log/keylime-openstack-sync-last.log
```

预期结果：

- Keylime agent 状态变为失败或非 PASS。
- `CUSTOM_KEYLIME_ATTESTED` 从 `csri9` 上被移除。
- 使用 `trusted.keylime.small` 创建虚拟机失败，Nova 返回 `No valid host was found`。

恢复策略：

```bash
cd /opt/keylime-docker

export KEYLIME_AGENT_UUID_FIXED="11111111-1111-4111-8111-000000000009"
export PCR7_SHA256="AD69DC884387BB14056F05ABC4AB0B8AA751824D909931618FB6365EE2C2938D"
export TPM_POLICY_CSRI9="{\"7\":[\"${PCR7_SHA256}\"]}"

docker compose run --rm keylime-tenant \
  -c update \
  -t 172.31.100.9 \
  -tp 9002 \
  -u "$KEYLIME_AGENT_UUID_FIXED" \
  -v 172.31.100.10 \
  -vp 8881 \
  -r 172.31.100.10 \
  -rp 8891 \
  --agent-api-version 2.5 \
  --tpm_policy "$TPM_POLICY_CSRI9"

docker compose run --rm keylime-tenant \
  -c reactivate \
  -u "$KEYLIME_AGENT_UUID_FIXED" \
  -v 172.31.100.10 \
  -vp 8881 \
  -r 172.31.100.10 \
  -rp 8891
```

然后等待 timer 恢复 trait。

### 阶段 2：引入 attestation freshness

目的：避免使用历史 PASS。

现在同步脚本只看 `attestation_status=PASS` 还不够。生产中必须检查：

- `last_successful_attestation` 是否存在；
- 距离当前时间是否小于阈值，例如 60 秒或 120 秒；
- agent 是否处于可达状态；
- verifier 是否仍在持续轮询。

建议新增逻辑：

```text
只有满足以下条件才加 CUSTOM_KEYLIME_ATTESTED：
1. attestation_status == PASS
2. last_successful_attestation 未过期
3. operational_state 不是 Failed / Terminated
4. agent UUID 与 OpenStack compute host 映射一致
```

### 阶段 3：Measured Boot 策略

目的：从“PCR 固定值匹配”升级到“启动事件日志可解释、可重放、可审计”。

PCR 直接比较适合小规模实验，但生产环境中不同机器、不同固件、不同启动路径会导致 PCR 值差异很大。Keylime 官方文档也说明，大规模部署中仅靠 PCR 值可能不够，应使用 UEFI event log 做 measured boot。

要验证：

```bash
ls -l /sys/kernel/security/tpm0/binary_bios_measurements
```

然后逐步引入：

- Secure Boot 状态检查；
- UEFI event log；
- kernel、initrd、grub、shim 相关测量；
- Keylime measured boot policy；
- 不同硬件型号的 reference state 管理。

### 阶段 4：IMA Runtime Integrity

目的：从“启动时可信”扩展到“运行时文件完整性可信”。

Keylime Runtime Policy 用于检查 IMA 度量日志。官方文档说明，runtime policy 本质上是一组文件黄金哈希和 keyring 规则。Verifier 会校验 agent 的 PCR10 与 IMA log；如果出现未允许文件、文件篡改或未知 keyring，agent 会进入 failed 状态。

实验方向：

- 在 `csri9` 开启 IMA measurement。
- 生成 runtime policy。
- 将 runtime policy 下发给 Keylime verifier。
- 执行一个不在 policy 中的 root 脚本。
- 验证 Keylime FAIL。
- 验证 Placement trait 自动移除。
- 验证 trusted workload 无法继续调度到该节点。

### 阶段 5：可信云产品化

目的：把可信能力暴露成云平台能力，而不是管理员手工操作。

建议做法：

- 创建私有 flavor：`trusted.keylime.small`。
- 只授权给需要可信计算的 project。
- 普通 project 无法看到或使用 trusted flavor。
- 镜像增加可信需求元数据，例如：

```text
hw:trusted_host_required=true
```

后续可以研究是否用 image property + scheduler prefilter 或自定义策略来表达可信需求。

### 阶段 6：安全分层 trait

目的：不要只有一个粗粒度的 `CUSTOM_KEYLIME_ATTESTED`。

建议逐步拆成多级 trait：

```text
CUSTOM_KEYLIME_AGENT_ONLINE
CUSTOM_KEYLIME_TPM_QUOTE_VALID
CUSTOM_KEYLIME_PCR_POLICY_VALID
CUSTOM_KEYLIME_MEASURED_BOOT_VALID
CUSTOM_KEYLIME_IMA_VALID
CUSTOM_KEYLIME_EK_CERT_VALID
CUSTOM_KEYLIME_PRODUCTION_TRUSTED
```

不同 flavor 使用不同要求：

```text
trusted.basic      -> CUSTOM_KEYLIME_PCR_POLICY_VALID
trusted.boot       -> CUSTOM_KEYLIME_MEASURED_BOOT_VALID
trusted.runtime    -> CUSTOM_KEYLIME_IMA_VALID
trusted.production -> CUSTOM_KEYLIME_PRODUCTION_TRUSTED
```

### 阶段 7：失信处置

目的：节点一旦失信，不只是移除 trait，还要进入云平台运维流程。

建议策略：

- 立即移除可信 trait。
- 禁用 nova-compute 或把节点从可信 host aggregate 移除。
- 新建 workload 不再调度到该节点。
- 已运行虚拟机标记为 running-on-untrusted-host。
- 触发告警。
- 人工确认后再恢复。

不建议第一阶段自动迁移虚拟机，因为迁移可能带来更大的故障面。

### 阶段 8：多节点接入

目的：从 `csri9` 单节点扩展到可信计算池。

2026-07-04 更新：`csri8` 的 TPM/SHA256 PCR 问题已经修复，并已接入 Keylime/OpenStack 可信池。后续 Case 8 已验证：

```text
csri8/csri9 同时可信时，trusted VM 可调度到两个节点。
csri8 Keylime agent 停止后，csri8 自动移除可信 trait 并禁用 nova-compute。
trusted VM 自动避开 csri8，继续调度到 csri9。
csri8 agent 恢复后，csri8 重新加入可信池并可再次承载 trusted VM。
```

历史上接入前必须先解决 `csri8` 问题：

- TPM 2.0 存在；
- 但 SHA256 PCR bank 当前为空；
- 不应直接加入可信池；
- 需要在 BIOS/UEFI 或 TPM 设置中启用 SHA256 PCR bank，并重新验证。

多节点映射建议：

```text
compute host -> management IP -> Keylime UUID -> Placement RP UUID -> trait set
```

例如：

```text
csri9 -> 172.31.100.9 -> 11111111-1111-4111-8111-000000000009 -> RP_CSRI9 -> traits
csri8 -> 172.31.100.8 -> future UUID -> RP_CSRI8 -> traits
```

### 阶段 9：最小权限与生产化

当前脚本使用 `admin-openrc.sh`，生产中不合适。

需要改造：

- 创建专用 Keystone 用户，例如 `keylime-placement-sync`。
- 只允许它操作 Placement resource provider traits。
- 不授予 Nova/Neutron/Glance/Cinder 管理权限。
- Keylime API、OpenStack API、同步脚本日志全部纳入审计。
- 避免使用 `latest` 镜像。
- 修复 TPM 设备权限，不再使用 `chmod a+rw /dev/tpm*`。
- 恢复 EK certificate / IAK / IDevID 等强身份证明能力。

## 4. 推荐实验顺序

推荐接下来严格按这个顺序推进：

1. PCR 策略实验：证明策略影响 Keylime PASS/FAIL。
2. PCR 策略反向验证：错误策略导致 trait 移除和 trusted flavor 调度失败。
3. 同步脚本 freshness 改造：禁止历史 PASS。
4. trusted flavor 私有化：只有指定 project 可用。
5. measured boot 实验：从 PCR 固定值升级到启动日志策略。
6. IMA runtime 实验：检测运行时文件篡改。
7. 多 trait 分层：把可信能力拆成不同安全等级。
8. csri8 修复并加入可信池。
9. 最小权限账户替代 admin-openrc。
10. 形成生产化可信云架构。

## 5. 关键认识

OpenStack 的 project 解决的是租户资源边界，不解决宿主机是否可信。

Keylime 解决的是宿主机可信状态证明，但它本身不负责云调度。

Placement trait 是二者之间最自然的结合点：

```text
Keylime 判断可信
OpenStack Placement 表达可信
Nova Scheduler 使用可信
Flavor / Project 消费可信
```

真正的可信云不是“装了 Keylime”，而是：

```text
可信证明 -> 策略判断 -> 云平台资源表达 -> 调度强制执行 -> 失信处置 -> 审计追踪
```

