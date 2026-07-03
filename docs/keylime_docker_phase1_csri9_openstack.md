# Docker 部署 Keylime 并接入 OpenStack：第一阶段只接入 csri9

日期：2026-07-02

## 1. 目标

基于你的当前环境，第一阶段只把 `csri9` 纳入可信计算池：

```text
csri10:
  Docker 部署 Keylime verifier / registrar / tenant
  部署 keylime-openstack-sync
  维护 OpenStack Placement trait

csri9:
  Docker 部署 Keylime agent
  使用宿主机 TPM 2.0 / SHA256 PCR bank

csri8:
  暂不接入可信池
  不加 CUSTOM_KEYLIME_ATTESTED trait
```

最终闭环：

```text
Keylime agent(csri9)
  -> Keylime verifier PASS
  -> csri9 Placement resource provider 加 CUSTOM_KEYLIME_ATTESTED
  -> trusted.keylime.small flavor 要求该 trait
  -> trusted VM 只能调度到 csri9
```

官方依据：

- Keylime 安装文档说明 verifier、registrar、tenant 可用 Docker 镜像部署，官方镜像在 Quay.io 的 Keylime organization 下，并且每次 commit/release 会自动生成镜像：<https://keylime.readthedocs.io/en/latest/installation.html>
- Keylime 官方 README 说明 Keylime 由 verifier、registrar、agent 三个主要组件组成；agent 是被测量的远端机器组件：<https://github.com/keylime/keylime>
- Keylime 文档说明 Rust agent 是当前官方 agent，默认配置文件是 `/etc/keylime/agent.conf`，不同于旧 Python agent 配置：<https://keylime.readthedocs.io/en/latest/installation.html>

## 2. 重要说明

Keylime 官方安装页明确提到 Docker 镜像覆盖：

```text
verifier
registrar
tenant
```

agent 是否使用 Docker，要看你的镜像仓库里是否有可用 agent 镜像，以及容器是否能正确访问宿主机：

```text
/dev/tpmrm0 或 /dev/tpm0
/sys/class/tpm
/sys/kernel/security
IMA / UEFI event log 相关路径
```

因此本方案提供两条 agent 路径：

```text
路径 A：csri9 agent 也 Docker 化。优先尝试。
路径 B：如果 agent 容器镜像或 TPM/IMA 挂载不通，则 csri9 agent 用宿主机部署，控制面仍 Docker 化。
```

第一阶段推荐先跑通：

```text
Docker registrar/verifier/tenant + Docker agent
```

若 agent 容器卡住，不要在这里硬耗，先切到：

```text
Docker registrar/verifier/tenant + host agent
```

OpenStack 集成部分不受影响。

## 3. 镜像变量

由于 Quay 页面可能需要浏览器 JS，命令行环境里建议先用变量集中管理镜像名。

在 `csri10` 和 `csri9` 都设置：

```bash
export KEYLIME_VERSION=latest

export KEYLIME_REGISTRAR_IMAGE=quay.io/keylime/keylime_registrar:${KEYLIME_VERSION}
export KEYLIME_VERIFIER_IMAGE=quay.io/keylime/keylime_verifier:${KEYLIME_VERSION}
export KEYLIME_TENANT_IMAGE=quay.io/keylime/keylime_tenant:${KEYLIME_VERSION}
export KEYLIME_AGENT_IMAGE=quay.io/keylime/keylime_agent:${KEYLIME_VERSION}
```

拉取前先验证镜像是否存在：

```bash
docker pull "$KEYLIME_REGISTRAR_IMAGE"
docker pull "$KEYLIME_VERIFIER_IMAGE"
docker pull "$KEYLIME_TENANT_IMAGE"
docker pull "$KEYLIME_AGENT_IMAGE"
```

如果 agent 镜像不存在，但 verifier/registrar/tenant 存在，第一阶段改用宿主机 agent。

如果镜像 tag 不存在，尝试具体 release tag，例如：

```bash
export KEYLIME_VERSION=7.14.2
```

如果你的环境无法访问 `quay.io`，需要先在可联网环境拉取并导出：

```bash
docker pull quay.io/keylime/keylime_verifier:latest
docker save quay.io/keylime/keylime_verifier:latest -o keylime_verifier.tar
```

再导入到实验节点：

```bash
docker load -i keylime_verifier.tar
```

## 4. csri10：准备目录

在 `csri10`：

```bash
mkdir -p /opt/keylime-docker/{config,varlib,logs}
mkdir -p /opt/keylime-docker/varlib/{registrar,verifier,tenant}
```

Keylime 默认会在 `/var/lib/keylime/cv_ca/` 生成 verifier CA 和 mTLS 证书。容器化时要把 `/var/lib/keylime` 持久化，否则容器重建后证书和数据库会丢。

不要把一个空目录直接挂载到 `/etc/keylime`。这样会遮住镜像内置的 `logging.conf`、`verifier.conf`、`registrar.conf` 等配置，容器会报：

```text
Config file not found in ['/etc/keylime/logging.conf', '/usr/etc/keylime/logging.conf']
FileNotFoundError: ... keylime/config/logging.conf
```

第一阶段先使用镜像内置 `/etc/keylime` 配置，只持久化 `/var/lib/keylime` 和日志。后续需要自定义配置时，再先从镜像中复制默认配置出来，修改后再挂载。

## 5. csri10：生成 Docker Compose

在 `csri10` 创建 `/opt/keylime-docker/docker-compose.yml`：

```bash
cat >/opt/keylime-docker/docker-compose.yml <<'EOF'
services:
  keylime-registrar:
    image: ${KEYLIME_REGISTRAR_IMAGE}
    container_name: keylime-registrar
    restart: unless-stopped
    network_mode: host
    volumes:
      - /opt/keylime-docker/varlib:/var/lib/keylime
      - /opt/keylime-docker/logs:/var/log/keylime

  keylime-verifier:
    image: ${KEYLIME_VERIFIER_IMAGE}
    container_name: keylime-verifier
    restart: unless-stopped
    network_mode: host
    depends_on:
      - keylime-registrar
    volumes:
      - /opt/keylime-docker/varlib:/var/lib/keylime
      - /opt/keylime-docker/logs:/var/log/keylime

  keylime-tenant:
    image: ${KEYLIME_TENANT_IMAGE}
    container_name: keylime-tenant
    network_mode: host
    profiles: ["tools"]
    volumes:
      - /opt/keylime-docker/varlib:/var/lib/keylime
      - /opt/keylime-docker/logs:/var/log/keylime
EOF
```

创建 `.env`：

```bash
cat >/opt/keylime-docker/.env <<EOF
KEYLIME_REGISTRAR_IMAGE=${KEYLIME_REGISTRAR_IMAGE}
KEYLIME_VERIFIER_IMAGE=${KEYLIME_VERIFIER_IMAGE}
KEYLIME_TENANT_IMAGE=${KEYLIME_TENANT_IMAGE}
EOF
```

启动：

```bash
cd /opt/keylime-docker
docker compose pull
docker compose up -d keylime-registrar keylime-verifier
docker ps | grep keylime
```

查看日志：

```bash
docker logs --tail 100 keylime-registrar
docker logs --tail 100 keylime-verifier
```

检查端口：

```bash
ss -lntp | egrep '8881|8891'
```

预期：

```text
8881 verifier
8891 registrar
```

## 6. csri10：确认 CA 是否生成

```bash
find /opt/keylime-docker/varlib -maxdepth 4 -type f | sort
find /opt/keylime-docker/varlib -path '*cv_ca*' -type f -maxdepth 5
```

常见路径：

```text
/opt/keylime-docker/varlib/cv_ca/cacert.crt
/opt/keylime-docker/varlib/cv_ca/client-cert.crt
/opt/keylime-docker/varlib/cv_ca/client-private.pem
```

如果没有生成，查看 verifier 日志：

```bash
docker logs keylime-verifier
```

## 7. csri9：准备 Docker agent

在 `csri9` 先确认 TPM：

```bash
tpm2_getcap pcrs
tpm2_pcrread sha256:0,1,2,3,4,5,6,7
ls -l /dev/tpm* /sys/class/tpm
```

准备目录：

```bash
mkdir -p /opt/keylime-agent-docker/{config,varlib,logs}
```

从 `csri10` 复制 CA：

```bash
scp /opt/keylime-docker/varlib/cv_ca/cacert.crt root@172.31.100.9:/opt/keylime-agent-docker/config/cacert.crt
```

## 8. csri9：启动 agent 容器

在 `csri9`：

```bash
export KEYLIME_VERSION=latest
export KEYLIME_AGENT_IMAGE=quay.io/keylime/keylime_agent:${KEYLIME_VERSION}
docker pull "$KEYLIME_AGENT_IMAGE"
```

先用 host network 和 privileged 模式跑通第一阶段。后续再收紧权限。

```bash
docker run -d \
  --name keylime-agent \
  --restart unless-stopped \
  --network host \
  --privileged \
  --device /dev/tpmrm0:/dev/tpmrm0 \
  --device /dev/tpm0:/dev/tpm0 \
  -e TCTI=device:/dev/tpmrm0 \
  -v /opt/keylime-agent-docker/config:/etc/keylime \
  -v /opt/keylime-agent-docker/varlib:/var/lib/keylime \
  -v /opt/keylime-agent-docker/logs:/var/log/keylime \
  -v /sys/class/tpm:/sys/class/tpm:ro \
  -v /sys/kernel/security:/sys/kernel/security:ro \
  "$KEYLIME_AGENT_IMAGE" \
  keylime_agent
```

如果 `/dev/tpmrm0` 不存在但 `/dev/tpm0` 存在：

```bash
docker rm -f keylime-agent

docker run -d \
  --name keylime-agent \
  --restart unless-stopped \
  --network host \
  --privileged \
  --device /dev/tpm0:/dev/tpm0 \
  -e TCTI=device:/dev/tpm0 \
  -v /opt/keylime-agent-docker/config:/etc/keylime \
  -v /opt/keylime-agent-docker/varlib:/var/lib/keylime \
  -v /opt/keylime-agent-docker/logs:/var/log/keylime \
  -v /sys/class/tpm:/sys/class/tpm:ro \
  -v /sys/kernel/security:/sys/kernel/security:ro \
  "$KEYLIME_AGENT_IMAGE" \
  keylime_agent
```

查看：

```bash
docker ps | grep keylime-agent
docker logs --tail 100 keylime-agent
ss -lntp | grep 9002 || true
```

从 `csri10` 测试：

```bash
nc -vz 172.31.100.9 9002
```

如果 agent 容器启动失败，先不要改 OpenStack，优先看：

```bash
docker logs keylime-agent
docker exec -it keylime-agent sh
ls -l /dev/tpm*
```

## 9. 如果 agent 镜像不可用

如果：

```bash
docker pull quay.io/keylime/keylime_agent:latest
```

失败，但控制面镜像可用，有两个选择：

### 9.1 临时改用宿主机 agent

这是最省时间的方式：

```bash
apt-get install -y keylime-agent tpm2-tools
```

然后按宿主机 agent 配置运行。控制面仍然是 Docker。

### 9.2 自行构建 agent 镜像

需要使用 Keylime Rust agent 仓库构建镜像。这个方式适合后续工程化，不建议作为第一阶段最短路径。

## 10. csri10：用 tenant 容器注册 csri9

在 `csri10`：

```bash
cd /opt/keylime-docker

docker compose run --rm keylime-tenant \
  -c add \
  -t 172.31.100.9 \
  -u csri9 \
  -v 172.31.100.10 \
  -vp 8881 \
  -r 172.31.100.10 \
  -rp 8891
```

查看状态：

```bash
docker compose run --rm keylime-tenant -c status -u csri9
docker compose run --rm keylime-tenant -c cvlist
docker compose run --rm keylime-tenant -c reglist
```

如果 tenant 容器找不到 CA 或证书，确认 `/opt/keylime-docker/varlib/cv_ca/` 是否挂进容器：

```bash
docker compose run --rm keylime-tenant sh -c 'find /var/lib/keylime -maxdepth 4 -type f | sort'
```

## 11. csri10：REST 查询 attestation_status

```bash
export KEYLIME_VERIFIER_URL="https://172.31.100.10:8881"
export KEYLIME_CA="/opt/keylime-docker/varlib/cv_ca/cacert.crt"
export KEYLIME_CLIENT_CERT="/opt/keylime-docker/varlib/cv_ca/client-cert.crt"
export KEYLIME_CLIENT_KEY="/opt/keylime-docker/varlib/cv_ca/client-private.pem"
```

查询：

```bash
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
  "$KEYLIME_VERIFIER_URL/v2.5/agents/csri9" \
  | jq -r '.results.attestation_status // "UNKNOWN"'
```

期望：

```text
PASS
```

如果是 `PENDING`，等 verifier 下一轮。  
如果是 `FAIL`，先查日志。

## 12. OpenStack Placement：只给 csri9 加 trait

在 `csri10`：

```bash
source /etc/kolla/admin-openrc.sh
export OS_PLACEMENT_API_VERSION=1.17
export TRUSTED_TRAIT=CUSTOM_KEYLIME_ATTESTED

export RP_CSRI9="$(openstack resource provider list --name csri9 -f value -c uuid)"
export RP_CSRI8="$(openstack resource provider list --name csri8 -f value -c uuid)"

openstack trait create "$TRUSTED_TRAIT" 2>/dev/null || true
```

定义函数：

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
```

手工验证：

```bash
rp_add_trait "$RP_CSRI9" "$TRUSTED_TRAIT"
rp_remove_trait "$RP_CSRI8" "$TRUSTED_TRAIT"

openstack resource provider trait list "$RP_CSRI9"
openstack resource provider trait list "$RP_CSRI8"
```

## 13. 创建 trusted flavor

```bash
source /etc/kolla/admin-openrc.sh

export TRUSTED_PROJECT=project-keylime-trusted
export TRUSTED_USER=keylime-trusted-user
export TRUSTED_PASS='KeylimeTrusted123!'
export TRUSTED_FLAVOR=trusted.keylime.small

openstack project show "$TRUSTED_PROJECT" >/dev/null 2>&1 || openstack project create "$TRUSTED_PROJECT"
openstack user show "$TRUSTED_USER" >/dev/null 2>&1 || openstack user create "$TRUSTED_USER" --password "$TRUSTED_PASS"
openstack role add --project "$TRUSTED_PROJECT" --user "$TRUSTED_USER" member

openstack flavor show "$TRUSTED_FLAVOR" >/dev/null 2>&1 || \
  openstack flavor create "$TRUSTED_FLAVOR" --private --ram 2048 --disk 10 --vcpus 1

openstack flavor set "$TRUSTED_FLAVOR" --property trait:"$TRUSTED_TRAIT"=required
openstack flavor set --project "$TRUSTED_PROJECT" "$TRUSTED_FLAVOR"

openstack flavor show "$TRUSTED_FLAVOR" -c name -c properties -f yaml
```

## 14. 创建 trusted 网络和 VM

```bash
export OS_AUTH_URL="${OS_AUTH_URL:-$(openstack endpoint list --service identity --interface public -f value -c URL | head -n 1)}"

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

export TRUSTED_NET_ID="$(openstack network show keylime-trusted-net -f value -c id)"
export IMAGE="$(openstack image list -f value -c Name | head -n 1)"
```

创建：

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
    --nic net-id="$TRUSTED_NET_ID"
```

查看落点：

```bash
source /etc/kolla/admin-openrc.sh

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
status: ACTIVE
OS-EXT-SRV-ATTR:host: csri9
```

## 15. Docker 同步器

第一阶段可以先用宿主机脚本同步 OpenStack trait，也可以把同步器做成一个轻量容器。

更简单的第一阶段方式是宿主机脚本，因为它要用 `/etc/kolla/admin-openrc.sh` 和 OpenStack CLI。

映射文件只写 csri9：

```bash
mkdir -p /etc/keylime-openstack-sync

cat >/etc/keylime-openstack-sync/nodes.env <<'EOF'
csri9 csri9
EOF
```

同步器逻辑：

```text
curl Keylime verifier /v2.5/agents/csri9
如果 attestation_status == PASS:
  给 csri9 provider 加 CUSTOM_KEYLIME_ATTESTED
否则:
  从 csri9 provider 移除 CUSTOM_KEYLIME_ATTESTED
```

等第一阶段跑通后，再把这个脚本容器化。不要一开始同时调试 Docker Keylime、Docker agent、Docker sync 三个变量，排错会很绕。

## 16. 验证失败路径

移除 csri9 trait：

```bash
source /etc/kolla/admin-openrc.sh
rp_remove_trait "$RP_CSRI9" "$TRUSTED_TRAIT"
```

再次创建 trusted VM：

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
    --nic net-id="$TRUSTED_NET_ID"
```

预期：

```text
No valid host
或实例进入 ERROR
```

恢复：

```bash
rp_add_trait "$RP_CSRI9" "$TRUSTED_TRAIT"
```

## 17. 常见问题

### 17.1 verifier/registrar 容器启动后没有端口

检查命令是否正确：

```bash
docker logs keylime-verifier
docker logs keylime-registrar
docker exec -it keylime-verifier sh -c 'which keylime_verifier; ls -l /etc/keylime /var/lib/keylime'
```

不同镜像可能入口点已经是对应组件，不需要 `command`。若日志显示找不到 `keylime_verifier`，查看镜像帮助：

```bash
docker run --rm "$KEYLIME_VERIFIER_IMAGE" --help
```

### 17.2 agent 容器无法访问 TPM

检查：

```bash
docker exec -it keylime-agent sh -c 'ls -l /dev/tpm*; echo $TCTI'
docker logs keylime-agent
```

如果容器内没有 `/dev/tpmrm0`，确认宿主机是否有该设备。第一阶段可以用 `--privileged`，跑通后再收紧。

### 17.3 mTLS 失败

确认 CA：

```bash
ls -l /opt/keylime-docker/varlib/cv_ca/
ls -l /opt/keylime-agent-docker/config/cacert.crt
```

CA 必须从 verifier 侧复制到 agent 侧。

### 17.4 REST API 不是 v2.5

查看容器版本：

```bash
docker exec keylime-verifier keylime_verifier --version || true
docker logs keylime-verifier | head -n 30
```

如果你的 Keylime 版本较旧，`attestation_status` 字段可能不在 v2.5 响应里，需要按实际 API 调整同步器。

## 18. 第一阶段结论

成功后你能证明：

```text
1. csri9 的 TPM attestation 能由 Docker Keylime 控制面验证。
2. Keylime PASS 可以转化为 Placement trait。
3. trusted flavor 可以强制 Nova 只选择 csri9。
4. csri8 即使是 nova-compute，也因为没有 trait 不会承载 trusted VM。
```

这就是 Keylime 接入 OpenStack 的最小正确闭环。

## 19. 参考资料

- Keylime Installation: <https://keylime.readthedocs.io/en/latest/installation.html>
- Keylime Overview: <https://keylime.readthedocs.io/en/latest/design/overview.html>
- Keylime REST APIs: <https://keylime.readthedocs.io/en/latest/rest_apis.html>
- Keylime GitHub README: <https://github.com/keylime/keylime>
- Nova flavor traits: <https://docs.openstack.org/nova/latest/user/flavors.html>
- osc-placement CLI: <https://docs.openstack.org/osc-placement/latest/cli/index.html>

