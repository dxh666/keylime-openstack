# Hygon22 experimental IMA/EVM kernel route

Date: 2026-07-12

This runbook is an experiment-only route for `hygon22`. It is intended to prove
the Keylime/OpenStack trust-plane flow end to end before the production kernel
trust-chain work is finalized.

Do not apply this route to `csri8` or `csri9`, and do not treat it as the final
production posture.

## Why this is needed

`hygon22` currently has the useful boot-time X.509 loading options:

```text
CONFIG_IMA_LOAD_X509=y
CONFIG_IMA_X509_PATH="/etc/keys/x509_ima.der"
CONFIG_EVM_LOAD_X509=y
CONFIG_EVM_X509_PATH="/etc/keys/x509_evm.der"
```

The certificate files are present in initramfs, but the kernel rejects the lab
self-signed certificate:

```text
integrity: Loading X.509 certificate: /etc/keys/x509_ima.der
integrity: Problem loading X.509 certificate -126
integrity: Loading X.509 certificate: /etc/keys/x509_evm.der
integrity: Problem loading X.509 certificate -126
```

For an experiment, build a temporary kernel that trusts the lab public
certificate by compiling it into the kernel builtin trusted keyring:

```text
CONFIG_INTEGRITY_TRUSTED_KEYRING=y
CONFIG_SYSTEM_TRUSTED_KEYS="certs/keylime-openstack-ima-evm.pem"
CONFIG_IMA_LOAD_X509=y
CONFIG_EVM_LOAD_X509=y
CONFIG_IMA_X509_PATH="/etc/keys/x509_ima.der"
CONFIG_EVM_X509_PATH="/etc/keys/x509_evm.der"
```

Do not disable `CONFIG_INTEGRITY_TRUSTED_KEYRING`: `CONFIG_IMA_LOAD_X509` and
`CONFIG_EVM_LOAD_X509` depend on it, so `make olddefconfig` will silently drop
the boot-time X.509 loading options if the trusted keyring is disabled.

This allows the lab self-signed certificate to load for IMA/EVM verification
because the same public certificate is already trusted by the kernel. It is
still an experiment-only shortcut because the lab certificate is self-signed
and manually compiled into a one-off host kernel.

## Pre-flight

Run on `hygon22`:

```bash
uname -r
cat /etc/os-release
cp -a /boot/config-$(uname -r) /root/config-$(uname -r).before-keylime-lab
grubby --default-kernel
grubby --info=/boot/vmlinuz-$(uname -r)
```

Keep a console/BMC/iKVM path available. Do not continue if there is no way to
select the old kernel from GRUB.

## Install build dependencies

Use the site's package mirror if direct repository access is restricted:

```bash
dnf install -y \
  bc bison dwarves elfutils-libelf-devel flex gcc git hmaccalc make ncurses-devel \
  openssl openssl-devel perl python3 redhat-rpm-config rpm-build rpmdevtools rsync \
  grubby dracut keyutils ima-evm-utils attr
```

If the package manager cannot reach repositories, copy equivalent RPMs from the
site mirror before proceeding.

## Obtain matching kernel source

Prefer the exact source package for the running kernel:

```bash
mkdir -p /root/keylime-kernel-lab
cd /root/keylime-kernel-lab

dnf download --source kernel-$(uname -r) || \
dnf download --source kernel
```

If `dnf download --source` is unavailable:

```bash
dnf install -y dnf-plugins-core
dnf download --source kernel-$(uname -r) || dnf download --source kernel
```

Install and prepare the source package:

```bash
rpm -ivh ./*.src.rpm
cd /root/rpmbuild/SPECS
rpmbuild -bp kernel*.spec
```

Find the prepared Linux source tree:

```bash
find /root/rpmbuild/BUILD -maxdepth 4 -type f -name Makefile | sort
```

Enter the directory that contains the kernel top-level `Makefile`.

## Build the experiment kernel

Inside the kernel source tree:

```bash
cp -a /boot/config-$(uname -r) .config

mkdir -p certs
openssl x509 \
  -inform DER \
  -in /etc/keys/x509_ima.der \
  -out certs/keylime-openstack-ima-evm.pem
openssl x509 \
  -in certs/keylime-openstack-ima-evm.pem \
  -noout -subject -issuer -fingerprint -sha256

scripts/config --enable INTEGRITY_TRUSTED_KEYRING
scripts/config --enable INTEGRITY_ASYMMETRIC_KEYS
scripts/config --enable SYSTEM_TRUSTED_KEYRING
scripts/config --enable SECONDARY_TRUSTED_KEYRING
scripts/config --set-str SYSTEM_TRUSTED_KEYS "certs/keylime-openstack-ima-evm.pem"
scripts/config --set-str SYSTEM_REVOCATION_KEYS ""
scripts/config --enable IMA_LOAD_X509
scripts/config --enable EVM_LOAD_X509
scripts/config --set-str IMA_X509_PATH "/etc/keys/x509_ima.der"
scripts/config --set-str EVM_X509_PATH "/etc/keys/x509_evm.der"

make olddefconfig
grep -E 'CONFIG_INTEGRITY_TRUSTED_KEYRING|CONFIG_SYSTEM_TRUSTED_KEYS|CONFIG_IMA_LOAD_X509|CONFIG_EVM_LOAD_X509|CONFIG_IMA_X509_PATH|CONFIG_EVM_X509_PATH' .config

make -j"$(nproc)" LOCALVERSION=-keylime-lab
make modules_install LOCALVERSION=-keylime-lab
make install LOCALVERSION=-keylime-lab
```

Confirm the kernel release string:

```bash
make kernelrelease LOCALVERSION=-keylime-lab
ls -lh /boot/*keylime-lab*
```

## Ensure the IMA/EVM certs are in initramfs

Use the project helper:

```bash
/usr/local/sbin/keylime-node-evm-appraisal install-compiled-x509-paths
```

If the helper rebuilt only the running kernel initramfs, rebuild the lab kernel
initramfs explicitly:

```bash
LAB_KERNEL="$(ls /lib/modules | grep keylime-lab | tail -1)"
dracut -f "/boot/initramfs-${LAB_KERNEL}.img" "${LAB_KERNEL}"
lsinitrd "/boot/initramfs-${LAB_KERNEL}.img" | grep -E 'etc/keys/x509_(ima|evm)\.der'
```

## Boot the lab kernel

Do not remove the original kernel. Set the lab kernel as the next/default boot
only after confirming the files exist:

```bash
LAB_KERNEL="$(ls /lib/modules | grep keylime-lab | tail -1)"
grubby --info="/boot/vmlinuz-${LAB_KERNEL}"
grubby --set-default "/boot/vmlinuz-${LAB_KERNEL}"
sync
reboot
```

Rollback from the console if needed:

```bash
grubby --set-default /boot/vmlinuz-6.6.102-5.3.3.an23.x86_64
reboot
```

## Verify certificate loading

After booting the lab kernel:

```bash
uname -r
grep -E 'CONFIG_INTEGRITY_TRUSTED_KEYRING|CONFIG_SYSTEM_TRUSTED_KEYS|CONFIG_IMA_LOAD_X509|CONFIG_EVM_LOAD_X509' /boot/config-$(uname -r)
journalctl -k -b --no-pager | grep -Ei 'integrity|ima|evm|x509|/etc/keys' | tail -160
keyctl list %:.builtin_trusted_keys
keyctl list %:.ima
keyctl list %:.evm
```

The target is:

```text
integrity: Loaded X.509 cert 'keylime-openstack-ima-evm: ...'
%:.ima contains keylime-openstack-ima-evm
%:.evm contains keylime-openstack-ima-evm
```

Only after this succeeds should you continue with `sign-paths`.

## Continue the trust-plane experiment

Follow the main node EVM/appraisal runbook from the signing stage:

```text
docs/keylime_openstack_node_evm_appraisal_enablement.md
```

Keep `ima_appraise=log` first. Do not use `ima_appraise=enforce` until the
Keylime/OpenStack control plane shows stable host evidence and the compute
service remains healthy after reboot.
