# Keylime + OpenStack 多计算节点可信监控问题复盘

记录日期：2026-07-04

## 当前阶段目标

本阶段不继续扩展新的实验场景，先把 csri9/csri8 两个计算节点的可信状态监控、Keylime agent 清单、Placement Trait 同步和 nova-compute 管控流程稳定下来，为下一阶段实验做基础。

当前已验证的目标状态：

```text
csri9 172.31.100.9  agent uuid=11111111-1111-4111-8111-000000000009  PASS_FRESH  可信
csri8 172.31.100.8  agent uuid=22222222-2222-4222-8222-000000000008  PASS_FRESH  可信
OpenStack Placement Trait: CUSTOM_KEYLIME_ATTESTED
前端访问地址: http://172.31.100.10:8088/
同步 timer: keylime-openstack-sync.timer
前端 systemd 服务: keylime-openstack-monitor.service
```

前端 `/api/status?force=1` 最终应看到：

```text
summary.total=2
summary.trusted=2
summary.untrusted=0
summary.no_agent=0
csri8 conclusion=可信 decision.result=PASS_FRESH trait_present=true
csri9 conclusion=可信 decision.result=PASS_FRESH trait_present=true
```

## 问题与解决方案

| 问题 | 原因 | 解决方案 | 验证方式 |
| --- | --- | --- | --- |
| csri8 前端显示“未装代理”，但 Placement Trait 已存在 | 主环境文件只配置了 csri9，前端无法知道 csri8 的 Keylime agent UUID/IP | 增加 `KEYLIME_AGENT_HOSTS`、`KEYLIME_AGENT_IP_MAP`、`KEYLIME_AGENT_UUID_MAP`，并让前端读取每个节点自己的 decision 文件 | `curl /api/status?force=1` 中 csri8 的 `agent.configured=true`、`conclusion.text=可信` |
| 新增计算节点后不想每次手工维护 agent 清单 | OpenStack 能发现 nova-compute，但 Keylime agent UUID 需要从 registrar 或配置映射得到 | 新增 `keylime-agent-inventory-refresh.sh`，先用 `reglist` 按 IP 自动匹配，失败时使用静态 env 兜底 | 查看 `/etc/keylime-openstack-sync/keylime-agent-inventory.env` 和 `/var/log/keylime-openstack-agent-inventory.json` |
| inventory 刷新脚本没有生成文件 | `reglist` 返回内容无法解析出可与 OpenStack hypervisor IP 匹配的 agent | 保留静态 csri9/csri8 映射作为稳定第一版兜底，自动匹配失败时仍可生成 inventory | `KEYLIME_AGENT_INVENTORY_FORCE=true /opt/keylime-openstack-sync/keylime-agent-inventory-refresh.sh` |
| `keylime-openstack-sync.service` 启动时报 systemd 环境变量格式错误 | `EnvironmentFile=` 不能解析 shell 风格的 `export` 和 `[ -r ... ] && source ...` | 从 systemd unit 移除 `EnvironmentFile=`，由脚本内部 source 环境文件 | `journalctl -u keylime-openstack-sync.service` 不再出现 invalid environment assignment |
| control loop 同步 csri8 时中途失败 | 子脚本 source 主 env 后覆盖了调用者传入的 `AGENT_UUID`、`RP_NAME`、`DECISION_FILE` 等变量 | `keylime-placement-sync.sh` 和 `keylime-nova-compute-quarantine.sh` 先保存 caller override，source env 后再恢复 | journal 中出现 `Placement sync target: rp_name=csri8 agent_uuid=...` |
| Keylime status 命令 rc=1 时脚本直接退出 | `get_keylime_status_raw()` 在 `return "$rc"` 前恢复了 `set -e`，导致非零返回没有进入判定逻辑 | 移除该位置的 `set -e`，让后续逻辑把状态转换为 `NOT_PASS/KEYLIME_COMMAND_FAILED` 或正常 PASS | 失败状态时仍会生成 decision JSON，而不是 service 直接异常退出 |
| csri8 已注册 registrar，但 verifier 中没有可查询状态 | agent 启动后还需要加入 verifier 管控 | 使用 `keylime-tenant -c add` 将 csri8 agent 加入 verifier，并在策略更新后使用 `update + reactivate` | `keylime-tenant -c status -u 2222...0008` 返回 PASS，控制循环生成 csri8 decision |
| 前端刷新慢且看起来不是实时 | 每个浏览器请求都同步执行 OpenStack CLI 会慢，且多个请求可能堆积 | 后端改为后台周期采集并缓存，`/api/status` 快速返回缓存，`force=1` 用于手动强制刷新 | `/api/status` 的 `cache.age_seconds` 和 `last_refresh_duration_seconds` 可观察 |
| csri8 TPM EK 创建失败 | TPM hierarchy auth 被设置，`tpm2_createek` 报 0x9A2 | 在 Dell BIOS 中 clear TPM，确认 `ownerAuthSet/endorsementAuthSet/lockoutAuthSet=0` 后再部署 agent | `tpm2_createek -c /tmp/csri8-ek.ctx -G rsa -u /tmp/csri8-ek.pub` 返回 0 |
| zip/scp 部署后脚本权限丢失 | Windows/压缩包可能不保留 shell 脚本可执行位 | 在目标机用 `install -m 0755` 部署脚本，或执行 `chmod 0755 /opt/keylime-openstack-sync/*.sh` | `ls -l /opt/keylime-openstack-sync/*.sh` 均有 x 权限 |

## 正确代码资料

核心文件：

```text
deploy/env/openstack-keylime-lab.env
deploy/scripts/keylime-agent-inventory-refresh.sh
deploy/scripts/keylime-sync-control-loop.sh
deploy/scripts/keylime-placement-sync.sh
deploy/scripts/keylime-nova-compute-quarantine.sh
deploy/systemd/keylime-openstack-sync.service
deploy/systemd/keylime-openstack-sync.timer
deploy/frontend/trust_monitor_server.py
deploy/frontend/index.html
deploy/frontend/README.md
docs/keylime_agent_inventory_auto_discovery.md
docs/keylime_openstack_frontend_tpm_pcr_policy_management.md
```

关键环境配置：

```bash
export KEYLIME_OPENSTACK_SYNC_DIR="/opt/keylime-openstack-sync"
export KEYLIME_OPENSTACK_LOG_DIR="/var/log"
export KEYLIME_OPENSTACK_STATE_DIR="/var/lib/keylime-openstack-sync"

export KEYLIME_VERIFIER_IP="172.31.100.10"
export KEYLIME_VERIFIER_PORT="8881"
export KEYLIME_REGISTRAR_IP="172.31.100.10"
export KEYLIME_REGISTRAR_PORT="8891"
export KEYLIME_AGENT_PORT="9002"
export KEYLIME_AGENT_API_VERSION="2.5"

export KEYLIME_AGENT_INVENTORY_FILE="/etc/keylime-openstack-sync/keylime-agent-inventory.env"
export KEYLIME_AGENT_INVENTORY_JSON="/var/log/keylime-openstack-agent-inventory.json"
export KEYLIME_AGENT_INVENTORY_REFRESH="true"
export KEYLIME_AGENT_INVENTORY_REFRESH_SECONDS="60"
export KEYLIME_AGENT_INVENTORY_ALLOW_EMPTY="false"

export KEYLIME_AGENT_HOSTS="csri9,csri8"
export KEYLIME_AGENT_IP_MAP="csri9=172.31.100.9,csri8=172.31.100.8"
export KEYLIME_AGENT_UUID_MAP="csri9=11111111-1111-4111-8111-000000000009,csri8=22222222-2222-4222-8222-000000000008"
[ -r "$KEYLIME_AGENT_INVENTORY_FILE" ] && source "$KEYLIME_AGENT_INVENTORY_FILE"

export TRUSTED_TRAIT="CUSTOM_KEYLIME_ATTESTED"
export COMPUTE_SERVICE="nova-compute"
```

## 部署顺序

在 csri10 上部署同步组件：

```bash
install -d -m 0755 /etc/keylime-openstack-sync /opt/keylime-openstack-sync /var/lib/keylime-openstack-sync
install -m 0644 deploy/env/openstack-keylime-lab.env /etc/keylime-openstack-sync/openstack-keylime-lab.env
install -m 0755 deploy/scripts/keylime-*.sh /opt/keylime-openstack-sync/
install -m 0644 deploy/systemd/keylime-openstack-sync.service /etc/systemd/system/keylime-openstack-sync.service
install -m 0644 deploy/systemd/keylime-openstack-sync.timer /etc/systemd/system/keylime-openstack-sync.timer
```

刷新 agent inventory：

```bash
KEYLIME_AGENT_INVENTORY_FORCE=true /opt/keylime-openstack-sync/keylime-agent-inventory-refresh.sh
cat /etc/keylime-openstack-sync/keylime-agent-inventory.env
cat /var/log/keylime-openstack-agent-inventory.json | python3 -m json.tool
```

启动同步 timer：

```bash
systemctl daemon-reload
systemctl enable --now keylime-openstack-sync.timer
systemctl start keylime-openstack-sync.service
```

部署前端：

```bash
install -d -m 0755 /opt/keylime-openstack-console
install -m 0644 deploy/frontend/index.html /opt/keylime-openstack-console/index.html
install -m 0644 deploy/frontend/trust_monitor_server.py /opt/keylime-openstack-console/trust_monitor_server.py
install -m 0644 deploy/frontend/README.md /opt/keylime-openstack-console/README.md
```

systemd 服务：

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
```

## 验证命令

语法检查：

```bash
bash -n /opt/keylime-openstack-sync/keylime-agent-inventory-refresh.sh
bash -n /opt/keylime-openstack-sync/keylime-sync-control-loop.sh
bash -n /opt/keylime-openstack-sync/keylime-placement-sync.sh
bash -n /opt/keylime-openstack-sync/keylime-nova-compute-quarantine.sh
python3 -m py_compile /opt/keylime-openstack-console/trust_monitor_server.py
```

运行态检查：

```bash
systemctl is-active keylime-openstack-sync.timer
systemctl is-enabled keylime-openstack-sync.timer
systemctl list-timers --all | grep keylime-openstack-sync

curl -s "http://172.31.100.10:8088/api/status?force=1" | python3 -m json.tool
```

节点摘要检查：

```bash
curl -s "http://172.31.100.10:8088/api/status?force=1" > /tmp/keylime-status.json

python3 - <<'PY'
import json
d = json.load(open("/tmp/keylime-status.json"))
print("summary:", d.get("summary"))
for n in d.get("nodes", []):
    print(
        n.get("host"),
        "ip=", n.get("ip"),
        "vm_count=", n.get("vm_count"),
        "trust=", n.get("conclusion", {}).get("text"),
        "decision=", n.get("decision", {}).get("result"),
        "trait=", n.get("placement", {}).get("trait_present"),
        "service=", n.get("service", {}).get("status"), "/", n.get("service", {}).get("state"),
    )
PY
```

预期输出应包含：

```text
summary: {'total': 2, 'trusted': 2, 'untrusted': 0, 'no_agent': 0, ...}
csri8 ip= 172.31.100.8 vm_count= 2 trust= 可信 decision= PASS_FRESH trait= True service= enabled / up
csri9 ip= 172.31.100.9 vm_count= 5 trust= 可信 decision= PASS_FRESH trait= True service= enabled / up
```

## 后续实验边界

第一版“自动纳管”的含义需要区分：

```text
OpenStack 监控层面：所有 nova-compute 节点都会自动显示，包括未装 agent 的节点。
Keylime 可信管控层面：只有能获得 agent UUID/IP 的节点才会进入 attestation、Trait 同步、策略下发和 quarantine 流程。
```

因此下一阶段新增计算节点时，最稳路径是：

```text
1. 先确认 OpenStack 中 nova-compute/hypervisor 可见。
2. 安装并启动 Keylime agent。
3. 确认 registrar reglist 中能看到 agent UUID 与 IP。
4. 运行 inventory refresh。
5. 若 reglist 暂时无法解析 IP，则在静态 env 映射中增加该节点作为兜底。
6. 运行 control loop 并通过前端确认状态。
```
