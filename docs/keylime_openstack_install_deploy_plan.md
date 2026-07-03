# Keylime 与 OpenStack 结合安装部署方案

日期：2026-07-02

## 1. 目标与边界

你的 OpenStack 环境：

```text
控制/API 节点: csri10 / 172.31.100.10
public API VIP: 172.31.107.100
internal API VIP: 172.31.100.100
计算节点:      csri8 / 172.31.100.8
计算节点:      csri9 / 172.31.100.9
部署方式:      Kolla / Docker / OpenStack 2026.1 Ubuntu Noble
admin openrc:   /etc/kolla/admin-openrc.sh
```

本方案第一阶段要实现：

```text
Keylime 判断 csri8/csri9 是否可信
        |
        v
同步器把 PASS/FAIL 转成 OpenStack Placement trait
        |
        v
Nova trusted flavor 只调度到带 CUSTOM_KEYLIME_ATTESTED 的 compute
        |
        v
可信 project 通过 private flavor 使用可信计算资源
```

第一阶段不做这些事：

- 不修改 Nova/Keystone/Neutron 源码。
- 不把 Keylime 当作 OpenStack 内置服务注册到 Keystone catalog。
- 不自动迁移已运行 VM。
- 不证明 guest OS 本身可信。
- 不把 vTPM 实验结果等同于硬件 TPM 生产信任。

## 2. 推荐架构

```text
csri10 控制节点
  - Keylime registrar
  - Keylime verifier
  - keylime_tenant 管理工具
  - keylime-openstack-sync 同步器
  - OpenStack CLI / Placement API

csri8 计算节点
  - nova-compute
  - keylime agent
  - TPM 2.0 / vTPM

csri9 计算节点
  - nova-compute
  - keylime agent
  - TPM 2.0 / vTPM
```

Keylime 官方设计中，agent 运行在被证明的系统上，registrar 管理 agent enrollment，verifier 执行 attestation，tenant 是管理 agent 和策略的 CLI 工具。Verifier 在 pull model 下会持续向 agent 拉取 TPM quote、PCR、IMA log、UEFI event log 等证据。参考 Keylime overview：<https://keylime.readthedocs.io/en/latest/design/overview.html>

## 3. 为什么这样接 OpenStack

OpenStack Nova 原生支持 Placement traits。Nova flavor 可以设置：

```text
trait:CUSTOM_KEYLIME_ATTESTED=required
```

Nova scheduler 会把 required traits 传给 Placement，只选择满足 trait 的 resource provider。OpenStack Nova 文档说明 required traits 用于要求实例调度到具有指定 traits 的 compute resource provider。参考：<https://docs.openstack.org/nova/latest/user/flavors.html>

因此最小集成点是：

```text
Keylime PASS  -> 给 compute resource provider 加 CUSTOM_KEYLIME_ATTESTED
Keylime FAIL  -> 从 compute resource provider 移除 CUSTOM_KEYLIME_ATTESTED
trusted flavor -> 要求 CUSTOM_KEYLIME_ATTESTED=required
trusted project -> 只能使用 trusted private flavor
```

这条路径低侵入、可回滚，并且和你前面 project 实验的结论一致：project 管“谁能请求可信资源”，Placement/Keylime 管“资源能落到哪些可信宿主机”。

## 4. 端口与网络规划

建议第一阶段使用 Keylime pull model：

| 方向 | 端口 | 说明 |
|---|---:|---|
| csri10 -> csri8/csri9 | agent 端口，常见为 `9002` | verifier/tenant 访问 agent |
| csri8/csri9 -> csri10 | registrar `8891` | agent 注册 |
| csri10 本机/管理端 -> csri10 | verifier `8881` | tenant/sync 查询 verifier |

以你的管理网络为准：

```text
csri10: 172.31.100.10
csri8:  172.31.100.8
csri9:  172.31.100.9
```

先在三台机器上互通检查：

```bash
ping -c 3 172.31.100.10
ping -c 3 172.31.100.8
ping -c 3 172.31.100.9
```

## 5. 预检查

### 5.1 OpenStack 侧

在 `csri10`：

```bash
source /etc/kolla/admin-openrc.sh

openstack compute service list
openstack hypervisor list
openstack resource provider list
openstack flavor list
```

预期：

```text
nova-compute csri8 enabled/up
nova-compute csri9 enabled/up
resource provider 中存在 csri8、csri9
```

如果没有 `openstack resource provider list`，说明缺少 `osc-placement` 插件，需要在能运行 OpenStack CLI 的环境安装对应插件。osc-placement 文档列出了 `resource provider trait list/set` 等命令；`resource provider trait set` 会替换 provider 上全部 traits，不是追加。参考：<https://docs.openstack.org/osc-placement/latest/cli/index.html>

### 5.2 TPM 侧

在 `csri8` 和 `csri9`：

```bash
ls -l /dev/tpm* /sys/class/tpm 2>/dev/null || true
dmesg | grep -i tpm | tail -n 30
```

安装 TPM 工具后检查：

```bash
apt-get update
apt-get install -y tpm2-tools

tpm2_getcap properties-fixed
tpm2_pcrread sha256:0,1,2,3,4,5,6,7
```

预期：

- 生产环境应使用硬件 TPM 2.0。
- 如果没有硬件 TPM，可以用 swtpm 做流程验证，但只能证明集成链路，不代表生产可信根。

Keylime 文档说明它依赖 TPM 2.0 软件栈和 `tpm2-tools`，并且默认使用内核 TPM resource manager。参考：<https://keylime.readthedocs.io/en/latest/installation.html>

## 6. Keylime 安装路径选择

Keylime 官方当前列出的安装方式包括：RHEL/SLE 包、Ansible roles、Keylime installer、Docker、manual install。参考：<https://keylime.readthedocs.io/en/latest/installation.html>

你的环境是 Ubuntu Noble/Kolla，因此建议按这个顺序选择：

```text
优先 1：发行版包，如果 Ubuntu 仓库或你的内部仓库提供 Keylime 包
优先 2：Keylime Ansible role，用于多节点一致部署
优先 3：Keylime Docker 部署 registrar/verifier，agent 仍建议宿主机运行并直连 TPM
优先 4：源码/manual install，用于实验或内部构建包
```

### 6.1 检查 Ubuntu 包

在 `csri10`、`csri8`、`csri9`：

```bash
apt-get update
apt-cache search keylime
apt-cache policy keylime keylime-agent keylime-verifier keylime-registrar keylime-tenant 2>/dev/null || true
```

如果能看到包，按实际包名安装。

控制节点 `csri10` 需要：

```bash
apt-get install -y \
  keylime-verifier \
  keylime-registrar \
  keylime-tenant \
  tpm2-tools \
  jq \
  curl
```

计算节点 `csri8/csri9` 需要：

```bash
apt-get install -y \
  keylime-agent \
  tpm2-tools \
  jq \
  curl
```

如果包名不同，以 `apt-cache search keylime` 为准。不要在没确认包名时盲目复制安装命令。

### 6.2 如果没有 Ubuntu 包

实验环境可以使用 Keylime 官方 Docker 或 installer/manual 路径。注意：

- registrar/verifier 可以容器化部署在 `csri10`。
- agent 最好运行在 `csri8/csri9` 宿主机，因为它需要访问宿主机 TPM、IMA/UEFI log 和内核接口。
- 如果把 agent 容器化，需要把 `/dev/tpm*`、必要的 `/sys/kernel/security`、IMA/TPM 相关路径和权限正确传入容器；这比宿主机安装更容易踩坑。

生产建议使用 Ansible role 或内部打包，把配置、证书、systemd、升级流程固定下来。

## 7. 部署 Keylime 控制面

以下以“包安装后 systemd 服务存在”为主线。不同包的服务名可能略有差异，先检查：

```bash
systemctl list-unit-files | grep -i keylime
ls -l /etc/keylime
```

### 7.1 配置 registrar/verifier 监听地址

在 `csri10`：

```bash
grep -R "8881\|8891\|ip\|port" /etc/keylime 2>/dev/null
```

目标配置：

```text
registrar 监听: 172.31.100.10:8891 或 0.0.0.0:8891
verifier  监听: 172.31.100.10:8881 或 0.0.0.0:8881
```

不同 Keylime 版本配置项名称可能不同。修改前先备份：

```bash
cp -a /etc/keylime /etc/keylime.bak.$(date +%F-%H%M%S)
```

启动服务：

```bash
systemctl enable --now keylime_registrar || systemctl enable --now keylime-registrar
systemctl enable --now keylime_verifier  || systemctl enable --now keylime-verifier

systemctl status keylime_registrar --no-pager || systemctl status keylime-registrar --no-pager
systemctl status keylime_verifier --no-pager  || systemctl status keylime-verifier --no-pager

ss -lntp | egrep '8881|8891'
```

### 7.2 mTLS 证书

Keylime 使用 mTLS。官方文档说明 verifier 首次启动默认会在 `/var/lib/keylime/cv_ca/` 创建 CA；其中包含 root CA、server cert/key、client cert/key。Agent 需要信任这个 CA，否则 tenant/verifier 不能连接 agent。参考：<https://keylime.readthedocs.io/en/latest/installation.html>

在 `csri10` 检查：

```bash
ls -l /var/lib/keylime/cv_ca/
```

常见文件：

```text
cacert.crt
server-cert.crt
server-private.pem
client-cert.crt
client-private.pem
```

把 CA 复制到计算节点，路径按你的 agent 配置确定：

```bash
scp /var/lib/keylime/cv_ca/cacert.crt root@172.31.100.8:/etc/keylime/cacert.crt
scp /var/lib/keylime/cv_ca/cacert.crt root@172.31.100.9:/etc/keylime/cacert.crt
```

生产环境建议自建 CA，并为 verifier、registrar、tenant、agent 分发独立证书，而不是长期依赖自动生成的实验 CA。

## 8. 部署 Keylime agent

在 `csri8` 和 `csri9`：

```bash
systemctl list-unit-files | grep -i keylime
ls -l /etc/keylime
```

Keylime 文档说明 Rust agent 是当前官方 agent，默认配置文件是 `/etc/keylime/agent.conf`，且与旧 Python agent 配置不可互换。参考：<https://keylime.readthedocs.io/en/latest/installation.html>

目标配置：

```text
agent listen: 当前计算节点管理 IP，例如 172.31.100.8 或 172.31.100.9
agent port:   常见为 9002，以实际配置为准
registrar:    172.31.100.10:8891
verifier:     172.31.100.10:8881
CA:           /etc/keylime/cacert.crt
TCTI:         优先使用 kernel resource manager，例如 /dev/tpmrm0
```

检查 TPM TCTI：

```bash
ls -l /dev/tpmrm0 /dev/tpm0 2>/dev/null || true
```

如果需要显式设置：

```bash
export TPM2TOOLS_TCTI="device:/dev/tpmrm0"
export TCTI="device:/dev/tpmrm0"
```

启动 agent：

```bash
systemctl enable --now keylime_agent || systemctl enable --now keylime-agent
systemctl status keylime_agent --no-pager || systemctl status keylime-agent --no-pager

ss -lntp | grep -i keylime || ss -lntp | grep 9002
```

从 `csri10` 测试连通：

```bash
nc -vz 172.31.100.8 9002
nc -vz 172.31.100.9 9002
```

如果没有 `nc`：

```bash
timeout 3 bash -c '</dev/tcp/172.31.100.8/9002' && echo ok
timeout 3 bash -c '</dev/tcp/172.31.100.9/9002' && echo ok
```

## 9. 注册并验证 agent

在 `csri10`：

```bash
sudo keylime_tenant -c add \
  -t 172.31.100.8 \
  -u csri8 \
  -v 172.31.100.10 \
  -vp 8881 \
  -r 172.31.100.10 \
  -rp 8891

sudo keylime_tenant -c add \
  -t 172.31.100.9 \
  -u csri9 \
  -v 172.31.100.10 \
  -vp 8881 \
  -r 172.31.100.10 \
  -rp 8891
```

Keylime tenant 官方 man page 中列出了 `add`、`delete`、`status`、`cvlist`、`reactivate` 等命令，并说明 verifier 默认端口为 `8881`、registrar 默认端口为 `8891`。参考：<https://keylime.readthedocs.io/en/latest/man/keylime_tenant.1.html>

查看状态：

```bash
sudo keylime_tenant -c status -u csri8
sudo keylime_tenant -c status -u csri9
sudo keylime_tenant -c cvlist
sudo keylime_tenant -c reglist
```

如果状态没有 PASS，先看日志：

```bash
journalctl -u keylime_verifier -n 200 --no-pager || journalctl -u keylime-verifier -n 200 --no-pager
journalctl -u keylime_registrar -n 200 --no-pager || journalctl -u keylime-registrar -n 200 --no-pager
```

在计算节点：

```bash
journalctl -u keylime_agent -n 200 --no-pager || journalctl -u keylime-agent -n 200 --no-pager
```

## 10. 用 Keylime REST API 查询状态

Keylime 7.14 的 v2.5 API 在 `GET /v2.5/agents/{agent_id}` 响应中增加了 `attestation_status`，取值为 `PASS`、`FAIL` 或 `PENDING`。参考：<https://keylime.readthedocs.io/en/latest/rest_apis.html>

在 `csri10`：

```bash
export KEYLIME_VERIFIER_URL="https://172.31.100.10:8881"
export KEYLIME_CA="/var/lib/keylime/cv_ca/cacert.crt"
export KEYLIME_CLIENT_CERT="/var/lib/keylime/cv_ca/client-cert.crt"
export KEYLIME_CLIENT_KEY="/var/lib/keylime/cv_ca/client-private.pem"
```

查询：

```bash
curl -sS \
  --cacert "$KEYLIME_CA" \
  --cert "$KEYLIME_CLIENT_CERT" \
  --key "$KEYLIME_CLIENT_KEY" \
  "$KEYLIME_VERIFIER_URL/v2.5/agents/csri8" | jq .

curl -sS \
  --cacert "$KEYLIME_CA" \
  --cert "$KEYLIME_CLIENT_CERT" \
  --key "$KEYLIME_CLIENT_KEY" \
  "$KEYLIME_VERIFIER_URL/v2.5/agents/csri9" | jq .
```

只取状态：

```bash
curl -sS \
  --cacert "$KEYLIME_CA" \
  --cert "$KEYLIME_CLIENT_CERT" \
  --key "$KEYLIME_CLIENT_KEY" \
  "$KEYLIME_VERIFIER_URL/v2.5/agents/csri8" \
  | jq -r '.results.attestation_status // "UNKNOWN"'
```

如果你部署的是 v3 push model，Keylime 文档说明 v3 verifier 提供 `GET /v3/agents/{agent_id}/attestations/latest` 用于返回最近一次 attestation 及验证状态。但第一阶段建议用 v2.5 pull model，便于调试。

## 11. OpenStack Placement 准备

在 `csri10`：

```bash
source /etc/kolla/admin-openrc.sh
export OS_PLACEMENT_API_VERSION=1.17
```

确认 resource provider：

```bash
openstack resource provider list

export RP_CSRI8="$(openstack resource provider list --name csri8 -f value -c uuid)"
export RP_CSRI9="$(openstack resource provider list --name csri9 -f value -c uuid)"

echo "csri8 RP=$RP_CSRI8"
echo "csri9 RP=$RP_CSRI9"
```

创建可信 trait：

```bash
export TRUSTED_TRAIT=CUSTOM_KEYLIME_ATTESTED

openstack trait create "$TRUSTED_TRAIT" 2>/dev/null || true
openstack trait show "$TRUSTED_TRAIT"
```

手工给 `csri8` 加 trait 做链路测试。注意：`resource provider trait set` 会替换所有 traits，所以必须保留原 traits。

```bash
rp_add_trait() {
  local rp_uuid="$1"
  local new_trait="$2"
  local traits
  traits="$(openstack resource provider trait list "$rp_uuid" -f value -c name 2>/dev/null | sort -u)"

  if ! printf '%s\n' "$traits" | grep -qx "$new_trait"; then
    traits="$(printf '%s\n%s\n' "$traits" "$new_trait" | sed '/^$/d' | sort -u)"
  fi

  local cmd=(openstack resource provider trait set)
  while IFS= read -r t; do
    [ -n "$t" ] && cmd+=(--trait "$t")
  done < <(printf '%s\n' "$traits")
  cmd+=("$rp_uuid")
  "${cmd[@]}"
}

rp_add_trait "$RP_CSRI8" "$TRUSTED_TRAIT"

openstack resource provider trait list "$RP_CSRI8"
openstack resource provider trait list "$RP_CSRI9"
```

用 Placement allocation candidate 验证：

```bash
openstack allocation candidate list \
  --resource VCPU=1 \
  --required "$TRUSTED_TRAIT"
```

应该只返回带 trait 的 provider。

## 12. 创建 trusted flavor 和 trusted project

如果你已经有测试 project，可复用；否则创建：

```bash
source /etc/kolla/admin-openrc.sh

export TRUSTED_PROJECT=project-keylime-trusted
export TRUSTED_USER=keylime-trusted-user
export TRUSTED_PASS='KeylimeTrusted123!'

openstack project show "$TRUSTED_PROJECT" >/dev/null 2>&1 || \
  openstack project create "$TRUSTED_PROJECT"

openstack user show "$TRUSTED_USER" >/dev/null 2>&1 || \
  openstack user create "$TRUSTED_USER" --password "$TRUSTED_PASS"

openstack role add --project "$TRUSTED_PROJECT" --user "$TRUSTED_USER" member
```

创建 private trusted flavor：

```bash
export TRUSTED_FLAVOR=trusted.keylime.small

openstack flavor show "$TRUSTED_FLAVOR" >/dev/null 2>&1 || \
  openstack flavor create "$TRUSTED_FLAVOR" \
    --private \
    --ram 2048 \
    --disk 10 \
    --vcpus 1

openstack flavor set "$TRUSTED_FLAVOR" \
  --property trait:"$TRUSTED_TRAIT"=required

openstack flavor set --project "$TRUSTED_PROJECT" "$TRUSTED_FLAVOR"

openstack flavor show "$TRUSTED_FLAVOR" -c name -c properties -f yaml
```

Nova flavor 文档说明 private flavor 只对 access list 中的 project 可见；required traits 的语法是 `trait:<trait_name>=required`。参考：<https://docs.openstack.org/nova/latest/user/flavors.html>

## 13. 手工验证调度闭环

选择一个镜像和网络：

```bash
export IMAGE="$(openstack image list -f value -c Name | head -n 1)"
export NET_ID="<可信project可用的network-id>"
```

如果你还没有给 `project-keylime-trusted` 建网络，可以先创建：

```bash
openstack \
  --os-auth-url "$OS_AUTH_URL" \
  --os-identity-api-version 3 \
  --os-username "$TRUSTED_USER" \
  --os-password "$TRUSTED_PASS" \
  --os-user-domain-name Default \
  --os-project-name "$TRUSTED_PROJECT" \
  --os-project-domain-name Default \
  network create keylime-trusted-net

openstack \
  --os-auth-url "$OS_AUTH_URL" \
  --os-identity-api-version 3 \
  --os-username "$TRUSTED_USER" \
  --os-password "$TRUSTED_PASS" \
  --os-user-domain-name Default \
  --os-project-name "$TRUSTED_PROJECT" \
  --os-project-domain-name Default \
  subnet create keylime-trusted-subnet \
    --network keylime-trusted-net \
    --subnet-range 10.91.0.0/24

export NET_ID="$(openstack network show keylime-trusted-net -f value -c id)"
```

创建 VM：

```bash
openstack \
  --os-auth-url "$OS_AUTH_URL" \
  --os-identity-api-version 3 \
  --os-username "$TRUSTED_USER" \
  --os-password "$TRUSTED_PASS" \
  --os-user-domain-name Default \
  --os-project-name "$TRUSTED_PROJECT" \
  --os-project-domain-name Default \
  server create keylime-trusted-vm-1 \
    --image "$IMAGE" \
    --flavor "$TRUSTED_FLAVOR" \
    --nic net-id="$NET_ID"
```

Admin 查看落点：

```bash
source /etc/kolla/admin-openrc.sh

openstack server list --all-projects --name keylime-trusted-vm-1

export TRUSTED_VM_ID="$(
  openstack server list --all-projects --name keylime-trusted-vm-1 -f value -c ID | head -n 1
)"

openstack server show "$TRUSTED_VM_ID" \
  -c status \
  -c project_id \
  -c OS-EXT-SRV-ATTR:host \
  -f yaml
```

预期：

```text
如果只有 csri8 有 CUSTOM_KEYLIME_ATTESTED，VM 应调度到 csri8。
如果 csri8 trait 被移除且 csri9 没有 trait，新 VM 应 No valid host 或进入 ERROR。
```

移除 trait 验证失败路径：

```bash
rp_remove_trait() {
  local rp_uuid="$1"
  local remove_trait="$2"
  local traits
  traits="$(openstack resource provider trait list "$rp_uuid" -f value -c name 2>/dev/null | grep -vx "$remove_trait" || true)"

  local cmd=(openstack resource provider trait set)
  while IFS= read -r t; do
    [ -n "$t" ] && cmd+=(--trait "$t")
  done < <(printf '%s\n' "$traits")
  cmd+=("$rp_uuid")
  "${cmd[@]}"
}

rp_remove_trait "$RP_CSRI8" "$TRUSTED_TRAIT"
openstack resource provider trait list "$RP_CSRI8"
```

再次创建 trusted VM，验证无可信 provider 时调度失败。

## 14. 部署 keylime-openstack-sync 同步器

同步器职责：

```text
定期读取 Keylime agent status
PASS      -> 给对应 resource provider 加 CUSTOM_KEYLIME_ATTESTED
FAIL      -> 移除 CUSTOM_KEYLIME_ATTESTED
PENDING   -> 移除 CUSTOM_KEYLIME_ATTESTED
UNKNOWN   -> 移除 CUSTOM_KEYLIME_ATTESTED
API error -> 移除 CUSTOM_KEYLIME_ATTESTED
```

### 14.1 映射文件

在 `csri10`：

```bash
mkdir -p /etc/keylime-openstack-sync
```

创建 `/etc/keylime-openstack-sync/nodes.env`：

```bash
cat >/etc/keylime-openstack-sync/nodes.env <<'EOF'
csri8 csri8
csri9 csri9
EOF
```

格式：

```text
<keylime_agent_id> <openstack_resource_provider_name>
```

### 14.2 同步脚本

创建 `/usr/local/sbin/keylime-openstack-sync-one`：

```bash
cat >/usr/local/sbin/keylime-openstack-sync-one <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

AGENT_ID="$1"
RP_NAME="$2"

TRUSTED_TRAIT="${TRUSTED_TRAIT:-CUSTOM_KEYLIME_ATTESTED}"
KEYLIME_VERIFIER_URL="${KEYLIME_VERIFIER_URL:-https://172.31.100.10:8881}"
KEYLIME_CA="${KEYLIME_CA:-/var/lib/keylime/cv_ca/cacert.crt}"
KEYLIME_CLIENT_CERT="${KEYLIME_CLIENT_CERT:-/var/lib/keylime/cv_ca/client-cert.crt}"
KEYLIME_CLIENT_KEY="${KEYLIME_CLIENT_KEY:-/var/lib/keylime/cv_ca/client-private.pem}"

source /etc/kolla/admin-openrc.sh
export OS_PLACEMENT_API_VERSION="${OS_PLACEMENT_API_VERSION:-1.17}"

status="$(
  curl -fsS \
    --cacert "$KEYLIME_CA" \
    --cert "$KEYLIME_CLIENT_CERT" \
    --key "$KEYLIME_CLIENT_KEY" \
    "$KEYLIME_VERIFIER_URL/v2.5/agents/$AGENT_ID" \
  | jq -r '.results.attestation_status // "UNKNOWN"'
)" || status="UNKNOWN"

rp_uuid="$(
  openstack resource provider list \
    --name "$RP_NAME" \
    -f value -c uuid
)"

if [ -z "$rp_uuid" ]; then
  echo "ERROR: resource provider not found: $RP_NAME" >&2
  exit 2
fi

existing_traits="$(
  openstack resource provider trait list "$rp_uuid" \
    -f value -c name 2>/dev/null | sort -u || true
)"

if [ "$status" = "PASS" ]; then
  desired_traits="$(
    printf '%s\n%s\n' "$existing_traits" "$TRUSTED_TRAIT" \
      | sed '/^$/d' \
      | sort -u
  )"
else
  desired_traits="$(
    printf '%s\n' "$existing_traits" \
      | grep -vx "$TRUSTED_TRAIT" \
      | sort -u || true
  )"
fi

if [ "$(printf '%s\n' "$existing_traits" | sort -u)" = "$(printf '%s\n' "$desired_traits" | sort -u)" ]; then
  echo "No change: agent=$AGENT_ID rp=$RP_NAME status=$status"
  exit 0
fi

cmd=(openstack resource provider trait set)
while IFS= read -r trait; do
  [ -n "$trait" ] && cmd+=(--trait "$trait")
done < <(printf '%s\n' "$desired_traits")
cmd+=("$rp_uuid")

"${cmd[@]}"

echo "Updated: agent=$AGENT_ID rp=$RP_NAME status=$status trait=$TRUSTED_TRAIT"
EOF

chmod +x /usr/local/sbin/keylime-openstack-sync-one
```

创建批量同步脚本 `/usr/local/sbin/keylime-openstack-sync`：

```bash
cat >/usr/local/sbin/keylime-openstack-sync <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

NODES_FILE="${NODES_FILE:-/etc/keylime-openstack-sync/nodes.env}"

while read -r agent rp; do
  [ -z "${agent:-}" ] && continue
  case "$agent" in \#*) continue ;; esac
  /usr/local/sbin/keylime-openstack-sync-one "$agent" "$rp" || true
done < "$NODES_FILE"
EOF

chmod +x /usr/local/sbin/keylime-openstack-sync
```

手工运行：

```bash
/usr/local/sbin/keylime-openstack-sync

openstack resource provider trait list "$RP_CSRI8"
openstack resource provider trait list "$RP_CSRI9"
```

### 14.3 systemd timer

创建 service：

```bash
cat >/etc/systemd/system/keylime-openstack-sync.service <<'EOF'
[Unit]
Description=Sync Keylime attestation status to OpenStack Placement traits
After=network-online.target

[Service]
Type=oneshot
Environment=TRUSTED_TRAIT=CUSTOM_KEYLIME_ATTESTED
Environment=KEYLIME_VERIFIER_URL=https://172.31.100.10:8881
ExecStart=/usr/local/sbin/keylime-openstack-sync
EOF
```

创建 timer：

```bash
cat >/etc/systemd/system/keylime-openstack-sync.timer <<'EOF'
[Unit]
Description=Run Keylime OpenStack sync every 30 seconds

[Timer]
OnBootSec=30s
OnUnitActiveSec=30s
AccuracySec=5s
Unit=keylime-openstack-sync.service

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now keylime-openstack-sync.timer

systemctl list-timers | grep keylime-openstack-sync
journalctl -u keylime-openstack-sync.service -n 100 --no-pager
```

## 15. 失败与恢复验证

### 15.1 模拟失败

不要一开始破坏 TPM 或系统文件。先手工让 Keylime 状态之外的 OpenStack 行为可见：

```bash
rp_remove_trait "$RP_CSRI8" "$TRUSTED_TRAIT"
rp_remove_trait "$RP_CSRI9" "$TRUSTED_TRAIT"
```

再创建 trusted VM，预期失败：

```bash
openstack \
  --os-auth-url "$OS_AUTH_URL" \
  --os-identity-api-version 3 \
  --os-username "$TRUSTED_USER" \
  --os-password "$TRUSTED_PASS" \
  --os-user-domain-name Default \
  --os-project-name "$TRUSTED_PROJECT" \
  --os-project-domain-name Default \
  server create keylime-trusted-vm-should-fail \
    --image "$IMAGE" \
    --flavor "$TRUSTED_FLAVOR" \
    --nic net-id="$NET_ID"
```

### 15.2 Keylime 恢复后自动加 trait

确保 agent PASS：

```bash
sudo keylime_tenant -c status -u csri8
sudo keylime_tenant -c status -u csri9
```

运行同步：

```bash
/usr/local/sbin/keylime-openstack-sync

openstack resource provider trait list "$RP_CSRI8"
openstack resource provider trait list "$RP_CSRI9"
```

PASS 的节点应重新获得 `CUSTOM_KEYLIME_ATTESTED`。

### 15.3 真实 FAIL 测试

只在非生产实验节点做。可以用错误 TPM policy 注册某个 agent，让 attestation 无法 PASS：

```bash
sudo keylime_tenant -c delete -u csri8 || true

sudo keylime_tenant -c add \
  -t 172.31.100.8 \
  -u csri8 \
  -v 172.31.100.10 \
  -vp 8881 \
  -r 172.31.100.10 \
  -rp 8891 \
  --tpm_policy '{"0":"0000000000000000000000000000000000000000000000000000000000000000"}'

sudo keylime_tenant -c status -u csri8
/usr/local/sbin/keylime-openstack-sync-one csri8 csri8
openstack resource provider trait list "$RP_CSRI8"
```

恢复：

```bash
sudo keylime_tenant -c delete -u csri8 || true

sudo keylime_tenant -c add \
  -t 172.31.100.8 \
  -u csri8 \
  -v 172.31.100.10 \
  -vp 8881 \
  -r 172.31.100.10 \
  -rp 8891

sudo keylime_tenant -c status -u csri8
/usr/local/sbin/keylime-openstack-sync-one csri8 csri8
```

## 16. 生产加固建议

第一阶段能跑通后，再做加固。

### 16.1 Keylime 策略加固

不要长期只依赖默认/空 policy。逐步启用：

- UEFI measured boot policy。
- IMA runtime integrity policy。
- 策略签名。
- 证书化 TPM/EK 信任链。

Keylime overview 说明 static PCR 值较脆弱，UEFI event log measured boot 更适合大规模部署；IMA 可验证运行期文件测量。参考：<https://keylime.readthedocs.io/en/latest/design/overview.html>

### 16.2 OpenStack 权限加固

实验阶段同步器可用 admin openrc。生产建议：

- 创建专用 `keylime-openstack-sync` 用户。
- 只授予 Placement trait 更新所需最小权限。
- 避免同步器持有全局 admin 权限。
- 通过 application credential 或受控 secret 管理 OpenStack 凭据。

### 16.3 调度策略加固

第一阶段只影响新建 VM。生产可增加：

- Keylime FAIL 后移除 trait。
- 严重 FAIL 后 `openstack compute service set --disable`。
- 告警到 Prometheus/Aodh。
- 结合 live migration/evacuation 策略处理已有 VM。

注意：禁用 `nova-compute` 会影响所有 workload，不只是 trusted flavor，默认不要启用。

### 16.4 Project/VPC 侧加固

对于 trusted project：

- trusted flavor 必须 private，只授权 trusted project。
- 禁止使用不可信 shared network。
- 限制 external network、router、floating IP。
- 只允许使用可信镜像。
- volume type 强制加密。
- Barbican 密钥释放可进一步绑定 Keylime attestation。

## 17. 验收标准

部署完成后，应满足：

```text
1. csri8/csri9 上 keylime agent 正常运行。
2. csri10 上 registrar/verifier 正常运行。
3. keylime_tenant -c status -u csri8/csri9 能看到 PASS/PENDING/FAIL。
4. Keylime REST v2.5 可返回 attestation_status。
5. PASS 节点对应 Placement provider 有 CUSTOM_KEYLIME_ATTESTED。
6. FAIL/PENDING/UNKNOWN 节点没有 CUSTOM_KEYLIME_ATTESTED。
7. trusted.keylime.small flavor 带 trait:CUSTOM_KEYLIME_ATTESTED=required。
8. trusted project 可以看到 trusted flavor，普通 project 看不到。
9. trusted VM 只能调度到带 trait 的 compute。
10. 移除所有 trusted trait 后，新建 trusted VM 失败。
```

## 18. 回滚

停止同步器：

```bash
systemctl disable --now keylime-openstack-sync.timer
systemctl stop keylime-openstack-sync.service || true
```

移除 trait：

```bash
source /etc/kolla/admin-openrc.sh

rp_remove_trait "$RP_CSRI8" "$TRUSTED_TRAIT"
rp_remove_trait "$RP_CSRI9" "$TRUSTED_TRAIT"
```

删除 trusted flavor：

```bash
openstack flavor delete "$TRUSTED_FLAVOR" || true
```

删除 Keylime agent enrollment：

```bash
sudo keylime_tenant -c delete -u csri8 || true
sudo keylime_tenant -c delete -u csri9 || true
```

停止 Keylime 服务：

```bash
systemctl stop keylime_verifier keylime_registrar 2>/dev/null || true
systemctl stop keylime-verifier keylime-registrar 2>/dev/null || true
```

在计算节点：

```bash
systemctl stop keylime_agent 2>/dev/null || true
systemctl stop keylime-agent 2>/dev/null || true
```

## 19. 参考资料

- Keylime Installation: <https://keylime.readthedocs.io/en/latest/installation.html>
- Keylime Overview: <https://keylime.readthedocs.io/en/latest/design/overview.html>
- Keylime REST APIs: <https://keylime.readthedocs.io/en/latest/rest_apis.html>
- keylime_tenant man page: <https://keylime.readthedocs.io/en/latest/man/keylime_tenant.1.html>
- Nova Flavors and Required Traits: <https://docs.openstack.org/nova/latest/user/flavors.html>
- osc-placement CLI: <https://docs.openstack.org/osc-placement/latest/cli/index.html>
- Placement Resource Provider Traits API: <https://docs.openstack.org/api-ref/placement/#resource-provider-traits>

