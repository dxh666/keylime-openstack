# Keylime Measured Boot 与 IMA 策略管理

## 能力边界

可信启动使用 Keylime Measured Boot，而不是只比较固定 PCR 值。节点上的
Keylime agent 上报 TPM Quote 和 UEFI event log，Keylime verifier 重放 event
log 并使用命名 measured boot policy 验证参考状态。

IMA 运行时策略包含两部分：

1. Ansible 管理节点 `/etc/ima/ima-policy`。
2. Keylime 根据节点的 `ascii_runtime_measurements` 生成 runtime policy，并由
   verifier 校验 PCR 10 和 IMA measurement log。

长期状态保存在 PostgreSQL。采集到的 event log 和 IMA measurement log 仅在
任务临时目录中存在，生成的 reference state、runtime policy、节点绑定和下发
状态写入数据库。

## Keylime verifier 前置配置

Measured Boot 不能使用 `accept-all` 作为生产判断规则。Keylime verifier 的
`verifier.conf` 需要配置实际的 elchecking policy，例如：

```ini
[verifier]
measured_boot_policy_name = example
```

如果使用自定义策略模块，还需要按 Keylime 版本配置 `measured_boot_imports`，并
把模块挂载到 verifier 容器。修改后重启 `keylime-verifier`。管理面保存的
`policy_engine` 必须与 verifier 实际配置一致。

控制面环境文件还必须声明相同的引擎：

```ini
KEYLIME_MEASURED_BOOT_POLICY_ENGINE=example
```

worker 会在下发前校验两者的管理面声明，且拒绝 `accept-all`。节点的 PCR 0-7
作为默认管理范围；最终纳入 Quote 和事件日志重放的 PCR 由 verifier 中启用的
elchecking policy 的 `get_relevant_pcrs()` 决定。

每个计算节点必须能够读取：

```text
/sys/kernel/security/tpm0/binary_bios_measurements
/sys/kernel/security/ima/ascii_runtime_measurements
```

## Ansible 准备

API 和 worker 容器使用数据库中的节点地址动态生成临时 inventory，不需要维护
静态主机清单。准备专用 SSH 密钥和 known_hosts：

```text
/etc/keylime-openstack/ansible/id_ed25519
/etc/keylime-openstack/ansible/known_hosts
```

私钥权限应为 `0600`。生产环境应保持 SSH host key 校验开启。

实验环境可在控制节点一次性执行：

```bash
install -d -m 0700 /etc/keylime-openstack/ansible
ssh-keygen -t ed25519 -N '' \
  -f /etc/keylime-openstack/ansible/id_ed25519

for ip in 172.31.100.8 172.31.100.9 172.31.100.22; do
  ssh-copy-id -i /etc/keylime-openstack/ansible/id_ed25519.pub root@"$ip"
  ssh-keyscan -H "$ip" >> /etc/keylime-openstack/ansible/known_hosts
done

chmod 0600 /etc/keylime-openstack/ansible/id_ed25519
chmod 0644 /etc/keylime-openstack/ansible/known_hosts
```

`ssh-keyscan` 只采集主机公钥，不验证公钥来源。生产部署必须通过带外管理或资产
系统核对指纹后再写入 `known_hosts`。
worker 启动策略任务前会检查 `ansible-playbook`、OpenSSH `ssh`、私钥和
`known_hosts` 是否存在。镜像默认使用 `INSTALL_OS_TOOLS=true` 安装 OpenSSH
客户端；即使旧环境文件仍设置为 `false`，当基础镜像缺少 `ssh` 时构建也会尝试
自动补齐，否则可信启动和 IMA 策略下发无法工作。

下发策略前还应确认 worker 容器到计算节点管理 IP 的 SSH 出口正常。宿主机能 SSH
而容器超时，通常是 Docker bridge 子网与 OpenStack 管理网段重叠或容器出口被
防火墙拦截：

```bash
ssh -o ConnectTimeout=5 root@172.31.100.8 hostname
docker exec keylime_openstack_worker sh -lc \
  "ssh -o ConnectTimeout=5 root@172.31.100.8 hostname"
docker exec keylime_openstack_worker ip route
docker network inspect keylime_openstack_net
```

默认控制面网络为 `10.245.0.0/24`。如果该网段在现场已被占用，修改
`KEYLIME_OPENSTACK_DOCKER_SUBNET` 后重建 API/worker 容器。

节点 IMA 策略下发会安装 `/etc/ima/ima-policy`。如果当前内核命令行含有
`ima_policy=tcb` 等内置策略参数，Ansible 会通过 Ubuntu 的 `update-grub` 或
Anolis/RHEL 系的 `grubby` 移除该参数，防止内置策略抢先占用自定义策略加载入口。
重启后任务会检查启动参数、IMA measurement log 和 `/etc/ima/ima-policy` 的加载
记录，确认策略真正生效后才生成 Keylime runtime policy。

## 部署更新

代码更新后执行：

```bash
cd /opt/keylime-openstack

deploy/scripts/keylime-openstack-compose-deploy.sh build
deploy/scripts/keylime-openstack-compose-deploy.sh migrate
deploy/scripts/keylime-openstack-compose-deploy.sh up
```

迁移 `0002_policy_deployment_state` 会为每个节点策略绑定增加执行器、下发状态、
Keylime 外部策略名、渲染后策略和错误记录。

## 任务流程

新增策略时 API 在一个事务中创建策略、节点绑定和 `policy_deploy` 任务。worker
处理任务：

```text
PostgreSQL policy_deploy
  -> Python Ansible executor
  -> node event log / IMA measurement log
  -> Keylime named policy create-or-update
  -> Keylime agent policy binding
  -> PostgreSQL binding status
```

Keylime 命名策略使用内容哈希稳定命名，并采用“先 update、不存在再 add”的幂等
写入方式，避免重复执行造成 allowlist 或 measured boot policy 名称冲突。

已下发策略不能原地修改。变更时新增策略版本并下发，新的节点绑定验证成功后，
旧绑定会标记为 `superseded`。这样可保留策略历史，且不会让数据库状态先于
Keylime verifier 发生不可回滚的变化。

IMA 策略发生变化但未选择维护重启时，节点绑定进入 `awaiting_reboot`。维护重启后
在策略详情中执行“重新下发”，worker 才会采集新一轮 IMA measurement log 并
生成 Keylime runtime policy。
