# 常用验证命令

## 查看 Keylime freshness 判定

```bash
cat /var/log/keylime-openstack-sync-decision.json | python3 -m json.tool
cat /var/log/keylime-openstack-sync-last.log
```

## 手动触发同步

```bash
/opt/keylime-openstack-sync/keylime-placement-sync-locked.sh
```

## 查看 csri9 是否有可信 trait

```bash
source /etc/kolla/admin-openrc.sh
export OS_PLACEMENT_API_VERSION=1.17
export RP_CSRI9="$(openstack resource provider list --name csri9 -f value -c uuid)"

openstack resource provider trait list "$RP_CSRI9" | grep CUSTOM_KEYLIME_ATTESTED
```

## 查看 private trusted flavor

```bash
source /etc/kolla/admin-openrc.sh

openstack flavor show trusted.keylime.private.small \
  -c name \
  -c os-flavor-access:is_public \
  -c access_project_ids \
  -c properties \
  -f yaml
```

## 跨 project 找 VM

```bash
openstack server list --all-projects --name trusted-private-allow-a
```

当前 openstackclient 支持 `server list --all-projects`，但不支持 `server show --all-projects`。因此先用 list 找 VM ID，再直接：

```bash
openstack server show <server-id> \
  -c status \
  -c OS-EXT-SRV-ATTR:host \
  -c fault \
  -f yaml
```


