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
evidence_fresh.boot=true
evidence_fresh.runtime=true
```

Use a host filter during debugging:

```bash
deploy/scripts/keylime-only-attestation-check.sh --strict --hosts hygon22
```

The same one-shot check is also exposed through the FastAPI control plane:

```bash
curl -fsS http://127.0.0.1:8088/api/keylime/check
curl -fsS 'http://127.0.0.1:8088/api/keylime/check?hosts=hygon22'
curl -fsS 'http://127.0.0.1:8088/api/keylime/check?failures_only=true'
```

The management UI has a dedicated `Keylime` page. It shows the verifier gate,
per-agent TPM/IMA state, freshness, the active Keylime event, and the first
remediation hint. This is the preferred daily operator view while OpenStack
enforcement remains disabled.

Run a stability gate without a shell loop:

```bash
deploy/scripts/keylime-only-attestation-check.sh --strict --count 10 --interval 30
```

When debugging a failed gate, limit output to untrusted nodes:

```bash
deploy/scripts/keylime-only-attestation-check.sh --strict --failures-only
```

Each failed node includes a `remediation` block. Treat it as the first
operator hint before refreshing policies. For example, `agent-reachability`
points to agent/network/reactivation checks, while `ima-runtime-policy` points
to the runtime policy diff helper.

For the common case where boot evidence is already trusted but IMA runtime
policy has drifted, use the repair wrapper:

```bash
export KEYLIME_RUNTIME_POLICY_EXCLUDE_PROFILE=kolla-docker-host
deploy/scripts/keylime-only-attestation-repair.sh --hosts csri8,csri9
```

It runs a pre-check, diffs each affected node against the bound runtime policy,
refreshes the IMA policy with the bound PCR policy included, force-replaces the
Keylime verifier enrollment, waits for the next attestation, and finishes with a
stability gate. It will not refresh a node whose boot/PCR evidence is failing.

The default `kolla-docker-host` profile is intentional for Docker/Kolla compute
hosts. It excludes Docker/containerd runtime state, Ansible temporary modules,
APT package indexes/caches, Python bytecode caches, Open vSwitch local DB
state, `/run`, temporary files, and logs from the Keylime runtime policy. Those
paths change continuously on a running cloud node and should not be learned as
trusted runtime baseline. Keep the measured set focused on stable host
binaries, service files, system libraries, and administrator-controlled
configuration. Use `KEYLIME_RUNTIME_POLICY_EXCLUDE_PROFILE=minimal` only for
narrow debugging.

## Diagnose IMA runtime failures

If a node reports:

```text
last_event_id=ima.validation.ima-ng.runtime_policy_hash
last_event_id=ima.validation.ima-ng.not_in_allowlist
```

compare the live IMA measurement list with the bound runtime policy before
refreshing the baseline:

```bash
deploy/scripts/keylime-ima-runtime-policy-diff.sh hygon22 bound
```

The helper is read-only. It writes a JSON report under the runtime policy state
directory and prints counts for:

```text
path_missing
hash_missing
excluded
malformed
```

Only refresh the runtime baseline after confirming the differences are expected
runtime drift, such as known transient paths that should be excluded or
legitimate package/container updates.

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
last_successful_attestation remains fresh for every trusted node
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
