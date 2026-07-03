# Keylime 与 OpenStack 结合实验记录及下一步自动同步实验

日期：2026-07-02

## 1. 当前实验目标

第一阶段已经完成：

```text
Keylime attestation PASS
  -> csri9 被标记 CUSTOM_KEYLIME_ATTESTED
  -> trusted.keylime.small flavor 要求该 trait
  -> Nova 只把 trusted VM 调度到 csri9
  -> VM ACTIVE
```

下一步实验目标：

```text
不要再手工维护 Placement trait。

让一个同步脚本周期性读取 Keylime verifier 的 attestation_status：
  PASS       -> 给 csri9 resource provider 添加 CUSTOM_KEYLIME_ATTESTED
  非 PASS    -> 从 csri9 resource provider 移除 CUSTOM_KEYLIME_ATTESTED

然后验证：
  Keylime PASS 时 trusted VM 可以调度到 csri9
  Keylime 不可用或 FAIL 时 trusted VM 无法调度
```

## 2. 实验环境

OpenStack：

```text
控制节点：
  csri10 / 172.31.100.10

计算节点：
  csri9 / 172.31.100.9
  csri8 / 172.31.100.8

部署方式：
  Kolla / Docker

已验证：
  nova-compute csri9 up
  nova-compute csri8 up
  Keystone / Nova / Placement / Neutron / Glance 正常
```

Keylime：

```text
csri10:
  keylime-registrar: quay.io/keylime/keylime_registrar:v7.14.2
  keylime-verifier : quay.io/keylime/keylime_verifier:v7.14.2
  keylime-tenant   : quay.io/keylime/keylime_tenant:v7.14.2

csri9:
  keylime-agent    : quay.io/keylime/keylime_agent:latest

Keylime agent UUID:
  11111111-1111-4111-8111-000000000009

OpenStack trusted trait:
  CUSTOM_KEYLIME_ATTESTED

Trusted flavor:
  trusted.keylime.small
```

TPM：

```text
csri9:
  TPM 2.0 可用
  SHA256 PCR bank 可读
  可作为第一阶段可信计算节点

csri8:
  TPM 2.0 存在
  但 SHA256 PCR bank 当前为空
  第一阶段不接入可信池
```

## 3. 已完成实验结果

### 3.1 Keylime attestation PASS

`keylime-tenant status` 显示 verifier 中 agent 状态：

```text
attestation_status: PASS
last_successful_attestation: ...
```

说明 `csri9` 的 Keylime agent 已经被 verifier 成功验证。

### 3.2 Placement trait 生效

`csri9` resource provider 被添加：

```text
CUSTOM_KEYLIME_ATTESTED
```

`csri8` 没有该 trait。

### 3.3 trusted flavor 生效

`trusted.keylime.small` 的 extra specs：

```yaml
properties:
  trait:CUSTOM_KEYLIME_ATTESTED: required
```

### 3.4 正向验证

创建 VM：

```bash
openstack server create keylime-trusted-test-1 \
  --image "$IMAGE" \
  --flavor trusted.keylime.small \
  --nic net-id="$NET_A_ID"
```

结果：

```yaml
OS-EXT-SRV-ATTR:host: csri9
status: ACTIVE
```

结论：

```text
trusted flavor 确实调度到了 Keylime PASS 后被标记可信的 csri9。
```

### 3.5 反向验证

从 `csri9` 移除 `CUSTOM_KEYLIME_ATTESTED` 后，再创建 trusted VM。

结果：

```yaml
OS-EXT-SRV-ATTR:host: null
status: ERROR
fault:
  message: 'No valid host was found.'
```

结论：

```text
trusted flavor 不是碰巧落到 csri9，而是真的依赖 CUSTOM_KEYLIME_ATTESTED。
没有该 trait 时，Nova Placement 找不到合法 host。
```

## 4. 实验中遇到的问题及处理

### 4.1 latest 控制面镜像缺少配置文件

问题：

```text
quay.io/keylime/keylime_registrar:latest
quay.io/keylime/keylime_verifier:latest

启动时报：
Config file not found in ['/etc/keylime/logging.conf', '/usr/etc/keylime/logging.conf']
```

原因：

```text
latest 镜像里没有 /etc/keylime/*.conf 配置文件。
```

处理：

```text
控制面改用固定 release tag：v7.14.2
```

### 4.2 registrar / verifier 默认只监听 127.0.0.1

问题：

```text
127.0.0.1:8891
127.0.0.1:8881
```

`csri9` 无法访问。

处理：

```text
复制 v7.14.2 镜像内配置到 /opt/keylime-docker/config
把 registrar.conf 和 verifier.conf 的 ip 改成 "0.0.0.0"
```

### 4.3 v7.14.2 没有单独 keylime_agent 镜像

问题：

```text
quay.io/keylime/keylime_agent:v7.14.2: not found
```

处理：

```text
控制面继续使用 v7.14.2
agent 暂用 quay.io/keylime/keylime_agent:latest
并使用 agent 镜像自己的 agent.conf
```

说明：

```text
这是实验折中方案。生产化建议使用宿主机 Rust agent 或自建同版本 agent 镜像。
```

### 4.4 agent.conf 中 IP 地址必须加引号

问题：

```text
registrar_ip = 172.31.100.10

Error:
expected newline, found a period
```

处理：

```text
registrar_ip = "172.31.100.10"
```

### 4.5 MissingActionsDir

问题：

```text
MissingActionsDir { path: "/var/lib/keylime" }
```

处理：

```bash
install -d -m 0777 /opt/keylime-agent-docker/varlib/actions
install -d -m 0777 /opt/keylime-agent-docker/logs
```

并挂载：

```bash
-v /opt/keylime-agent-docker/varlib:/var/lib/keylime:rw
-v /opt/keylime-agent-docker/logs:/var/log/keylime:rw
```

### 4.6 TPM 设备 Permission denied

问题：

```text
Failed to open specified TCTI device file /dev/tpmrm0: Permission denied
```

原因：

```text
agent 在容器内降权为 keylime:tss 后，仍受 /dev/tpmrm0 文件权限限制。
```

实验处理：

```bash
[ -e /dev/tpmrm0 ] && chmod a+rw /dev/tpmrm0
[ -e /dev/tpm0 ] && chmod a+rw /dev/tpm0
```

生产化处理：

```text
用 udev 规则或对齐容器内外 tss group gid，不应长期 chmod 666。
```

### 4.7 registrar 端口使用错误

问题：

agent 日志：

```text
Building Registrar client: scheme=http, registrar=172.31.100.10:8891, TLS=false
Requesting registrar API version to http://172.31.100.10:8891/version
Network error
```

原因：

```text
8891 是 registrar TLS 端口，但 agent 用 HTTP 访问。
```

处理：

```text
agent -> registrar: 8890, HTTP
tenant -> registrar: 8891, TLS
tenant -> verifier : 8881, TLS
```

agent 环境变量：

```bash
-e KEYLIME_AGENT_REGISTRAR_PORT="8890"
-e KEYLIME_AGENT_REGISTRAR_TLS_ENABLED="false"
```

### 4.8 agent 不信任 tenant 的 mTLS 证书

问题：

```text
Keylime agent does not recognize mTLS certificate from tenant.
Check if agents trusted_client_ca is configured correctly
```

处理：

把控制面 CA 复制到 agent：

```bash
scp /opt/keylime-docker/varlib/cv_ca/cacert.crt \
  root@172.31.100.9:/opt/keylime-agent-docker/config/cacert.crt
```

agent 配置：

```text
enable_agent_mtls = true
trusted_client_ca = "/etc/keylime/cacert.crt"
```

agent 环境变量：

```bash
-e KEYLIME_AGENT_ENABLE_AGENT_MTLS="true"
-e KEYLIME_AGENT_TRUSTED_CLIENT_CA="/etc/keylime/cacert.crt"
```

### 4.9 TPM 没有 EK certificate

问题：

```text
No EK certificate found in TPM NVRAM
No EK cert provided, require_ek_cert option in config set to True
```

原因：

```text
当前 TPM NVRAM 中没有 EK certificate。
```

第一阶段处理：

```text
tenant.conf 和 verifier.conf 中设置 require_ek_cert = false
```

说明：

```text
这是第一阶段跑通链路的实验设置。
生产可信增强阶段应补充 EK cert 校验、平台证书链或自定义信任根。
```

### 4.10 旧 AK / nonce 校验异常

问题：

```text
TPM Quote ... is invalid for nonce
```

处理：

```text
删除旧 verifier/registrar 记录
清理 csri9 agent_data.json、server cert、payload key
重新启动 agent，让其以固定 UUID 重新注册
```

## 5. 固定 agent 重启命令

以后在 `csri9` 重启 agent 使用：

```bash
export KEYLIME_AGENT_IMAGE=quay.io/keylime/keylime_agent:latest
export KEYLIME_AGENT_ENTRYPOINT=/usr/bin/keylime_agent
export KEYLIME_AGENT_UUID_FIXED=11111111-1111-4111-8111-000000000009

docker rm -f keylime-agent 2>/dev/null || true

TPM_DEVICE=/dev/tpmrm0
[ -e "$TPM_DEVICE" ] || TPM_DEVICE=/dev/tpm0

[ -e /dev/tpmrm0 ] && chmod a+rw /dev/tpmrm0
[ -e /dev/tpm0 ] && chmod a+rw /dev/tpm0

install -d -m 0777 /opt/keylime-agent-docker/varlib/actions
install -d -m 0777 /opt/keylime-agent-docker/logs

docker run -d \
  --name keylime-agent \
  --restart unless-stopped \
  --network host \
  --privileged \
  --entrypoint "$KEYLIME_AGENT_ENTRYPOINT" \
  --device "$TPM_DEVICE:$TPM_DEVICE" \
  -e RUST_LOG=debug \
  -e TCTI="device:$TPM_DEVICE" \
  -e KEYLIME_AGENT_UUID="$KEYLIME_AGENT_UUID_FIXED" \
  -e KEYLIME_AGENT_IP="0.0.0.0" \
  -e KEYLIME_AGENT_PORT="9002" \
  -e KEYLIME_AGENT_CONTACT_IP="172.31.100.9" \
  -e KEYLIME_AGENT_CONTACT_PORT="9002" \
  -e KEYLIME_AGENT_REGISTRAR_IP="172.31.100.10" \
  -e KEYLIME_AGENT_REGISTRAR_PORT="8890" \
  -e KEYLIME_AGENT_REGISTRAR_TLS_ENABLED="false" \
  -e KEYLIME_AGENT_ENABLE_AGENT_MTLS="true" \
  -e KEYLIME_AGENT_TRUSTED_CLIENT_CA="/etc/keylime/cacert.crt" \
  -e KEYLIME_AGENT_REVOCATION_ACTIONS_DIR="/var/lib/keylime/actions" \
  -v /opt/keylime-agent-docker/config:/etc/keylime:ro \
  -v /opt/keylime-agent-docker/varlib:/var/lib/keylime:rw \
  -v /opt/keylime-agent-docker/logs:/var/log/keylime:rw \
  -v /sys/class/tpm:/sys/class/tpm:ro \
  -v /sys/kernel/security:/sys/kernel/security:ro \
  "$KEYLIME_AGENT_IMAGE"

sleep 8

docker ps --filter name=keylime-agent \
  --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'

docker logs --tail 200 keylime-agent

ss -lntp | grep 9002 || true
```

## 6. 下一步实验：Keylime 自动同步 Placement trait

### 6.1 实验目标

当前 trait 是手工加减的。下一步要验证：

```text
Keylime verifier 状态 PASS：
  自动给 csri9 加 CUSTOM_KEYLIME_ATTESTED

Keylime verifier 状态不是 PASS，或状态过期：
  自动从 csri9 移除 CUSTOM_KEYLIME_ATTESTED
```

### 6.2 在 csri10 创建同步目录

```bash
mkdir -p /opt/keylime-openstack-sync
```

### 6.3 创建同步脚本

在 `csri10` 创建：

```bash
cat >/opt/keylime-openstack-sync/keylime-placement-sync.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

OPENRC=/etc/kolla/admin-openrc.sh
KEYLIME_DIR=/opt/keylime-docker

AGENT_UUID="11111111-1111-4111-8111-000000000009"
RP_NAME="csri9"
TRUSTED_TRAIT="CUSTOM_KEYLIME_ATTESTED"

VERIFIER_IP="172.31.100.10"
VERIFIER_PORT="8881"
REGISTRAR_IP="172.31.100.10"
REGISTRAR_PORT="8891"

LOG_FILE="/var/log/keylime-openstack-sync-last.log"

source "$OPENRC"
export OS_PLACEMENT_API_VERSION=1.17

RP_UUID="$(openstack resource provider list --name "$RP_NAME" -f value -c uuid)"

openstack trait create "$TRUSTED_TRAIT" >/dev/null 2>&1 || true

get_keylime_status() {
  local out
  set +e
  out="$(
    cd "$KEYLIME_DIR" && \
    docker compose run --rm keylime-tenant \
      -c status \
      -u "$AGENT_UUID" \
      -v "$VERIFIER_IP" \
      -vp "$VERIFIER_PORT" \
      -r "$REGISTRAR_IP" \
      -rp "$REGISTRAR_PORT" 2>&1
  )"
  local rc=$?
  set -e

  printf '%s\n' "$out" > "$LOG_FILE"

  if [ "$rc" -eq 0 ] && printf '%s\n' "$out" | grep -q '"attestation_status": "PASS"'; then
    printf 'PASS\n'
  else
    printf 'NOT_PASS\n'
  fi
}

rp_add_trait() {
  local traits
  traits="$(openstack resource provider trait list "$RP_UUID" -f value -c name 2>/dev/null | sort -u)"

  if ! printf '%s\n' "$traits" | grep -qx "$TRUSTED_TRAIT"; then
    traits="$(printf '%s\n%s\n' "$traits" "$TRUSTED_TRAIT" | sed '/^$/d' | sort -u)"
  fi

  local cmd=(openstack resource provider trait set)
  while IFS= read -r t; do
    [ -n "$t" ] && cmd+=(--trait "$t")
  done < <(printf '%s\n' "$traits")
  cmd+=("$RP_UUID")
  "${cmd[@]}"
}

rp_remove_trait() {
  local traits
  traits="$(openstack resource provider trait list "$RP_UUID" -f value -c name 2>/dev/null | grep -vx "$TRUSTED_TRAIT" || true)"

  local cmd=(openstack resource provider trait set)
  while IFS= read -r t; do
    [ -n "$t" ] && cmd+=(--trait "$t")
  done < <(printf '%s\n' "$traits")
  cmd+=("$RP_UUID")
  "${cmd[@]}"
}

status="$(get_keylime_status)"

if [ "$status" = "PASS" ]; then
  echo "Keylime status PASS: ensure $TRUSTED_TRAIT on $RP_NAME"
  rp_add_trait
else
  echo "Keylime status NOT_PASS: remove $TRUSTED_TRAIT from $RP_NAME"
  rp_remove_trait
fi

echo "Current $RP_NAME traits containing Keylime:"
openstack resource provider trait list "$RP_UUID" | grep "$TRUSTED_TRAIT" || true
EOF

chmod +x /opt/keylime-openstack-sync/keylime-placement-sync.sh
```

### 6.4 PASS 场景验证

先确认 Keylime 当前是 PASS：

```bash
cd /opt/keylime-docker
export KEYLIME_AGENT_UUID_FIXED="11111111-1111-4111-8111-000000000009"

docker compose run --rm keylime-tenant \
  -c status \
  -u "$KEYLIME_AGENT_UUID_FIXED" \
  -v 172.31.100.10 \
  -vp 8881 \
  -r 172.31.100.10 \
  -rp 8891
```

运行同步脚本：

```bash
/opt/keylime-openstack-sync/keylime-placement-sync.sh
```

预期：

```text
Keylime status PASS: ensure CUSTOM_KEYLIME_ATTESTED on csri9
```

验证 trait：

```bash
source /etc/kolla/admin-openrc.sh
export OS_PLACEMENT_API_VERSION=1.17
export RP_CSRI9="$(openstack resource provider list --name csri9 -f value -c uuid)"

openstack resource provider trait list "$RP_CSRI9" | grep CUSTOM_KEYLIME_ATTESTED
```

### 6.5 非 PASS 场景验证

在 `csri9` 停止 agent：

```bash
docker stop keylime-agent
```

等待 verifier 几轮检测：

```bash
sleep 30
```

在 `csri10` 运行同步脚本：

```bash
/opt/keylime-openstack-sync/keylime-placement-sync.sh
```

预期：

```text
Keylime status NOT_PASS: remove CUSTOM_KEYLIME_ATTESTED from csri9
```

验证 trait 被移除：

```bash
openstack resource provider trait list "$RP_CSRI9" | grep CUSTOM_KEYLIME_ATTESTED || \
  echo "OK: csri9 trusted trait removed"
```

创建 trusted VM，预期失败：

```bash
export FAIL_VM=keylime-trusted-sync-fail

openstack server create "$FAIL_VM" \
  --image "$IMAGE" \
  --flavor trusted.keylime.small \
  --nic net-id="$NET_A_ID"

sleep 20

openstack server show "$FAIL_VM" \
  -c status \
  -c OS-EXT-SRV-ATTR:host \
  -c fault \
  -f yaml
```

预期：

```text
status: ERROR
No valid host was found.
```

### 6.6 恢复 PASS 场景

在 `csri9` 使用固定命令重启 agent。

等待 agent activated：

```bash
docker logs --tail 80 keylime-agent
ss -lntp | grep 9002
```

在 `csri10` 等待 Keylime PASS：

```bash
sleep 20

cd /opt/keylime-docker
docker compose run --rm keylime-tenant \
  -c status \
  -u "$KEYLIME_AGENT_UUID_FIXED" \
  -v 172.31.100.10 \
  -vp 8881 \
  -r 172.31.100.10 \
  -rp 8891
```

运行同步脚本：

```bash
/opt/keylime-openstack-sync/keylime-placement-sync.sh
```

预期 trait 恢复：

```bash
openstack resource provider trait list "$RP_CSRI9" | grep CUSTOM_KEYLIME_ATTESTED
```

清理失败 VM：

```bash
openstack server delete "$FAIL_VM" 2>/dev/null || true
```

## 7. 自动同步实验结论判断

如果以下三点都成立，Phase 2 成功：

```text
1. Keylime PASS 时，同步脚本会给 csri9 添加 CUSTOM_KEYLIME_ATTESTED。
2. keylime-agent 停止或状态非 PASS 时，同步脚本会移除该 trait。
3. trait 移除后，trusted.keylime.small 无法调度，报 No valid host。
```

这说明：

```text
Keylime 不只是被动证明节点可信，
而是已经变成 OpenStack 调度决策的一部分。
```

## 8. 后续增强方向

后续可以继续做：

```text
1. 把同步脚本改为 systemd timer 或 Docker 容器。
2. 用 verifier REST API 替代 tenant CLI，提高效率。
3. 加入 last_successful_attestation 新鲜度判断，防止陈旧 PASS。
4. 接入 csri8，先修复 SHA256 PCR bank。
5. 引入 EK certificate / 平台证书链校验。
6. 将 trusted flavor 变为项目级私有 flavor，只给可信租户使用。
7. 把 Keylime fail 事件接入告警、隔离、迁移或禁止新建 VM。
```

