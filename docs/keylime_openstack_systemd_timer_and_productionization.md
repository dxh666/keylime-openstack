# Keylime 与 OpenStack：systemd timer 自动同步及生产化改造方案

日期：2026-07-02

## 1. 推荐路线

当前建议优先使用：

```text
systemd timer
```

而不是立即做 Docker 容器。

原因：

```text
1. 当前同步脚本依赖 csri10 宿主机上的 /etc/kolla/admin-openrc.sh。
2. 当前同步脚本会调用 docker compose run keylime-tenant。
3. 如果容器化同步器，需要把 Docker socket、OpenStack 凭据、Keylime CA、配置目录挂进容器，权限面更大。
4. systemd timer 更适合当前阶段：简单、可观测、易回滚。
```

后续生产化再把同步逻辑改为直接调用 Keylime verifier REST API 和 OpenStack Placement API，此时再容器化更合适。

官方依据：

```text
Keylime 官方文档说明 Docker 镜像覆盖 verifier、registrar、tenant；Rust agent 是官方 agent，且配置文件与旧 Python agent 不可互换。
OpenStack Nova flavor 支持通过 trait:<trait_name>=required 限定调度目标。
systemd timer 由 .timer 触发匹配的 .service，可用 OnUnitActiveSec 定期执行。
```

参考：

- Keylime Installation: <https://keylime.readthedocs.io/en/latest/installation.html>
- OpenStack Nova Flavors / Required traits: <https://docs.openstack.org/nova/latest/user/flavors.html>
- systemd.timer manual: <https://man7.org/linux/man-pages/man5/systemd.timer.5.html>

## 2. 当前已完成的实验闭环

已完成：

```text
1. csri9 keylime-agent 启动并 activated。
2. keylime-tenant status 显示 attestation_status = PASS。
3. csri9 resource provider 有 CUSTOM_KEYLIME_ATTESTED。
4. trusted.keylime.small flavor 设置 trait:CUSTOM_KEYLIME_ATTESTED=required。
5. trusted VM 成功调度到 csri9，状态 ACTIVE。
6. 停止 keylime-agent 后，同步脚本移除 trait。
7. trait 被移除后，trusted VM 创建失败，报 No valid host。
8. 重启 agent 并 reactivate 后，同步脚本恢复 trait。
```

这说明：

```text
Keylime 的 attestation 结果已经实际影响 OpenStack Nova 调度。
```

## 3. systemd timer 部署

以下命令在 `csri10` 控制节点执行。

### 3.1 确认同步脚本存在

```bash
ls -l /opt/keylime-openstack-sync/keylime-placement-sync.sh
chmod +x /opt/keylime-openstack-sync/keylime-placement-sync.sh
```

手动运行一次：

```bash
/opt/keylime-openstack-sync/keylime-placement-sync.sh
```

预期：

```text
Keylime status PASS: ensure CUSTOM_KEYLIME_ATTESTED on csri9
```

或在 agent 停止时：

```text
Keylime status NOT_PASS: remove CUSTOM_KEYLIME_ATTESTED from csri9
```

### 3.2 给脚本加互斥锁

防止 timer 下一轮触发时上一轮还没结束。

先备份：

```bash
cp -a /opt/keylime-openstack-sync/keylime-placement-sync.sh \
  /opt/keylime-openstack-sync/keylime-placement-sync.sh.before-lock.bak
```

创建带锁 wrapper：

```bash
cat >/opt/keylime-openstack-sync/keylime-placement-sync-locked.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

LOCK_FILE=/run/keylime-openstack-sync.lock
SYNC_SCRIPT=/opt/keylime-openstack-sync/keylime-placement-sync.sh

exec 9>"$LOCK_FILE"

if ! flock -n 9; then
  echo "Another keylime-openstack-sync run is still active; skip this round."
  exit 0
fi

exec "$SYNC_SCRIPT"
EOF

chmod +x /opt/keylime-openstack-sync/keylime-placement-sync-locked.sh
```

测试：

```bash
/opt/keylime-openstack-sync/keylime-placement-sync-locked.sh
```

### 3.3 创建 systemd service

```bash
cat >/etc/systemd/system/keylime-openstack-sync.service <<'EOF'
[Unit]
Description=Sync Keylime attestation status to OpenStack Placement trait
Wants=docker.service
After=docker.service network-online.target

[Service]
Type=oneshot
ExecStart=/opt/keylime-openstack-sync/keylime-placement-sync-locked.sh
TimeoutStartSec=90

# Keep this as root for the current Kolla/OpenStack CLI based experiment.
# Production hardening should replace this with a dedicated service user
# and direct API credentials scoped to Placement only.
User=root
Group=root

StandardOutput=journal
StandardError=journal
EOF
```

### 3.4 创建 systemd timer

建议先用 30 秒周期。验证稳定后可改为 60 秒或更长。

```bash
cat >/etc/systemd/system/keylime-openstack-sync.timer <<'EOF'
[Unit]
Description=Run Keylime to OpenStack Placement sync periodically

[Timer]
OnBootSec=60s
OnUnitActiveSec=30s
AccuracySec=5s
Unit=keylime-openstack-sync.service

[Install]
WantedBy=timers.target
EOF
```

说明：

```text
OnBootSec=60s       系统启动 60 秒后第一次运行
OnUnitActiveSec=30s 上一次 service 启动后 30 秒再次运行
AccuracySec=5s      允许 5 秒内调度，降低抖动
```

### 3.5 启用 timer

```bash
systemctl daemon-reload
systemctl enable --now keylime-openstack-sync.timer
```

查看：

```bash
systemctl status keylime-openstack-sync.timer --no-pager
systemctl list-timers --all | grep keylime-openstack-sync
```

手动触发一次：

```bash
systemctl start keylime-openstack-sync.service
```

查看日志：

```bash
journalctl -u keylime-openstack-sync.service -n 100 --no-pager
cat /var/log/keylime-openstack-sync-last.log
```

### 3.6 PASS 自动加 trait 验证

确认 agent 正常：

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

等待 timer 自动运行：

```bash
sleep 40
```

验证 trait：

```bash
source /etc/kolla/admin-openrc.sh
export OS_PLACEMENT_API_VERSION=1.17
export RP_CSRI9="$(openstack resource provider list --name csri9 -f value -c uuid)"

openstack resource provider trait list "$RP_CSRI9" | grep CUSTOM_KEYLIME_ATTESTED
```

### 3.7 FAIL 自动移除 trait 验证

在 `csri9`：

```bash
docker stop keylime-agent
```

在 `csri10` 等待：

```bash
sleep 60
```

如果 verifier 状态进入 Failed，timer 应自动移除 trait。

验证：

```bash
openstack resource provider trait list "$RP_CSRI9" | grep CUSTOM_KEYLIME_ATTESTED || \
  echo "OK: trusted trait removed by timer"
```

创建 trusted VM，预期失败：

```bash
export FAIL_VM=keylime-trusted-timer-fail

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

### 3.8 恢复验证

在 `csri9` 用固定命令重启 agent。

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
  -rp 8891 || true

sleep 60

openstack resource provider trait list "$RP_CSRI9" | grep CUSTOM_KEYLIME_ATTESTED
```

清理失败 VM：

```bash
openstack server delete "$FAIL_VM" 2>/dev/null || true
```

## 4. Docker 容器化同步器为何暂不推荐

当前同步脚本如果做成 Docker 容器，需要挂载：

```text
/etc/kolla/admin-openrc.sh
/opt/keylime-docker
/var/run/docker.sock
OpenStack CLI
Keylime tenant 配置和 CA
```

其中 `/var/run/docker.sock` 等价于给容器宿主机 root 级控制权，不适合作为当前生产化第一步。

后续若要容器化，建议先把同步器改为：

```text
1. 直接调用 Keylime verifier REST API，不再 docker compose run keylime-tenant。
2. 直接调用 OpenStack Placement REST API，不依赖 openstack CLI。
3. 使用 Keystone application credential 或最小权限 service user。
4. 容器只挂载自己的配置文件和 CA，不挂载 Docker socket。
```

## 5. 生产化改造清单

### 5.1 Keylime 组件版本固定

当前：

```text
control plane: v7.14.2
agent: latest
```

生产建议：

```text
所有 Keylime 组件使用同一 release。
不要使用 latest。
```

推荐：

```text
csri10:
  verifier / registrar / tenant: 固定 tag

csri9:
  使用宿主机 Rust agent，或自建同版本 agent 镜像
```

原因：

```text
Keylime 文档说明 Rust agent 是官方 agent，且 Rust agent 使用 /etc/keylime/agent.conf，配置与旧 Python agent 不可互换。
```

### 5.2 agent 改为宿主机服务

当前实验：

```text
Docker agent + --privileged + chmod a+rw /dev/tpm*
```

生产建议：

```text
csri9 使用宿主机 systemd 运行 keylime-agent。
```

收益：

```text
1. 避免 --privileged。
2. 避免容器内外 tss 用户组不一致。
3. 避免 chmod a+rw /dev/tpmrm0。
4. 更符合 Keylime Rust agent 的部署路径。
```

### 5.3 TPM 权限改为 udev 规则

当前实验：

```bash
chmod a+rw /dev/tpmrm0
chmod a+rw /dev/tpm0
```

生产应改为：

```text
通过 udev 规则让 /dev/tpmrm0 归属 tss 组，权限 0660。
keylime-agent 运行用户加入 tss 组。
```

示例：

```bash
cat >/etc/udev/rules.d/99-tpm-keylime.rules <<'EOF'
KERNEL=="tpmrm0", GROUP="tss", MODE="0660"
KERNEL=="tpm0", GROUP="tss", MODE="0660"
EOF

udevadm control --reload-rules
udevadm trigger
```

### 5.4 恢复 EK certificate 校验

当前实验：

```text
require_ek_cert = false
```

这是为了跑通没有 EK cert 的实验 TPM。

生产建议：

```text
1. 确认 TPM 是否能提供 EK certificate。
2. 如果硬件 TPM 没有内置 EK cert，建立自有 EK/平台证书信任根。
3. 恢复 require_ek_cert = true。
4. 记录 TPM 供应商、固件版本、EK public hash。
```

### 5.5 增加 attestation 新鲜度判断

当前同步脚本只判断：

```text
attestation_status == PASS
```

生产应同时判断：

```text
1. attestation_status == PASS
2. last_successful_attestation 距当前时间不能太久
3. maximum_attestation_interval 未超时
4. last_event_id 为空或不属于严重错误
```

否则可能出现“陈旧 PASS”。

### 5.6 自动 reactivate 策略

当前恢复时需要手工：

```bash
keylime-tenant -c reactivate
```

生产建议：

```text
不要无限自动 reactivate。
```

建议策略：

```text
1. verifier.not_reachable 后，如果 agent 重新可达，可自动 reactivate 一次。
2. PCR/IMA/Measured Boot 策略失败，不自动 reactivate。
3. reactivate 次数和时间窗口要记录。
4. 所有 reactivate 写审计日志。
```

### 5.7 trusted flavor 改为私有 flavor

当前：

```text
trusted.keylime.small is_public=True
```

生产建议：

```text
把 trusted flavor 改为 private，只授权可信项目使用。
```

示例：

```bash
openstack flavor set --private trusted.keylime.small
openstack flavor set --project <trusted-project> trusted.keylime.small
```

### 5.8 OpenStack 权限最小化

当前同步脚本使用：

```text
/etc/kolla/admin-openrc.sh
```

生产建议：

```text
创建专用 service user 或 Keystone application credential。
只允许访问 Placement trait/resource provider 所需 API。
不要长期使用 admin-openrc。
```

### 5.9 日志与审计

至少记录：

```text
1. 每次读取到的 Keylime 状态。
2. 每次 trait add/remove。
3. agent UUID 与 OpenStack resource provider 映射。
4. reactivate 行为。
5. trusted VM 调度失败原因。
```

### 5.10 支持多节点

当前只接入：

```text
csri9
```

生产需要映射表：

```text
agent_uuid,compute_hostname,resource_provider_name,enabled
11111111-1111-4111-8111-000000000009,csri9,csri9,true
...
```

同步器按映射表循环处理每个 compute。

### 5.11 修复 csri8 后再接入

当前 csri8：

```text
TPM 2.0 存在
SHA256 PCR bank 为空
```

生产接入前必须：

```text
1. 启用 SHA256 PCR bank。
2. 重启并确认 tpm2_pcrread sha256:0,1,2,3,4,5,6,7 有值。
3. 部署 agent。
4. 加入映射表。
5. 单独验证 PASS/FAIL。
```

## 6. 生产化目标架构

推荐最终架构：

```text
csri10:
  Keylime registrar/verifier/tenant 固定版本
  PostgreSQL/MariaDB 后端，而不是临时 SQLite
  keylime-openstack-sync systemd timer
  最小权限 OpenStack 凭据

csri9/csri8:
  宿主机 Rust keylime-agent
  TPM 权限由 udev + tss group 控制
  mTLS CA 正确下发
  EK/平台证书策略逐步开启

OpenStack:
  Placement trait 表示可信状态
  trusted flavor 使用 trait required
  trusted flavor 私有化
  非可信节点无法承载 trusted VM
```

## 7. 本阶段验收标准

systemd timer 成功：

```text
systemctl list-timers 能看到 keylime-openstack-sync.timer
journalctl 能看到周期性执行
Keylime PASS 时 trait 自动存在
Keylime FAIL 时 trait 自动消失
trusted VM 在 trait 消失时 No valid host
恢复 PASS 后 trait 自动恢复
```

生产化第一阶段成功：

```text
不再依赖手工执行同步脚本
同步行为有日志
trait 状态能自动跟随 Keylime verifier 状态
实验妥协项已列入后续整改清单
```

