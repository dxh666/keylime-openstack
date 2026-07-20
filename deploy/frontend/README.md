# 管理界面

本目录是 FastAPI 容器直接提供的 Vue 静态页面，不需要 Node.js 构建步骤。

当前阶段保留生产控制台的基础信息架构：

```text
首页             查看控制节点、计算节点、在线会话和近期事件
节点管理         分为控制节点和计算节点
策略管理         通过侧边子导航管理可信启动和 IMA 运行时策略
全局策略控制     查看当前参与可信判定的能力开关
告警中心         查看当前不可信或纳管异常节点
任务中心         查看后台同步、策略下发等任务
审计日志         查看策略操作和证据采集记录
```

当前不在前端暴露复杂功能：

```text
OpenStack 调度联动
nova-compute 隔离
VM 风险标记
策略批量编排和回滚
任务与审计详情
```

策略管理包含独立子页面。每个策略类型页面只展示：

```text
新增策略按钮
该类型的策略列表
每条策略的查看和删除操作
```

当前不在列表里提供编辑入口。

这些能力后续在 Keylime 基本能力稳定后再逐步加入。

主要接口：

```text
GET    /api/dashboard
GET    /api/keylime/check
GET    /api/nodes
GET    /api/policies
GET    /api/tasks
GET    /api/audit
POST   /api/policies
PUT    /api/policies/{policy_id}
DELETE /api/policies/{policy_id}
```

策略新增、删除会弹出确认框。操作者需要在确认框里输入
`ADMIN_TOKEN` 对应的管理令牌，令牌不会常驻显示在页面顶部。

部署后验证：

```bash
curl -fsS http://127.0.0.1:8088/api/health
curl -fsS http://127.0.0.1:8088/api/keylime/check
curl -fsS http://127.0.0.1:8088/api/policies
```
