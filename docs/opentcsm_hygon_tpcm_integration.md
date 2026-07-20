# OpenTCSM / Hygon TPCM Integration

This project supports a mixed trust-agent model:

```text
csri8   keylime        TPM 2.0 + Keylime agent
csri9   keylime        TPM 2.0 + Keylime agent
hygon22 opentcsm_tpcm  Hygon TPCM + OpenTCSM
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
TRUST_AGENT_TYPE_MAP=csri8=keylime,csri9=keylime,hygon22=opentcsm_tpcm
OPENTCSM_EVIDENCE_FRESH_SECONDS=300
```

The deploy helper fills these defaults when they are missing.

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
  http://127.0.0.1:8088/api/nodes/hygon22/opentcsm-evidence \
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

## Policy Deployment Boundary

Keylime nodes still receive measured boot and IMA runtime policies through the
existing Keylime tenant path. OpenTCSM/TPCM nodes are marked as
`external_pending` for policy deployment until the OpenTCSM command/API adapter
is implemented. This avoids calling Keylime tenant against a node that no
longer runs Keylime agent.

