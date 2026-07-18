# OpenStack + Keylime Trusted Cloud Lab

This repository records a staged lab that integrates Keylime remote attestation with OpenStack scheduling and project authorization.

## Production Trust Plane Branch

This branch is being refactored from shell-heavy lab automation into a
production-oriented trust control plane:

```text
FastAPI API + static Vue management UI
Python worker for sync / quarantine / VM risk marking / policy apply
PostgreSQL for policy, evidence, decision, task, and audit state
OpenStack SDK / Placement API first, CLI fallback only where needed
Keylime verifier / registrar API first, tenant-policy tooling as fallback
```

The current Kolla/OpenStack environment is:

```text
controller: csri10
compute:    csri8 / csri9 / hygon22
```

The simplified scheduling traits for the landing architecture are:

```text
CUSTOM_KEYLIME_BOOT_TRUSTED
CUSTOM_KEYLIME_RUNTIME_TRUSTED
CUSTOM_KEYLIME_TRUSTED
CUSTOM_KEYLIME_ATTESTED
```

Read the production design first:

```text
docs/keylime_openstack_production_trust_plane_design.md
```

Deployment for the new FastAPI/PostgreSQL control plane:

```text
docs/keylime_openstack_production_deployment.md
```

Measured Boot、IMA 与 Ansible 策略管理：

```text
docs/keylime_measured_boot_ima_ansible_management.md
```

Node-side IMA appraisal and EVM enablement:

```text
docs/keylime_openstack_node_evm_appraisal_enablement.md
```

Hygon22 experiment-only kernel route:

```text
docs/keylime_openstack_hygon22_experimental_kernel.md
```

The current recommended experiment mode is IMA measurement first:

```text
TRUST_POLICY_MODE=ima-only
```

In this mode, Keylime IMA runtime attestation can drive
`CUSTOM_KEYLIME_RUNTIME_TRUSTED` without waiting for EVM/appraisal evidence.
Switch to `TRUST_POLICY_MODE=evm-required` only after node-side keyrings,
appraisal policy, and EVM signatures are stable.

Before enabling OpenStack enforcement, run the Keylime-only attestation gate:

```text
docs/keylime_first_attestation_gate.md
deploy/scripts/keylime-only-attestation-check.sh --strict
```

The main idea is:

```text
Keylime verifies compute-node trust
  -> a sync controller writes OpenStack Placement traits
  -> Nova Scheduler consumes those traits through trusted flavors
  -> private flavors expose trusted-compute capability only to selected projects
```

## Current Next Phase

The next phase is to turn the current Keylime/OpenStack integration into a TPM-driven trusted control plane.

This means the lab should stop treating Keylime as only a status source for one Placement trait, and start using TPM evidence as the basis for OpenStack security decisions:

```text
TPM quote / PCR / measured boot / IMA evidence
  -> Keylime policy and attestation decision
  -> OpenStack trust traits, scheduling, quarantine, VM marking, audit
```

The immediate next case is:

```text
Case 10: TPM evidence baseline and Keylime policy-driven OpenStack trust control plane
```

Read first:

```text
docs/keylime_openstack_next_phase_tpm_trust_control_plane.md
```

## Lab Topology

```text
OpenStack:
  controller: csri10 / 172.31.100.10
  compute:    csri9  / 172.31.100.9
  compute:    csri8  / 172.31.100.8

Keylime:
  registrar/verifier/tenant: csri10
  agent:                     csri9 / csri8

Trusted trait:
  CUSTOM_KEYLIME_ATTESTED
```

## Repository Layout

```text
docs/
  case summaries, staged lab records, OpenStack project analysis,
  Keylime deployment notes, troubleshooting records

deploy/scripts/
  Keylime-to-Placement sync scripts, freshness checks,
  nova-compute quarantine prototype, agent restart helper

deploy/systemd/
  systemd service/timer units for periodic sync and the management console

deploy/env/
  sanitized lab variable template

deploy/examples/
  command snippets for private trusted flavor and verification

deploy/frontend/
  frontend for compute-node trust monitoring and TPM PCR policy management
```

## Completed Cases

### Case 1: Keylime -> Placement Trait -> Trusted Flavor

Keylime attestation status is synchronized to the OpenStack Placement trait:

```text
PASS -> add CUSTOM_KEYLIME_ATTESTED to csri9
FAIL -> remove CUSTOM_KEYLIME_ATTESTED from csri9
```

Nova trusted flavors then require:

```text
trait:CUSTOM_KEYLIME_ATTESTED=required
```

### Case 2: PCR Policy and Freshness

The trusted trait is no longer only an "agent is alive" signal. It requires:

```text
PCR policy match
attestation_status == PASS
last_successful_attestation is fresh
operational_state is not Failed / Terminated
```

### Case 3: Private Trusted Flavor + Project Authorization

Trusted compute is exposed as a private flavor:

```text
trusted.keylime.private.small
```

Only the authorized project can see and use it.

### Case 4: Host Quarantine Prototype

When Keylime trust fails:

```text
remove trusted trait
disable csri9 nova-compute
block any new workload from scheduling to the untrusted host
```

When Keylime returns to `PASS_FRESH`, the prototype can re-enable the compute service if it was disabled by the controller.

### Case 5: Trust Monitor Frontend

The first frontend module provides a dashboard for compute-node trust status:

```text
auto-discover nova-compute services
show node IP, hosted VM count, trust state, and service state
mark trusted / untrusted / no-agent nodes with green / red / yellow status
expand node cards for details
```

### Case 6: TPM PCR Policy Management Frontend

The second frontend module manages lab TPM PCR policies:

```text
separate Keylime policy modules for boot measurements and runtime integrity
keep the default PCR policy screen focused on baseline import and bound-policy apply
create / edit / delete TPM PCR policies
store policy JSON under /var/lib/keylime-openstack-sync/
bind policies to compute nodes by Keylime agent UUID
apply one policy to one node or all configured agent nodes
call keylime-tenant update + reactivate with --tpm_policy for verifier policy changes
```

### Case 7: Multi-Node Trust Inventory and Sync

The current controller handles csri9 and csri8 as trusted-compute candidates:

```text
auto-discover nova-compute nodes from OpenStack
refresh Keylime agent inventory from registrar reglist
fall back to static host/IP/UUID maps when reglist cannot be matched by IP
write per-host decision files for multi-node monitoring and quarantine
```

### Case 8: Dynamic Trusted Compute Pool

The trusted compute pool now shrinks and recovers according to Keylime state:

```text
csri8/csri9 PASS_FRESH -> both nodes accept trusted workloads
csri8 agent stopped -> csri8 loses trusted trait and nova-compute is disabled
trusted workload pinned to csri8 fails with NoValidHost
trusted workload continues to run on csri9
csri8 agent recovered -> csri8 regains the trait and can host trusted workloads again
```

### Case 9: Existing VM Risk Marking

Existing VMs on an untrusted compute host are marked for audit:

```text
untrusted host -> set keylime_trust_* metadata on hosted VMs
trusted host -> clear keylime_trust_* metadata
metadata audit file records MARK/CLEAR actions
the marker runs from the existing keylime-openstack-sync.timer control loop
```

### Case 10B: TPM PCR Policy Management Control Plane

TPM PCR policy management is now part of the management console and deployable scripts:

```text
collect TPM evidence baseline
render per-node PCR7 and PCR0-7 policies from the baseline
store and bind policies under /var/lib/keylime-openstack-sync/
apply bound policies to all nodes
surface only production-safe policy operations in the management console
```

This case completes the first boot-measurement trust loop. PCR7 is the current stable attestation policy for csri8/csri9. PCR0-7 exact policies are kept as diagnostics for measured boot work because they triggered `measured_boot.parser.tpm2_eventlog.warning` during Case 10B. Runtime integrity policy is now a separate management-console module and is reserved for the next IMA-based stage.

### Case 11A: IMA Runtime Integrity Control Plane Scaffolding

The next runtime-integrity stage now has deployable control-plane scripts:

```text
collect IMA / PCR10 runtime evidence from csri8/csri9
register Keylime runtime policy JSON files in the existing policy store
bind runtime policies separately from PCR7 boot policies
apply runtime policies through keylime-tenant update + reactivate
reuse the existing Keylime decision -> Placement trait -> quarantine -> VM marker loop
```

Additional compute nodes can be added by running a Keylime agent with a stable UUID and
then refreshing the registrar-backed inventory. The generic agent container helper is:

```text
deploy/scripts/keylime-agent-container-restart.sh
```

The repository intentionally does not synthesize Keylime runtime policy JSON by guessing the format. Generate that JSON with the Keylime tooling that matches the running verifier/tenant version, then register it with:

```text
deploy/scripts/keylime-ima-runtime-policy-register.sh
deploy/scripts/keylime-ima-runtime-policy-apply.sh
deploy/scripts/keylime-ima-runtime-policy-generate.sh
deploy/scripts/keylime-ima-runtime-policy-refresh.sh
```

Runtime policy IDs in this project remain stable control-plane identifiers.
When a policy is applied to Keylime, the actual verifier `allowlists.name` is
unique by default (`KEYLIME_RUNTIME_POLICY_APPLY_NAME_MODE=unique`) and includes
the policy content hash plus an apply suffix. This avoids repeated Keylime
`allowlists.name` conflicts when re-baselining or re-enrolling a node. Use
`KEYLIME_RUNTIME_POLICY_APPLY_NAME_MODE=content-hash` for deterministic names,
or `stable` only when intentionally reusing the old verifier name behavior.

`keylime-ima-runtime-policy-generate.sh` captures the live
`/sys/kernel/security/ima/ascii_runtime_measurements` from one compute host,
generates a Keylime runtime policy with the deployed tenant image, applies
default Docker transient-file excludes, and registers the generated policy for
the target host.

For normal operations, use `keylime-ima-runtime-policy-refresh.sh <host>`; it
wraps generate, register, apply, bound PCR policy inclusion, and the configured
post-apply sync path. The apply step waits briefly before syncing because
Keylime tenant update/reactivate puts the verifier back into a transient
`PENDING` state until the next quote succeeds.

The 2026-07-11 hygon22 recovery validated the full PCR7 + PCR10/IMA path for
csri8, csri9, and hygon22. See
`docs/keylime_openstack_case11_hygon22_runtime_policy_lessons_2026-07-11.md`.

## Integrated Runtime

The automated lab control plane now runs from systemd:

```text
keylime-openstack-monitor.service
  -> serves the trust monitor and TPM PCR policy frontend on 172.31.100.10:8088

keylime-openstack-sync.timer
  -> keylime-sync-control-loop.sh
  -> keylime-agent-inventory-refresh.sh
  -> keylime-placement-sync.sh
  -> keylime-nova-compute-quarantine.sh
  -> keylime-vm-risk-marker.sh
```

One-time trusted flavor provisioning is available as:

```text
deploy/scripts/keylime-openstack-trusted-flavor-setup.sh
```

Control-plane installation and health checking:

```text
deploy/scripts/keylime-openstack-compose-deploy.sh
deploy/scripts/keylime-only-attestation-check.sh
deploy/scripts/keylime-openstack-control-plane-install.sh
deploy/scripts/keylime-openstack-capability-check.sh
```

Use `keylime-openstack-compose-deploy.sh deploy` for the new
FastAPI/PostgreSQL trust plane. The older
`keylime-openstack-control-plane-install.sh` remains for the shell/systemd lab
control plane and should not be used as the primary production-path installer.

## Important Security Notes

This repository is sanitized for GitHub:

```text
lab passwords are replaced with <LAB_USER_PASSWORD>
OpenStack tokens are replaced with <REDACTED_OPENSTACK_TOKEN>
VM adminPass values are redacted
```

The current implementation is lab-grade. Before production use:

```text
use a least-privilege OpenStack service account instead of admin-openrc.sh
pin Keylime agent image versions
remove privileged container and broad TPM device permissions
enable EK certificate / measured boot / IMA runtime policies
replace keylime-tenant CLI polling with direct verifier API calls
add alerting, dampening, and human approval for host quarantine
```

## Suggested Reading Order

1. `docs/openstack_project_principles.md`
2. `docs/keylime_openstack_next_phase_tpm_trust_control_plane.md`
3. `docs/openstack_keylime_integration_case_summary.md`
4. `docs/keylime_openstack_current_issue_hardening_baseline.md`
5. `docs/openstack_keylime_deep_integration_roadmap.md`
6. `docs/keylime_openstack_phase2_attestation_freshness.md`
7. `docs/keylime_openstack_phase3_private_flavor_project_auth.md`
8. `docs/keylime_openstack_case4_host_quarantine.md`
9. `docs/keylime_openstack_frontend_trust_monitor_baseline.md`
10. `docs/keylime_openstack_frontend_tpm_pcr_policy_management.md`
11. `docs/keylime_agent_inventory_auto_discovery.md`
12. `docs/keylime_openstack_multinode_sync_current_issues_2026-07-04.md`
13. `docs/keylime_openstack_case8_dynamic_trusted_pool.md`
14. `docs/keylime_openstack_case9_vm_risk_marker.md`
15. `docs/keylime_openstack_case10b_pcr_policy_management.md`
16. `docs/keylime_openstack_case11_ima_runtime_integrity.md`
17. `docs/keylime_openstack_case11_hygon22_runtime_policy_lessons_2026-07-11.md`
18. `docs/keylime_openstack_integrated_capability_audit.md`
