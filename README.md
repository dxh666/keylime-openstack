# OpenStack + Keylime Trusted Cloud Lab

This repository records a staged lab that integrates Keylime remote attestation with OpenStack scheduling and project authorization.

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
deploy/scripts/keylime-openstack-control-plane-install.sh
deploy/scripts/keylime-openstack-capability-check.sh
```

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
16. `docs/keylime_openstack_integrated_capability_audit.md`
