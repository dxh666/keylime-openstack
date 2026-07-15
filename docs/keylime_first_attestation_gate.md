# Keylime-first attestation gate

Date: 2026-07-15

This stage intentionally keeps OpenStack enforcement disabled. The goal is to
prove that Keylime itself is fully useful for compute-node trust management
before translating trust into Placement traits and Nova scheduling decisions.

## Scope

The Keylime-first gate validates:

```text
Keylime registrar sees every compute-node agent
Keylime verifier can continuously attest every agent
TPM PCR policy is bound and passing
IMA runtime policy is bound and passing
FastAPI trust plane reads verifier status through the Keylime API
No OpenStack Placement trait, nova-compute quarantine, or VM marker action is required
```

For the current lab, keep:

```text
TRUST_POLICY_MODE=ima-only
OPENSTACK_ENFORCEMENT_ENABLED=false
```

## Check current state

Run this on `csri10`:

```bash
cd /opt/keylime-openstack

deploy/scripts/keylime-only-attestation-check.sh --strict
```

Expected result:

```json
{
  "ok": true,
  "mode": "keylime-only",
  "nodes_total": 3,
  "nodes_trusted": 3
}
```

Each node should report:

```text
attestation_status=PASS
has_runtime_policy=true
evidence.boot=pass
evidence.runtime=pass
```

Use a host filter during debugging:

```bash
deploy/scripts/keylime-only-attestation-check.sh --strict --hosts hygon22
```

## Refresh IMA baseline

When a node has a legitimate runtime baseline change:

```bash
deploy/scripts/keylime-ima-runtime-policy-refresh.sh hygon22
```

The refresh helper generates the runtime policy from the live IMA measurement
list, registers it, applies it to Keylime with the node's bound PCR policy,
waits for the verifier to finish the next attestation, and then runs a
trust-plane sync. The wait avoids the transient `PENDING` state created by
Keylime tenant update/reactivate.

## Exit criteria before OpenStack integration

Do not enable OpenStack enforcement until all of these are true:

```text
keylime-only-attestation-check.sh --strict passes repeatedly
refreshing one node's IMA policy returns to trusted without manual repair
Keylime verifier API shows PASS after reactivation
runtime policy names do not collide in verifier allowlists
dynamic runtime paths are excluded instead of learned as trusted
```

Only after this gate is stable should the project move to:

```text
OPENSTACK_ENFORCEMENT_ENABLED=true
Placement trait writes
trusted flavors / Nova Scheduler decisions
host quarantine and VM risk marking
```
