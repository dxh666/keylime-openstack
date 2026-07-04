# Keylime + OpenStack 集成能力盘点

日期：2026-07-04

## 结论

截至 Case 9，持续运行能力已经统一挂载到两条 systemd 链路：

```text
keylime-openstack-monitor.service
  -> 管理系统前端与 API

keylime-openstack-sync.timer
  -> keylime-sync-control-loop.sh
  -> agent inventory refresh
  -> per-host Keylime decision
  -> Placement trait 同步
  -> nova-compute quarantine
  -> VM risk metadata marker
```

因此核心能力不再停留在手动实验脚本。

仍保留在 `deploy/examples/` 的脚本属于实验复跑或故障注入命令，不应该常驻自动执行。例如停止 agent、强制创建测试 VM、恢复 agent 等动作会改变实验环境，适合作为案例验证，不适合作为自动化守护任务。

## Case 集成状态

| Case | 能力 | 当前状态 | 自动化入口 |
| --- | --- | --- | --- |
| Case 1 | Keylime PASS/FAIL 写入 Placement trait | 已集成 | `keylime-openstack-sync.timer` -> `keylime-placement-sync.sh` |
| Case 2 | PCR/freshness 判定，禁止历史 PASS | 已集成 | `keylime-placement-sync.sh` decision JSON |
| Case 3 | trusted flavor 与 project 授权 | 一次性 provisioning | `keylime-openstack-trusted-flavor-setup.sh` |
| Case 4 | 失信 host quarantine / 禁用 nova-compute | 已集成 | `keylime-nova-compute-quarantine.sh` |
| Case 5 | 计算节点可信状态监控前端 | 已集成 | `keylime-openstack-monitor.service` |
| Case 6 | TPM PCR 策略管理前端 | 已集成 | `keylime-openstack-monitor.service` |
| Case 7 | 多节点 agent inventory 与 per-host decision | 已集成 | `keylime-agent-inventory-refresh.sh` + control loop |
| Case 8 | 可信池动态收缩与恢复 | 已集成为运行时能力 | Trait/quarantine/control loop；故障注入命令保留在 example |
| Case 9 | 失信 host 上存量 VM 风险 metadata 标记与恢复清理 | 已集成 | `keylime-vm-risk-marker.sh` |

## 运行时能力链路

### 管理前端

```text
deploy/systemd/keylime-openstack-monitor.service
  WorkingDirectory=/opt/keylime-openstack-console
  ExecStart=/usr/bin/python3 /opt/keylime-openstack-console/trust_monitor_server.py --host 172.31.100.10 --port 8088
```

提供：

```text
计算节点可信状态实时监控
TPM PCR 策略管理
/api/status
/api/health
```

### 同步控制器

```text
deploy/systemd/keylime-openstack-sync.timer
  OnUnitActiveSec=30s

deploy/systemd/keylime-openstack-sync.service
  ExecStart=/opt/keylime-openstack-sync/keylime-sync-control-loop.sh
```

每轮执行：

```text
1. 刷新 Keylime agent inventory
2. 对每个 agent host 生成 decision JSON
3. 根据 decision 添加/移除 CUSTOM_KEYLIME_ATTESTED
4. 根据 decision 启用/禁用 nova-compute
5. 根据 host trust 状态标记或清理 VM keylime_trust_* metadata
```

## 正式脚本

```text
deploy/scripts/keylime-openstack-control-plane-install.sh
deploy/scripts/keylime-openstack-capability-check.sh
deploy/scripts/keylime-openstack-trusted-flavor-setup.sh
deploy/scripts/keylime-agent-inventory-refresh.sh
deploy/scripts/keylime-placement-sync.sh
deploy/scripts/keylime-nova-compute-quarantine.sh
deploy/scripts/keylime-sync-control-loop.sh
deploy/scripts/keylime-vm-risk-marker.sh
```

## 保留为手动实验复跑的脚本

```text
deploy/examples/case8-dynamic-trusted-pool-commands.sh
deploy/examples/phase3-private-flavor-commands.sh
deploy/examples/verification-commands.md
```

其中 `phase3-private-flavor-commands.sh` 已由正式脚本 `keylime-openstack-trusted-flavor-setup.sh` 替代，保留它只是为了对应早期案例记录。

以下 helper 仍是人工运维辅助，不应自动运行：

```text
deploy/scripts/keylime-reactivate-csri9.sh
deploy/scripts/restart-keylime-agent-csri9.sh
```

## 一键部署

在 csri10 的仓库根目录运行：

```bash
deploy/scripts/keylime-openstack-control-plane-install.sh
```

该脚本会：

```text
安装 /opt/keylime-openstack-sync/*.sh
安装 /opt/keylime-openstack-console/
安装 systemd service/timer
保留现有 /etc/keylime-openstack-sync/openstack-keylime-lab.env
将仓库 env 模板保存为 openstack-keylime-lab.env.repo-template
为缺失的新配置追加默认值
启用 keylime-openstack-monitor.service
启用 keylime-openstack-sync.timer
触发一次 keylime-openstack-sync.service
```

trusted flavor/project 授权可单独幂等执行：

```bash
/opt/keylime-openstack-sync/keylime-openstack-trusted-flavor-setup.sh
```

## 能力检查

部署后运行：

```bash
/opt/keylime-openstack-sync/keylime-openstack-capability-check.sh
```

检查内容：

```text
systemd service/timer 状态
核心脚本可执行状态
前端 /api/status 状态
OpenStack nova-compute 状态
Placement trusted trait 状态
trusted flavor 状态
VM risk marker 审计摘要
```

## 下一阶段建议

在完成整体部署和自动化验证后，下一阶段建议推进：

```text
Case 10: 多级可信 Trait
  CUSTOM_KEYLIME_AGENT_ONLINE
  CUSTOM_KEYLIME_TPM_QUOTE_VALID
  CUSTOM_KEYLIME_PCR_POLICY_VALID
  CUSTOM_KEYLIME_RUNTIME_RISK_MARKED
  CUSTOM_KEYLIME_PRODUCTION_TRUSTED
```

这样 OpenStack 不再只有一个粗粒度 `CUSTOM_KEYLIME_ATTESTED`，而是可以根据 workload 等级选择不同可信条件。
