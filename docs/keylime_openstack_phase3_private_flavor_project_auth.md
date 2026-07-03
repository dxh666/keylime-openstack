# Keylime 与 OpenStack 深度结合：阶段 3 Trusted Flavor 私有化与 Project 授权

日期：2026-07-02

## 1. 阶段目标

阶段 1 和阶段 2 已经完成：

```text
PCR 策略匹配
  + Keylime attestation PASS
  + attestation freshness 未过期
  -> csri9 拥有 CUSTOM_KEYLIME_ATTESTED
  -> trusted flavor 可以调度到 csri9
```

阶段 3 要把可信计算能力从“管理员实验能力”升级成“按 project 授权的云能力”：

```text
普通 project:
  看不到 / 用不了 trusted private flavor

可信 project:
  可以看到 / 使用 trusted private flavor
  但仍然只能调度到 Keylime 持续可信的宿主机
```

最终形成三层控制：

```text
Project 授权边界:
  谁能申请可信计算资源

Flavor 资源边界:
  申请的资源是否声明需要可信宿主机

Placement / Keylime 可信边界:
  当前有哪些宿主机真的满足可信策略
```

## 2. 重要说明

不要直接把已经存在的 `trusted.keylime.small` 原地改成 private。

原因是 OpenStack 官方 CLI 中，`--private` 是 `flavor create` 的参数；私有 flavor 创建后，再通过 `openstack flavor set --project` 给 project 授权。已有 flavor 的访问授权也通过 `flavor set --project` / `flavor unset --project` 管理。

本阶段建议新建一个私有 flavor：

```text
trusted.keylime.private.small
```

这样不会破坏前面已经验证过的 public 实验 flavor：

```text
trusted.keylime.small
```

参考文档：

- OpenStackClient flavor create 支持 `--private` 和 `--project`：<https://docs.openstack.org/python-openstackclient/latest/cli/command-objects/compute/v2/index.html#flavor-create>
- OpenStackClient flavor set 支持 `--project` 授权私有 flavor：<https://docs.openstack.org/python-openstackclient/latest/cli/command-objects/compute/v2/index.html#flavor-set>
- Nova 管理文档说明 private flavor 创建后再分配给 project：<https://docs.openstack.org/nova/latest/admin/flavors.html>

## 3. 实验对象

沿用前面 project 边界实验中的两个 project：

```text
可信 project:
  proj-boundary-a
  user: alice-a
  network: net-boundary-a
  NET_A_ID=64cef53a-83c7-49ba-beeb-2d7ea395d029

普通 project:
  proj-boundary-b
  user: bob-b
  network: net-boundary-b
  NET_B_ID=563d2a45-ad75-422a-b82a-0dd4e544db64
```

本阶段授权关系：

```text
trusted.keylime.private.small
  -> 只授权给 proj-boundary-a
  -> 不授权给 proj-boundary-b
```

## 4. 在 csri10 设置实验变量

以下命令全部在 `csri10` 执行。

```bash
source /etc/kolla/admin-openrc.sh

export DOMAIN="Default"

export TRUSTED_PROJECT="proj-boundary-a"
export TRUSTED_USER="alice-a"

export ORDINARY_PROJECT="proj-boundary-b"
export ORDINARY_USER="bob-b"

export USER_PASS="<LAB_USER_PASSWORD>"

export SOURCE_FLAVOR="m1.small"
export PUBLIC_TRUSTED_FLAVOR="trusted.keylime.small"
export PRIVATE_TRUSTED_FLAVOR="trusted.keylime.private.small"
export TRUSTED_TRAIT="CUSTOM_KEYLIME_ATTESTED"

export IMAGE="Fedora-Cloud-Base-AmazonEC2-44-1.7.x86_64.raw"
export NET_A_ID="64cef53a-83c7-49ba-beeb-2d7ea395d029"
export NET_B_ID="563d2a45-ad75-422a-b82a-0dd4e544db64"

echo "OS_AUTH_URL=$OS_AUTH_URL"
```

如果你不想修改 `alice-a` / `bob-b` 的密码，可以跳过下一段，并把 `USER_PASS` 改成你之前实际设置的密码。

实验环境中建议直接统一密码，减少认证变量错误：

```bash
openstack user set --password "$USER_PASS" "$TRUSTED_USER"
openstack user set --password "$USER_PASS" "$ORDINARY_USER"
```

确保两个用户仍然只在各自 project 里有普通 member 权限：

```bash
openstack role add --project "$TRUSTED_PROJECT" --user "$TRUSTED_USER" member || true
openstack role add --project "$ORDINARY_PROJECT" --user "$ORDINARY_USER" member || true

openstack role assignment list --project "$TRUSTED_PROJECT" --names
openstack role assignment list --project "$ORDINARY_PROJECT" --names
```

## 5. 确认 Keylime 可信 trait 当前存在

```bash
export OS_PLACEMENT_API_VERSION=1.17
export RP_CSRI9="$(openstack resource provider list --name csri9 -f value -c uuid)"

openstack resource provider trait list "$RP_CSRI9" | grep "$TRUSTED_TRAIT"
cat /var/log/keylime-openstack-sync-decision.json | python3 -m json.tool
```

预期：

```text
CUSTOM_KEYLIME_ATTESTED
result: PASS_FRESH
```

如果 trait 不存在，不要继续创建 VM。先恢复 Keylime agent 和 systemd timer 同步。

## 6. 创建 private trusted flavor

从 `m1.small` 复制基础规格，避免手写 RAM、disk、vCPU 出错：

```bash
export FLAVOR_RAM="$(openstack flavor show "$SOURCE_FLAVOR" -f value -c ram)"
export FLAVOR_DISK="$(openstack flavor show "$SOURCE_FLAVOR" -f value -c disk)"
export FLAVOR_VCPUS="$(openstack flavor show "$SOURCE_FLAVOR" -f value -c vcpus)"

echo "ram=$FLAVOR_RAM disk=$FLAVOR_DISK vcpus=$FLAVOR_VCPUS"
```

创建私有 flavor：

```bash
if openstack flavor show "$PRIVATE_TRUSTED_FLAVOR" >/dev/null 2>&1; then
  echo "Flavor $PRIVATE_TRUSTED_FLAVOR already exists"
else
  openstack flavor create "$PRIVATE_TRUSTED_FLAVOR" \
    --private \
    --id auto \
    --ram "$FLAVOR_RAM" \
    --disk "$FLAVOR_DISK" \
    --vcpus "$FLAVOR_VCPUS"
fi
```

设置 Keylime Placement trait 要求：

```bash
openstack flavor set "$PRIVATE_TRUSTED_FLAVOR" \
  --property "trait:${TRUSTED_TRAIT}=required"
```

只授权给可信 project：

```bash
openstack flavor set "$PRIVATE_TRUSTED_FLAVOR" \
  --project "$TRUSTED_PROJECT" \
  --project-domain "$DOMAIN"
```

确认 flavor：

```bash
openstack flavor show "$PRIVATE_TRUSTED_FLAVOR" \
  -c id \
  -c name \
  -c ram \
  -c disk \
  -c vcpus \
  -c os-flavor-access:is_public \
  -c access_project_ids \
  -c properties \
  -f yaml
```

预期：

```text
os-flavor-access:is_public: false
properties:
  trait:CUSTOM_KEYLIME_ATTESTED: required
access_project_ids:
  包含 proj-boundary-a 的 project id
```

## 7. 可信 project 可见性验证

用 `alice-a` 登录 `proj-boundary-a`：

```bash
openstack \
  --os-auth-url "$OS_AUTH_URL" \
  --os-identity-api-version 3 \
  --os-username "$TRUSTED_USER" \
  --os-password "$USER_PASS" \
  --os-user-domain-name "$DOMAIN" \
  --os-project-name "$TRUSTED_PROJECT" \
  --os-project-domain-name "$DOMAIN" \
  flavor list | grep "$PRIVATE_TRUSTED_FLAVOR"
```

预期能看到：

```text
trusted.keylime.private.small
```

## 8. 普通 project 不可见性验证

用 `bob-b` 登录 `proj-boundary-b`：

```bash
openstack \
  --os-auth-url "$OS_AUTH_URL" \
  --os-identity-api-version 3 \
  --os-username "$ORDINARY_USER" \
  --os-password "$USER_PASS" \
  --os-user-domain-name "$DOMAIN" \
  --os-project-name "$ORDINARY_PROJECT" \
  --os-project-domain-name "$DOMAIN" \
  flavor list | grep "$PRIVATE_TRUSTED_FLAVOR" || \
  echo "OK: ordinary project cannot see private trusted flavor"
```

预期：

```text
OK: ordinary project cannot see private trusted flavor
```

进一步验证普通 project 不能使用该 flavor：

```bash
export DENY_VM="trusted-private-deny-b"

openstack \
  --os-auth-url "$OS_AUTH_URL" \
  --os-identity-api-version 3 \
  --os-username "$ORDINARY_USER" \
  --os-password "$USER_PASS" \
  --os-user-domain-name "$DOMAIN" \
  --os-project-name "$ORDINARY_PROJECT" \
  --os-project-domain-name "$DOMAIN" \
  server create "$DENY_VM" \
  --image "$IMAGE" \
  --flavor "$PRIVATE_TRUSTED_FLAVOR" \
  --nic net-id="$NET_B_ID"
```

预期失败，常见表现是：

```text
No Flavor found
```

或类似“无权访问该 flavor”的错误。

## 9. 可信 project 正向创建 VM

用 `alice-a` 在 `proj-boundary-a` 创建可信 VM：

```bash
export ALLOW_VM="trusted-private-allow-a"

openstack \
  --os-auth-url "$OS_AUTH_URL" \
  --os-identity-api-version 3 \
  --os-username "$TRUSTED_USER" \
  --os-password "$USER_PASS" \
  --os-user-domain-name "$DOMAIN" \
  --os-project-name "$TRUSTED_PROJECT" \
  --os-project-domain-name "$DOMAIN" \
  server create "$ALLOW_VM" \
  --image "$IMAGE" \
  --flavor "$PRIVATE_TRUSTED_FLAVOR" \
  --nic net-id="$NET_A_ID"
```

等待：

```bash
sleep 30
```

用 admin 查看落点：

```bash
source /etc/kolla/admin-openrc.sh

openstack server show "$ALLOW_VM" \
  -c id \
  -c name \
  -c status \
  -c project_id \
  -c flavor \
  -c OS-EXT-SRV-ATTR:host \
  -c fault \
  -f yaml
```

预期：

```text
status: ACTIVE
OS-EXT-SRV-ATTR:host: csri9
flavor: trusted.keylime.private.small
```

这说明：

```text
proj-boundary-a 有权使用 private trusted flavor
private trusted flavor 要求 CUSTOM_KEYLIME_ATTESTED
当前只有 csri9 有该 trait
所以 VM 被调度到 csri9
```

## 10. 可信 project 但 Keylime 失效时仍不能调度

这是可选但强烈建议做的复合验证。

目标是证明：

```text
project 授权只表示“可以申请可信资源”
不表示“无条件创建成功”

Keylime / Placement trait 才决定当前是否有可信宿主机可用
```

操作：

1. 在 `csri9` 停止 agent。

```bash
docker stop keylime-agent
```

2. 在 `csri10` 等待 freshness 过期并确认 trait 被移除。

```bash
sleep 150

/opt/keylime-openstack-sync/keylime-placement-sync-locked.sh
cat /var/log/keylime-openstack-sync-decision.json | python3 -m json.tool

source /etc/kolla/admin-openrc.sh
export OS_PLACEMENT_API_VERSION=1.17
export RP_CSRI9="$(openstack resource provider list --name csri9 -f value -c uuid)"

openstack resource provider trait list "$RP_CSRI9" | grep "$TRUSTED_TRAIT" || \
  echo "OK: trusted trait removed"
```

3. 再用 `alice-a` 创建 private trusted VM。

```bash
export FAIL_VM="trusted-private-keylime-fail-a"

openstack \
  --os-auth-url "$OS_AUTH_URL" \
  --os-identity-api-version 3 \
  --os-username "$TRUSTED_USER" \
  --os-password "$USER_PASS" \
  --os-user-domain-name "$DOMAIN" \
  --os-project-name "$TRUSTED_PROJECT" \
  --os-project-domain-name "$DOMAIN" \
  server create "$FAIL_VM" \
  --image "$IMAGE" \
  --flavor "$PRIVATE_TRUSTED_FLAVOR" \
  --nic net-id="$NET_A_ID"

sleep 20

source /etc/kolla/admin-openrc.sh
openstack server show "$FAIL_VM" \
  -c status \
  -c OS-EXT-SRV-ATTR:host \
  -c fault \
  -f yaml
```

预期：

```text
status: ERROR
OS-EXT-SRV-ATTR:host: null
fault.message: No valid host was found.
```

这一步说明：

```text
即使 project 被授权使用 trusted flavor，
只要当前没有 Keylime 持续可信的计算节点，
Nova 仍然无法调度。
```

## 11. 恢复

在 `csri9` 重启 `keylime-agent`。

在 `csri10`：

```bash
cd /opt/keylime-docker
export KEYLIME_AGENT_UUID_FIXED="11111111-1111-4111-8111-000000000009"

docker compose run --rm keylime-tenant \
  -c reactivate \
  -u "$KEYLIME_AGENT_UUID_FIXED" \
  -v 172.31.100.10 \
  -vp 8881 \
  -r 172.31.100.10 \
  -rp 8891

sleep 30

/opt/keylime-openstack-sync/keylime-placement-sync-locked.sh
cat /var/log/keylime-openstack-sync-decision.json | python3 -m json.tool
```

确认 trait 恢复：

```bash
source /etc/kolla/admin-openrc.sh
export OS_PLACEMENT_API_VERSION=1.17
export RP_CSRI9="$(openstack resource provider list --name csri9 -f value -c uuid)"

openstack resource provider trait list "$RP_CSRI9" | grep "$TRUSTED_TRAIT"
```

## 12. 回滚

如果只想撤销普通授权，不删除 flavor：

```bash
source /etc/kolla/admin-openrc.sh

openstack flavor unset "$PRIVATE_TRUSTED_FLAVOR" \
  --project "$TRUSTED_PROJECT" \
  --project-domain "$DOMAIN"
```

如果要删除实验 flavor：

```bash
source /etc/kolla/admin-openrc.sh

openstack flavor delete "$PRIVATE_TRUSTED_FLAVOR"
```

已创建的 VM 不会因为 flavor 被删除而立刻消失，但后续不能再用该 flavor 创建新 VM。

## 13. 阶段 3 验收标准

阶段 3 成功标准：

```text
1. trusted.keylime.private.small 是 private flavor。
2. trusted.keylime.private.small 只授权给 proj-boundary-a。
3. alice-a / proj-boundary-a 能看到并使用该 flavor。
4. bob-b / proj-boundary-b 看不到也不能使用该 flavor。
5. 使用该 flavor 创建的 VM 只能调度到有 CUSTOM_KEYLIME_ATTESTED 的计算节点。
6. Keylime FAIL / freshness 过期 / trait 移除后，即使 proj-boundary-a 有 flavor 权限，也无法创建可信 VM。
```

完成后，你的可信云能力会变成：

```text
Project 决定谁可以申请可信计算
Flavor 决定 workload 声明需要可信计算
Keylime 决定哪些宿主机当前可信
Placement/Nova 决定是否能实际调度
```


