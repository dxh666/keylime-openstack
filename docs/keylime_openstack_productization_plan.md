# Keylime + OpenStack Productization Plan

Date: 2026-07-20

This project should be treated as a trust-management product for OpenStack
compute nodes, not as a collection of one-off attestation scripts. The product
boundary is:

```text
Keylime owns node trust verification
  -> the trust plane records evidence, policies, bindings, decisions, and audit
  -> OpenStack consumes only stable trust decisions
```

OpenStack integration should remain disabled until the Keylime-only gate is
stable. A node that cannot pass Keylime consistently must not be hidden by
Placement traits or scheduling logic.

## Product Goal

The first product milestone is compute-node trusted enrollment:

```text
TPM quote + boot PCR policy + Linux IMA measurement policy
  -> Keylime attestation PASS
  -> trust plane node state TRUSTED / UNTRUSTED / DRIFT / UNREACHABLE
  -> operator can see, diagnose, and repair policy drift
```

The final node decision must be capability-driven. A disabled capability is
shown as evidence when available, but it must not block the node from becoming
trusted. An enabled capability must pass before the node can be trusted.
OpenStack service state is only an optional extra gate; it cannot replace
Keylime trust evidence.

Only after this milestone should the project enable:

```text
OpenStack Placement traits
trusted flavors
Nova scheduling constraints
host quarantine
VM risk marking
```

## Current Gaps

The current lab proves that Keylime can attest `csri8` and `csri9` reliably,
but several product gaps remain:

1. `hygon22` still uses broad `ima_policy=tcb`, which produces a very large IMA
   measurement log. Keylime verifier timeouts then appear as
   `internal.verifier.not_reachable`. This is a node policy problem, not an
   OpenStack problem.
2. IMA runtime baselines can still be created too often. A product should create
   policy versions intentionally, not learn every drift as trusted.
3. The management UI is not yet the primary workflow for policy generation,
   deployment, repair, and audit.
4. Keylime operations are split between API calls, tenant-tool calls, and shell
   wrappers. The product needs one workflow layer that hides those details.
5. OpenStack enforcement is correctly disabled, but the system still needs a
   clear handoff from Keylime-only trust to Placement trait writes.
6. EVM and IMA appraisal are not ready for production use in this lab. They
   should remain a later milestone.

## Product Objects

Use these objects as the stable product model.

### Node

A managed OpenStack compute host.

Required fields:

```text
hostname
management_ip
keylime_agent_uuid
keylime_agent_ip
keylime_agent_port
openstack_hypervisor_name
node_role
status
```

Important states:

```text
registered
awaiting_policy
awaiting_reboot
attesting
trusted
untrusted
policy_drift
agent_unreachable
quote_timeout
maintenance
```

### Trust Policy

A versioned policy managed by the trust plane.

Policy types:

```text
trusted_boot
ima_runtime
evm
```

The UI should label `trusted_boot` as `可信启动`.

### Policy Binding

The relation between a policy version and one or more nodes.

Required behavior:

```text
one active trusted_boot policy per node
one active ima_runtime policy per node
one active evm policy per node when EVM mode is enabled
old bindings are superseded, not deleted
```

### Evidence

Raw or normalized material used to make trust decisions:

```text
TPM PCR quote
PCR bank and mask
Keylime attestation status
Keylime last event id
IMA measurement list summary
runtime policy checksum
measured boot event log when enabled
freshness window
```

Evidence should be stored for audit and troubleshooting. It should not be
treated as a policy by itself.

### Trust Decision

The current decision generated from Keylime evidence and product policy.

Decision fields:

```text
boot_status
runtime_status
evm_status
freshness_status
trusted
reason
desired_openstack_traits
remediation
enabled_trust_capabilities
```

The product should support these trust capability switches:

```text
TRUST_BOOT_ENABLED=true
TRUST_IMA_ENABLED=true
TRUST_EVM_ENABLED=false
TRUST_OPENSTACK_SERVICE_ENABLED=false
```

Examples:

```text
only TRUST_BOOT_ENABLED=true
  -> boot pass is enough for node trusted

TRUST_BOOT_ENABLED=true and TRUST_IMA_ENABLED=true
  -> boot and IMA runtime must both pass

TRUST_EVM_ENABLED=true
  -> EVM/appraisal evidence must also pass

TRUST_OPENSTACK_SERVICE_ENABLED=true
  -> nova-compute service enabled/up must also pass, but at least one Keylime
     capability still has to be enabled and trusted
```

## IMA Measurement Model

IMA measurement has two layers, and the product must manage both.

### Node-Side IMA Policy

The Linux kernel decides what enters the IMA measurement log. This is controlled
by the node-side IMA policy.

For Docker/Kolla OpenStack compute nodes, the default product profile should
measure stable security-relevant objects:

```text
measure func=KEY_CHECK keyrings=.ima
measure func=BPRM_CHECK mask=MAY_EXEC
measure func=MMAP_CHECK mask=MAY_EXEC
measure func=MODULE_CHECK
measure func=FIRMWARE_CHECK
measure func=POLICY_CHECK
```

Broad `ima_policy=tcb` should be an expert diagnostic profile only. It measures
too many mutable host files for a long-running OpenStack compute node and can
make Keylime quotes too slow to be operationally useful.

### Keylime Runtime Policy

Keylime validates the IMA measurement log against a runtime allowlist and
exclude rules. The runtime policy should be generated from an approved node
profile and an approved baseline window.

The product workflow should be:

```text
select node(s)
select IMA profile
deploy node-side IMA policy
reboot only when required
collect IMA measurement log
generate Keylime runtime policy
review diff and excludes
apply policy to Keylime verifier
wait for fresh attestation
mark binding active only after PASS
```

The system should not silently convert every runtime drift into a new trusted
baseline. Drift must first appear as an operator-visible event.

## Keylime Usage Boundary

Keylime should be used as the source of attestation truth:

```text
registrar: agent registration and identity
verifier: continuous quote, PCR policy, IMA runtime policy, attestation result
tenant tooling: policy creation and enrollment fallback where API coverage is incomplete
```

The trust plane should wrap Keylime into product operations:

```text
enroll agent
delete agent
reactivate agent
read verifier status
create runtime policy
store runtime policy
apply boot + runtime policy together
diff live IMA log against active policy
repair policy drift with operator approval
```

All of these operations should produce task records and audit events.

## Management UI

The first product UI should stay small and operational:

```text
节点状态
策略管理
  - 可信启动
  - IMA 运行时策略
  - EVM 策略
任务记录
审计日志
```

The policy pages should show only the list of policy versions and node bindings.
Each policy item should support:

```text
查看
绑定节点
部署
删除
```

Create, update, delete, bind, deploy, and repair actions must ask for the admin
token in a confirmation dialog at execution time. The token should not be shown
as a permanent top-bar field.

## OpenStack Handoff

OpenStack should consume trust decisions only after Keylime-only stability is
proven.

Enable OpenStack integration in this order:

1. Dry-run Placement trait decision output.
2. Write traits only, without changing scheduling.
3. Create trusted flavor extra specs.
4. Enable scheduling constraints for selected projects.
5. Add quarantine and VM risk marking.

The first trait mapping remains:

```text
boot_status=pass      -> CUSTOM_KEYLIME_BOOT_TRUSTED
runtime_status=pass   -> CUSTOM_KEYLIME_RUNTIME_TRUSTED
trusted=true          -> CUSTOM_KEYLIME_TRUSTED
fresh Keylime PASS    -> CUSTOM_KEYLIME_ATTESTED
```

## Milestones

### M1: Keylime-Only Product Gate

Deliverables:

```text
stable node inventory
trusted boot policy CRUD
IMA runtime policy CRUD
node-side IMA policy deployment through agent or Ansible
Keylime runtime policy generation and apply
drift detection
operator-approved repair
stability check
audit trail
```

Exit criteria:

```text
csri8 and csri9 pass 10 consecutive Keylime-only checks
hygon22 is either trusted or explicitly marked quote_timeout/maintenance
runtime drift is explainable through diff reports
no policy name collision in Keylime allowlists
no manual database edits are required
```

### M2: OpenStack Trust Sync

Deliverables:

```text
dry-run trait decisions
Placement trait write with audit
read-only comparison between Keylime state and Placement state
safe rollback
```

### M3: Trusted Scheduling

Deliverables:

```text
trusted flavor template
selected project access
Nova scheduling validation
untrusted-node scheduling rejection demo
```

### M4: Response Actions

Deliverables:

```text
host quarantine
VM risk marking
operator approval workflow
recovery workflow
```

### M5: EVM / Appraisal / DIM

Deliverables:

```text
IMA appraisal lab profile
EVM signature validation
keyring management
DIM integration point
combined trust decision
```

This milestone should not block M1 to M4.

## Product Rule

Do not make OpenStack trust a manual label. A compute node becomes trusted only
when Keylime can prove fresh TPM and IMA evidence against an active product
policy. OpenStack should only receive that decision after the trust plane has
recorded the evidence, policy version, binding, and audit trail.
