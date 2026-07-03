# Keylime 与 OpenStack 结合实验实践方案

日期：2026-07-02

## 1. 实验目标

本实验的目标不是一次性把 Keylime 做成 OpenStack 的内置服务，而是先实现一个清晰、可验证、可扩展的闭环：

```text
Keylime attestation PASS/FAIL
        |
        v
OpenStack Placement custom trait
        |
        v
Nova flavor required trait
        |
        v
指定 OpenStack project 只能把可信 workload 调度到可信 compute 节点
```

最终你要验证三件事：

1. OpenStack `project` 可以控制哪些用户能看到和使用“可信 flavor”。
2. Nova/Placement 可以根据 `CUSTOM_KEYLIME_ATTESTED` trait 把实例只调度到可信 compute 节点。
3. Keylime verifier 的 attestation 状态变化可以自动驱动 OpenStack trait 增删，从而影响后续 VM 调度。

这个方案的定位是“第一阶段集成”：阻止新的可信实例落到不可信宿主机。它不自动迁移已运行实例，也不等价于 guest 内部完整性证明。

## 2. 核心原理

OpenStack Keystone 中的 `project` 是资源和身份对象的隔离容器。用户在某个 project 上被授予角色后，OpenStack 服务会结合 token、角色、policy 和资源里的 `project_id` 判断能否访问。

Keylime 负责判断宿主机是否可信。Keylime agent 运行在需要被证明的机器上；registrar 管理 agent 注册；verifier 持续验证 TPM quote、PCR、UEFI measured boot、IMA 日志等证据。

把两者接起来的关键不是改 Nova，而是使用 OpenStack Placement 的 trait：

- Keylime PASS：给该 compute 节点的 Placement resource provider 加上 `CUSTOM_KEYLIME_ATTESTED`
- Keylime FAIL/PENDING/UNKNOWN：移除 `CUSTOM_KEYLIME_ATTESTED`
- 可信 flavor：设置 `trait:CUSTOM_KEYLIME_ATTESTED=required`
- 可信 project：只把这个 private flavor 授权给 `proj-secure`

## 3. 推荐实验拓扑

最小拓扑可以用 1 个 controller + 1 个 compute；推荐用 1 个 controller + 2 个 compute，因为更容易观察调度差异。

```text
+-----------------------------+
| controller                   |
| - Keystone                   |
| - Nova API / Scheduler       |
| - Placement                  |
| - Glance / Neutron           |
| - Keylime registrar          |
| - Keylime verifier           |
| - keylime-openstack-sync     |
+-----------------------------+
       | mgmt network
       |
+-----------------------------+        +-----------------------------+
| compute-1                   |        | compute-2                   |
| - nova-compute              |        | - nova-compute              |
| - keylime_agent             |        | - keylime_agent             |
| - TPM 2.0 or vTPM/swtpm     |        | - TPM 2.0 or vTPM/swtpm     |
+-----------------------------+        +-----------------------------+
```

建议变量：

```bash
export OS_CLOUD=admin
export OS_PLACEMENT_API_VERSION=1.17

export SECURE_PROJECT=proj-secure
export NORMAL_PROJECT=proj-normal
export SECURE_USER=alice
export NORMAL_USER=bob

export TRUSTED_TRAIT=CUSTOM_KEYLIME_ATTESTED
export TRUSTED_FLAVOR=trusted.small
export NORMAL_FLAVOR=m1.small

export KEYLIME_VERIFIER_HOST=192.168.10.10
export KEYLIME_VERIFIER_PORT=8881
export KEYLIME_REGISTRAR_HOST=192.168.10.10
export KEYLIME_REGISTRAR_PORT=8891
```

## 4. 前置条件

OpenStack 侧：

- 已部署 Keystone、Nova、Placement、Glance、Neutron。
- controller 上可使用 admin 凭据运行 `openstack` CLI。
- 已安装 `osc-placement`，能运行 `openstack resource provider list`。
- 至少有一个可用镜像、网络和 keypair。

Keylime 侧：

- controller 或独立管理节点运行 `keylime_registrar` 和 `keylime_verifier`。
- 每台 compute 节点运行 `keylime_agent`。
- compute 节点有 TPM 2.0。实验环境可使用 vTPM/swtpm，但这只适合验证流程，不代表生产级硬件信任根。
- controller 能访问 compute 上的 Keylime agent 端口。
- Keylime 组件的 mTLS 证书已配置好。

安全前提：

- 本实验假设 OpenStack admin 和 Keylime 管理面是可信的。
- 本实验保护的是“新建可信 VM 的调度位置”，不是对已经运行 VM 的自动处置。

## 5. Phase A：验证 project 边界

这一阶段先不接 Keylime，只验证 `project` 能否作为“可信资源入口”的边界。

### A.1 创建 project、用户和角色

```bash
openstack project create "$SECURE_PROJECT"
openstack project create "$NORMAL_PROJECT"

openstack user create --password pass "$SECURE_USER"
openstack user create --password pass "$NORMAL_USER"

openstack role add --project "$SECURE_PROJECT" --user "$SECURE_USER" member
openstack role add --project "$NORMAL_PROJECT" --user "$NORMAL_USER" member
```

### A.2 创建只给 secure project 使用的 private flavor

```bash
openstack flavor create --private "$TRUSTED_FLAVOR" \
  --ram 1024 \
  --disk 10 \
  --vcpus 1

openstack flavor set --project "$SECURE_PROJECT" "$TRUSTED_FLAVOR"
```

验证：

```bash
openstack --os-username "$SECURE_USER" \
  --os-password pass \
  --os-project-name "$SECURE_PROJECT" \
  flavor list

openstack --os-username "$NORMAL_USER" \
  --os-password pass \
  --os-project-name "$NORMAL_PROJECT" \
  flavor list
```

预期结果：

- Alice 在 `proj-secure` 中能看到 `trusted.small`。
- Bob 在 `proj-normal` 中看不到 `trusted.small`，也不能用它创建实例。

结论：

`project` 可以作为可信资源的授权入口，但它本身不知道宿主机是否可信。

## 6. Phase B：手工验证 Placement trait 调度

这一阶段先人工给 compute 节点打可信 trait，确认 Nova 调度链路能工作。

### B.1 找到 compute 对应的 resource provider

```bash
openstack compute service list --service nova-compute
openstack hypervisor list
openstack resource provider list
```

通常 Placement resource provider 的名字等于 hypervisor hostname。假设：

```bash
export COMPUTE1_RP_NAME=compute-1
export COMPUTE2_RP_NAME=compute-2

export COMPUTE1_RP_UUID=$(openstack resource provider list \
  --name "$COMPUTE1_RP_NAME" \
  -f value -c uuid)

export COMPUTE2_RP_UUID=$(openstack resource provider list \
  --name "$COMPUTE2_RP_NAME" \
  -f value -c uuid)
```

检查：

```bash
openstack resource provider show "$COMPUTE1_RP_UUID"
openstack resource provider show "$COMPUTE2_RP_UUID"
```

### B.2 创建自定义 trait

```bash
openstack trait create "$TRUSTED_TRAIT" || true
openstack trait show "$TRUSTED_TRAIT"
```

### B.3 给 compute-1 打可信 trait

注意：`resource provider trait set` 是全量替换，不是追加。实验时可以先只打在空 trait 的节点上；生产脚本必须先读取已有 traits，再合并。

```bash
openstack resource provider trait list "$COMPUTE1_RP_UUID"

openstack resource provider trait set \
  --trait "$TRUSTED_TRAIT" \
  "$COMPUTE1_RP_UUID"

openstack resource provider trait list "$COMPUTE1_RP_UUID"
```

如果 compute-1 已有其他 traits，使用下面这种思路保留原 trait：

```bash
EXISTING_TRAITS=$(openstack resource provider trait list "$COMPUTE1_RP_UUID" -f value -c name)

CMD="openstack resource provider trait set"
for t in $EXISTING_TRAITS; do
  CMD="$CMD --trait $t"
done
CMD="$CMD --trait $TRUSTED_TRAIT $COMPUTE1_RP_UUID"
eval "$CMD"
```

### B.4 给 trusted flavor 加 required trait

```bash
openstack flavor set "$TRUSTED_FLAVOR" \
  --property trait:"$TRUSTED_TRAIT"=required

openstack flavor show "$TRUSTED_FLAVOR" -c properties
```

Nova 会把 flavor 中的 required traits 传给 Placement allocation candidates，只返回满足这些 traits 的 provider。

### B.5 创建可信 VM

准备网络、镜像、keypair。以下变量按你的环境替换：

```bash
export IMAGE=cirros
export NETWORK=private
export KEYPAIR=demo-key
```

用 secure project 创建实例：

```bash
openstack --os-username "$SECURE_USER" \
  --os-password pass \
  --os-project-name "$SECURE_PROJECT" \
  server create secure-vm-1 \
  --image "$IMAGE" \
  --flavor "$TRUSTED_FLAVOR" \
  --network "$NETWORK" \
  --key-name "$KEYPAIR"
```

验证 VM 落点：

```bash
openstack server show secure-vm-1 \
  -c status \
  -c OS-EXT-SRV-ATTR:host \
  -c OS-EXT-SRV-ATTR:hypervisor_hostname
```

预期结果：

- `secure-vm-1` 只能落在拥有 `CUSTOM_KEYLIME_ATTESTED` 的 compute-1。

### B.6 移除 trait，验证调度失败

```bash
openstack resource provider trait set "$COMPUTE1_RP_UUID"

openstack --os-username "$SECURE_USER" \
  --os-password pass \
  --os-project-name "$SECURE_PROJECT" \
  server create secure-vm-should-fail \
  --image "$IMAGE" \
  --flavor "$TRUSTED_FLAVOR" \
  --network "$NETWORK"
```

预期结果：

- 如果没有任何 compute 节点带 `CUSTOM_KEYLIME_ATTESTED`，Nova 应返回 `No valid host` 或实例进入 `ERROR`。

结论：

OpenStack 侧已经具备“可信 flavor 只能去可信 compute”的调度能力。下一步只需要让 Keylime 自动维护这个 trait。

## 7. Phase C：部署并验证 Keylime

这一阶段让 Keylime 独立工作，暂时不操作 OpenStack。

### C.1 启动 Keylime 服务

controller：

```bash
sudo systemctl enable --now keylime_registrar
sudo systemctl enable --now keylime_verifier

sudo systemctl status keylime_registrar --no-pager
sudo systemctl status keylime_verifier --no-pager
```

compute 节点：

```bash
sudo systemctl enable --now keylime_agent
sudo systemctl status keylime_agent --no-pager
```

如果你的发行版使用 Rust agent，服务名和配置文件可能是 `keylime-agent` 与 `/etc/keylime/agent.conf`。以实际包为准。

### C.2 注册 compute agent

在 controller 上：

```bash
sudo keylime_tenant -c add \
  -t <compute-1-mgmt-ip> \
  -u compute-1 \
  -v "$KEYLIME_VERIFIER_HOST" \
  -vp "$KEYLIME_VERIFIER_PORT" \
  -r "$KEYLIME_REGISTRAR_HOST" \
  -rp "$KEYLIME_REGISTRAR_PORT"

sudo keylime_tenant -c status -u compute-1
sudo keylime_tenant -c cvlist
```

如果有 compute-2：

```bash
sudo keylime_tenant -c add \
  -t <compute-2-mgmt-ip> \
  -u compute-2 \
  -v "$KEYLIME_VERIFIER_HOST" \
  -vp "$KEYLIME_VERIFIER_PORT" \
  -r "$KEYLIME_REGISTRAR_HOST" \
  -rp "$KEYLIME_REGISTRAR_PORT"

sudo keylime_tenant -c status -u compute-2
```

预期结果：

- `status` 或 `cvlist` 能看到 agent。
- 正常情况下 attestation 状态最终为 PASS。

### C.3 使用 Keylime REST API 查询状态

Keylime v2.5 在 `GET /v2.5/agents/{agent_id}` 响应中包含 `attestation_status`，值通常为 `PASS`、`FAIL` 或 `PENDING`。

mTLS 环境中，curl 需要带 CA、client cert 和 client key。路径按你的部署调整：

```bash
export KEYLIME_CA=/var/lib/keylime/cv_ca/cacert.crt
export KEYLIME_CLIENT_CERT=/var/lib/keylime/cv_ca/client-cert.crt
export KEYLIME_CLIENT_KEY=/var/lib/keylime/cv_ca/client-private.pem

curl -sS \
  --cacert "$KEYLIME_CA" \
  --cert "$KEYLIME_CLIENT_CERT" \
  --key "$KEYLIME_CLIENT_KEY" \
  "https://$KEYLIME_VERIFIER_HOST:$KEYLIME_VERIFIER_PORT/v2.5/agents/compute-1" | jq .
```

提取状态：

```bash
curl -sS \
  --cacert "$KEYLIME_CA" \
  --cert "$KEYLIME_CLIENT_CERT" \
  --key "$KEYLIME_CLIENT_KEY" \
  "https://$KEYLIME_VERIFIER_HOST:$KEYLIME_VERIFIER_PORT/v2.5/agents/compute-1" \
  | jq -r '.results.attestation_status'
```

如果你的 Keylime 已使用 v3 push model，则应查询 verifier 的 latest attestation endpoint：

```bash
curl -sS \
  --cacert "$KEYLIME_CA" \
  --cert "$KEYLIME_CLIENT_CERT" \
  --key "$KEYLIME_CLIENT_KEY" \
  "https://$KEYLIME_VERIFIER_HOST:$KEYLIME_VERIFIER_PORT/v3/agents/compute-1/attestations/latest" | jq .
```

本实验建议先用 v2.5 pull model，链路更直观。

## 8. Phase D：实现 Keylime 到 OpenStack 的同步器

同步器只做一件事：周期性读取 Keylime agent 状态，并维护 compute resource provider 上的 `CUSTOM_KEYLIME_ATTESTED` trait。

### D.1 映射文件

创建 `/etc/keylime-openstack-sync/mapping.yaml`：

```yaml
trait: CUSTOM_KEYLIME_ATTESTED
keylime:
  verifier_url: "https://192.168.10.10:8881"
  api_version: "v2.5"
  ca_cert: "/var/lib/keylime/cv_ca/cacert.crt"
  client_cert: "/var/lib/keylime/cv_ca/client-cert.crt"
  client_key: "/var/lib/keylime/cv_ca/client-private.pem"
openstack:
  cloud: "admin"
nodes:
  - agent_id: "compute-1"
    resource_provider_name: "compute-1"
  - agent_id: "compute-2"
    resource_provider_name: "compute-2"
```

### D.2 同步逻辑

伪代码：

```text
for node in mapping.nodes:
    status = keylime_get_attestation_status(node.agent_id)
    rp = placement_find_resource_provider(node.resource_provider_name)
    traits = placement_get_resource_provider_traits(rp)

    if status == "PASS":
        desired_traits = traits + CUSTOM_KEYLIME_ATTESTED
    else:
        desired_traits = traits - CUSTOM_KEYLIME_ATTESTED

    if desired_traits != traits:
        placement_set_resource_provider_traits(rp, desired_traits)
```

关键规则：

- 只有 `PASS` 才加 trait。
- `FAIL`、`PENDING`、`UNKNOWN`、API 超时都移除 trait。
- 更新 traits 时必须保留原有 traits。
- 不要在同步器里直接删除 VM。第一阶段只影响新调度。

### D.3 CLI 原型

这个版本适合实验，依赖 `openstack`、`jq`、`curl`。

```bash
#!/usr/bin/env bash
set -euo pipefail

TRAIT="CUSTOM_KEYLIME_ATTESTED"
AGENT_ID="$1"
RP_NAME="$2"

KEYLIME_VERIFIER_URL="${KEYLIME_VERIFIER_URL:-https://192.168.10.10:8881}"
KEYLIME_CA="${KEYLIME_CA:-/var/lib/keylime/cv_ca/cacert.crt}"
KEYLIME_CLIENT_CERT="${KEYLIME_CLIENT_CERT:-/var/lib/keylime/cv_ca/client-cert.crt}"
KEYLIME_CLIENT_KEY="${KEYLIME_CLIENT_KEY:-/var/lib/keylime/cv_ca/client-private.pem}"

status="$(
  curl -sS \
    --cacert "$KEYLIME_CA" \
    --cert "$KEYLIME_CLIENT_CERT" \
    --key "$KEYLIME_CLIENT_KEY" \
    "$KEYLIME_VERIFIER_URL/v2.5/agents/$AGENT_ID" \
  | jq -r '.results.attestation_status // "UNKNOWN"'
)"

rp_uuid="$(
  openstack resource provider list \
    --name "$RP_NAME" \
    -f value -c uuid
)"

existing_traits="$(
  openstack resource provider trait list "$rp_uuid" \
    -f value -c name || true
)"

desired_traits="$existing_traits"

if [ "$status" = "PASS" ]; then
  if ! printf '%s\n' "$existing_traits" | grep -qx "$TRAIT"; then
    desired_traits="$(printf '%s\n%s\n' "$existing_traits" "$TRAIT" | sed '/^$/d' | sort -u)"
  fi
else
  desired_traits="$(printf '%s\n' "$existing_traits" | grep -vx "$TRAIT" || true)"
fi

if [ "$(printf '%s\n' "$existing_traits" | sort)" = "$(printf '%s\n' "$desired_traits" | sort)" ]; then
  echo "No change: agent=$AGENT_ID rp=$RP_NAME status=$status"
  exit 0
fi

cmd=(openstack resource provider trait set)
while IFS= read -r trait; do
  [ -n "$trait" ] && cmd+=(--trait "$trait")
done < <(printf '%s\n' "$desired_traits")
cmd+=("$rp_uuid")

"${cmd[@]}"

echo "Updated: agent=$AGENT_ID rp=$RP_NAME status=$status trait=$TRAIT"
```

运行：

```bash
chmod +x ./keylime-openstack-sync-one.sh

./keylime-openstack-sync-one.sh compute-1 compute-1
./keylime-openstack-sync-one.sh compute-2 compute-2
```

周期运行：

```bash
while true; do
  ./keylime-openstack-sync-one.sh compute-1 compute-1 || true
  ./keylime-openstack-sync-one.sh compute-2 compute-2 || true
  sleep 30
done
```

生产化时建议改成 Python 或 Go 服务，增加日志、Prometheus metrics、重试、并发保护和 systemd unit。

## 9. Phase E：端到端实验

### E.1 PASS 时可以创建可信 VM

确认 Keylime PASS：

```bash
sudo keylime_tenant -c status -u compute-1
./keylime-openstack-sync-one.sh compute-1 compute-1
openstack resource provider trait list "$COMPUTE1_RP_UUID"
```

创建 VM：

```bash
openstack --os-username "$SECURE_USER" \
  --os-password pass \
  --os-project-name "$SECURE_PROJECT" \
  server create secure-vm-pass \
  --image "$IMAGE" \
  --flavor "$TRUSTED_FLAVOR" \
  --network "$NETWORK"
```

验证：

```bash
openstack server show secure-vm-pass \
  -c status \
  -c OS-EXT-SRV-ATTR:host \
  -c OS-EXT-SRV-ATTR:hypervisor_hostname
```

预期：

- VM 创建成功。
- VM 位于 Keylime PASS 且有 `CUSTOM_KEYLIME_ATTESTED` trait 的 compute。

### E.2 FAIL/PENDING/UNKNOWN 时阻止新建可信 VM

最安全的实验方式是不要破坏真实 compute，而是手工模拟同步器看到 FAIL 后的动作：

```bash
openstack resource provider trait set "$COMPUTE1_RP_UUID"
```

再创建 VM：

```bash
openstack --os-username "$SECURE_USER" \
  --os-password pass \
  --os-project-name "$SECURE_PROJECT" \
  server create secure-vm-fail \
  --image "$IMAGE" \
  --flavor "$TRUSTED_FLAVOR" \
  --network "$NETWORK"
```

预期：

- 没有其他可信 compute 时，创建失败或实例进入 `ERROR`。
- 如果 compute-2 仍有可信 trait，实例会被调度到 compute-2。

### E.3 使用 Keylime 制造真实 FAIL

在非生产实验节点上，可以删除 agent 后用一个不可能满足的 PCR policy 重新添加：

```bash
sudo keylime_tenant -c delete -u compute-1 || true

sudo keylime_tenant -c add \
  -t <compute-1-mgmt-ip> \
  -u compute-1 \
  --tpm_policy '{"0":"0000000000000000000000000000000000000000000000000000000000000000"}'
```

如果 compute-1 的 PCR 0 不等于全零，attestation 应失败。

确认：

```bash
sudo keylime_tenant -c status -u compute-1
./keylime-openstack-sync-one.sh compute-1 compute-1
openstack resource provider trait list "$COMPUTE1_RP_UUID"
```

预期：

- Keylime 状态为 FAIL 或无法达到 PASS。
- 同步器移除 `CUSTOM_KEYLIME_ATTESTED`。
- trusted flavor 不再调度到 compute-1。

恢复：

```bash
sudo keylime_tenant -c delete -u compute-1 || true

sudo keylime_tenant -c add \
  -t <compute-1-mgmt-ip> \
  -u compute-1

sudo keylime_tenant -c status -u compute-1
./keylime-openstack-sync-one.sh compute-1 compute-1
```

也可以在修复策略后使用：

```bash
sudo keylime_tenant -c reactivate -u compute-1
```

## 10. Phase F：把 project 纳入可信入口

现在把 project 和 Keylime 结合起来验证。

### F.1 secure project 可以用 trusted flavor

```bash
openstack --os-username "$SECURE_USER" \
  --os-password pass \
  --os-project-name "$SECURE_PROJECT" \
  flavor show "$TRUSTED_FLAVOR"
```

预期：能看到。

### F.2 normal project 看不到 trusted flavor

```bash
openstack --os-username "$NORMAL_USER" \
  --os-password pass \
  --os-project-name "$NORMAL_PROJECT" \
  flavor show "$TRUSTED_FLAVOR"
```

预期：报无权限或找不到 flavor。

### F.3 secure project 也不能绕过 Keylime 调度边界

移除所有 compute 的可信 trait：

```bash
openstack resource provider trait set "$COMPUTE1_RP_UUID"
openstack resource provider trait set "$COMPUTE2_RP_UUID"
```

Alice 再创建 trusted VM：

```bash
openstack --os-username "$SECURE_USER" \
  --os-password pass \
  --os-project-name "$SECURE_PROJECT" \
  server create secure-vm-no-trusted-host \
  --image "$IMAGE" \
  --flavor "$TRUSTED_FLAVOR" \
  --network "$NETWORK"
```

预期：

- Alice 有 project 权限和 flavor 权限，但因为没有可信 compute，调度仍失败。

这说明：

- `project` 控制“谁能请求可信资源”。
- Keylime + Placement 控制“可信资源能落在哪里”。

## 11. 可选增强：失败后禁用 nova-compute

第一阶段推荐只移除 trait。更激进的策略是 Keylime FAIL 后禁用 compute 服务：

```bash
openstack compute service set --disable --disable-reason "Keylime attestation failed" \
  <compute-hostname> nova-compute
```

恢复：

```bash
openstack compute service set --enable <compute-hostname> nova-compute
```

谨慎点：

- 禁用 compute 会影响所有 flavor，不只是 trusted flavor。
- 适合强安全场景；普通实验先用 trait 更清晰。

## 12. 可选增强：接入 Ironic 裸金属

如果你的 OpenStack 使用 Ironic，可以把同样的思路应用到 bare metal：

- Ironic node 部署后启动 Keylime agent。
- Keylime PASS 后给 Ironic node 或 Placement provider 打可信 trait。
- Keylime FAIL 后把 Ironic node 设为 maintenance，或移除可信 trait。
- 使用 baremetal flavor 的 required trait 控制调度。

这适合验证物理机启动链和运行时完整性，但比 Nova compute 实验复杂，建议放在第二阶段。

## 13. 实验记录模板

```markdown
## 实验记录

### 环境

- OpenStack 版本：
- Keylime 版本：
- Controller IP：
- Compute 节点：
- TPM 类型：硬件 TPM / vTPM / swtpm

### Project 边界

- secure project：
- normal project：
- trusted flavor 是否仅 secure 可见：

### Placement trait

- trait 名称：
- compute-1 RP UUID：
- compute-2 RP UUID：
- PASS 时 trait 是否存在：
- FAIL 时 trait 是否移除：

### 调度结果

| 场景 | Keylime 状态 | trait 状态 | project | flavor | 预期 | 实际 |
|---|---|---|---|---|---|---|
| PASS 创建可信 VM | PASS | 存在 | proj-secure | trusted.small | 成功 | |
| FAIL 创建可信 VM | FAIL | 移除 | proj-secure | trusted.small | 失败或调度到其他可信节点 | |
| normal project 使用 trusted flavor | 任意 | 任意 | proj-normal | trusted.small | 无权限/不可见 | |
| 普通 flavor 创建 VM | 任意 | 任意 | proj-normal | m1.small | 成功 | |
```

## 14. 成功标准

实验完成后，你应该能证明：

1. `trusted.small` 是 private flavor，只暴露给 `proj-secure`。
2. `trusted.small` 要求 `CUSTOM_KEYLIME_ATTESTED=required`。
3. 只有 Keylime PASS 的 compute 才会被同步器加上 `CUSTOM_KEYLIME_ATTESTED`。
4. Keylime FAIL/PENDING/UNKNOWN 后，同步器移除 trait。
5. 没有可信 compute 时，`proj-secure` 即使有权限使用 trusted flavor，也无法创建 trusted VM。
6. `proj-normal` 无法看到或使用 trusted flavor。

## 15. 边界与风险

这个方案能做到：

- 把 Keylime attestation 状态转成 OpenStack 可调度属性。
- 让某个 project 使用“只能落到可信 compute”的 flavor。
- 在宿主机状态异常时阻止新的 trusted workload 调度过去。

这个方案不能自动做到：

- 证明 guest OS 自身没有被篡改。
- 自动迁移或关停已经运行在失败宿主机上的 VM。
- 防止 OpenStack admin 或 Keylime admin 绕过策略。
- 把 vTPM 实验环境等同于硬件 TPM 生产信任根。

后续可扩展方向：

- 接 Barbican：Keylime PASS 后才释放 workload 密钥。
- 接 Aodh/Prometheus：Keylime FAIL 触发告警。
- 接 Nova evacuate/live migration：失败后迁移 workload。
- 接 Ironic：对裸金属节点做入池前 attestation。
- 接 guest vTPM：对 VM 内部做二级 attestation。

## 16. 参考资料

- OpenStack Keystone identity concepts: https://docs.openstack.org/keystone/latest/admin/identity-concepts.html
- Nova flavors and required traits: https://docs.openstack.org/nova/latest/user/flavors.html
- osc-placement CLI reference: https://docs.openstack.org/osc-placement/latest/cli/index.html
- Keylime design overview: https://keylime.readthedocs.io/en/latest/design/overview.html
- Keylime installation: https://keylime.readthedocs.io/en/latest/installation.html
- Keylime REST APIs: https://keylime.readthedocs.io/en/latest/rest_apis.html
- keylime_tenant man page: https://keylime.readthedocs.io/en/latest/man/keylime_tenant.1.html

