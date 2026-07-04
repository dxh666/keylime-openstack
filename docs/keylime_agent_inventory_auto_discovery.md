# Keylime Agent Inventory 自动发现第一版

## 目标

第一版自动发现用于解决新增计算节点后需要手工维护 `KEYLIME_AGENT_HOSTS`、
`KEYLIME_AGENT_IP_MAP`、`KEYLIME_AGENT_UUID_MAP` 的问题。

系统每轮同步时会先刷新 agent 清单：

1. 从 OpenStack 读取 `nova-compute` 服务和 hypervisor 管理 IP。
2. 通过 `keylime-tenant -c reglist` 读取 Keylime registrar 中已注册的 agent。
3. 按 OpenStack hypervisor `Host IP` 与 Keylime agent `contact_ip/ip` 匹配。
4. 成功后生成 `/etc/keylime-openstack-sync/keylime-agent-inventory.env`。
5. 主配置文件 source 该 inventory 文件，使前端和同步循环都使用最新清单。

## 文件

```text
/opt/keylime-openstack-sync/keylime-agent-inventory-refresh.sh
/etc/keylime-openstack-sync/keylime-agent-inventory.env
/var/log/keylime-openstack-agent-inventory.json
```

## 配置项

```bash
export KEYLIME_AGENT_INVENTORY_FILE="/etc/keylime-openstack-sync/keylime-agent-inventory.env"
export KEYLIME_AGENT_INVENTORY_JSON="/var/log/keylime-openstack-agent-inventory.json"
export KEYLIME_AGENT_INVENTORY_REFRESH="true"
export KEYLIME_AGENT_INVENTORY_REFRESH_SECONDS="60"
export KEYLIME_AGENT_INVENTORY_ALLOW_EMPTY="false"
```

主配置中仍保留静态 csri9/csri8 清单作为兜底。自动刷新成功后，inventory 文件会覆盖静态清单。
如果刷新失败，脚本不会覆盖已有 inventory，control loop 会继续使用上一份有效清单。

## 验证

```bash
/opt/keylime-openstack-sync/keylime-agent-inventory-refresh.sh

cat /etc/keylime-openstack-sync/keylime-agent-inventory.env
cat /var/log/keylime-openstack-agent-inventory.json | python3 -m json.tool

systemctl start keylime-openstack-sync.service
ls -l /var/log/keylime-openstack-sync-decision*.json
```

## 边界

- OpenStack 中出现的新 `nova-compute` 会自动进入监控。
- 只有已经注册到 Keylime registrar，且 agent IP 能匹配 OpenStack hypervisor IP 的节点，才会自动进入可信管控。
- 未安装 agent 或 IP 无法匹配的节点仍显示为“未装代理”，不会被自动执行 attestation、Trait 同步或策略下发。
