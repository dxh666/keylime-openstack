# 计算节点可信状态实时监控

这是 Keylime + OpenStack 前端管理系统当前唯一启用的功能。

当前页面只做一件事：

```text
实时监控计算节点可信状态
```

它通过只读本地 API 自动识别 OpenStack 中的 `nova-compute` 节点，并把所有计算节点纳入监控视图。当前实验中只有 `COMPUTE_HOST=csri9` 绑定 Keylime attestation 结果；其它暂未安装或暂未纳入 Keylime agent 的计算节点会显示为黄色。

## 监控内容

页面每 5 秒自动刷新一次：

```text
1. openstack compute service list 中的所有 nova-compute 节点。
2. openstack hypervisor list 中可用的节点 IP 信息。
3. openstack hypervisor list/show 中的承载虚拟机数量。
4. keylime-openstack-sync.timer 状态。
5. 已纳入 Keylime 的节点是否具有 CUSTOM_KEYLIME_ATTESTED。
6. 已纳入 Keylime 的节点是否存在 disabled-by-keylime marker。
```

虚拟机数量读取顺序：

```text
1. openstack hypervisor list --long -f json
2. openstack hypervisor show <host> -f json
3. openstack server list --all-projects --host <host> -f json
```

## 首页状态颜色

```text
绿色：节点可信，可作为可信计算节点使用。
红色：节点不可信、状态过期、被禁用或 nova-compute 掉线。
黄色：计算节点存在，但暂未安装或暂未纳入 Keylime agent。
```

## 交互方式

```text
首页以计算节点卡片展示摘要。
点击计算节点卡片显示节点明细。
再次点击同一卡片隐藏节点明细。
```

## 部署到 csri10

复制 `deploy/frontend/` 到 csri10 后启动：

```bash
cd /opt/keylime-openstack-console
python3 trust_monitor_server.py --host 172.31.100.10 --port 8088
```

浏览器访问：

```text
http://172.31.100.10:8088/
```

如果希望只允许 SSH 隧道访问，可以改为：

```bash
python3 trust_monitor_server.py --host 127.0.0.1 --port 8088
```

## systemd 运行方式

```ini
[Unit]
Description=Keylime OpenStack Trust Monitor
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/keylime-openstack-console
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

## 状态结论规则

```text
PASS_FRESH + trait 存在 + nova-compute enabled/up + marker 不存在
  -> 绿色，可信

非 PASS_FRESH、nova-compute disabled/down、Keylime 状态未知
  -> 红色，不可信或异常

计算节点存在，但未配置为 Keylime agent host
  -> 黄色，未安装或未纳入 Keylime agent
```
