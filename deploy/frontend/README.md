# 管理界面

本目录是 FastAPI 容器直接提供的 Vue 静态页面，不需要 Node.js 构建步骤。

当前阶段只保留两个基础页面：

```text
节点状态   查看 Keylime 对计算节点的 TPM 启动度量、IMA 运行时度量和证明新鲜度
策略管理   通过侧边子导航管理可信启动、IMA 运行时策略和 EVM 策略
```

当前不在前端暴露复杂功能：

```text
OpenStack 调度联动
nova-compute 隔离
VM 风险标记
策略下发和绑定
任务与审计详情
```

策略管理包含三个独立子页面。每个策略类型页面只展示：

```text
新增策略按钮
该类型的策略列表
每条策略的查看和删除操作
```

当前不在列表里提供编辑入口。

这些能力后续在 Keylime 基本能力稳定后再逐步加入。

主要接口：

```text
GET    /api/keylime/check
GET    /api/policies
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
