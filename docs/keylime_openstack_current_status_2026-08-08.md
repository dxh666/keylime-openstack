# Keylime OpenStack Current Status - 2026-08-08

This document records the current project status after the first TPCM management iterations. It is intentionally written as a productization audit, not as a feature list.

## Overall Judgment

The project has moved beyond one-off lab scripts and is now a working product prototype for an OpenStack compute-node trust management plane. The core product objects exist, the API/frontend/worker/deployment path exists, and TPM and TPCM nodes can be represented under one management model.

The project is not yet a fully productized architecture. The largest remaining gap is that the OpenTCSM/TPCM execution path still relies on Ansible playbooks that wrap OpenTCSM test CLI tools. This path has been hardened enough for lab operation, but it should be treated as a migration bridge rather than the final adapter.

Approximate maturity:

| Area | Status | Maturity |
| --- | --- | --- |
| OpenStack node inventory | Product usable | 75-80% |
| Unified trusted-node profile | Product usable with small gaps | 75% |
| TPM/Keylime trust management | Product prototype | 70% |
| TPCM/OpenTCSM trust management | Basic usable, needs adapter convergence | 55-60% |
| Policy/task/audit center | Product usable | 70% |
| OpenStack trust consumption | Conservative prototype | 50% |
| Architecture extensibility | Needs convergence | 45-55% |

## Confirmed Product Direction

The current code already follows the intended product model in several places:

- `backend/keylime_openstack/services/trust_agents.py` defines product state as compute/non-compute, managed/unmanaged, and trusted-root type.
- `backend/keylime_openstack/models.py` contains `TrustedNodeProfile` with `trusted_root_type`, `adapter_type`, `agent_identity`, and `capabilities`.
- `backend/keylime_openstack/services/trust_registration.py` treats Keylime and OpenTCSM as adapter choices behind a unified trusted-node profile.
- `backend/keylime_openstack/services/trust_capabilities.py` creates capability summaries independent of the low-level provider.
- `deploy/frontend/js/modules/nodes.js` and `deploy/frontend/js/modules/node-detail.js` mostly present product terms: trusted agent, trusted root, capability, license, and trust state.

This is the correct foundation and should not be replaced by a separate TPCM console.

## Current Runtime State

The known lab state is:

| Node | OpenStack Role | Trusted Root | Management State | Notes |
| --- | --- | --- | --- | --- |
| `csri8` | compute | TPM | managed | Current IMA runtime policy issues are preserved, not expanded in this phase. |
| `csri9` | compute | TPM | managed | Same TPM/Keylime path as `csri8`. |
| `hygon22` | compute | TPCM | managed after OpenTCSM deployment | Reset/init/PIK/authorization command validation completed; dynamic policy succeeded after manual initialization and later management retry. |
| `hygon23` | compute | TPCM | managed | TPCM evidence and authorization verified; current dynamic policy deployment still exposed the CLI-path issue where only `idt_table` remained after failed deployment. |

No product code should depend on these hostnames.

## Completed Capabilities

### Product Infrastructure

Status: product usable.

Implemented areas:

- FastAPI backend;
- PostgreSQL-backed model;
- worker loop;
- static Vue frontend;
- task center;
- audit center;
- admin login/session/token flow;
- paginated list views;
- deployment through Docker Compose.

Risk: `backend/keylime_openstack/models.py` centralizes many model classes in one file. This is acceptable for the current stage, but new core code should use a more explicit package structure. Do not perform a large model split in the next patch set.

### OpenStack Inventory and Trust Decisions

Status: product usable with conservative enforcement.

Implemented areas include compute-node inventory, OpenStack service state collection, trust decision generation, capability-driven decision logic, and Placement trait/quarantine foundations from earlier phases.

Risk: OpenStack consumption must remain downstream of trust evidence. It should not become a manual label or a workaround for incomplete attestation.

### TPM / Keylime Path

Status: product prototype.

Implemented areas:

- Keylime registrar/verifier access;
- measured boot policy management;
- IMA runtime policy management;
- Keylime tenant-tool fallback;
- policy deployment and binding state;
- remediation summaries.

Known boundary: `csri8` and `csri9` IMA runtime policy state is not the focus of this phase. EVM stays disabled.

### Unified Trusted Node Registration

Status: product usable.

Implemented areas:

- static Keylime map synchronization;
- Keylime discovery;
- TPCM evidence-backed registration;
- manual profile preservation;
- conflict detection between TPM and TPCM adapters;
- safe TPCM auth reference preservation in `agent_identity.auth_refs`.

Risk: `TRUST_AGENT_TYPE_MAP` and env examples still mention lab nodes. This is acceptable as deployment sample data but must not become product logic.

### TPCM Evidence Collection

Status: basic usable, needs adapter convergence.

Implemented areas:

- active OpenTCSM evidence collection through Ansible;
- parsing of trust status, trust report, global policy, dynamic policy, boot records, TPCM ID, and license status;
- normalized `boot` and `runtime` evidence records;
- profile synchronization from TPCM evidence.

Product gap: `deploy/ansible/playbooks/collect-opentcsm-evidence.yml` still shells out to OpenTCSM commands and parses stdout. It is sufficient for status collection, but it is not the final product adapter.

### TPCM License

Status: basic product display.

Implemented areas:

- `get_license_status` collection;
- status normalization to `valid`, `expired`, `missing`, `unavailable`, or `unknown`;
- node detail display in the TPCM capability section.

Product gap: license is currently parsed from command output only. There is no dedicated adapter capability record for license source, version, or health.

### TPCM Authorization References

Status: product direction correct.

Implemented areas:

- per-node `auth_refs.boot` and `auth_refs.dynamic`;
- fallback defaults through `OPENTCSM_DEFAULT_BOOT_AUTH_REF` and `OPENTCSM_DEFAULT_DYNAMIC_AUTH_REF`;
- protected JSON key directory under `/etc/keylime-openstack/opentcsm/keys`;
- API/task/binding metadata showing `auth_ref`, UID, and public key fingerprint.

Product gap: the current playbooks pass raw SM2 material into remote command execution. Some OpenTCSM test CLI tools print private/public key hexdumps to stdout. Playbooks redact command arguments, but stdout/stderr handling is still a risk boundary. A formal helper must prevent secret output at the source.

### TPCM Trusted Boot

Status: management baseline usable; hardware apply is a hardened bridge.

Implemented areas:

- TPCM trusted boot policy can bind a management-side baseline from collected OpenTCSM evidence.
- Hardware apply entry exists.
- `update_bmeasure_references` and `set_measure_ctrl_switch` are supported.
- authorization missing/invalid/rejected cases are classified.
- `0x88` and `0x98` style errors are categorized.
- control switch execution stops when reference update fails.

Product gap: hardware apply still relies on CLI tools in `deploy/ansible/playbooks/apply-opentcsm-boot-policy.yml`. The next phase should not migrate trusted boot unless the dynamic helper work proves stable and the change remains small.

### TPCM Dynamic Measurement

Status: basic usable but not product-grade yet.

Implemented areas:

- policy type `tpcm_dynamic_measurement`;
- per-node object configuration for `kernel_section`, `syscall_table`, and `idt_table`;
- per-node dynamic auth reference selection;
- global enable/disable switch;
- write-and-readback validation;
- failure productization for missing commands and rejected authorization;
- retry logic around empty initial policy reads.

Observed issue: `hygon23` still failed management deployment in the current remote validation and left only `idt_table` in `get_dmeasure_policy`.

Root cause direction: the current implementation drives OpenTCSM through the behavior of the test CLI `update_dmeasure_policy`. The CLI has mode-specific behavior: default set can create multiple objects, while object-specific operations can modify only one object. Continuing to patch command order per node is not a product solution.

Required next step: replace this path with a formal OpenTCSM TPCM helper/adapter that applies desired dynamic policy state atomically and verifies observed state.

### TPCM Global Policy

Status: useful but should remain on the bridge path for now.

Implemented areas:

- global policy state aggregation across managed TPCM compute nodes;
- writable controls for boot measurement, boot control, dynamic measurement, and dynamic busy-delay limit;
- readback mismatch detection;
- task and audit records.

Known boundary: global policy uses the same CLI bridge through `deploy/ansible/playbooks/apply-opentcsm-global-policy.yml`. This phase should not migrate it before dynamic measurement is stabilized.

### Frontend

Status: product usable with small cleanup opportunities.

Implemented areas:

- unified compute-node list;
- node detail with OpenStack state, trusted-agent state, trusted root, TPCM ID, license, capability summary, trust report, and history;
- policy center;
- global controls;
- task center;
- audit center.

Product gap: some UI/API details still expose implementation artifacts such as `keylime_artifact` or adapter wording. This is acceptable for operator diagnostics but should be minimized in primary product views.

## Experimental or Non-Product Code

| Area | Why It Is Transitional | Required Direction |
| --- | --- | --- |
| OpenTCSM dynamic playbook | Uses embedded Python and test CLI commands to perform policy writes | Replace with formal helper/adapter |
| OpenTCSM boot hardware playbook | Uses test CLI commands for boot reference writes and control switch | Keep stable for now, migrate later |
| OpenTCSM global policy playbook | Uses command output parsing and command retries | Keep stable for now, migrate later |
| OpenTCSM evidence collection playbook | Reads utility commands and parses stdout | Later replace with helper-backed JSON evidence |
| `deploy/examples/experimental/evm` | Explicitly outside current phase | Do not enable |
| Host-specific docs and examples | Useful historical records, not product behavior | Keep as docs/examples only |
| Static env node maps | Useful fallback and lab defaults | Do not hard-code in services |
| Capability booleans | Adequate for first phase, weak for future expansion | Introduce versioned capability schema later |

## Main Risks

1. TPCM execution depends on OpenTCSM test CLI behavior.
2. Sensitive key material can leak through CLI stdout if the test tool prints hexdumps.
3. Adapter selection exists as data, but not yet as a formal interface.
4. Policy deployment service still contains adapter branching and orchestration.
5. Capability model is currently a fixed boolean map.
6. Current error messages are improved but still derive from command text.
7. TPCM dynamic policy does not yet have a product-grade atomic apply operation.

## Recommended Immediate Direction

Do not continue adding one-off shell patches around `hygon23`.

The next stage should:

1. Add a formal OpenTCSM TPCM adapter/helper design.
2. Implement the helper first for dynamic measurement.
3. Keep trusted boot and global policy on the existing bridge path until dynamic measurement is stable.
4. Preserve existing TPM/Keylime behavior.
5. Avoid unrelated frontend expansion.
