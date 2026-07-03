# Keylime + OpenStack 计算节点可信状态监控前端基线

日期：2026-07-03

## 1. 目标

本阶段先完成前端管理系统的第一个稳定功能：

```text
计算节点可信状态实时监控
```

该版本不做策略写入、不做失信处置按钮、不在浏览器中执行任意 root 命令，只提供只读状态展示。

## 2. 部署位置

推荐部署在 `csri10`：

```text
/opt/keylime-openstack-console/
```

启动命令：

```bash
cd /opt/keylime-openstack-console
python3 trust_monitor_server.py --host 172.31.100.10 --port 8088
```

访问地址：

```text
http://172.31.100.10:8088/
```

## 3. 当前能力

前端页面可以：

```text
1. 自动识别 OpenStack 中所有 nova-compute 节点。
2. 为每个计算节点生成一个状态卡片。
3. 展示节点 IP、承载虚拟机数量、可信状态、服务状态。
4. 使用绿色、红色、黄色圆点区分可信、不可信、未装代理。
5. 点击节点卡片展开明细，再次点击隐藏明细。
6. 显示 keylime-openstack-sync.timer 当前状态。
```

后端只读 API：

```text
GET /api/status
GET /api/health
```

## 4. 状态来源

只读 API 在 `csri10` 上读取：

```text
1. /var/log/keylime-openstack-sync-decision.json
2. keylime-openstack-sync.timer
3. openstack compute service list -f json
4. openstack hypervisor list --long -f json
5. openstack hypervisor show <host> -f json
6. openstack server list --all-projects --host <host> -f json
7. OpenStack Placement resource provider traits
8. /var/lib/keylime-openstack-sync/*.disabled-by-keylime
```

## 5. 状态颜色规则

```text
绿色：
  Keylime result 为 PASS_FRESH；
  可信 trait 存在；
  nova-compute enabled/up；
  不存在 disabled-by-keylime marker。

红色：
  Keylime 不可信或状态过期；
  或 nova-compute disabled/down；
  或可信状态与 OpenStack 同步结果不一致。

黄色：
  OpenStack 中存在该 nova-compute 节点；
  但该节点暂未安装或暂未纳入 Keylime agent。
```

当前实验中：

```text
csri9 已纳入 Keylime agent；
csri8 可被自动识别为 OpenStack 计算节点，但暂未纳入 Keylime agent。
```

## 6. 已修复问题：虚拟机数量显示未知

### 6.1 问题现象

前端节点卡片中的：

```text
虚拟机
```

最初全部显示：

```text
未知
```

### 6.2 原因

初版只从以下命令读取承载虚拟机数量：

```bash
openstack hypervisor list --long -f json
```

但不同 OpenStackClient 版本、Nova API microversion 或输出字段差异下，该命令可能不返回 `running_vms` 字段。

### 6.3 修复方案

后端读取顺序改为：

```text
1. openstack hypervisor list --long -f json
2. openstack hypervisor show <host> -f json
3. openstack server list --all-projects --host <host> -f json
```

同时 API 为每个节点返回：

```text
vm_count
vm_count_source
vm_count_errors
```

用于确认数量来自哪条命令，以及是否有回退失败原因。

### 6.4 验证命令

```bash
curl -s http://172.31.100.10:8088/api/status | python3 -m json.tool
```

重点检查：

```text
nodes[].vm_count
nodes[].vm_count_source
nodes[].vm_count_errors
```

## 7. 安全边界

当前前端服务仍是实验级：

```text
1. 无登录认证。
2. 只读 API 不提供写操作。
3. 不在浏览器保存 OpenStack token。
4. 不提供策略写入、节点禁用、节点恢复等按钮。
5. 建议只在实验内网或 SSH 隧道中访问。
```

后续如果加入策略管理或失信处置功能，必须先补：

```text
认证
授权
操作审计
CSRF 防护
最小权限 OpenStack service account
```

## 8. 下一阶段实验入口

该前端基线可用于观察后续实验结果：

```text
1. csri8 修复 SHA256 PCR bank 后接入 Keylime agent。
2. 多计算节点可信状态同时展示。
3. Keylime 策略变化导致节点从绿色变为红色。
4. 未安装 agent 的节点保持黄色。
5. 失信节点处置逻辑触发后，页面显示 nova-compute 状态变化。
```
