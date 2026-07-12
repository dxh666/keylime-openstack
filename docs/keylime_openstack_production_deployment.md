# Production FastAPI/PostgreSQL deployment

Date: 2026-07-11

This deployment path is for the new FastAPI + worker + PostgreSQL trust plane.
It is separate from the older shell/systemd timer based lab control plane.

## Target host

Install this control plane on the OpenStack controller that can access:

```text
/etc/kolla/clouds.yaml
/etc/kolla/admin-openrc.sh
/opt/keylime-docker
/var/run/docker.sock
```

For the current lab, that host is:

```text
csri10
```

The compute-node inventory seeded by default is:

```text
csri8
csri9
hygon22
```

## Install source

Install the repository under `/opt/keylime-openstack`:

```bash
git clone -b codex/production-fastapi-postgres-trust-plane \
  https://github.com/dxh666/keylime-openstack.git \
  /opt/keylime-openstack
```

If the branch has not been pushed yet, copy the staged repository contents to
`/opt/keylime-openstack` by the site's normal release process.

## Prepare configuration

```bash
install -d -m 0755 /etc/keylime-openstack
install -d -m 0755 /var/lib/keylime-openstack/postgres
install -d -m 0755 /tmp/keylime-openstack

cp /opt/keylime-openstack/deploy/env/keylime-openstack.env.example \
  /etc/keylime-openstack/keylime-openstack.env
chmod 0600 /etc/keylime-openstack/keylime-openstack.env
```

Edit `/etc/keylime-openstack/keylime-openstack.env` before starting services.
At minimum, set:

```text
POSTGRES_PASSWORD
DATABASE_URL
ADMIN_TOKEN
KEYLIME_VERIFIER_URL
KEYLIME_REGISTRAR_URL
KEYLIME_TLS_CA_CERT
KEYLIME_TLS_CLIENT_CERT
KEYLIME_TLS_CLIENT_KEY
KEYLIME_DOCKER_DIR
KEYLIME_TENANT_SERVICE
OPENSTACK_CLOUDS_YAML
OPENSTACK_OPENRC
```

For the current csri10 Docker Keylime deployment, tenant output reports
`TLS is enabled`; use the TLS ports as HTTPS endpoints:

```text
KEYLIME_VERIFIER_URL=https://172.31.100.10:8881
KEYLIME_REGISTRAR_URL=https://172.31.100.10:8891
KEYLIME_TLS_VERIFY=true
KEYLIME_TLS_VERIFY_HOSTNAME=false
KEYLIME_TLS_CA_CERT=/opt/keylime-docker/varlib/cv_ca/cacert.crt
KEYLIME_TLS_CLIENT_CERT=/opt/keylime-docker/varlib/cv_ca/client-cert.crt
KEYLIME_TLS_CLIENT_KEY=/opt/keylime-docker/varlib/cv_ca/client-private.pem
```

`KEYLIME_TLS_VERIFY_HOSTNAME=false` is used because the current generated
Keylime verifier certificate does not contain `172.31.100.10` in its SAN. This
still validates the CA chain. In a hardened production deployment, reissue the
verifier certificate with the management IP or DNS name in SAN and set
`KEYLIME_TLS_VERIFY_HOSTNAME=true`.

Keep these defaults for the first dry run:

```text
OPENSTACK_ENFORCEMENT_ENABLED=false
DEFAULT_CONTROLLER_HOST=csri10
DEFAULT_COMPUTE_HOSTS=csri8,csri9,hygon22
```

Only set `OPENSTACK_ENFORCEMENT_ENABLED=true` after API access, Keylime
evidence collection, and Placement trait changes have been verified.

## Restricted-network build notes

If the controller cannot reach Docker Hub, the build can fail before project
code is compiled:

```text
failed to resolve source metadata for docker.io/library/python:3.12-slim
```

Use one of these approaches before building:

```bash
# Preferred when an internal registry exists.
docker pull <internal-registry>/library/python:3.12-slim
docker pull <internal-registry>/library/postgres:16
```

Then set:

```text
PYTHON_BASE_IMAGE=<internal-registry>/library/python:3.12-slim
POSTGRES_IMAGE=<internal-registry>/library/postgres:16
```

Or import the base image from another machine that can access Docker Hub:

```bash
# On the networked machine:
docker pull python:3.12-slim
docker pull postgres:16
docker save python:3.12-slim -o python-3.12-slim.tar
docker save postgres:16 -o postgres-16.tar

# Copy the tar files to csri10, then on csri10:
docker load -i python-3.12-slim.tar
docker load -i postgres-16.tar
```

If PyPI access is also restricted, set a site-approved Python package mirror:

```text
PIP_INDEX_URL=https://<site-pypi-mirror>/simple
PIP_TRUSTED_HOST=<site-pypi-mirror-hostname>
```

The current Kolla controller can reuse the local toolbox image as the Python
base image:

```text
PYTHON_BASE_IMAGE=172.31.100.10:4000/openstack.kolla/kolla-toolbox:2026.1-ubuntu-noble
PYTHON_BIN=python3
DOCKER_BUILD_NETWORK=host
```

`kolla-toolbox` already provides Python 3.12, pip, and openstacksdk, but it
does not provide FastAPI, SQLAlchemy, or Pydantic. The build still needs access
to a Python package source for those dependencies unless an offline wheelhouse
is added later.

Some Kolla images default to a non-root user. The project image switches back
to root during build so Python package metadata can be written under `/app`.

The image no longer installs operating-system packages by default. If later
operations require in-container shell tools, set:

```text
INSTALL_OS_TOOLS=true
```

That option requires the container build environment to reach the Debian apt
repositories or a configured apt mirror.

## Build and initialize

```bash
cd /opt/keylime-openstack

docker compose \
  --env-file /etc/keylime-openstack/keylime-openstack.env \
  -f deploy/compose/keylime-openstack-control-plane.yml \
  build

docker compose \
  --env-file /etc/keylime-openstack/keylime-openstack.env \
  -f deploy/compose/keylime-openstack-control-plane.yml \
  up -d postgres

docker compose \
  --env-file /etc/keylime-openstack/keylime-openstack.env \
  -f deploy/compose/keylime-openstack-control-plane.yml \
  run --rm api alembic upgrade head

docker compose \
  --env-file /etc/keylime-openstack/keylime-openstack.env \
  -f deploy/compose/keylime-openstack-control-plane.yml \
  run --rm api keylime-openstackctl bootstrap
```

## Start API and worker

```bash
docker compose \
  --env-file /etc/keylime-openstack/keylime-openstack.env \
  -f deploy/compose/keylime-openstack-control-plane.yml \
  up -d api worker
```

The management UI is served by the API process:

```text
http://<controller-ip>:8088/
```

For the current lab controller:

```text
http://172.31.100.10:8088/
```

## Health checks

```bash
curl -fsS http://127.0.0.1:8088/api/health
curl -fsS http://127.0.0.1:8088/api/overview
```

Run one manual sync:

```bash
curl -fsS \
  -H "X-Admin-Token: <ADMIN_TOKEN>" \
  -X POST \
  http://127.0.0.1:8088/api/tasks/sync
```

Or use the helper container:

```bash
/opt/keylime-openstack/deploy/scripts/keylime-openstackctl sync
```

## Host Integrity Probe

Keylime verifier status does not expose enough EVM/keyring detail in the
current deployment. Run the host-side probe on each compute node and report the
result back to the control-plane API:

If the node still reports `evm_status=missing` or `evm_status=fail`, complete
the staged node-side enablement first:

```text
docs/keylime_openstack_node_evm_appraisal_enablement.md
```

```bash
TOKEN=$(grep '^ADMIN_TOKEN=' /etc/keylime-openstack/keylime-openstack.env | cut -d= -f2-)

for entry in csri8=172.31.100.8 csri9=172.31.100.9 hygon22=172.31.100.22; do
  host="${entry%%=*}"
  ip="${entry#*=}"

  scp /opt/keylime-openstack/deploy/scripts/keylime-host-integrity-probe.py \
    root@"$ip":/usr/local/sbin/keylime-host-integrity-probe

  ssh root@"$ip" chmod 0755 /usr/local/sbin/keylime-host-integrity-probe

  ssh root@"$ip" \
    KEYLIME_OPENSTACK_API_URL=http://172.31.100.10:8088 \
    KEYLIME_OPENSTACK_ADMIN_TOKEN="$TOKEN" \
    /usr/local/sbin/keylime-host-integrity-probe --hostname "$host"
done
```

The API endpoint is:

```text
POST /api/nodes/{hostname}/host-integrity
```

The backend writes this report as `evm` evidence. It returns `pass` only when
IMA appraisal, `.ima` and `.evm` keyrings, EVM initialization, and signed
`security.ima` / `security.evm` xattrs are present. Measurement-only hosts stay
`missing`; hosts with `ima-sig` measurements but empty keyrings are marked
`fail`.

## Optional systemd management

After the first migration and bootstrap succeed, install the systemd wrapper:

```bash
cp /opt/keylime-openstack/deploy/systemd/keylime-openstack-control-plane.service \
  /etc/systemd/system/keylime-openstack-control-plane.service

systemctl daemon-reload
systemctl enable --now keylime-openstack-control-plane.service
```

Operational commands:

```bash
systemctl status keylime-openstack-control-plane.service
journalctl -u keylime-openstack-control-plane.service -n 100 --no-pager

docker logs --tail=100 keylime_openstack_api
docker logs --tail=100 keylime_openstack_worker
docker logs --tail=100 keylime_openstack_postgres
```

## First production gate

Before enabling OpenStack enforcement, verify:

```text
1. /api/nodes shows csri8, csri9, and hygon22.
2. /api/tasks shows successful sync task execution.
3. /api/audit records Keylime and OpenStack adapter activity.
4. Placement trait changes are correct in dry-run logs.
5. Keylime evidence for TPM boot trust, IMA runtime trust, and EVM/keyring trust
   is present in PostgreSQL.
```

Then switch:

```text
OPENSTACK_ENFORCEMENT_ENABLED=true
```

Restart:

```bash
docker compose \
  --env-file /etc/keylime-openstack/keylime-openstack.env \
  -f /opt/keylime-openstack/deploy/compose/keylime-openstack-control-plane.yml \
  up -d api worker
```
