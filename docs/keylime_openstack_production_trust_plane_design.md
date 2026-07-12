# Keylime + OpenStack production trust plane design

Date: 2026-07-11

This branch starts the transition from lab scripts to a long-running trusted
compute control plane for the current Kolla-based OpenStack cluster.

## Target environment

```text
OpenStack deployment:
  Kolla / Docker
  controller: csri10
  compute:    csri8, csri9, hygon22

Keylime deployment:
  Docker-based verifier / registrar / tenant tooling

Database:
  PostgreSQL
  lab default data dir: /var/lib/keylime-openstack/postgres
  config:               /etc/keylime-openstack/keylime-openstack.env
```

`hygon22` is treated as a formal compute node in the same OpenStack cluster.
It is not a side experiment.

## Main architecture

```text
FastAPI API
  serves management APIs and static Vue frontend

Worker
  periodically collects evidence, evaluates trust, writes OpenStack traits,
  handles quarantine and VM risk marking

PostgreSQL
  stores long-lived policy, binding, evidence, decision, task, and audit state

OpenStack adapter
  openstacksdk / Placement API first
  openstack CLI fallback only when the target Kolla API behavior is not yet verified

Keylime adapter
  verifier / registrar API first
  keylime-tenant / keylime-policy tool container fallback for version-specific operations
```

Long-lived policy state must live in PostgreSQL. Temporary files are permitted
only as a tool adapter when Keylime tooling requires a filesystem path. Those
files must be generated from database state, used once, deleted, and audited
back into the database.

## Scheduling traits

The production-facing trait set is intentionally small:

```text
CUSTOM_KEYLIME_BOOT_TRUSTED
CUSTOM_KEYLIME_RUNTIME_TRUSTED
CUSTOM_KEYLIME_TRUSTED
CUSTOM_KEYLIME_ATTESTED
```

Meaning:

```text
CUSTOM_KEYLIME_BOOT_TRUSTED
  TPM quote, PCR boot policy, and freshness are accepted.

CUSTOM_KEYLIME_RUNTIME_TRUSTED
  Linux IMA runtime appraisal and EVM/keyring verification are accepted.

CUSTOM_KEYLIME_TRUSTED
  boot trusted, runtime trusted, and nova-compute is enabled/up.

CUSTOM_KEYLIME_ATTESTED
  legacy compatibility trait for existing flavors and lab workflows.
```

More detailed states such as agent online, quote valid, PCR policy valid, IMA
valid, and EVM valid are stored in PostgreSQL and shown in the management UI,
but they are not all exposed as Placement traits by default.

## Heterogeneous policy model

The cluster has at least two hardware families:

```text
intel-xeon-4216:
  csri8, csri9, csri10
  Ubuntu kernel 6.8.0-134

hygon-c86-7380:
  hygon22
  Anolis kernel 6.6.102-5.3.3.an23
```

Policies bind to hardware profiles by default and can be overridden per node.
This avoids applying Intel PCR / IMA / EVM assumptions to the Hygon host.

## Management UI modules

The Vue frontend is kept static and build-free, but its product shape changes
to match the new backend:

```text
Overview
Nodes
Policies
Tasks
Audit
```

The first iteration displays database-backed state and starts sync tasks. Later
iterations can add policy editors, baseline import flows, and EVM keyring views.

## DIM-ready evidence model

Keylime remains the primary trust source for TPM, IMA, and EVM. The code keeps
an explicit provider boundary so openEuler DIM can be added next:

```text
Keylime evidence
+ future DIM evidence
+ OpenStack compute state
-> trust decision
-> Placement traits / quarantine / VM risk marker
```

DIM should enter as another evidence provider, not as a parallel control plane.
