# OpenStack Compute Trust Capability Management Platform - Overall Design

Date: 2026-08-08

This document defines the product-level design for the first phase of this project. The first phase is not a Keylime console and not an OpenTCSM console. It is an OpenStack compute-node trust capability management platform.

## Product Goal

The purpose of managing compute-node trust capability is to turn heterogeneous hardware trust roots into stable, auditable, and consumable OpenStack resource capability.

The platform must answer four operational questions:

1. Is this host an OpenStack compute node?
2. Is this compute node managed by a trusted agent?
3. Which local trust root backs that agent: TPM, TPCM, or unknown?
4. Which trust capabilities are available, configured, verified, and safe for OpenStack to consume?

The downstream OpenStack consumption scenarios are scheduling admission for trusted workloads, trusted resource-pool construction, host quarantine or recovery after trust drift, audit of trust policy changes, workload risk marking, and future policy-aware placement or tenant authorization.

In the first phase, OpenStack enforcement may remain disabled or conservative. The management plane must still produce trustworthy evidence, decisions, policy bindings, and audit records because these become the contract consumed by later phases.

## Product Model

The stable product model is:

```text
OpenStack node inventory
  -> compute node / non-compute node
  -> trusted-agent managed / unmanaged
  -> trusted root: TPM / TPCM / unknown
  -> capability schema
  -> policy binding
  -> evidence
  -> trust decision
  -> OpenStack-consumable output
```

Keylime and OpenTCSM are internal adapters behind this model. They must not be the main product concept. User-facing labels should prefer "trusted agent", "trusted root", "trusted boot", "runtime integrity", "environment dynamic measurement", and "license".

Adapter names may appear only in operator diagnostics, deployment configuration, audit/debug details, and API fields intended for integration or automation.

## Trusted Agents

The platform supports multiple trusted-agent adapters.

| Product Concept | TPM Path | TPCM Path |
| --- | --- | --- |
| Trusted root | TPM 2.0 | Hygon TPCM |
| Internal adapter | Keylime | OpenTCSM TPCM adapter |
| Transport | Keylime registrar/verifier/tenant APIs and tools | SSH/Ansible first, formal helper next |
| Main evidence | TPM quote, measured boot, IMA | TPCM trust report, boot measurement, dynamic measurement, license |
| Product label | Trusted agent | Trusted agent |

The current TPM implementation may keep its Keylime-specific service path. The TPCM implementation must not be forced through Keylime. Both paths converge at the product model: profile, capability, evidence, policy, binding, task, audit, and trust decision.

## Core Objects

### Compute Node

The node object represents an OpenStack host known to the management plane. The product must first distinguish compute nodes from controller, storage, or other nodes. Only enabled compute nodes can enter trusted resource pools.

Required product fields are hostname, OpenStack compute name, management IP, role, enabled state, and OpenStack service status/state.

### Trusted Node Profile

The profile is the product-level registration record. It is the right place for trusted-root type, adapter type, endpoint, identity, capability summary, and safe authorization references.

Current code already has the right direction in `backend/keylime_openstack/models.py` through `TrustedNodeProfile`:

```text
trust_managed
trusted_root_type
adapter_type
agent_endpoint
agent_identity
capabilities
registration_status
last_evidence_summary
```

The profile may store safe references such as:

```json
{
  "tpcm_id": "...",
  "auth_refs": {
    "boot": "bmeasure-hygon23",
    "dynamic": "dmeasure-hygon23"
  }
}
```

It must not store raw private keys.

### Capability

Capabilities should be treated as a versioned schema, not as a permanent fixed list. The current booleans are acceptable for the first phase, but the design must allow later expansion.

Current first-phase capability names are `trusted_boot`, `ima_runtime`, `tpcm_dynamic_measurement`, and `evm`. EVM remains disabled in this phase.

A later capability record should be able to express supported objects, apply mode, schema version, provider, and verification state. The product layer should not assume that TPCM will only ever expose boot measurement, dynamic measurement, authorization, license, and policy apply.

### Policy and Binding

A trust policy is a versioned product object. It describes a desired trust state, not a one-off command. A policy binding attaches that policy version to a target node and records whether it is applied, failed, awaiting reboot, or waiting for a future adapter.

Bindings may store product detail such as application status, external policy name, rendered policy hash, last error, safe `auth_ref`, UID, public key fingerprint, and observed evidence hash. They must not store private key material or full command output containing secrets.

### Evidence and Decision

Evidence is normalized, stored, and time-bounded. First-phase evidence providers are `keylime` and `opentcsm`. Future providers such as DIM should enter as additional evidence providers, not as a separate product model.

The trust decision combines compute-node state, trusted-agent management state, trusted-root type, enabled capabilities, fresh evidence, policy binding state, and optional OpenStack service state. OpenStack consumes product decisions, not raw Keylime or OpenTCSM output.

## Adapter Boundary

Adapters own protocol and vendor-specific execution. The product layer owns state, policy semantics, audit, and OpenStack consumption.

```text
Product services
  -> TrustAgentAdapter interface
     -> KeylimeTpmAdapter
     -> OpenTcsmTpcmAdapter
  -> normalized result objects
  -> policy binding / task / audit / decision
```

The adapter interface should eventually cover capability collection, evidence collection, authorization health, license reading, policy apply, policy readback, and policy verification. The first formal adapter work should prioritize the OpenTCSM TPCM path because that is where the current code still relies most heavily on test tools and shell orchestration.

## Security Rules

Authorization material handling is a product boundary.

Rules:

- database stores `auth_ref`, not private keys;
- private keys live under `/etc/keylime-openstack/opentcsm/keys/*.json`;
- task, audit, API, and frontend output may show `auth_ref`, UID, and public key fingerprint only;
- command lines must not contain raw private keys in product logs;
- helper stdout/stderr must never print private keys;
- lab test keys from OpenTCSM examples must not be used in production.

The current OpenTCSM test command path is acceptable only as a temporary bridge because some test tools print sensitive hexdumps to stdout. The formal helper must remove that risk.

## OpenStack Consumption Boundary

OpenStack consumes product decisions, not raw adapter output.

Recommended consumption order:

1. Read-only trust state and evidence freshness.
2. Dry-run Placement trait decisions.
3. Placement trait writes with audit.
4. Trusted flavors and scheduling policy.
5. Host quarantine and VM risk marking.
6. Tenant/project-level trust consumption.

The first-phase platform must avoid manually labeling nodes as trusted. A node is trusted only when the active product policy and fresh trusted-agent evidence agree.

## First-Phase Non-Goals

These are intentionally outside this phase:

- EVM enablement;
- broad database model split or migration;
- rewriting the frontend;
- replacing the TPM/Keylime path;
- hard-coding `hygon22`, `hygon23`, `csri8`, or `csri9` into product logic;
- making OpenTCSM a user-facing product concept;
- using OpenStack traits to hide incomplete trust evidence;
- continuing to build product behavior from one-off shell tests.
