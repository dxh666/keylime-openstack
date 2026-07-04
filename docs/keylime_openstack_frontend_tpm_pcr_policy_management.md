# Keylime + OpenStack 前端 TPM PCR 策略管理

## 目标

在现有计算节点可信状态监控页面基础上，新增 Keylime TPM PCR 策略管理模块：

```text
策略库管理
节点策略绑定
单节点下发
统一下发
下发结果记录
```

第一版只覆盖 TPM PCR 策略，不包含 IMA runtime policy 和 measured boot policy。

## 策略模型

前端保存结构化策略：

```json
{
  "id": "csri8-pcr7",
  "name": "csri8 PCR7 基线",
  "description": "csri8 当前 SHA256 PCR7",
  "type": "tpm_pcr",
  "hash_alg": "sha256",
  "pcrs": {
    "7": "AD69DC884387BB14056F05ABC4AB0B8AA751824D909931618FB6365EE2C2938D"
  },
  "mask": "0x80",
  "tpm_policy": {
    "mask": "0x80",
    "7": [
      "ad69dc884387bb14056f05abc4ab0b8aa751824d909931618fb6365ee2c2938d"
    ]
  }
}
```

`mask` 由 PCR 编号自动计算。比如 PCR7 对应：

```text
1 << 7 = 0x80
```

## 后端 API

```text
GET    /api/policies
POST   /api/policies
DELETE /api/policies/<policy_id>
POST   /api/policies/apply
```

状态监控 API：

```text
GET /api/status
GET /api/status?force=1
```

`/api/status` 默认返回后台采集缓存，避免每次浏览器刷新都同步执行多条 OpenStack CLI。
`force=1` 用于手动强制刷新。

后台采集间隔：

```bash
export KEYLIME_STATUS_REFRESH_SECONDS="3"
```

VM 数量默认由一次全量 server list 聚合得到，避免每个计算节点单独查询：

```bash
openstack server list --all-projects --long -f json
```

如果某个 OpenStack CLI 版本不返回 Host 列，可以开启慢速回退：

```bash
export KEYLIME_VM_COUNT_SLOW_FALLBACK="true"
```

计算服务、hypervisor、server count、resource provider、trait 查询会并发执行。
`/api/status` 的 `cache.last_refresh_duration_seconds` 字段用于观察最近一次后台采集耗时。

Resource provider traits 会优先尝试批量读取；如果 CLI 不支持，可保留慢速回退：

```bash
export KEYLIME_TRAIT_SLOW_FALLBACK="true"
```

策略库默认保存位置：

```text
/var/lib/keylime-openstack-sync/tpm-pcr-policies.json
```

可通过环境变量覆盖：

```bash
export KEYLIME_PCR_POLICY_FILE="/var/lib/keylime-openstack-sync/tpm-pcr-policies.json"
```

## 多节点 agent 映射

策略下发需要将 OpenStack compute host 映射到 Keylime agent UUID。

示例：

```bash
export KEYLIME_AGENT_HOSTS="csri9,csri8"
export KEYLIME_AGENT_IP_MAP="csri9=172.31.100.9,csri8=172.31.100.8"
export KEYLIME_AGENT_UUID_MAP="csri9=11111111-1111-4111-8111-000000000009,csri8=22222222-2222-4222-8222-000000000008"
export KEYLIME_AGENT_PORT="9002"
```

## 下发逻辑

第一版使用实验中最稳定的方式：先从 verifier 删除旧记录，再用新 TPM policy 添加。

```bash
docker compose run --rm keylime-tenant \
  -c update \
  -t <agent_ip> \
  -tp 9002 \
  -u <agent_uuid> \
  -v 172.31.100.10 \
  -vp 8881 \
  -r 172.31.100.10 \
  -rp 8891 \
  --agent-api-version 2.5 \
  --tpm_policy '<policy_json>'

docker compose run --rm keylime-tenant \
  -c reactivate \
  -u <agent_uuid> \
  -v 172.31.100.10 \
  -vp 8881 \
  -r 172.31.100.10 \
  -rp 8891
```

这个过程会短暂重置该 agent 的 verifier 状态。等待 verifier 下一轮 quote 后，状态应进入 `PASS` 或 `FAIL`。

## 写 API 保护

实验环境可以不设置令牌。若设置：

```bash
export KEYLIME_POLICY_ADMIN_TOKEN="<TOKEN>"
```

则前端写操作需要请求头：

```text
X-Admin-Token: <TOKEN>
```

## csri8 PCR7 策略示例

基于当前实验记录，csri8 的 SHA256 PCR7 为：

```text
AD69DC884387BB14056F05ABC4AB0B8AA751824D909931618FB6365EE2C2938D
```

可创建策略：

```json
{
  "name": "csri8 PCR7 基线",
  "hash_alg": "sha256",
  "pcrs": {
    "7": "AD69DC884387BB14056F05ABC4AB0B8AA751824D909931618FB6365EE2C2938D"
  }
}
```

## 验证

保存策略后查看策略文件：

```bash
cat /var/lib/keylime-openstack-sync/tpm-pcr-policies.json | python3 -m json.tool
```

下发后查看 verifier 状态：

```bash
cd /opt/keylime-docker

docker compose run --rm keylime-tenant \
  -c status \
  -u 22222222-2222-4222-8222-000000000008 \
  -v 172.31.100.10 \
  -vp 8881 \
  -r 172.31.100.10 \
  -rp 8891
```

若状态为 `PASS`，说明 PCR 策略与当前 TPM quote 匹配。若状态为 `FAIL`，优先检查：

```text
PCR 摘要是否来自同一节点
hash_alg 是否为 sha256
PCR 编号是否正确
节点是否重启或 BIOS / Secure Boot 状态是否变化
```
