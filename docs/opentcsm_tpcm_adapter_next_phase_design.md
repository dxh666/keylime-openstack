# OpenTCSM TPCM Adapter - Next Phase Design

Date: 2026-08-08

This document defines the next development phase. The purpose is to converge the TPCM path into a real product adapter, starting with dynamic measurement.

## Phase Goal

Build the first formal OpenTCSM TPCM adapter path so that TPCM dynamic measurement policy is applied as desired product state, not as a sequence of lab CLI experiments.

The phase must fix the class of issue observed on `hygon23`, where management deployment failed and left only one dynamic object. The fix must apply to all TPCM nodes and must not hard-code a node name.

## Design Principle

The project should not push TPCM through Keylime. Keylime remains the TPM adapter. OpenTCSM becomes the TPCM adapter. Both adapters feed the same product model:

```text
TrustedNodeProfile
  -> capabilities
  -> evidence
  -> policy binding
  -> task
  -> audit
  -> trust decision
```

The implementation should be small and incremental. Do not rewrite the whole policy engine or split all models in this phase.

## Scope

### In Scope

- Add a formal adapter package structure for trusted-agent adapters.
- Add OpenTCSM TPCM adapter code for dynamic measurement.
- Add a node-side OpenTCSM helper design and first implementation.
- Use helper JSON input/output instead of test CLI stdout parsing for dynamic measurement policy apply.
- Add capability, license, and auth-health helper commands if they are small enough to support the dynamic path.
- Keep authorization material out of the database.
- Keep task and audit output productized and secret-free.
- Preserve the global fallback auth references.
- Preserve existing TPM/Keylime behavior.

### Out of Scope

- EVM.
- Large database model split.
- Large frontend redesign.
- Replacing the Keylime TPM path.
- Migrating TPCM trusted boot hardware apply unless the helper foundation makes a small, low-risk migration obvious.
- Migrating TPCM global policy in the first helper patch.
- Supporting both TCS and TCF implementations at the same time.
- Per-node hard-coded fixes for `hygon22` or `hygon23`.

## Proposed Code Structure

New code should use an explicit adapter structure:

```text
backend/keylime_openstack/adapters/
  __init__.py
  base.py
  opentcsm_tpcm/
    __init__.py
    adapter.py
    auth.py
    capabilities.py
    dynamic_measurement.py
    helper_client.py
    license.py

node_helpers/opentcsm_tpcm/
  klos-opentcsm-helper.c
  README.md
```

Existing services remain in place initially. The first integration point should be narrow: route only `tpcm_dynamic_measurement` apply through the new adapter after the helper is installed.

Do not move `backend/keylime_openstack/models.py` in this phase. New code can be structured correctly without forcing a migration of old code.

## Adapter Interface

The base adapter should be minimal:

```python
class TrustAgentAdapter:
    adapter_type: str
    trusted_root_type: str

    def collect_capabilities(self, node): ...
    def collect_evidence(self, node): ...
    def check_auth_health(self, node, purpose: str, auth_ref: str): ...
    def read_license(self, node): ...
    def apply_policy(self, policy, node): ...
```

Only methods needed by the first implementation have to be concrete. Avoid building a large framework before it is used.

## Node-Side Helper

### Why a Helper Is Required

The current dynamic playbook uses `get_dmeasure_policy`, `update_dmeasure_policy`, and `get_global_control_policy`. These are OpenTCSM test tools. They are useful for lab validation but weak as a product interface because operation semantics depend on CLI options, command stdout is not a stable API, some tools print private/public key hexdumps, errors need to be reverse-engineered from stdout/stderr, and partial updates can leave node state inconsistent.

The helper should call OpenTCSM TCS APIs directly and return stable JSON.

### First Implementation Layer

Use the TCS API first.

Reason:

- current node validation already uses the TCS tool path under `/usr/local/opentcsm/tcsm/user/test/tcs`;
- the tested commands map directly to TCS APIs;
- it is less risky than introducing TCF and TCS at the same time.

Do not implement TCF and TCS in parallel in this phase. A later phase can add a TCF backend if it gives better long-term API stability.

### Helper Commands

The helper should support these JSON commands:

```text
capabilities
license
auth-health
get-dmeasure-policy
apply-dmeasure-policy
```

Optional later commands:

```text
get-boot-measurement
apply-boot-references
get-global-policy
apply-global-policy
```

### Helper Invocation

The backend should invoke the helper over the existing Ansible/SSH transport at first. Ansible remains transport, not business logic.

Desired shape:

```bash
klos-opentcsm-helper --json
```

Input is JSON on stdin. Output is JSON on stdout. Stderr is diagnostic only and must not contain keys.

## Dynamic Measurement Contract

### Input

```json
{
  "operation": "apply-dmeasure-policy",
  "schema_version": "1",
  "auth": {
    "ref": "dmeasure-hygon23",
    "uid": "dmeasure-uid",
    "auth_type": 1,
    "private_key": "...",
    "public_key": "..."
  },
  "desired": {
    "enabled": true,
    "objects": [
      {"name": "kernel_section", "interval_milli": 60000},
      {"name": "syscall_table", "interval_milli": 60000},
      {"name": "idt_table", "interval_milli": 60000}
    ],
    "delete_unmanaged_objects": true
  }
}
```

Private keys may enter the helper process through stdin only. They must not be printed, logged, stored in files, or returned in JSON.

### Output

```json
{
  "ok": true,
  "operation": "apply-dmeasure-policy",
  "schema_version": "1",
  "adapter": "opentcsm_tpcm",
  "backend": "tcs",
  "auth": {
    "ref": "dmeasure-hygon23",
    "uid": "dmeasure-uid",
    "public_key_sha256": "..."
  },
  "observed_before": [
    {"name": "idt_table", "interval_milli": 60000}
  ],
  "observed_after": [
    {"name": "kernel_section", "interval_milli": 60000},
    {"name": "syscall_table", "interval_milli": 60000},
    {"name": "idt_table", "interval_milli": 60000}
  ],
  "applied_at": "2026-08-08T00:00:00Z",
  "native_rc": 0,
  "native_error": ""
}
```

### Apply Semantics

The helper should treat the requested dynamic policy as the complete desired state for managed kernel dynamic objects.

Rules:

- build the complete `dmeasure_policy_item[]` desired list;
- use a set/replace operation where the TCS API supports it;
- if zero objects are desired, intentionally clear the managed set;
- read back the node state after apply;
- return `ok=true` only when desired and observed state match;
- do not apply per-object fallback sequences that can leave partial state.

This is the product-level fix for the current `hygon23` partial-object problem.

## Capability Output

Capability output should be versioned and extensible:

```json
{
  "schema_version": "1",
  "adapter": "opentcsm_tpcm",
  "backend": "tcs",
  "trusted_root_type": "tpcm",
  "capabilities": {
    "trusted_boot": {"supported": true},
    "tpcm_dynamic_measurement": {
      "supported": true,
      "objects": ["kernel_section", "syscall_table", "idt_table"],
      "apply_mode": "atomic_set"
    },
    "license": {"supported": true},
    "authorization.external_sm2": {"supported": true}
  }
}
```

The backend can map this to the current boolean `capabilities` field while preserving the raw versioned capability payload in `agent_identity` or evidence details later.

## License Output

License output should be normalized:

```json
{
  "status": "valid",
  "available": true,
  "expires_at": "2027-12-31",
  "summary": "license valid",
  "source": "opentcsm",
  "native_rc": 0
}
```

Allowed status values are `valid`, `expired`, `missing`, `unavailable`, and `unknown`. The frontend should continue to show a concise status in compute-node detail. Do not add raw license fields unless they are needed for operators.

## Auth-Health Output

Auth-health checks both management-side configuration and node-side registration.

Backend-side checks:

- `auth_ref` is selected from profile, policy content, or default fallback;
- key JSON exists in `OPENTCSM_KEY_DIR`;
- JSON contains UID, private key, public key, and valid hex lengths.

Helper-side checks:

- UID is registered on the node;
- registered public key matches the configured public key;
- the target purpose is usable by the TPCM policy class when detectable.

Output:

```json
{
  "auth_ref": "dmeasure-hygon23",
  "uid": "dmeasure-uid",
  "public_key_sha256": "...",
  "status": "ready",
  "reason": ""
}
```

Allowed status values are `ready`, `missing`, `invalid`, `not_registered`, `public_key_mismatch`, `rejected`, and `unknown`.

## Error Model

The adapter should return product error codes independent of raw command text:

```text
TPCM_HELPER_NOT_INSTALLED
TPCM_HELPER_PROTOCOL_ERROR
TPCM_AUTH_MISSING
TPCM_AUTH_INVALID
TPCM_AUTH_NOT_REGISTERED
TPCM_AUTH_REJECTED
TPCM_DYNAMIC_APPLY_FAILED
TPCM_DYNAMIC_VERIFY_FAILED
TPCM_LICENSE_UNAVAILABLE
TPCM_CAPABILITY_UNSUPPORTED
```

Native return codes may be stored as diagnostics. They should not be the main user-facing error.

## Deployment Plan

### Batch 1: Documentation and Architecture Skeleton

Deliverables:

- overall design document;
- current status document;
- next-phase OpenTCSM adapter design;
- adapter package skeleton if approved after the documents.

No runtime behavior change.

### Batch 2: Helper Build and Install Path

Deliverables:

- `node_helpers/opentcsm_tpcm/klos-opentcsm-helper.c`;
- README with compile/install instructions;
- Ansible playbook or task to install helper on TPCM nodes;
- helper `capabilities`, `license`, `auth-health`, and `get-dmeasure-policy` commands.

No policy deployment switch yet.

### Batch 3: Dynamic Measurement Apply Through Helper

Deliverables:

- backend helper client;
- `OpenTcsmTpcmAdapter.apply_dynamic_measurement`;
- policy deployment route switch for `tpcm_dynamic_measurement`;
- task/audit/binding output with safe fields only;
- fallback disabled by default unless explicitly configured.

Validation:

- `hygon22` and `hygon23` both apply the same three-object policy;
- repeated apply is idempotent;
- empty current policy applies complete desired state;
- partial current policy becomes complete desired state;
- disabled object is removed only when `delete_unmanaged_objects=true`;
- missing/invalid/not-registered auth produces product error;
- no private key appears in task, audit, API response, or Ansible output.

### Batch 4: Evidence Collection Improvements

Deliverables:

- use helper for dynamic policy readback;
- use helper for license;
- keep existing trust-report collection if a direct API mapping is not ready.

### Batch 5: Decide Boot and Global Migration

After dynamic measurement is stable, decide whether to migrate TPCM trusted boot reference write, global policy read/write, and richer capability discovery. Do not start this before Batch 3 is verified.

## Acceptance Criteria for This Phase

The phase is complete when:

- no TPCM dynamic measurement deployment depends on `update_dmeasure_policy` test CLI semantics;
- the same code path works for both `hygon22` and `hygon23`;
- a new TPCM node can be onboarded by profile/auth_ref configuration only;
- policy apply is desired-state based and readback verified;
- task and audit details show `auth_ref`, UID, and public key fingerprint, but never private key material;
- TPM/Keylime behavior is unchanged;
- TPCM trusted boot and global policy continue to work on their existing bridge path.
