# OpenTCSM / Hygon TPCM Integration

This project supports a mixed trust-agent model:

```text
csri8   keylime        TPM 2.0 + Keylime agent
csri9   keylime        TPM 2.0 + Keylime agent
hygon22 unmanaged      Hygon TPCM, no trusted agent connected
hygon23 opentcsm_tpcm  Hygon TPCM + OpenTCSM
```

The management UI and trust decision model stay the same. Each compute node
still exposes:

- trust-agent management state
- trusted root
- trusted boot status
- runtime/dynamic measurement status
- final trusted state
- policy binding/deployment state

The collection backend is different. Keylime nodes are read from the Keylime
verifier API. OpenTCSM/TPCM nodes are read from evidence reported into the
trust-plane API.

## Environment

Set the node trust-agent map in `/etc/keylime-openstack/keylime-openstack.env`:

```text
DEFAULT_COMPUTE_HOSTS=csri8,csri9,hygon22,hygon23
TRUST_AGENT_TYPE_MAP=csri8=keylime,csri9=keylime,hygon22=unmanaged,hygon23=opentcsm_tpcm
OPENTCSM_EVIDENCE_FRESH_SECONDS=300
OPENTCSM_ACTIVE_COLLECT_ENABLED=true
OPENTCSM_COLLECT_INTERVAL_SECONDS=60
OPENTCSM_KEY_DIR=/etc/keylime-openstack/opentcsm/keys
OPENTCSM_DEFAULT_DYNAMIC_AUTH_REF=dmeasure-uid
```

OpenTCSM/TPCM nodes must not be added to the Keylime verifier inventory:

```text
KEYLIME_AGENT_HOSTS=csri8,csri9
KEYLIME_AGENT_IP_MAP=csri8=172.31.100.8,csri9=172.31.100.9
KEYLIME_AGENT_UUID_MAP=csri8=22222222-2222-4222-8222-000000000008,csri9=11111111-1111-4111-8111-000000000009
```

The deploy helper fills these defaults when they are missing.

## Structured Registration

The product-level registration record is `trusted_node_profile`. Existing
`TRUST_AGENT_TYPE_MAP` and node facts are only a migration bridge. New
automation should upsert the profile with:

```text
PUT /api/nodes/{hostname}/trusted-node-profile
```

For TPCM managed nodes, set `trust_managed=true`,
`trusted_root_type=tpcm`, and `adapter_type=opentcsm`. For TPM managed nodes,
set `trusted_root_type=tpm` and `adapter_type=keylime`. The management UI uses
the profile capabilities, not hostnames, to decide whether a node can appear in
TPCM dynamic-measurement targets.

## Evidence Ingestion

OpenTCSM integration reports current TPCM evidence to:

```text
POST /api/nodes/{hostname}/opentcsm-evidence
```

Example:

```bash
TOKEN=$(grep '^ADMIN_TOKEN=' /etc/keylime-openstack/keylime-openstack.env | cut -d= -f2-)

curl -fsS -X POST \
  -H "X-Admin-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  http://127.0.0.1:8088/api/nodes/hygon23/opentcsm-evidence \
  -d '{
    "trust_root": "Hygon TPCM",
    "agent_name": "OpenTCSM",
    "boot_status": "pass",
    "dynamic_measurement_status": "pass",
    "summary": "OpenTCSM TPCM report verified"
  }'
```

The API writes provider `opentcsm` evidence records:

```text
boot     trusted boot / static chain status
runtime  dynamic measurement status
evm      optional, only when reported
```

The trust decision engine then evaluates these evidence records with the same
global capability switches used by Keylime nodes.

## Active Collection

The management system can actively collect OpenTCSM/Hygon TPCM evidence over
the configured Ansible SSH channel:

```bash
cd /opt/keylime-openstack
bash deploy/scripts/opentcsm-evidence-collect.sh hygon23
```

This runs the OpenTCSM utilities on the node, captures the trust report,
global control policy, boot measurement records, TPCM ID, and TSB log tail, and
stores normalized `boot` and `runtime` evidence records in the management
database.

The API also exposes an operator refresh endpoint:

```text
POST /api/nodes/{hostname}/opentcsm-collect
```

This endpoint actively collects the current OpenTCSM trusted report and does
not require an admin token because it is treated as a status refresh operation.
The worker performs the same active collection periodically for
`opentcsm_tpcm` nodes when `OPENTCSM_ACTIVE_COLLECT_ENABLED=true`.

## Policy Deployment Boundary

Keylime nodes still receive measured boot and IMA runtime policies through the
existing Keylime tenant path. OpenTCSM/TPCM nodes are not sent to the Keylime
tenant. OpenTCSM policy types that do not yet have a native adapter are marked
as `external_pending`; the `tpcm_dynamic_measurement` policy type is handled by
the OpenTCSM evidence collector described below.

## TPCM Dynamic Measurement Policy

The management plane supports a productized `tpcm_dynamic_measurement` policy
type for OpenTCSM/TPCM nodes. This policy type is intentionally scoped to
OpenTCSM nodes and is not offered for Keylime-agent nodes.

Policy deployment is a write-and-verify workflow:

1. The backend loads an OpenTCSM authorization material file from
   `OPENTCSM_KEY_DIR`.
2. The worker runs `global_control_policy` to set the dynamic measurement
   switch according to the policy.
3. The worker runs `update_dmeasure_policy` for the selected environment
   measurement objects: `kernel_section`, `syscall_table`, and `idt_table`.
4. The worker collects the OpenTCSM trust report and `get_dmeasure_policy`
   output after the update.
5. The binding is marked `applied` only when the OpenTCSM commands succeeded,
   selected objects are present with the configured interval, and the trust
   report passes the policy's post-deployment checks.

### Authorization Material

Private keys are not stored in PostgreSQL or exposed to the frontend. Each key
is a root-owned JSON file mounted read-only into the API and worker containers.

Example lab file:

```bash
install -d -m 0700 /etc/keylime-openstack/opentcsm/keys
cat >/etc/keylime-openstack/opentcsm/keys/dmeasure-uid.json <<'JSON'
{
  "uid": "dmeasure-uid",
  "auth_type": 1,
  "private_key": "REPLACE_WITH_DMEASURE_PRIVATE_KEY_HEX",
  "public_key": "REPLACE_WITH_DMEASURE_PUBLIC_KEY_HEX"
}
JSON
chown root:root /etc/keylime-openstack/opentcsm/keys/dmeasure-uid.json
chmod 0600 /etc/keylime-openstack/opentcsm/keys/dmeasure-uid.json
```

The corresponding `uid` must already be registered and authorized in OpenTCSM.
For a lab, the OpenTCSM `tcs-test.sh` script demonstrates the root certificate
and dynamic-measurement role grant flow. In production, generate and protect a
dedicated dynamic-measurement management certificate instead of using lab keys.

### Managed Fields

The first managed dynamic-measurement scope covers environment measurement:

- dynamic measurement switch, through global policy `dynamic_measure_on`
- selected objects: `kernel_section`, `syscall_table`, `idt_table`
- object interval in milliseconds
- optional deletion of unmanaged environment objects

Process dynamic measurement is exposed by OpenTCSM
`get_dmeasure_process_policy` / `update_dmeasure_process_policy`, but it is not
enabled in the UI until the process object-number model is mapped into product
terms.
