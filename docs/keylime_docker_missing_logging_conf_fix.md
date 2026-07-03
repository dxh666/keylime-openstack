# Keylime Docker 启动失败：缺少 logging.conf 的处理方案

日期：2026-07-02

## 1. 当前结论

你现在的报错已经不是 Docker Compose 的 `command` 写法问题了，因为容器命令已经变成：

```text
"keylime_verifier"
"keylime_registrar"
```

真正的失败点是：

```text
Config file not found in ['/etc/keylime/logging.conf', '/usr/etc/keylime/logging.conf']
FileNotFoundError: ... /usr/local/lib/python3.9/site-packages/keylime/config/logging.conf
```

这说明当前 `quay.io/keylime/keylime_verifier:latest` 和 `quay.io/keylime/keylime_registrar:latest` 在启动时没有找到可用的 `logging.conf`。按 Keylime README，Keylime 会从 `/etc/keylime/*.conf` 或 `/usr/etc/keylime/*.conf` 读取配置，也可以通过 `keylime_{VERIFIER,REGISTRAR,TENANT,CA,LOGGING}_CONFIG` 指定替代配置路径。因此现在要先确认镜像内到底有没有配置模板。

## 2. 先停止重启循环

在 `csri10` 执行：

```bash
cd /opt/keylime-docker
docker compose stop keylime-registrar keylime-verifier
```

## 3. 检查镜像内部是否带配置文件

在 `csri10` 执行：

```bash
cd /opt/keylime-docker

for img in "$KEYLIME_REGISTRAR_IMAGE" "$KEYLIME_VERIFIER_IMAGE"; do
  echo "### image: $img"
  docker run --rm --entrypoint sh "$img" -c '
    echo "# config files"
    find /etc/keylime /usr/etc/keylime /usr/local/lib/python3.9/site-packages/keylime/config \
      -maxdepth 2 -type f 2>/dev/null | sort || true

    echo "# keylime commands"
    ls -1 /usr/local/bin/keylime* 2>/dev/null || true
  '
done
```

看输出里有没有这些文件：

```text
logging.conf
registrar.conf
verifier.conf
tenant.conf
ca.conf
```

## 4. 分支 A：如果镜像里有配置模板

如果上一步能找到配置文件，把它们复制出来：

```bash
cd /opt/keylime-docker
mkdir -p /opt/keylime-docker/config

docker run --rm --entrypoint sh \
  -v /opt/keylime-docker/config:/out \
  "$KEYLIME_VERIFIER_IMAGE" \
  -c '
    cp -av /etc/keylime/* /out/ 2>/dev/null || \
    cp -av /usr/etc/keylime/* /out/ 2>/dev/null || \
    cp -av /usr/local/lib/python3.9/site-packages/keylime/config/* /out/ 2>/dev/null || \
    true
  '

ls -l /opt/keylime-docker/config
```

然后把 compose 改成显式挂载配置目录：

```yaml
services:
  keylime-registrar:
    image: ${KEYLIME_REGISTRAR_IMAGE}
    container_name: keylime-registrar
    restart: unless-stopped
    network_mode: host
    volumes:
      - /opt/keylime-docker/config:/etc/keylime:ro
      - /opt/keylime-docker/varlib:/var/lib/keylime
      - /opt/keylime-docker/logs:/var/log/keylime

  keylime-verifier:
    image: ${KEYLIME_VERIFIER_IMAGE}
    container_name: keylime-verifier
    restart: unless-stopped
    network_mode: host
    depends_on:
      - keylime-registrar
    volumes:
      - /opt/keylime-docker/config:/etc/keylime:ro
      - /opt/keylime-docker/varlib:/var/lib/keylime
      - /opt/keylime-docker/logs:/var/log/keylime

  keylime-tenant:
    image: ${KEYLIME_TENANT_IMAGE}
    container_name: keylime-tenant
    network_mode: host
    profiles: ["tools"]
    volumes:
      - /opt/keylime-docker/config:/etc/keylime:ro
      - /opt/keylime-docker/varlib:/var/lib/keylime
      - /opt/keylime-docker/logs:/var/log/keylime
```

重启：

```bash
cd /opt/keylime-docker
docker compose up -d keylime-registrar keylime-verifier
docker logs --tail 100 keylime-registrar
docker logs --tail 100 keylime-verifier
ss -lntp | egrep '8881|8891'
```

## 5. 分支 B：如果镜像里没有配置模板

如果第 3 步找不到 `logging.conf`，优先不要继续用 `latest`。你这次输出已经确认：

```text
# config files
```

为空，所以当前 `latest` 镜像不适合作为本实验基线。先把版本固定到当前文档对应的稳定 release。GitHub release 名称带 `v` 前缀，因此优先尝试 `v7.14.2`，如果镜像仓库没有这个 tag，再尝试 `7.14.2`：

```bash
cd /opt/keylime-docker

export KEYLIME_VERSION=v7.14.2
export KEYLIME_REGISTRAR_IMAGE=quay.io/keylime/keylime_registrar:${KEYLIME_VERSION}
export KEYLIME_VERIFIER_IMAGE=quay.io/keylime/keylime_verifier:${KEYLIME_VERSION}
export KEYLIME_TENANT_IMAGE=quay.io/keylime/keylime_tenant:${KEYLIME_VERSION}

cat >/opt/keylime-docker/.env <<EOF
KEYLIME_REGISTRAR_IMAGE=${KEYLIME_REGISTRAR_IMAGE}
KEYLIME_VERIFIER_IMAGE=${KEYLIME_VERIFIER_IMAGE}
KEYLIME_TENANT_IMAGE=${KEYLIME_TENANT_IMAGE}
EOF

docker compose pull

for img in "$KEYLIME_REGISTRAR_IMAGE" "$KEYLIME_VERIFIER_IMAGE"; do
  echo "### image: $img"
  docker run --rm --entrypoint sh "$img" -c '
    find /etc/keylime /usr/etc/keylime /usr/local/lib/python3.9/site-packages/keylime/config \
      -maxdepth 2 -type f 2>/dev/null | sort || true
  '
done

docker compose up -d --force-recreate keylime-registrar keylime-verifier

docker ps -a | grep keylime
docker logs --tail 100 keylime-registrar
docker logs --tail 100 keylime-verifier
ss -lntp | egrep '8881|8891'
```

预期是：

```text
keylime-registrar Up
keylime-verifier Up
8891 registrar listening
8881 verifier listening
```

如果 `v7.14.2` tag 不存在，再尝试：

```bash
export KEYLIME_VERSION=7.14.1
```

更准确的 fallback 顺序是：

```bash
v7.14.2
7.14.2
v7.14.1
7.14.1
```

使用你能成功 pull 且包含配置文件的具体 release tag。不要优先使用 `latest`，因为 `latest` 可能对应某个 commit 镜像，而不是稳定 release。

## 6. v7.14.2 启动成功但只监听 127.0.0.1

你当前已经看到：

```text
keylime-registrar Up
keylime-verifier Up
127.0.0.1:8891
127.0.0.1:8881
```

这说明 Keylime 控制面已经正常启动，但只监听本机回环地址。`csri10` 本机 tenant 可以访问，`csri9` 上的 agent 不能通过 `172.31.100.10:8881/8891` 访问它。因此下一步要把镜像里的配置复制出来，把监听地址改成管理网地址或 `0.0.0.0`。

推荐实验阶段先绑定 `0.0.0.0`，后续再通过防火墙只允许 `172.31.100.9` 访问：

```bash
cd /opt/keylime-docker
mkdir -p /opt/keylime-docker/config

docker run --rm --entrypoint sh \
  -v /opt/keylime-docker/config:/out \
  "$KEYLIME_VERIFIER_IMAGE" \
  -c 'cp -av /etc/keylime/* /out/'

ls -l /opt/keylime-docker/config
grep -nE '(^|_)(ip|port)[[:space:]]*=' /opt/keylime-docker/config/registrar.conf /opt/keylime-docker/config/verifier.conf
```

你当前 `v7.14.2` 的真实配置项如下：

```text
/opt/keylime-docker/config/registrar.conf:8:ip = "127.0.0.1"
/opt/keylime-docker/config/registrar.conf:9:port = 8890
/opt/keylime-docker/config/registrar.conf:10:tls_port = 8891
/opt/keylime-docker/config/verifier.conf:13:ip = "127.0.0.1"
/opt/keylime-docker/config/verifier.conf:14:port = 8881
/opt/keylime-docker/config/verifier.conf:17:registrar_ip = 127.0.0.1
/opt/keylime-docker/config/verifier.conf:18:registrar_port = 8891
```

因此只改两个监听地址即可。`verifier.conf` 里的 `registrar_ip = 127.0.0.1` 先保持不变，因为 verifier 和 registrar 都在 `csri10` 上，走本机回环更简单。

修改配置：

```bash
cp -a /opt/keylime-docker/config/registrar.conf /opt/keylime-docker/config/registrar.conf.bak
cp -a /opt/keylime-docker/config/verifier.conf /opt/keylime-docker/config/verifier.conf.bak

sed -i -E 's/^ip = .*/ip = "0.0.0.0"/' /opt/keylime-docker/config/registrar.conf
sed -i -E 's/^ip = .*/ip = "0.0.0.0"/' /opt/keylime-docker/config/verifier.conf

grep -nE '(^|_)(ip|port)[[:space:]]*=' /opt/keylime-docker/config/registrar.conf /opt/keylime-docker/config/verifier.conf
```

然后把配置目录挂进 compose。直接重写当前实验用 compose：

```bash
cat >/opt/keylime-docker/docker-compose.yml <<'EOF'
services:
  keylime-registrar:
    image: ${KEYLIME_REGISTRAR_IMAGE}
    container_name: keylime-registrar
    restart: unless-stopped
    network_mode: host
    volumes:
      - /opt/keylime-docker/config:/etc/keylime:ro
      - /opt/keylime-docker/varlib:/var/lib/keylime
      - /opt/keylime-docker/logs:/var/log/keylime

  keylime-verifier:
    image: ${KEYLIME_VERIFIER_IMAGE}
    container_name: keylime-verifier
    restart: unless-stopped
    network_mode: host
    depends_on:
      - keylime-registrar
    volumes:
      - /opt/keylime-docker/config:/etc/keylime:ro
      - /opt/keylime-docker/varlib:/var/lib/keylime
      - /opt/keylime-docker/logs:/var/log/keylime

  keylime-tenant:
    image: ${KEYLIME_TENANT_IMAGE}
    container_name: keylime-tenant
    network_mode: host
    profiles: ["tools"]
    volumes:
      - /opt/keylime-docker/config:/etc/keylime:ro
      - /opt/keylime-docker/varlib:/var/lib/keylime
      - /opt/keylime-docker/logs:/var/log/keylime
EOF
```

重启后检查：

```bash
cd /opt/keylime-docker
docker compose up -d --force-recreate keylime-registrar keylime-verifier
docker logs --tail 80 keylime-registrar
docker logs --tail 80 keylime-verifier
ss -lntp | egrep '8881|8891'
```

预期应变成：

```text
0.0.0.0:8891
0.0.0.0:8881
```

或：

```text
172.31.100.10:8891
172.31.100.10:8881
```

## 7. 仅用于定位的临时 logging.conf

如果你必须继续验证当前 `latest` 镜像，可以只挂载一个最小 `logging.conf`，用于看下一步还缺什么配置。

创建文件：

```bash
mkdir -p /opt/keylime-docker/config

cat >/opt/keylime-docker/config/logging.conf <<'EOF'
[loggers]
keys=root

[handlers]
keys=console

[formatters]
keys=simple

[logger_root]
level=INFO
handlers=console

[handler_console]
class=StreamHandler
level=INFO
formatter=simple
args=()

[formatter_simple]
format=%(asctime)s - %(name)s - %(levelname)s - %(message)s
EOF
```

然后不要挂载整个 `/etc/keylime` 目录，只挂载单个文件，避免遮住镜像内已有配置：

```yaml
volumes:
  - /opt/keylime-docker/config/logging.conf:/etc/keylime/logging.conf:ro
  - /opt/keylime-docker/varlib:/var/lib/keylime
  - /opt/keylime-docker/logs:/var/log/keylime
```

重启后如果继续报 `verifier.conf` 或 `registrar.conf` 缺失，说明这个 `latest` 镜像确实不适合作为当前实验基线，应切换到固定 release tag。

## 8. v7.14.2 没有单独 keylime_agent 镜像

如果在 `csri9` 拉取 agent 镜像时报：

```text
quay.io/keylime/keylime_agent:v7.14.2: not found
```

不要直接切回 `latest` 作为整套 Keylime 基线。`v7.14.2` 的 `keylime_verifier` 镜像能提供 `/etc/keylime/agent.conf` 模板，但不一定包含 `keylime_agent` 可执行文件。因此 agent 侧要先自动探测哪个镜像里真的有 agent 命令。

在 `csri9` 使用：

```bash
docker rm -f keylime-agent 2>/dev/null || true

unset KEYLIME_AGENT_IMAGE
unset KEYLIME_AGENT_ENTRYPOINT

for img in \
  quay.io/keylime/keylime_verifier:v7.14.2 \
  quay.io/keylime/keylime_agent:latest
do
  echo "### inspect agent executable in $img"
  docker pull "$img" || continue

  ep="$(
    docker run --rm --entrypoint sh "$img" -c '
      command -v keylime_agent 2>/dev/null || \
      command -v keylime-agent 2>/dev/null || \
      find /usr/local/bin /usr/bin -maxdepth 1 \( -name keylime_agent -o -name keylime-agent \) 2>/dev/null | head -n 1
    '
  )"

  if [ -n "$ep" ]; then
    export KEYLIME_AGENT_IMAGE="$img"
    export KEYLIME_AGENT_ENTRYPOINT="$ep"
    break
  fi
done

echo "KEYLIME_AGENT_IMAGE=${KEYLIME_AGENT_IMAGE:-NONE}"
echo "KEYLIME_AGENT_ENTRYPOINT=${KEYLIME_AGENT_ENTRYPOINT:-NONE}"
```

如果输出不是 `NONE`，启动 agent 时使用探测出的绝对路径：

```bash
--entrypoint "$KEYLIME_AGENT_ENTRYPOINT"
```

这样可以优先保持 control plane 和 agent 使用同一个 release；如果 release 镜像确实没有 agent 可执行文件，再退到 `keylime_agent:latest`，但仍然挂载前面复制出的 `agent.conf`。

### 8.1 agent.conf 里的 IP 地址必须加引号

如果 agent 启动时报：

```text
Error: Configuration(Config(expected newline, found a period at line 59 column 22 in etc/keylime/agent.conf))
```

通常是因为配置里写成了：

```text
registrar_ip = 172.31.100.10
```

TOML 会把未加引号的 `172.31.100.10` 当成非法数字表达式。应改成字符串：

```text
registrar_ip = "172.31.100.10"
```

修复命令：

```bash
docker rm -f keylime-agent 2>/dev/null || true

sed -i -E 's/^registrar_ip = .*/registrar_ip = "172.31.100.10"/' \
  /opt/keylime-agent-docker/config/agent.conf

nl -ba /opt/keylime-agent-docker/config/agent.conf | sed -n '54,63p'
```

### 8.2 MissingActionsDir

如果 agent 启动时报：

```text
Error: Configuration(MissingActionsDir { path: "/var/lib/keylime", source: Os { code: 2, kind: NotFound, message: "No such file or directory" } })
```

说明 agent 要求的 actions/runtime 目录在容器内不可见。先在宿主机补齐目录，并用临时 shell 验证容器内确实能看到：

```bash
docker rm -f keylime-agent 2>/dev/null || true

install -d -m 0777 /opt/keylime-agent-docker/varlib
install -d -m 0777 /opt/keylime-agent-docker/varlib/actions
install -d -m 0777 /opt/keylime-agent-docker/logs

TPM_DEVICE=/dev/tpmrm0
[ -e "$TPM_DEVICE" ] || TPM_DEVICE=/dev/tpm0

docker run --rm \
  --network host \
  --privileged \
  --entrypoint sh \
  --device "$TPM_DEVICE:$TPM_DEVICE" \
  -e TCTI="device:$TPM_DEVICE" \
  -v /opt/keylime-agent-docker/config:/etc/keylime:ro \
  -v /opt/keylime-agent-docker/varlib:/var/lib/keylime:rw \
  -v /opt/keylime-agent-docker/logs:/var/log/keylime:rw \
  -v /sys/class/tpm:/sys/class/tpm:ro \
  -v /sys/kernel/security:/sys/kernel/security:ro \
  "$KEYLIME_AGENT_IMAGE" \
  -c 'id; ls -ld /var/lib/keylime /var/lib/keylime/actions /var/log/keylime; touch /var/lib/keylime/.write-test && rm -f /var/lib/keylime/.write-test'
```

临时 shell 检查通过后，再启动 agent：

```bash
docker run -d \
  --name keylime-agent \
  --restart unless-stopped \
  --network host \
  --privileged \
  --entrypoint "$KEYLIME_AGENT_ENTRYPOINT" \
  --device "$TPM_DEVICE:$TPM_DEVICE" \
  -e TCTI="device:$TPM_DEVICE" \
  -v /opt/keylime-agent-docker/config:/etc/keylime:ro \
  -v /opt/keylime-agent-docker/varlib:/var/lib/keylime:rw \
  -v /opt/keylime-agent-docker/logs:/var/log/keylime:rw \
  -v /sys/class/tpm:/sys/class/tpm:ro \
  -v /sys/kernel/security:/sys/kernel/security:ro \
  "$KEYLIME_AGENT_IMAGE"
```

如果临时 shell 能看到 `/var/lib/keylime`，但 agent 仍然报 `MissingActionsDir`，说明 Docker 挂载没问题，更可能是当前运行的 agent 镜像与 `agent.conf` 来源版本不一致。例如：运行的是 `keylime_agent:latest`，但配置文件来自 `keylime_verifier:v7.14.2`。

此时应从实际运行的 agent 镜像重新导出 `agent.conf`：

```bash
docker rm -f keylime-agent 2>/dev/null || true

cp -a /opt/keylime-agent-docker/config/agent.conf \
  /opt/keylime-agent-docker/config/agent.conf.from-v7142.bak

docker run --rm --entrypoint sh \
  -v /opt/keylime-agent-docker/config:/out \
  "$KEYLIME_AGENT_IMAGE" \
  -c 'cp -av /etc/keylime/agent.conf /out/agent.conf'

chmod -R a+rX /opt/keylime-agent-docker/config

sed -i -E \
  -e 's/^uuid = .*/uuid = "csri9"/' \
  -e 's/^ip = .*/ip = "0.0.0.0"/' \
  -e 's/^port = .*/port = 9002/' \
  -e 's/^contact_ip = .*/contact_ip = "172.31.100.9"/' \
  -e 's/^registrar_ip = .*/registrar_ip = "172.31.100.10"/' \
  -e 's/^registrar_port = .*/registrar_port = 8891/' \
  -e 's|^revocation_actions_dir = .*|revocation_actions_dir = "/var/lib/keylime/actions"|' \
  /opt/keylime-agent-docker/config/agent.conf

grep -nE '^(uuid|ip|port|contact_ip|contact_port|registrar_ip|registrar_port|revocation_actions_dir)[[:space:]]*=' \
  /opt/keylime-agent-docker/config/agent.conf
```

再启动 agent。

### 8.3 TPM 设备 Permission denied

如果 agent 日志出现：

```text
Running the service as keylime:tss...
Failed to open specified TCTI device file /dev/tpmrm0: Permission denied
Failed to create TPM context
```

说明 agent 已经启动，但它在容器内降权为 `keylime:tss` 后没有权限访问 TPM 字符设备。`--privileged` 允许容器访问设备，但不一定绕过设备文件本身的 Unix 权限。

先检查：

```bash
docker rm -f keylime-agent 2>/dev/null || true

TPM_DEVICE=/dev/tpmrm0
[ -e "$TPM_DEVICE" ] || TPM_DEVICE=/dev/tpm0

ls -l /dev/tpmrm0 /dev/tpm0 2>/dev/null || true
stat -c '%n mode=%a owner=%U:%G uid=%u gid=%g' /dev/tpmrm0 /dev/tpm0 2>/dev/null || true

docker run --rm \
  --network host \
  --privileged \
  --entrypoint sh \
  --device "$TPM_DEVICE:$TPM_DEVICE" \
  -e TCTI="device:$TPM_DEVICE" \
  -v /opt/keylime-agent-docker/config:/etc/keylime:ro \
  -v /opt/keylime-agent-docker/varlib:/var/lib/keylime:rw \
  -v /opt/keylime-agent-docker/logs:/var/log/keylime:rw \
  "$KEYLIME_AGENT_IMAGE" \
  -c 'id; getent passwd keylime || true; getent group tss || true; ls -l /dev/tpm* 2>/dev/null || true; stat -c "%n mode=%a uid=%u gid=%g" /dev/tpm* 2>/dev/null || true'
```

第一阶段实验可用临时放权：

```bash
[ -e /dev/tpmrm0 ] && chmod a+rw /dev/tpmrm0
[ -e /dev/tpm0 ] && chmod a+rw /dev/tpm0

ls -l /dev/tpmrm0 /dev/tpm0 2>/dev/null || true
```

然后用原来的 `docker run -d ... keylime-agent` 命令重启 agent。

长期做法应改为 udev 规则或让容器内 `keylime:tss` 的 gid 与宿主机 TPM 设备所属 group 对齐，而不是永久 `chmod 666`。

### 8.4 agent 日志无报错后确认 9002

如果日志显示：

```text
Running the service as keylime:tss...
Starting server with API versions: 2.1, 2.2, 2.3, 2.4, 2.5
```

说明 TPM 权限已经过关。此时不要立刻注册 tenant，先等几秒确认容器没有重启，并确认 9002 端口监听：

```bash
sleep 5

docker ps --filter name=keylime-agent \
  --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'

docker inspect -f 'status={{.State.Status}} exit={{.State.ExitCode}} restart={{.RestartCount}}' keylime-agent

docker logs --tail 200 keylime-agent

ss -lntp | grep 9002 || true
```

在 `csri10` 测试连通：

```bash
nc -vz 172.31.100.9 9002
docker logs --tail 100 keylime-registrar
docker logs --tail 100 keylime-verifier
```

### 8.5 agent 运行但没有 9002，且 UUID 变成随机值

如果看到：

```text
Agent Data not found in: /var/lib/keylime/agent_data.json
Agent UUID: beebfa92-...
```

同时 `ss -lntp | grep 9002` 没有输出，说明 agent 进程已经越过 TPM 初始化，但 `latest` agent 镜像实际使用的配置字段可能与 `v7.14.2` 模板不同。此时不要立刻 `keylime_tenant add`，先抓取实际配置和监听端口：

```bash
echo "### host ports"
ss -lntp | egrep '9002|900|keylime' || true

echo "### agent container process"
docker exec keylime-agent sh -c '
  id
  ps -ef
  echo "### key config"
  grep -nEi "uuid|agent_uuid|ip|port|listen|contact|registrar|action|tls|mtls|server" /etc/keylime/agent.conf || true
  echo "### agent data"
  ls -l /var/lib/keylime || true
  cat /var/lib/keylime/agent_data.json 2>/dev/null || true
'

echo "### local curl probes"
curl -k -sS https://127.0.0.1:9002/v2.5/version || true
curl -sS http://127.0.0.1:9002/v2.5/version || true
```

如果需要强制重新生成 agent 身份，先停止 agent 并删除持久化身份文件：

```bash
docker rm -f keylime-agent 2>/dev/null || true
rm -f /opt/keylime-agent-docker/varlib/agent_data.json
```

但不要在没确认正确 UUID 配置字段前反复删除，否则每次都会生成新 UUID。

### 8.6 用合法 UUID 和环境变量覆盖 agent 配置

如果配置里已经是：

```text
uuid = "csri9"
ip = "0.0.0.0"
port = 9002
```

但日志仍显示随机 UUID，且 `9002` 没有监听，先把 agent ID 改为合法 UUID。Rust agent 更倾向把 `uuid` 当作真正 UUID，而不是普通主机名。OpenStack 侧仍然可以把这个 UUID 映射到计算节点 `csri9`。

第一阶段建议固定为：

```text
11111111-1111-4111-8111-000000000009
```

重新启动：

```bash
docker rm -f keylime-agent 2>/dev/null || true

export KEYLIME_AGENT_UUID_FIXED="11111111-1111-4111-8111-000000000009"

# 第一阶段可以删除 agent 生成的临时身份状态，避免继续沿用旧随机 UUID。
find /opt/keylime-agent-docker/varlib -mindepth 1 ! -name actions -exec rm -rf {} +

sed -i -E \
  -e "s/^uuid = .*/uuid = \"${KEYLIME_AGENT_UUID_FIXED}\"/" \
  -e 's/^ip = .*/ip = "0.0.0.0"/' \
  -e 's/^port = .*/port = 9002/' \
  -e 's/^contact_ip = .*/contact_ip = "172.31.100.9"/' \
  -e 's/^contact_port = .*/contact_port = 9002/' \
  -e 's/^registrar_ip = .*/registrar_ip = "172.31.100.10"/' \
  -e 's/^registrar_port = .*/registrar_port = 8891/' \
  -e 's|^revocation_actions_dir = .*|revocation_actions_dir = "/var/lib/keylime/actions"|' \
  /opt/keylime-agent-docker/config/agent.conf

install -d -m 0777 /opt/keylime-agent-docker/varlib/actions
install -d -m 0777 /opt/keylime-agent-docker/logs

TPM_DEVICE=/dev/tpmrm0
[ -e "$TPM_DEVICE" ] || TPM_DEVICE=/dev/tpm0

docker run -d \
  --name keylime-agent \
  --restart unless-stopped \
  --network host \
  --privileged \
  --entrypoint "$KEYLIME_AGENT_ENTRYPOINT" \
  --device "$TPM_DEVICE:$TPM_DEVICE" \
  -e RUST_LOG=debug \
  -e TCTI="device:$TPM_DEVICE" \
  -e KEYLIME_AGENT_UUID="$KEYLIME_AGENT_UUID_FIXED" \
  -e KEYLIME_AGENT_IP="0.0.0.0" \
  -e KEYLIME_AGENT_PORT="9002" \
  -e KEYLIME_AGENT_CONTACT_IP="172.31.100.9" \
  -e KEYLIME_AGENT_CONTACT_PORT="9002" \
  -e KEYLIME_AGENT_REGISTRAR_IP="172.31.100.10" \
  -e KEYLIME_AGENT_REGISTRAR_PORT="8891" \
  -e KEYLIME_AGENT_REVOCATION_ACTIONS_DIR="/var/lib/keylime/actions" \
  -v /opt/keylime-agent-docker/config:/etc/keylime:ro \
  -v /opt/keylime-agent-docker/varlib:/var/lib/keylime:rw \
  -v /opt/keylime-agent-docker/logs:/var/log/keylime:rw \
  -v /sys/class/tpm:/sys/class/tpm:ro \
  -v /sys/kernel/security:/sys/kernel/security:ro \
  "$KEYLIME_AGENT_IMAGE"

sleep 5
docker ps --filter name=keylime-agent --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'
docker logs --tail 240 keylime-agent
ss -lntp | grep 9002 || true
```

### 8.7 正确收敛：不要只修端口，要补齐 CA/TLS 和 EK 策略

如果日志停在：

```text
No EK certificate found in TPM NVRAM
```

并且 `9002` 仍未监听，当前问题已经不是 Docker volume、TPM 权限或 UUID，而是实验路径混用了不同版本组件，并且 agent 没有拿到控制面 CA。

正确收敛原则：

```text
1. control plane 用 v7.14.2：registrar/verifier/tenant
2. agent 不再盲目混用 latest 配置；如果必须用 latest agent，就必须用 latest agent 自己的 agent.conf
3. agent 访问 registrar 的 8891 时必须启用 registrar TLS，并配置 CA
4. 你的 TPM 没有 EK certificate，第一阶段 verifier 要关闭 require_ek_cert
5. 更稳的生产路径：Docker 只跑 registrar/verifier/tenant，csri9 用宿主机 Rust agent
```

在 `csri10` 先确认并复制 CA：

```bash
find /opt/keylime-docker/varlib -path '*cv_ca*' -type f -maxdepth 5 | sort

scp /opt/keylime-docker/varlib/cv_ca/cacert.crt \
  root@172.31.100.9:/opt/keylime-agent-docker/config/cacert.crt
```

在 `csri10` 关闭实验阶段的 EK certificate 强制要求：

```bash
cd /opt/keylime-docker

grep -nEi 'ek.*cert|require.*ek|ekcert' /opt/keylime-docker/config/verifier.conf || true

cp -a /opt/keylime-docker/config/verifier.conf /opt/keylime-docker/config/verifier.conf.before-ekcert.bak

sed -i -E \
  -e 's/^require_ek_cert = .*/require_ek_cert = false/' \
  -e 's/^require_ek_cert[[:space:]]*=.*/require_ek_cert = false/' \
  /opt/keylime-docker/config/verifier.conf

grep -nEi 'ek.*cert|require.*ek|ekcert' /opt/keylime-docker/config/verifier.conf || true

docker compose up -d --force-recreate keylime-verifier
docker logs --tail 100 keylime-verifier
```

在 `csri9` 配置 agent 使用 registrar TLS 和 CA：

```bash
docker rm -f keylime-agent 2>/dev/null || true

ls -l /opt/keylime-agent-docker/config/cacert.crt
chmod a+r /opt/keylime-agent-docker/config/cacert.crt

install -d -m 0777 /opt/keylime-agent-docker/varlib/actions
install -d -m 0755 /opt/keylime-agent-docker/varlib/cv_ca
cp -a /opt/keylime-agent-docker/config/cacert.crt /opt/keylime-agent-docker/varlib/cv_ca/cacert.crt
chmod -R a+rX /opt/keylime-agent-docker/varlib/cv_ca

export KEYLIME_AGENT_UUID_FIXED="11111111-1111-4111-8111-000000000009"

find /opt/keylime-agent-docker/varlib -mindepth 1 \
  ! -name actions \
  ! -name cv_ca \
  -exec rm -rf {} +

sed -i -E \
  -e "s/^uuid = .*/uuid = \"${KEYLIME_AGENT_UUID_FIXED}\"/" \
  -e 's/^ip = .*/ip = "0.0.0.0"/' \
  -e 's/^port = .*/port = 9002/' \
  -e 's/^contact_ip = .*/contact_ip = "172.31.100.9"/' \
  -e 's/^contact_port = .*/contact_port = 9002/' \
  -e 's/^registrar_ip = .*/registrar_ip = "172.31.100.10"/' \
  -e 's/^registrar_port = .*/registrar_port = 8891/' \
  -e 's/^registrar_tls_enabled = .*/registrar_tls_enabled = true/' \
  -e 's|^registrar_tls_ca_cert = .*|registrar_tls_ca_cert = "/etc/keylime/cacert.crt"|' \
  -e 's|^revocation_actions_dir = .*|revocation_actions_dir = "/var/lib/keylime/actions"|' \
  -e 's/^tls_accept_invalid_hostnames = .*/tls_accept_invalid_hostnames = true/' \
  /opt/keylime-agent-docker/config/agent.conf

grep -nE '^(uuid|ip|port|contact_ip|contact_port|registrar_ip|registrar_port|registrar_tls_enabled|registrar_tls_ca_cert|revocation_actions_dir|tls_accept_invalid_hostnames)[[:space:]]*=' \
  /opt/keylime-agent-docker/config/agent.conf
```

然后重新启动 agent：

```bash
TPM_DEVICE=/dev/tpmrm0
[ -e "$TPM_DEVICE" ] || TPM_DEVICE=/dev/tpm0

[ -e /dev/tpmrm0 ] && chmod a+rw /dev/tpmrm0
[ -e /dev/tpm0 ] && chmod a+rw /dev/tpm0

docker run -d \
  --name keylime-agent \
  --restart unless-stopped \
  --network host \
  --privileged \
  --entrypoint "$KEYLIME_AGENT_ENTRYPOINT" \
  --device "$TPM_DEVICE:$TPM_DEVICE" \
  -e RUST_LOG=debug \
  -e TCTI="device:$TPM_DEVICE" \
  -e KEYLIME_AGENT_UUID="$KEYLIME_AGENT_UUID_FIXED" \
  -e KEYLIME_AGENT_IP="0.0.0.0" \
  -e KEYLIME_AGENT_PORT="9002" \
  -e KEYLIME_AGENT_CONTACT_IP="172.31.100.9" \
  -e KEYLIME_AGENT_CONTACT_PORT="9002" \
  -e KEYLIME_AGENT_REGISTRAR_IP="172.31.100.10" \
  -e KEYLIME_AGENT_REGISTRAR_PORT="8891" \
  -e KEYLIME_AGENT_REGISTRAR_TLS_ENABLED="true" \
  -e KEYLIME_AGENT_REGISTRAR_TLS_CA_CERT="/etc/keylime/cacert.crt" \
  -e KEYLIME_AGENT_TLS_ACCEPT_INVALID_HOSTNAMES="true" \
  -e KEYLIME_AGENT_REVOCATION_ACTIONS_DIR="/var/lib/keylime/actions" \
  -v /opt/keylime-agent-docker/config:/etc/keylime:ro \
  -v /opt/keylime-agent-docker/varlib:/var/lib/keylime:rw \
  -v /opt/keylime-agent-docker/logs:/var/log/keylime:rw \
  -v /sys/class/tpm:/sys/class/tpm:ro \
  -v /sys/kernel/security:/sys/kernel/security:ro \
  "$KEYLIME_AGENT_IMAGE"

sleep 8
docker ps --filter name=keylime-agent --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'
docker logs --tail 300 keylime-agent
ss -lntp | grep 9002 || true
```

如果仍然没有 `9002`，停止 Docker agent 路线，改用宿主机 Rust agent。官方文档明确 Docker 部署项是 verifier、registrar、tenant；Rust agent 是单独安装路径，且 Rust agent 配置文件和旧 Python agent 不可互换。

### 8.8 agent 用 HTTP 访问了 registrar TLS 端口

如果日志出现：

```text
Building Registrar client: scheme=http, registrar=172.31.100.10:8891, TLS=false
Requesting registrar API version to http://172.31.100.10:8891/version
Network error
```

这说明 agent 正在把 `8891` 当作 HTTP 端口访问。Keylime registrar 默认：

```text
8890: HTTP port
8891: TLS port
```

第一阶段为了先跑通，推荐让 agent 访问 `8890`，并关闭 agent 到 registrar 的 TLS：

```bash
docker rm -f keylime-agent 2>/dev/null || true

sed -i -E \
  -e 's/^registrar_port = .*/registrar_port = 8890/' \
  -e 's/^registrar_tls_enabled = .*/registrar_tls_enabled = false/' \
  /opt/keylime-agent-docker/config/agent.conf

grep -nE '^(registrar_ip|registrar_port|registrar_tls_enabled)[[:space:]]*=' \
  /opt/keylime-agent-docker/config/agent.conf
```

重启 agent 时同步改环境变量：

```bash
-e KEYLIME_AGENT_REGISTRAR_PORT="8890" \
-e KEYLIME_AGENT_REGISTRAR_TLS_ENABLED="false" \
```

并且不要再用会删除 `cv_ca/cacert.crt` 的 `find ... -exec rm -rf` 清理命令。需要清理身份时，明确删除这些文件即可：

```bash
rm -f /opt/keylime-agent-docker/varlib/agent_data.json
rm -f /opt/keylime-agent-docker/varlib/server-cert.crt
rm -f /opt/keylime-agent-docker/varlib/server-private.pem
rm -f /opt/keylime-agent-docker/varlib/payload-private.pem
rm -rf /opt/keylime-agent-docker/varlib/secure
```

### 8.9 agent 成功注册 registrar 后

如果 agent 日志出现：

```text
Response code: 200 OK
SUCCESS: Agent 11111111-1111-4111-8111-000000000009 registered
```

说明 `csri9` agent 已经成功注册到 `csri10` registrar。`No EK certificate found in TPM NVRAM` 在第一阶段可以先作为警告处理，前提是 verifier 侧关闭了 `require_ek_cert`。

继续检查 agent 端口和 registrar 记录：

```bash
# csri9
ss -lntp | grep 9002 || true

# csri10
export KEYLIME_AGENT_UUID_FIXED="11111111-1111-4111-8111-000000000009"
curl -sS "http://172.31.100.10:8890/v2.5/agents/${KEYLIME_AGENT_UUID_FIXED}" | python3 -m json.tool
```

然后在 `csri10` 使用 tenant 把 agent 加入 verifier：

```bash
cd /opt/keylime-docker

docker compose run --rm keylime-tenant --help | head -n 80
```

根据 help 确认参数名称后执行 add。常见形式是：

```bash
docker compose run --rm keylime-tenant \
  -c add \
  -t 172.31.100.9 \
  -u 11111111-1111-4111-8111-000000000009 \
  -v 172.31.100.10 \
  -vp 8881 \
  -r 172.31.100.10 \
  -rp 8890
```

查询状态：

```bash
docker compose run --rm keylime-tenant \
  -c status \
  -u 11111111-1111-4111-8111-000000000009

docker compose run --rm keylime-tenant -c cvlist
docker compose run --rm keylime-tenant -c reglist
```

### 8.10 tenant 启用 TLS 时不要连 registrar 8890

如果 `keylime-tenant` 输出：

```text
TLS is enabled.
```

就不要给 tenant 使用 registrar 的 HTTP 端口 `8890`。当前端口语义是：

```text
agent -> registrar: 8890, HTTP, registrar_tls_enabled=false
tenant -> registrar: 8891, TLS, tenant config TLS enabled
tenant -> verifier: 8881, TLS
```

因此 tenant 命令应使用：

```bash
-r 172.31.100.10 -rp 8891
```

不是：

```bash
-r 172.31.100.10 -rp 8890
```

示例：

```bash
cd /opt/keylime-docker
export KEYLIME_AGENT_UUID_FIXED="11111111-1111-4111-8111-000000000009"

docker compose run --rm keylime-tenant \
  -c reglist \
  -r 172.31.100.10 \
  -rp 8891

docker compose run --rm keylime-tenant \
  -c add \
  -t 172.31.100.9 \
  -tp 9002 \
  -u "$KEYLIME_AGENT_UUID_FIXED" \
  -v 172.31.100.10 \
  -vp 8881 \
  -r 172.31.100.10 \
  -rp 8891 \
  --agent-api-version 2.5
```

### 8.11 tenant add 失败：agent 不信任 tenant mTLS 证书

如果 `tenant add` 报：

```text
Keylime agent does not recognize mTLS certificate from tenant.
Check if agents trusted_client_ca is configured correctly
```

说明 registrar 已经可用，但 tenant 访问 agent 时使用了客户端证书，而 agent 没有信任签发该客户端证书的 CA。解决方法是在 `csri9` 的 agent 配置里设置：

```text
trusted_client_ca = "/etc/keylime/cacert.crt"
enable_agent_mtls = true
```

先在 `csri10` 确认并复制 CA：

```bash
ls -l /opt/keylime-docker/varlib/cv_ca/cacert.crt \
      /opt/keylime-docker/varlib/cv_ca/client-cert.crt \
      /opt/keylime-docker/varlib/cv_ca/client-private.pem

scp /opt/keylime-docker/varlib/cv_ca/cacert.crt \
  root@172.31.100.9:/opt/keylime-agent-docker/config/cacert.crt
```

在 `csri9` 配置 agent：

```bash
docker rm -f keylime-agent 2>/dev/null || true

chmod a+r /opt/keylime-agent-docker/config/cacert.crt
cp -a /opt/keylime-agent-docker/config/agent.conf \
  /opt/keylime-agent-docker/config/agent.conf.before-mtls-ca.bak

grep -q '^trusted_client_ca[[:space:]]*=' /opt/keylime-agent-docker/config/agent.conf && \
  sed -i -E 's|^trusted_client_ca[[:space:]]*=.*|trusted_client_ca = "/etc/keylime/cacert.crt"|' /opt/keylime-agent-docker/config/agent.conf || \
  printf '\ntrusted_client_ca = "/etc/keylime/cacert.crt"\n' >> /opt/keylime-agent-docker/config/agent.conf

sed -i -E 's/^enable_agent_mtls[[:space:]]*=.*/enable_agent_mtls = true/' \
  /opt/keylime-agent-docker/config/agent.conf

grep -nE '^(enable_agent_mtls|trusted_client_ca|registrar_port|registrar_tls_enabled)[[:space:]]*=' \
  /opt/keylime-agent-docker/config/agent.conf
```

重启 agent 时增加：

```bash
-e KEYLIME_AGENT_TRUSTED_CLIENT_CA="/etc/keylime/cacert.crt" \
-e KEYLIME_AGENT_ENABLE_AGENT_MTLS="true" \
```

并保持：

```bash
-e KEYLIME_AGENT_REGISTRAR_PORT="8890" \
-e KEYLIME_AGENT_REGISTRAR_TLS_ENABLED="false" \
```

### 8.12 tenant add 进入 quote 校验但失败

如果 `tenant add` 已经能拿到 quote，但最后报：

```text
No EK cert provided, require_ek_cert option in config set to True
TPM Quote from Agent ... is invalid for nonce
```

说明已经进入真正 attestation 阶段。这里要处理两个问题：

```text
1. tenant.conf 仍然要求 EK cert，但当前 TPM NVRAM 没有 EK certificate。
2. 前面多次重启/清理后，registrar 里可能保存了旧 AK 记录，需要删除后让 agent 重新注册。
```

在 `csri10` 关闭实验阶段 EK cert 强校验：

```bash
cd /opt/keylime-docker

cp -a /opt/keylime-docker/config/tenant.conf \
  /opt/keylime-docker/config/tenant.conf.before-ekcert.bak
cp -a /opt/keylime-docker/config/verifier.conf \
  /opt/keylime-docker/config/verifier.conf.before-ekcert.bak.$(date +%s)

grep -q '^require_ek_cert[[:space:]]*=' /opt/keylime-docker/config/tenant.conf && \
  sed -i -E 's/^require_ek_cert[[:space:]]*=.*/require_ek_cert = false/' /opt/keylime-docker/config/tenant.conf || \
  printf '\nrequire_ek_cert = false\n' >> /opt/keylime-docker/config/tenant.conf

grep -q '^require_ek_cert[[:space:]]*=' /opt/keylime-docker/config/verifier.conf && \
  sed -i -E 's/^require_ek_cert[[:space:]]*=.*/require_ek_cert = false/' /opt/keylime-docker/config/verifier.conf || \
  printf '\nrequire_ek_cert = false\n' >> /opt/keylime-docker/config/verifier.conf

grep -nE '^require_ek_cert[[:space:]]*=' \
  /opt/keylime-docker/config/tenant.conf \
  /opt/keylime-docker/config/verifier.conf

docker compose up -d --force-recreate keylime-verifier
```

删除旧 verifier/registrar 记录：

```bash
export KEYLIME_AGENT_UUID_FIXED="11111111-1111-4111-8111-000000000009"

docker compose run --rm keylime-tenant \
  -c delete \
  -u "$KEYLIME_AGENT_UUID_FIXED" \
  -v 172.31.100.10 \
  -vp 8881 \
  -r 172.31.100.10 \
  -rp 8891 || true

docker compose run --rm keylime-tenant \
  -c regdelete \
  -u "$KEYLIME_AGENT_UUID_FIXED" \
  -r 172.31.100.10 \
  -rp 8891 || true
```

在 `csri9` 清理 agent 旧 AK/证书状态，然后用固定 UUID 重启 agent：

```bash
docker rm -f keylime-agent 2>/dev/null || true

rm -f /opt/keylime-agent-docker/varlib/agent_data.json
rm -f /opt/keylime-agent-docker/varlib/server-cert.crt
rm -f /opt/keylime-agent-docker/varlib/server-private.pem
rm -f /opt/keylime-agent-docker/varlib/payload-private.pem
rm -rf /opt/keylime-agent-docker/varlib/secure
install -d -m 0777 /opt/keylime-agent-docker/varlib/actions
install -d -m 0777 /opt/keylime-agent-docker/logs
```

重启 agent 后，回到 `csri10` 确认 `reglist` 再执行 `tenant add`。

## 9. 参考依据

- Keylime 安装文档说明 verifier、registrar、tenant 可以使用 Docker 镜像部署，官方镜像位于 Quay.io，并按 commit/release 自动生成：<https://keylime.readthedocs.io/en/latest/installation.html>
- Keylime README 说明配置文件默认位于 `/etc/keylime/*.conf` 或 `/usr/etc/keylime/*.conf`，也支持通过 `keylime_{VERIFIER,REGISTRAR,TENANT,CA,LOGGING}_CONFIG` 指定替代配置：<https://github.com/keylime/keylime>
- Keylime 安装文档说明 verifier 默认会在 `/var/lib/keylime/cv_ca/` 生成 mTLS CA 和证书，因此 Docker 部署时必须持久化 `/var/lib/keylime`：<https://keylime.readthedocs.io/en/latest/installation.html>

