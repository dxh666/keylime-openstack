# OpenStack + Keylime Trusted Cloud Lab

This repository records a staged lab that integrates Keylime remote attestation with OpenStack scheduling and project authorization.

The main idea is:

```text
Keylime verifies compute-node trust
  -> a sync controller writes OpenStack Placement traits
  -> Nova Scheduler consumes those traits through trusted flavors
  -> private flavors expose trusted-compute capability only to selected projects
```

## Lab Topology

```text
OpenStack:
  controller: csri10 / 172.31.100.10
  compute:    csri9  / 172.31.100.9
  compute:    csri8  / 172.31.100.8

Keylime:
  registrar/verifier/tenant: csri10
  agent:                     csri9

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
  systemd service/timer units for periodic sync

deploy/env/
  sanitized lab variable template

deploy/examples/
  command snippets for private trusted flavor and verification

deploy/frontend/
  read-only frontend for real-time compute-node trust monitoring
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

### Case 5: Read-only Trust Monitor Frontend

The first frontend module provides a read-only dashboard for compute-node trust status:

```text
auto-discover nova-compute services
show node IP, hosted VM count, trust state, and service state
mark trusted / untrusted / no-agent nodes with green / red / yellow status
expand node cards for details
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
2. `docs/openstack_keylime_integration_case_summary.md`
3. `docs/keylime_openstack_current_issue_hardening_baseline.md`
4. `docs/openstack_keylime_deep_integration_roadmap.md`
5. `docs/keylime_openstack_phase2_attestation_freshness.md`
6. `docs/keylime_openstack_phase3_private_flavor_project_auth.md`
7. `docs/keylime_openstack_case4_host_quarantine.md`
8. `docs/keylime_openstack_frontend_trust_monitor_baseline.md`
