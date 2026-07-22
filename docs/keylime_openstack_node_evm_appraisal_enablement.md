# Node-side IMA appraisal and EVM enablement

Date: 2026-07-12

This runbook turns the current measurement-only compute nodes into nodes that
can produce host-side evidence for:

```text
IMA appraisal policy
.ima keyring verification
.evm keyring verification
security.ima signatures
security.evm signatures
Keylime/OpenStack scheduling decisions
```

The control plane remains conservative: a node gets
`CUSTOM_KEYLIME_RUNTIME_TRUSTED`, `CUSTOM_KEYLIME_TRUSTED`, and
`CUSTOM_KEYLIME_ATTESTED` only after Keylime boot/runtime evidence and the host
integrity probe both report passing evidence.

## Current lab baseline

The last observed state was:

```text
csri8   Ubuntu 6.8, Keylime boot/runtime pass, EVM missing
csri9   Ubuntu 6.8, Keylime boot/runtime pass, EVM missing
hygon22 Anolis 6.6, Keylime boot/runtime fail, EVM fail
```

All three nodes already have `ima_policy=tcb`. That means measurement exists,
but full appraisal/EVM trust is not yet enabled.

## Safety model

Use these stages. Do not skip directly to enforcement.

```text
Stage 0: diagnose current kernel, policy, keyring, xattr, and Keylime state
Stage 1: create signing material and install only the public X.509 cert on nodes
Stage 2: anchor the public cert in the kernel machine/secondary trust chain
Stage 3: load the cert into .ima and .evm keyrings
Stage 4: sign selected OpenStack/Kolla host runtime files
Stage 5: boot audit-only appraisal with ima_appraise=log
Stage 6: run the host integrity probe and sync the OpenStack trust plane
Stage 7: optional enforcement with ima_appraise=enforce after repeated green runs
```

`ima_appraise=log` is intentional for the first pass. It lets the kernel verify
appraisal paths and report failures without blocking compute services.

## Packages

Install the node-side tools first.

Ubuntu nodes, `csri8` and `csri9`:

```bash
apt-get update
apt-get install -y keyutils ima-evm-utils attr openssl
```

Anolis/RHEL-family node, `hygon22`:

```bash
dnf install -y keyutils ima-evm-utils attr openssl || \
  yum install -y keyutils ima-evm-utils attr openssl
```

Verify:

```bash
command -v keyctl
command -v evmctl
command -v openssl
```

## Install the helper

From `csri10`:

```bash
for entry in csri8=172.31.100.8 csri9=172.31.100.9 hygon22=172.31.100.22; do
  ip="${entry#*=}"
  scp /opt/keylime-openstack/deploy/examples/experimental/evm/keylime-node-evm-appraisal.sh \
    root@"$ip":/usr/local/sbin/keylime-node-evm-appraisal
  ssh root@"$ip" chmod 0755 /usr/local/sbin/keylime-node-evm-appraisal
  ssh root@"$ip" /usr/local/sbin/keylime-node-evm-appraisal status
done
```

## Create signing material

Create one lab signing keypair on a controlled host. For the current lab,
`csri10` is acceptable. In production, keep this key on a dedicated signing
host or HSM-backed workflow.

```bash
mkdir -p /root/keylime-openstack-ima-evm-signing
cd /opt/keylime-openstack

KEYLIME_NODE_EVM_ROOT=/root/keylime-openstack-ima-evm-signing \
  deploy/examples/experimental/evm/keylime-node-evm-appraisal.sh create-keypair
```

The generated files are:

```text
/root/keylime-openstack-ima-evm-signing/private/keylime-openstack-ima-evm.key
/root/keylime-openstack-ima-evm-signing/keylime-openstack-ima-evm.pem
/root/keylime-openstack-ima-evm-signing/keylime-openstack-ima-evm.der
```

Only copy the DER public certificate to compute nodes for normal operation.
The private key is needed only while signing files.

## Install and load the public cert on each compute node

From `csri10`:

```bash
for entry in csri8=172.31.100.8 csri9=172.31.100.9 hygon22=172.31.100.22; do
  ip="${entry#*=}"

  scp /root/keylime-openstack-ima-evm-signing/keylime-openstack-ima-evm.der \
    root@"$ip":/root/keylime-openstack-ima-evm.der

  ssh root@"$ip" \
    /usr/local/sbin/keylime-node-evm-appraisal install-public-cert \
      --cert /root/keylime-openstack-ima-evm.der
done
```

At this point the certificate is only present on disk. Many production kernels
restrict `.ima` and `.evm` to certificates accepted by the kernel integrity
keyring restriction. If `load-keys` reports:

```text
add_key: Required key not available
```

the kernel rejected the certificate for IMA/EVM keyring use.

Run diagnostics on each node:

```bash
/usr/local/sbin/keylime-node-evm-appraisal trust-diagnostics
```

For `csri8` and `csri9`, MOK enrollment proves that shim/firmware accepts the
certificate, but it may still be insufficient for `.ima` and `.evm`. The current
Ubuntu 6.8 nodes have:

```text
CONFIG_IMA_KEYRINGS_PERMIT_SIGNED_BY_BUILTIN_OR_SECONDARY is not set
CONFIG_IMA_LOAD_X509 is not set
CONFIG_EVM_LOAD_X509 is not set
```

In the current lab, after MOK enrollment the project certificate appears in
`.platform`, while `.machine` remains empty and `keyctl padd ... %:.ima` still
returns `add_key: Required key not available`. Treat that as a kernel policy
block, not as a user error.

The MOK enrollment flow is still useful to record and verify this state:

On `csri8` and `csri9`:

```bash
command -v mokutil || true
mokutil --sb-state || true
mokutil --import /etc/ima/keys/keylime-openstack-ima-evm.der
reboot
```

During the next boot, enter the MOK manager, enroll the key, confirm the
password, and continue booting. After the node returns:

```bash
mokutil --test-key /etc/ima/keys/keylime-openstack-ima-evm.der || true
/usr/local/sbin/keylime-node-evm-appraisal trust-diagnostics
/usr/local/sbin/keylime-node-evm-appraisal load-keys
```

Then install the boot-time key loader:

```bash
/usr/local/sbin/keylime-node-evm-appraisal install-key-loader
systemctl start keylime-node-evm-keyload.service
/usr/local/sbin/keylime-node-evm-appraisal status
```

Expected result:

```text
keyctl list %:.ima  includes the Keylime/OpenStack signing certificate
keyctl list %:.evm  includes the Keylime/OpenStack signing certificate
```

If the key appears only under `%:.platform` and `load-keys` still fails, do not
continue to `sign-paths` or `ima_appraise=log`. The practical options for
csri8/csri9 are:

```text
1. boot a kernel that permits IMA keyring certificates signed by builtin or
   secondary trusted keys;
2. boot a kernel with CONFIG_IMA_LOAD_X509 / CONFIG_EVM_LOAD_X509 and load the
   project cert from initramfs, like the hygon22 route;
3. use a site certificate/key whose IMA/EVM signing certificate is accepted by
   the Ubuntu kernel's builtin integrity trust chain.
```

Do not treat Ubuntu's `Build time autogenerated kernel key` as sufficient for
this project. The loaded key must correspond to
`keylime-openstack-ima-evm.der`, or appraisal will not validate signatures made
by the project signing key.

For `hygon22`, do not use the Ubuntu MOK path as the primary route. Its current
kernel has `CONFIG_INTEGRITY_MACHINE_KEYRING` disabled, but has these compiled
certificate paths:

```text
CONFIG_IMA_LOAD_X509=y
CONFIG_IMA_X509_PATH="/etc/keys/x509_ima.der"
CONFIG_EVM_LOAD_X509=y
CONFIG_EVM_X509_PATH="/etc/keys/x509_evm.der"
```

Install the same public certificate into those paths, include those files in
the dracut initramfs, and reboot:

```bash
/usr/local/sbin/keylime-node-evm-appraisal install-compiled-x509-paths
reboot
```

The helper writes:

```text
/etc/keys/x509_ima.der
/etc/keys/x509_evm.der
/etc/dracut.conf.d/99-keylime-openstack-ima-evm.conf
```

and rebuilds the current kernel's initramfs with `dracut -f --kver $(uname -r)`.
This is required because the kernel attempts to open
`/etc/keys/x509_ima.der` and `/etc/keys/x509_evm.der` before the real root
filesystem is fully available.

After reboot:

```bash
/usr/local/sbin/keylime-node-evm-appraisal trust-diagnostics
/usr/local/sbin/keylime-node-evm-appraisal status
```

If `hygon22` still has empty `.ima` or `.evm` keyrings after reboot, inspect
dmesg. The error below means the certs were not present in initramfs:

```text
integrity: Unable to open file: /etc/keys/x509_ima.der (-2)
integrity: Unable to open file: /etc/keys/x509_evm.der (-2)
```

If the files are present in initramfs but the kernel reports
`Problem loading X.509 certificate -126`, the lab self-signed certificate is
being rejected by the kernel integrity trust-chain rules. For an experiment-only
route on `hygon22`, use:

```text
docs/keylime_openstack_hygon22_experimental_kernel.md
```

If neither MOK enrollment nor compiled X.509 paths are possible, use a
site-approved certificate that already chains to the kernel builtin/secondary
trust keyring, or build the public certificate into the kernel/initramfs trust
path. Do not continue to signing or GRUB appraisal changes until `.ima` and
`.evm` both contain the project signing certificate.

## Sign protected host runtime files

The first path list is intentionally small. It targets host runtime binaries
that matter to Kolla/OpenStack compute behavior. It does not try to sign Docker
overlay contents.

On each compute node:

```bash
/usr/local/sbin/keylime-node-evm-appraisal write-default-path-list
cat /etc/keylime-openstack/ima-evm/protected-paths.txt
```

For the lab, copy the private key temporarily, sign, then remove it. From
`csri10`:

```bash
for entry in csri8=172.31.100.8 csri9=172.31.100.9 hygon22=172.31.100.22; do
  ip="${entry#*=}"

  ssh root@"$ip" install -d -m 0700 /root/keylime-openstack-ima-evm-signing
  scp /root/keylime-openstack-ima-evm-signing/private/keylime-openstack-ima-evm.key \
    root@"$ip":/root/keylime-openstack-ima-evm-signing/
  scp /root/keylime-openstack-ima-evm-signing/keylime-openstack-ima-evm.pem \
    root@"$ip":/root/keylime-openstack-ima-evm-signing/

  ssh root@"$ip" \
    KEYLIME_NODE_EVM_PRIVATE_KEY=/root/keylime-openstack-ima-evm-signing/keylime-openstack-ima-evm.key \
    KEYLIME_NODE_EVM_CERT_PEM=/root/keylime-openstack-ima-evm-signing/keylime-openstack-ima-evm.pem \
    /usr/local/sbin/keylime-node-evm-appraisal sign-paths --dry-run

  ssh root@"$ip" \
    KEYLIME_NODE_EVM_PRIVATE_KEY=/root/keylime-openstack-ima-evm-signing/keylime-openstack-ima-evm.key \
    KEYLIME_NODE_EVM_CERT_PEM=/root/keylime-openstack-ima-evm-signing/keylime-openstack-ima-evm.pem \
    /usr/local/sbin/keylime-node-evm-appraisal sign-paths

  ssh root@"$ip" /usr/local/sbin/keylime-node-evm-appraisal verify-paths
  ssh root@"$ip" shred -u /root/keylime-openstack-ima-evm-signing/keylime-openstack-ima-evm.key
done
```

Expected result for existing paths:

```text
security.ima=present:<length>
security.evm=present:<length>
```

If `evmctl` on the target release requires explicit certificate arguments, run
the same command with:

```bash
KEYLIME_NODE_EVM_USE_CERT_ARG=true
```

## Enable audit-only appraisal

Make the final boot command line contain exactly one `ima_policy` argument:

```text
ima_policy=tcb,appraise_tcb ima_appraise=log
```

Ubuntu `csri8` and `csri9`:

```bash
cp -a /etc/default/grub /root/grub.before-keylime-evm
vi /etc/default/grub
update-grub
reboot
```

After editing, `GRUB_CMDLINE_LINUX` should contain:

```text
ima_policy=tcb,appraise_tcb ima_appraise=log
```

Anolis/RHEL-family `hygon22`:

```bash
grubby --update-kernel=ALL --remove-args="ima_policy ima_appraise"
grubby --update-kernel=ALL --args="ima_policy=tcb,appraise_tcb ima_appraise=log"
reboot
```

After reboot:

```bash
cat /proc/cmdline
/usr/local/sbin/keylime-node-evm-appraisal status
```

Stop before enforcement if dmesg contains appraisal failures for core runtime
services or if `.ima` / `.evm` keyrings are empty after reboot.

## Report evidence to the trust plane

From `csri10`:

```bash
TOKEN=$(grep '^ADMIN_TOKEN=' /etc/keylime-openstack/keylime-openstack.env | cut -d= -f2-)

for entry in csri8=172.31.100.8 csri9=172.31.100.9 hygon22=172.31.100.22; do
  host="${entry%%=*}"
  ip="${entry#*=}"

  scp /opt/keylime-openstack/deploy/scripts/keylime-host-integrity-probe.py \
    root@"$ip":/usr/local/sbin/keylime-host-integrity-probe
  ssh root@"$ip" chmod 0755 /usr/local/sbin/keylime-host-integrity-probe

  ssh root@"$ip" \
    KEYLIME_OPENSTACK_API_URL=http://172.31.100.10:8088 \
    KEYLIME_OPENSTACK_ADMIN_TOKEN="$TOKEN" \
    /usr/local/sbin/keylime-host-integrity-probe --hostname "$host"
done

curl -fsS -X POST \
  -H "X-Admin-Token: $TOKEN" \
  http://127.0.0.1:8088/api/tasks/sync

curl -fsS http://127.0.0.1:8088/api/overview
```

Target state:

```text
boot_status=pass
runtime_status=pass
evm_status=pass
desired_traits includes:
  CUSTOM_KEYLIME_BOOT_TRUSTED
  CUSTOM_KEYLIME_RUNTIME_TRUSTED
  CUSTOM_KEYLIME_TRUSTED
  CUSTOM_KEYLIME_ATTESTED
```

For `hygon22`, fix the Keylime IMA runtime policy failure first if boot/runtime
still report `fail`; EVM passing alone is not enough for trusted scheduling.

## Optional enforcement

Only move from audit-only to enforcement after multiple successful reboots and
after OpenStack compute services remain healthy:

```text
ima_policy=tcb,appraise_tcb ima_appraise=enforce
```

Ubuntu:

```bash
cp -a /etc/default/grub /root/grub.before-keylime-evm-enforce
vi /etc/default/grub
update-grub
reboot
```

Anolis/RHEL-family:

```bash
grubby --update-kernel=ALL --remove-args="ima_policy ima_appraise"
grubby --update-kernel=ALL --args="ima_policy=tcb,appraise_tcb ima_appraise=enforce"
reboot
```

Keep an out-of-band console or rescue path available before enforcing. If a
node fails to start services, remove `ima_appraise=enforce`, boot back into
`ima_appraise=log`, sign missing files, then retry.

## OpenStack enforcement

Do not set `OPENSTACK_ENFORCEMENT_ENABLED=true` until at least one compute node
has:

```text
boot_status=pass
runtime_status=pass
evm_status=pass
service_status=enabled
service_state=up
```

After that, enable OpenStack enforcement on `csri10`:

```bash
vi /etc/keylime-openstack/keylime-openstack.env

docker compose \
  --env-file /etc/keylime-openstack/keylime-openstack.env \
  -f /opt/keylime-openstack/deploy/compose/keylime-openstack-control-plane.yml \
  up -d api worker
```

Then run one sync and inspect traits before scheduling trusted workloads.
