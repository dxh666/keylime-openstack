# Keylime + OpenStack 管理系统

当前前端包含两个功能模块：

```text
计算节点可信状态实时监控
Keylime TPM PCR 策略管理
```

## 部署到 csri10

复制 `deploy/frontend/` 到 `csri10`：

```bash
mkdir -p /opt/keylime-openstack-console
cp -a deploy/frontend/* /opt/keylime-openstack-console/
```

启动：

```bash
cd /opt/keylime-openstack-console
python3 trust_monitor_server.py --host 172.31.100.10 --port 8088
```

浏览器访问：

```text
http://172.31.100.10:8088/
```

## 实时刷新机制

后端不再让每个浏览器请求都同步执行 OpenStack CLI。服务启动后会在后台周期性采集状态，
`/api/status` 默认直接返回最近一次缓存结果，因此前端可以 1-2 秒刷新一次而不会堆积大量
`openstack` 命令。

后台采集间隔：

```bash
export KEYLIME_STATUS_REFRESH_SECONDS="3"
```

虚拟机数量默认使用一次全量查询聚合：

```bash
openstack server list --all-projects --long -f json
```

如果 OpenStack CLI 不返回 Host 列，可临时开启慢速逐节点回退：

```bash
export KEYLIME_VM_COUNT_SLOW_FALLBACK="true"
```

Resource provider traits are queried in bulk first. If the OpenStack CLI does not support
bulk trait listing, keep the slower fallback enabled:

```bash
export KEYLIME_TRAIT_SLOW_FALLBACK="true"
```

`/api/status` exposes `cache.last_refresh_duration_seconds` to show how long the last
background collection took.

前端“立即刷新”按钮会调用：

```text
/api/status?force=1
```

这个请求会强制同步采集一次，可能比普通自动刷新慢。

## 多计算节点 agent 配置

策略下发需要知道每个计算节点对应的 Keylime agent UUID 和 IP。建议在
`/etc/keylime-openstack-sync/openstack-keylime-lab.env` 中配置：

```bash
export KEYLIME_AGENT_HOSTS="csri9,csri8"
export KEYLIME_AGENT_IP_MAP="csri9=172.31.100.9,csri8=172.31.100.8"
export KEYLIME_AGENT_UUID_MAP="csri9=11111111-1111-4111-8111-000000000009,csri8=22222222-2222-4222-8222-000000000008"
export KEYLIME_AGENT_PORT="9002"
```

可信状态监控会读取每个节点自己的判定文件。单节点默认文件仍是：

```text
/var/log/keylime-openstack-sync-decision.json
```

其他节点建议使用按主机名命名的判定文件，例如：

```bash
AGENT_UUID="22222222-2222-4222-8222-000000000008" \
RP_NAME="csri8" \
RAW_STATUS_FILE="/var/log/keylime-openstack-sync-status-csri8.raw.log" \
DECISION_FILE="/var/log/keylime-openstack-sync-decision-csri8.json" \
LAST_LOG_FILE="/var/log/keylime-openstack-sync-last-csri8.log" \
/opt/keylime-openstack-sync/keylime-placement-sync.sh
```

如果只配置单节点，系统会继续使用：

```bash
COMPUTE_HOST
KEYLIME_AGENT_IP
KEYLIME_AGENT_UUID_FIXED
```

## TPM PCR 策略文件

策略库默认保存到：

```text
/var/lib/keylime-openstack-sync/tpm-pcr-policies.json
```

可用环境变量覆盖：

```bash
export KEYLIME_PCR_POLICY_FILE="/var/lib/keylime-openstack-sync/tpm-pcr-policies.json"
```

## 写 API 令牌

如果设置了：

```bash
export KEYLIME_POLICY_ADMIN_TOKEN="<TOKEN>"
```

前端保存策略、删除策略、下发策略时需要在页面右上角填写同一个管理令牌。
实验环境可以先留空。

## TPM PCR 策略格式

前端保存的是结构化策略：

```json
{
  "id": "csri8-pcr7",
  "name": "csri8 PCR7 基线",
  "type": "tpm_pcr",
  "hash_alg": "sha256",
  "pcrs": {
    "7": "AD69DC884387BB14056F05ABC4AB0B8AA751824D909931618FB6365EE2C2938D"
  },
  "mask": "0x80",
  "tpm_policy": {
    "mask": "0x80",
    "7": "AD69DC884387BB14056F05ABC4AB0B8AA751824D909931618FB6365EE2C2938D"
  }
}
```

下发时后端会调用：

```bash
docker compose run --rm keylime-tenant \
  -c delete \
  -u <agent_uuid> \
  -v <verifier_ip> \
  -vp <verifier_port> \
  -r <registrar_ip> \
  -rp <registrar_port>

docker compose run --rm keylime-tenant \
  -c add \
  -t <agent_ip> \
  -tp <agent_port> \
  -u <agent_uuid> \
  -v <verifier_ip> \
  -vp <verifier_port> \
  -r <registrar_ip> \
  -rp <registrar_port> \
  --tpm_policy '<policy_json>'
```

## systemd 运行方式

```ini
[Unit]
Description=Keylime OpenStack Console
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/keylime-openstack-console
Environment=KEYLIME_OPENSTACK_ENV_FILE=/etc/keylime-openstack-sync/openstack-keylime-lab.env
ExecStart=/usr/bin/python3 /opt/keylime-openstack-console/trust_monitor_server.py --host 172.31.100.10 --port 8088
Restart=on-failure
User=root
Group=root

[Install]
WantedBy=multi-user.target
```

启用：

```bash
systemctl daemon-reload
systemctl enable --now keylime-openstack-monitor.service
systemctl status keylime-openstack-monitor.service --no-pager
```

## 监控状态颜色

```text
绿色：节点可信，可作为可信计算节点使用
红色：节点不可信、状态过期、被禁用或 nova-compute 下线
黄色：计算节点存在，但暂未安装或暂未纳入 Keylime agent
```
