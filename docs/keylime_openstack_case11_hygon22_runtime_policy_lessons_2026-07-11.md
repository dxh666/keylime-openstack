# Case 11 hygon22 runtime policy lessons - 2026-07-11

## Result

The lab reached a three-node trusted pool:

```text
csri8   PASS_FRESH / IMA pass / TPM mask 0x480 / trait present / nova-compute enabled up
csri9   PASS_FRESH / IMA pass / TPM mask 0x480 / trait present / nova-compute enabled up
hygon22 PASS_FRESH / IMA pass / TPM mask 0x480 / trait present / nova-compute enabled up
```

For hygon22 this means the node is no longer PCR7-only. It is protected by the
combined PCR7 boot policy and PCR10 / IMA runtime policy path.

## What failed

The first hygon22 runtime policy failed for three independent reasons.

1. The evidence audit file was not a complete runtime policy input.

   `ima-hygon22.txt` contained host diagnostics, IMA policy snippets, PCR output,
   dmesg output, and only a sample of the IMA measurement list. It had 379 lines,
   while the live kernel IMA measurement count was over 7000. Feeding this file to
   `keylime-policy create runtime -m` produced parser errors.

2. The old runtime policy was stale.

   `hygon22-runtime-guard.json` was generated on 2026-07-09. After reboot,
   Secure Boot experiments, Docker/OpenStack service changes, and new IMA
   measurements, the verifier correctly reported:

   ```text
   ima.validation.ima-ng.runtime_policy_hash
   ima.validation.ima-ng.not_in_allowlist
   ```

3. Docker transient files must not be allowlisted.

   The verifier logs showed dynamic Docker paths such as:

   ```text
   /var/lib/docker/containers/<id>/.tmp-config.v2.json<random>
   /var/lib/docker/containers/<id>/.tmp-hostconfig.json<random>
   /var/lib/docker/containers/<id>/<id>-json.log
   /var/lib/docker/network/files/local-kv.db
   ```

   These files change names and hashes during normal Docker operation. They
   should be excluded from IMA runtime policy instead of learned as trusted
   stable files.

## Operational fix

Generate runtime policy from the live IMA list and an explicit exclude file:

```bash
/opt/keylime-openstack-sync/keylime-ima-runtime-policy-generate.sh hygon22
/opt/keylime-openstack-sync/keylime-ima-runtime-policy-apply.sh hygon22 bound
```

The generator:

```text
1. Resolves host -> IP from KEYLIME_AGENT_IP_MAP.
2. Reads /sys/kernel/security/ima/ascii_runtime_measurements over SSH.
3. Validates that digest algorithms are supported by Keylime.
4. Adds default Docker transient path excludes.
5. Runs keylime-policy create runtime inside the deployed keylime-tenant image.
6. Registers and binds the generated policy in tpm-pcr-policies.json.
```

The default exclude file is:

```text
/var/lib/keylime-openstack-sync/policies/runtime/<host>-runtime-exclude.txt
```

Default entries:

```text
^/var/lib/docker/containers/[0-9a-f]+/\.tmp-config\.v2\.json.*$
^/var/lib/docker/containers/[0-9a-f]+/\.tmp-hostconfig\.json.*$
^/var/lib/docker/containers/[0-9a-f]+/[0-9a-f]+-json\.log.*$
^/var/lib/docker/network/files/local-kv\.db$
^/tmp/tmp[A-Za-z0-9._-]+$
^/var/log/journal/[0-9a-f]+/.*\.journal$
```

Add more host-specific transient path patterns only after confirming the
verifier failure is a normal runtime artifact and not a real integrity drift.

## Correct validation sequence

Use this sequence after policy generation or after a host reboot:

```bash
docker compose run --rm keylime-tenant \
  -c cvstatus \
  -u "$HYGON22_UUID" \
  -v "$VERIFIER_IP" \
  -vp "$VERIFIER_PORT" \
  -r "$REGISTRAR_IP" \
  -rp "$REGISTRAR_PORT"

systemctl start keylime-openstack-sync.service || true
sleep 15

curl -s "http://172.31.100.10:8088/api/status?force=1" | python3 -m json.tool
```

Expected verifier state:

```text
operational_state = Get Quote
attestation_status = PASS
has_runtime_policy = 1
tpm_policy mask = 0x480
last_event_id = null
```

Expected OpenStack control-plane state:

```text
decision = PASS_FRESH
runtime = IMA pass
trait_present = true
service = enabled/up
conclusion = TRUSTED
```

## Production notes

The lab fix is intentionally conservative but is not a complete production
security model.

Required production hardening remains:

```text
1. Stop using broad TPM device permissions such as MODE=0666.
2. Pin Keylime container image digests instead of using latest.
3. Enable EK trust checks or an environment-specific EK allowlist check.
4. Enable measured boot policy once firmware/Secure Boot state is stable.
5. Generate IMA policy from controlled rootfs/package/signature sources, not
   by repeatedly learning from a mutable live host.
6. Introduce IMA/EVM signatures for protected files and use keyrings policy.
7. Keep Docker/container runtime transient paths out of allowlists.
8. Record policy generation inputs and generated JSON as audit artifacts.
```

## Main lesson

PCR7 tells whether the boot baseline is accepted. PCR10 / IMA tells whether
runtime measurements remain within policy. They must be treated as separate
layers and then combined into the OpenStack trust decision. For mutable compute
hosts, the runtime policy generator must distinguish stable protected files
from expected transient runtime artifacts.
