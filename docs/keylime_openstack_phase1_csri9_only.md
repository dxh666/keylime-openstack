# Keylime + OpenStack 第一阶段部署方案：只接入 csri9

日期：2026-07-02

## 1. 第一阶段目标

当前 TPM 预检查结果：

```text
csri9:
  TPM 2.0 正常
  SHA256 PCR bank 可用
  可作为第一阶段 Keylime agent 节点

csri8:
  TPM 2.0 存在
  但 SHA256 PCR bank 未启用
  第一阶段暂不纳入可信池
```

第一阶段只实现：

```text
Keylime attests csri9
        |
        v
csri9 PASS -> OpenStack Placement trait: CUSTOM_KEYLIME_ATTESTED
        |
        v
trusted flavor requires CUSTOM_KEYLIME_ATTESTED
        |
        v
trusted VM 只能调度到 csri9
```

`csri8` 暂时不加可信 trait，用来证明 trusted flavor 不会调度到未纳入可信池的 compute。

## 2. 节点角色

```text
csri10 / 172.31.100.10
  - OpenStack controller/API
  - Keylime registrar
  - Keylime verifier
  - keylime-openstack-sync

csri9 / 172.31.100.9
  - nova-compute
  - Keylime agent
  - TPM 2.0 SHA256 PCR bank 可用

csri8 / 172.31.100.8
  - nova-compute
  - 暂不接入 Keylime trusted pool
```

## 3. OpenStack 预检查

在 `csri10`：

```bash
source /etc/kolla/admin-openrc.sh
export OS_PLACEMENT_API_VERSION=1.17

openstack compute service list --service nova-compute
openstack hypervisor list
openstack resource provider list
```

确认：

```text
nova-compute csri9 enabled/up
nova-compute csri8 enabled/up
resource provider 中存在 csri9 和 csri8
```

设置 csri9/csri8 resource provider UUID：

```bash
export RP_CSRI9="$(openstack resource provider list --name csri9 -f value -c uuid)"
export RP_CSRI8="$(openstack resource provider list --name csri8 -f value -c uuid)"

echo "RP_CSRI9=$RP_CSRI9"
echo "RP_CSRI8=$RP_CSRI8"
```

## 4. csri9 TPM 再确认

在 `csri9`：

```bash
tpm2_getcap pcrs
tpm2_pcrread sha256:0,1,2,3,4,5,6,7
ls -l /dev/tpm* /sys/class/tpm 2>/dev/null || true
```

必须看到：

```text
sha256:
  0: 0x...
  1: 0x...
```

如果 `/dev/tpmrm0` 存在，后续优先使用：

```bash
export TPM2TOOLS_TCTI=device:/dev/tpmrm0
```

## 5. 安装 Keylime

先检查包名。三台机器都可以查，但第一阶段真正需要 `csri10` 和 `csri9`。

```bash
apt-get update
apt-cache search keylime
apt-cache policy keylime keylime-agent keylime-verifier keylime-registrar keylime-tenant 2>/dev/null || true
```

如果 Ubuntu 仓库或你的内部仓库提供这些包：

在 `csri10`：

```bash
apt-get install -y \
  keylime-verifier \
  keylime-registrar \
  keylime-tenant \
  tpm2-tools \
  jq \
  curl
```

在 `csri9`：

```bash
apt-get install -y \
  keylime-agent \
  tpm2-tools \
  jq \
  curl
```

如果包名不同，以 `apt-cache search keylime` 输出为准。

如果没有 Ubuntu 包，第一阶段建议：

```text
csri10: registrar/verifier 可以用 Keylime Docker 或源码/manual install
csri9: agent 建议宿主机部署，避免容器访问 TPM/IMA/UEFI log 的权限复杂度
```

## 6. 启动 Keylime 控制面

在 `csri10`：

```bash
systemctl list-unit-files | grep -i keylime
ls -l /etc/keylime
```

启动 registrar/verifier，服务名按实际存在的选择：

```bash
systemctl enable --now keylime_registrar || systemctl enable --now keylime-registrar
systemctl enable --now keylime_verifier  || systemctl enable --now keylime-verifier

systemctl status keylime_registrar --no-pager || systemctl status keylime-registrar --no-pager
systemctl status keylime_verifier --no-pager  || systemctl status keylime-verifier --no-pager

ss -lntp | egrep '8881|8891'
```

目标：

```text
verifier:  172.31.100.10:8881 或 0.0.0.0:8881
registrar: 172.31.100.10:8891 或 0.0.0.0:8891
```

检查 Keylime CA：

```bash
ls -l /var/lib/keylime/cv_ca/
```

如果存在 `cacert.crt`，复制给 `csri9`：

```bash
scp /var/lib/keylime/cv_ca/cacert.crt root@172.31.100.9:/etc/keylime/cacert.crt
```

## 7. 启动 csri9 Keylime agent

在 `csri9`：

```bash
systemctl list-unit-files | grep -i keylime
ls -l /etc/keylime
```

确认 agent 配置文件，常见为：

```text
/etc/keylime/agent.conf
```

目标配置：

```text
agent listen IP: 172.31.100.9
registrar:       172.31.100.10:8891
verifier:        172.31.100.10:8881
CA:              /etc/keylime/cacert.crt
TPM TCTI:         /dev/tpmrm0 优先
```

启动：

```bash
systemctl enable --now keylime_agent || systemctl enable --now keylime-agent
systemctl status keylime_agent --no-pager || systemctl status keylime-agent --no-pager

ss -lntp | grep -i keylime || ss -lntp | grep 9002
```

从 `csri10` 测试 agent 端口：

```bash
nc -vz 172.31.100.9 9002
```

如果没有 `nc`：

```bash
timeout 3 bash -c '</dev/tcp/172.31.100.9/9002' && echo ok
```

## 8. 注册 csri9 agent

在 `csri10`：

```bash
sudo keylime_tenant -c add \
  -t 172.31.100.9 \
  -u csri9 \
  -v 172.31.100.10 \
  -vp 8881 \
  -r 172.31.100.10 \
  -rp 8891
```

查看状态：

```bash
sudo keylime_tenant -c status -u csri9
sudo keylime_tenant -c cvlist
sudo keylime_tenant -c reglist
```

如果失败，查看日志：

在 `csri10`：

```bash
journalctl -u keylime_verifier -n 200 --no-pager || journalctl -u keylime-verifier -n 200 --no-pager
journalctl -u keylime_registrar -n 200 --no-pager || journalctl -u keylime-registrar -n 200 --no-pager
```

在 `csri9`：

```bash
journalctl -u keylime_agent -n 200 --no-pager || journalctl -u keylime-agent -n 200 --no-pager
```

## 9. Keylime REST 状态查询

在 `csri10`：

```bash
export KEYLIME_VERIFIER_URL="https://172.31.100.10:8881"
export KEYLIME_CA="/var/lib/keylime/cv_ca/cacert.crt"
export KEYLIME_CLIENT_CERT="/var/lib/keylime/cv_ca/client-cert.crt"
export KEYLIME_CLIENT_KEY="/var/lib/keylime/cv_ca/client-private.pem"
```

查询 csri9：

```bash
curl -sS \
  --cacert "$KEYLIME_CA" \
  --cert "$KEYLIME_CLIENT_CERT" \
  --key "$KEYLIME_CLIENT_KEY" \
  "$KEYLIME_VERIFIER_URL/v2.5/agents/csri9" | jq .
```

只取 attestation 状态：

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

如果是 `PENDING`，等待一轮 verifier 轮询后重试。  
如果是 `FAIL`，先不要接 OpenStack，先看 Keylime verifier/agent 日志。

## 10. Placement trait 手工闭环

在 `csri10`：

```bash
source /etc/kolla/admin-openrc.sh
export OS_PLACEMENT_API_VERSION=1.17
export TRUSTED_TRAIT=CUSTOM_KEYLIME_ATTESTED

export RP_CSRI9="$(openstack resource provider list --name csri9 -f value -c uuid)"
export RP_CSRI8="$(openstack resource provider list --name csri8 -f value -c uuid)"

openstack trait create "$TRUSTED_TRAIT" 2>/dev/null || true
openstack trait show "$TRUSTED_TRAIT"
```

定义安全的 trait 添加/移除函数：

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

只给 csri9 加可信 trait，确保 csri8 没有：

```bash
rp_add_trait "$RP_CSRI9" "$TRUSTED_TRAIT"
rp_remove_trait "$RP_CSRI8" "$TRUSTED_TRAIT"

openstack resource provider trait list "$RP_CSRI9"
openstack resource provider trait list "$RP_CSRI8"
```

预期：

```text
csri9: 有 CUSTOM_KEYLIME_ATTESTED
csri8: 没有 CUSTOM_KEYLIME_ATTESTED
```

## 11. 创建 trusted flavor 和 trusted project

在 `csri10`：

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

## 12. 创建 trusted project 网络

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
  network show keylime-trusted-net >/dev/null 2>&1 || \
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
  subnet show keylime-trusted-subnet >/dev/null 2>&1 || \
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
echo "$TRUSTED_NET_ID"
```

## 13. 创建 trusted VM，验证只能落到 csri9

选择镜像：

```bash
export IMAGE="$(openstack image list -f value -c Name | head -n 1)"
echo "$IMAGE"
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
    --nic net-id="$TRUSTED_NET_ID"
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
status: ACTIVE
OS-EXT-SRV-ATTR:host: csri9
```

如果状态是 `ERROR`：

```bash
openstack server show "$TRUSTED_VM_ID" -f yaml
journalctl -u keylime-openstack-sync.service -n 100 --no-pager 2>/dev/null || true
docker logs --tail 100 nova_scheduler
```

## 14. 验证没有 trait 就不能调度

移除 csri9 trait：

```bash
source /etc/kolla/admin-openrc.sh
rp_remove_trait "$RP_CSRI9" "$TRUSTED_TRAIT"

openstack resource provider trait list "$RP_CSRI9"
openstack resource provider trait list "$RP_CSRI8"
```

再次创建 VM：

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

恢复 csri9 trait：

```bash
rp_add_trait "$RP_CSRI9" "$TRUSTED_TRAIT"
```

## 15. 部署 csri9-only 同步器

第一阶段映射文件只写 csri9：

```bash
mkdir -p /etc/keylime-openstack-sync

cat >/etc/keylime-openstack-sync/nodes.env <<'EOF'
csri9 csri9
EOF
```

后续 `keylime-openstack-sync` 只会同步：

```text
Keylime agent csri9 -> Placement resource provider csri9
```

确认：

```bash
cat /etc/keylime-openstack-sync/nodes.env
```

如果你已经按总方案创建了同步脚本，直接运行：

```bash
/usr/local/sbin/keylime-openstack-sync

openstack resource provider trait list "$RP_CSRI9"
openstack resource provider trait list "$RP_CSRI8"
```

预期：

```text
csri9 PASS -> csri9 有 CUSTOM_KEYLIME_ATTESTED
csri8 不在 nodes.env -> 不会被同步器加 trait
```

## 16. 第一阶段验收标准

通过条件：

```text
1. csri9 TPM SHA256 PCR 可读。
2. csri9 Keylime agent 可注册。
3. keylime_tenant -c status -u csri9 能看到 PASS/PENDING/FAIL。
4. REST API 能读取 csri9 attestation_status。
5. csri9 Placement provider 有 CUSTOM_KEYLIME_ATTESTED。
6. csri8 Placement provider 没有 CUSTOM_KEYLIME_ATTESTED。
7. trusted.keylime.small 要求 trait:CUSTOM_KEYLIME_ATTESTED=required。
8. trusted VM 创建后落到 csri9。
9. 移除 csri9 trait 后，trusted VM 无法调度。
```

第一阶段成功后，再处理 `csri8` 的 SHA256 PCR bank，再把 nodes.env 扩展成：

```text
csri8 csri8
csri9 csri9
```

