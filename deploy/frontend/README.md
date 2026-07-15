# Management UI

This directory contains the static Vue management UI served by the FastAPI
container. It does not require a local Node.js build step.

Current views:

```text
Overview   Trust-plane summary, traits, and latest decisions
Keylime    Keylime-only attestation gate, TPM boot state, IMA runtime state, freshness, remediation hints
Nodes      Compute inventory and Keylime agent mapping
Policies   Database-backed trust policies
Tasks      Worker and API task runs
Audit      Trust-plane audit events
```

The UI calls these backend endpoints:

```text
GET  /api/overview
GET  /api/keylime/check
GET  /api/nodes
GET  /api/policies
GET  /api/tasks
GET  /api/audit
POST /api/tasks/sync
```

For the Keylime-first phase keep OpenStack enforcement disabled and use the
Keylime page as the operator view. It should match:

```bash
deploy/scripts/keylime-only-attestation-check.sh --strict
curl -fsS http://127.0.0.1:8088/api/keylime/check
```

After changing frontend files, rebuild and restart the API container:

```bash
docker compose \
  --env-file /etc/keylime-openstack/keylime-openstack.env \
  -f deploy/compose/keylime-openstack-control-plane.yml \
  build api

docker compose \
  --env-file /etc/keylime-openstack/keylime-openstack.env \
  -f deploy/compose/keylime-openstack-control-plane.yml \
  up -d api
```
