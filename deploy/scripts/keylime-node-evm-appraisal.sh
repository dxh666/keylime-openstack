#!/usr/bin/env bash
# Node-side helper for staged IMA appraisal and EVM signature enablement.

set -Eeuo pipefail

INSTALL_ROOT="${KEYLIME_NODE_EVM_ROOT:-/etc/keylime-openstack/ima-evm}"
PRIVATE_DIR="${KEYLIME_NODE_EVM_PRIVATE_DIR:-$INSTALL_ROOT/private}"
KEY_DIR="${KEYLIME_NODE_EVM_KEY_DIR:-/etc/ima/keys}"
PRIVATE_KEY="${KEYLIME_NODE_EVM_PRIVATE_KEY:-$PRIVATE_DIR/keylime-openstack-ima-evm.key}"
CERT_PEM="${KEYLIME_NODE_EVM_CERT_PEM:-$INSTALL_ROOT/keylime-openstack-ima-evm.pem}"
CERT_DER="${KEYLIME_NODE_EVM_CERT_DER:-$INSTALL_ROOT/keylime-openstack-ima-evm.der}"
PUBLIC_DER="${KEYLIME_NODE_EVM_PUBLIC_DER:-$KEY_DIR/keylime-openstack-ima-evm.der}"
COMPILED_IMA_X509_PATH="${KEYLIME_NODE_EVM_COMPILED_IMA_X509_PATH:-/etc/keys/x509_ima.der}"
COMPILED_EVM_X509_PATH="${KEYLIME_NODE_EVM_COMPILED_EVM_X509_PATH:-/etc/keys/x509_evm.der}"
DRACUT_CONF="${KEYLIME_NODE_EVM_DRACUT_CONF:-/etc/dracut.conf.d/99-keylime-openstack-ima-evm.conf}"
PATH_LIST="${KEYLIME_NODE_EVM_PATH_LIST:-$INSTALL_ROOT/protected-paths.txt}"
MAIN_BIN="${KEYLIME_NODE_EVM_MAIN_BIN:-/usr/local/sbin/keylime-node-evm-appraisal}"
KEYLOAD_BIN="${KEYLIME_NODE_EVM_KEYLOAD_BIN:-/usr/local/sbin/keylime-node-evm-load-keys}"
KEYLOAD_UNIT="${KEYLIME_NODE_EVM_KEYLOAD_UNIT:-/etc/systemd/system/keylime-node-evm-keyload.service}"

DEFAULT_SUBJECT="${KEYLIME_NODE_EVM_SUBJECT:-/CN=keylime-openstack-ima-evm/}"
DEFAULT_DAYS="${KEYLIME_NODE_EVM_DAYS:-3650}"
DEFAULT_BOOT_ARGS_LOG="ima_policy=tcb,appraise_tcb ima_appraise=log"
DEFAULT_BOOT_ARGS_ENFORCE="ima_policy=tcb,appraise_tcb ima_appraise=enforce"

DEFAULT_PROTECTED_PATHS=(
  /usr/bin/runc
  /usr/bin/containerd
  /usr/bin/dockerd
  /usr/bin/qemu-system-x86_64
  /usr/bin/qemu-system-x86_64-spice
  /usr/bin/virsh
  /usr/sbin/libvirtd
  /usr/sbin/virtqemud
  /usr/bin/python3
  /usr/bin/bash
)

usage() {
  cat <<'EOF'
Usage:
  keylime-node-evm-appraisal.sh <command> [args]

Commands:
  status
      Print current kernel, policy, keyring, xattr, and integrity dmesg state.

  create-keypair
      Create a lab IMA/EVM signing keypair under /etc/keylime-openstack/ima-evm.
      Keep the private key off compute nodes in production.

  install-public-cert --cert <cert.pem|cert.der>
      Install the public X.509 certificate as /etc/ima/keys/keylime-openstack-ima-evm.der.

  load-keys
      Load the public certificate into the .ima and .evm keyrings.

  trust-diagnostics
      Print integrity keyring restrictions and machine trust-ring state.

  install-compiled-x509-paths
      Copy the public cert to kernel CONFIG_IMA_X509_PATH / CONFIG_EVM_X509_PATH
      and include it in dracut initramfs when dracut is available.

  install-key-loader
      Install this helper and a systemd oneshot that loads the public certificate early at boot.

  write-default-path-list
      Write a small protected path list for OpenStack/Kolla compute hosts.

  sign-paths [--path-list <file>] [--key <private-key>] [--cert <cert.pem>] [--dry-run]
      Apply security.ima and security.evm signatures to protected regular files.

  verify-paths [--path-list <file>]
      Check whether protected files have security.ima and security.evm xattrs.

  print-boot-args
      Print Ubuntu and Anolis/RHEL-family boot argument commands for log/enforce stages.

Environment:
  KEYLIME_NODE_EVM_ROOT
  KEYLIME_NODE_EVM_PRIVATE_KEY
  KEYLIME_NODE_EVM_CERT_PEM
  KEYLIME_NODE_EVM_PUBLIC_DER
  KEYLIME_NODE_EVM_PATH_LIST
  KEYLIME_NODE_EVM_IMA_SIGN_EXTRA
  KEYLIME_NODE_EVM_EVM_SIGN_EXTRA
  KEYLIME_NODE_EVM_USE_CERT_ARG=true to pass --cert to evmctl on hosts that require it
EOF
}

main() {
  local command="${1:-}"
  case "$command" in
    status)
      cmd_status
      ;;
    create-keypair)
      cmd_create_keypair
      ;;
    install-public-cert)
      shift
      cmd_install_public_cert "$@"
      ;;
    load-keys)
      cmd_load_keys
      ;;
    trust-diagnostics)
      cmd_trust_diagnostics
      ;;
    install-compiled-x509-paths)
      cmd_install_compiled_x509_paths
      ;;
    install-key-loader)
      cmd_install_key_loader
      ;;
    write-default-path-list)
      cmd_write_default_path_list
      ;;
    sign-paths)
      shift
      cmd_sign_paths "$@"
      ;;
    verify-paths)
      shift
      cmd_verify_paths "$@"
      ;;
    print-boot-args)
      cmd_print_boot_args
      ;;
    -h|--help|help|"")
      usage
      ;;
    *)
      echo "unknown command: $command" >&2
      usage >&2
      return 2
      ;;
  esac
}

need_root() {
  if [ "$(id -u)" != "0" ]; then
    echo "this command must run as root" >&2
    return 1
  fi
}

require_cmd() {
  local command="$1"
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "missing required command: $command" >&2
    return 1
  fi
}

cmd_status() {
  echo "== kernel =="
  uname -a || true
  echo

  echo "== cmdline =="
  cat /proc/cmdline || true
  echo

  echo "== ima policy appraise lines =="
  if [ -r /sys/kernel/security/ima/policy ]; then
    grep -n appraise /sys/kernel/security/ima/policy || true
  else
    echo "ima policy is not readable"
  fi
  echo

  echo "== keyrings =="
  if command -v keyctl >/dev/null 2>&1; then
    keyctl list %:.ima 2>&1 || true
    keyctl list %:.evm 2>&1 || true
  else
    echo "keyctl is not installed"
    grep -E '(\.ima|\.evm)' /proc/keys 2>/dev/null || true
  fi
  echo

  echo "== protected path xattrs =="
  ensure_path_list_exists quiet
  while IFS= read -r path; do
    is_usable_path_line "$path" || continue
    print_xattr_state "$path"
  done < "$PATH_LIST"
  echo

  echo "== integrity dmesg tail =="
  dmesg 2>/dev/null | grep -Ei 'ima|evm|appraisal|x509|keyring' | tail -80 || true
}

cmd_create_keypair() {
  need_root
  require_cmd openssl

  install -d -m 0755 "$INSTALL_ROOT"
  install -d -m 0700 "$PRIVATE_DIR"
  umask 077

  if [ -e "$PRIVATE_KEY" ] || [ -e "$CERT_PEM" ] || [ -e "$CERT_DER" ]; then
    echo "refusing to overwrite existing key material under $INSTALL_ROOT" >&2
    return 1
  fi

  openssl req \
    -new \
    -x509 \
    -sha256 \
    -newkey rsa:3072 \
    -nodes \
    -days "$DEFAULT_DAYS" \
    -subj "$DEFAULT_SUBJECT" \
    -keyout "$PRIVATE_KEY" \
    -out "$CERT_PEM"

  chmod 0600 "$PRIVATE_KEY"
  openssl x509 -in "$CERT_PEM" -outform DER -out "$CERT_DER"
  chmod 0644 "$CERT_PEM" "$CERT_DER"

  echo "created private key: $PRIVATE_KEY"
  echo "created public cert PEM: $CERT_PEM"
  echo "created public cert DER: $CERT_DER"
}

cmd_install_public_cert() {
  need_root
  require_cmd openssl

  local source_cert=""
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --cert)
        source_cert="${2:-}"
        shift 2
        ;;
      *)
        echo "unknown install-public-cert option: $1" >&2
        return 2
        ;;
    esac
  done

  if [ -z "$source_cert" ] || [ ! -f "$source_cert" ]; then
    echo "usage: install-public-cert --cert <cert.pem|cert.der>" >&2
    return 2
  fi

  install -d -m 0755 "$KEY_DIR"
  if openssl x509 -in "$source_cert" -outform DER -out "$PUBLIC_DER" 2>/dev/null; then
    :
  elif openssl x509 -inform DER -in "$source_cert" -outform DER -out "$PUBLIC_DER" 2>/dev/null; then
    :
  else
    echo "failed to parse X.509 certificate: $source_cert" >&2
    return 1
  fi
  chmod 0644 "$PUBLIC_DER"
  echo "installed public cert: $PUBLIC_DER"
}

cmd_load_keys() {
  need_root
  require_cmd keyctl

  if [ ! -f "$PUBLIC_DER" ]; then
    echo "public certificate not found: $PUBLIC_DER" >&2
    return 1
  fi
  if [ ! -d /sys/kernel/security/ima ] || [ ! -e /sys/kernel/security/evm ]; then
    echo "securityfs IMA/EVM paths are not available" >&2
    return 1
  fi

  load_keyring_cert %:.ima "$PUBLIC_DER"
  load_keyring_cert %:.evm "$PUBLIC_DER"
}

load_keyring_cert() {
  local ring="$1"
  local cert="$2"
  echo "loading $cert into $ring"
  if keyctl padd asymmetric "" "$ring" < "$cert"; then
    keyctl list "$ring" || true
    return 0
  fi

  echo "failed to load $cert into $ring" >&2
  local listing
  listing="$(keyctl list "$ring" 2>&1 || true)"
  echo "$listing"
  echo "The kernel rejected this certificate for $ring." >&2
  echo "Usually this means the certificate is not accepted by the kernel integrity keyring restriction." >&2
  echo "On Ubuntu stock kernels, a MOK certificate may appear in .platform but still be rejected by .ima/.evm." >&2
  echo "Run: keylime-node-evm-appraisal trust-diagnostics" >&2
  return 1
}

cmd_trust_diagnostics() {
  need_root
  echo "== kernel =="
  uname -a || true
  echo

  echo "== cmdline =="
  cat /proc/cmdline || true
  echo

  echo "== integrity kernel config =="
  print_kernel_config
  echo

  echo "== public cert =="
  if [ -f "$PUBLIC_DER" ]; then
    openssl x509 -inform DER -in "$PUBLIC_DER" -noout -subject -issuer -fingerprint -sha256 2>&1 || true
  else
    echo "public cert not found: $PUBLIC_DER"
  fi
  echo

  echo "== mokutil =="
  if command -v mokutil >/dev/null 2>&1; then
    mokutil --sb-state 2>&1 || true
    if [ -f "$PUBLIC_DER" ]; then
      mokutil --test-key "$PUBLIC_DER" 2>&1 || true
    fi
  else
    echo "mokutil is not installed"
  fi
  echo

  echo "== kernel trust keyrings =="
  if command -v keyctl >/dev/null 2>&1; then
    for ring in %:.builtin_trusted_keys %:.secondary_trusted_keys %:.machine %:.platform %:.ima %:.evm; do
      echo "-- $ring --"
      keyctl list "$ring" 2>&1 || true
    done
  else
    echo "keyctl is not installed"
  fi
  echo

  echo "== recent integrity key errors =="
  dmesg 2>/dev/null | grep -Ei 'integrity|ima|evm|asymmetric|x509|keyring|mok|certificate' | tail -120 || true
}

cmd_install_compiled_x509_paths() {
  need_root

  if [ ! -f "$PUBLIC_DER" ]; then
    echo "public certificate not found: $PUBLIC_DER" >&2
    return 1
  fi

  install -d -m 0755 "$(dirname "$COMPILED_IMA_X509_PATH")"
  install -m 0644 "$PUBLIC_DER" "$COMPILED_IMA_X509_PATH"
  install -m 0644 "$PUBLIC_DER" "$COMPILED_EVM_X509_PATH"
  restorecon -Rv "$(dirname "$COMPILED_IMA_X509_PATH")" 2>/dev/null || true

  echo "installed IMA X.509 cert: $COMPILED_IMA_X509_PATH"
  echo "installed EVM X.509 cert: $COMPILED_EVM_X509_PATH"

  if command -v dracut >/dev/null 2>&1; then
    install -d -m 0755 "$(dirname "$DRACUT_CONF")"
    cat > "$DRACUT_CONF" <<EOF
# Added by keylime-node-evm-appraisal.
install_items+=" ${COMPILED_IMA_X509_PATH} ${COMPILED_EVM_X509_PATH} "
EOF
    chmod 0644 "$DRACUT_CONF"
    echo "installed dracut config: $DRACUT_CONF"
    dracut -f --kver "$(uname -r)"
    echo "rebuilt initramfs for kernel: $(uname -r)"
    if command -v lsinitrd >/dev/null 2>&1; then
      lsinitrd | grep -E "($(basename "$COMPILED_IMA_X509_PATH")|$(basename "$COMPILED_EVM_X509_PATH"))" || true
    fi
  else
    echo "dracut is not installed; add these files to initramfs manually before rebooting" >&2
  fi

  echo "reboot is required for kernels that load these paths at boot"
}

print_kernel_config() {
  local config="/boot/config-$(uname -r)"
  local patterns='CONFIG_(INTEGRITY|IMA|EVM|SYSTEM_TRUSTED|SECONDARY_TRUSTED|INTEGRITY_MACHINE|LOAD_UEFI|KEYS|ASYMMETRIC)'
  if [ -r "$config" ]; then
    grep -E "$patterns" "$config" || true
    return 0
  fi
  if [ -r /proc/config.gz ] && command -v zgrep >/dev/null 2>&1; then
    zgrep -E "$patterns" /proc/config.gz || true
    return 0
  fi
  echo "kernel config not readable from $config or /proc/config.gz"
}

cmd_install_key_loader() {
  need_root

  install -d -m 0755 "$(dirname "$MAIN_BIN")"
  install -m 0755 "$0" "$MAIN_BIN"

  install -d -m 0755 "$(dirname "$KEYLOAD_BIN")"
  cat > "$KEYLOAD_BIN" <<EOF
#!/usr/bin/env bash
set -Eeuo pipefail
export KEYLIME_NODE_EVM_PUBLIC_DER="${PUBLIC_DER}"
exec "${MAIN_BIN}" load-keys
EOF
  chmod 0755 "$KEYLOAD_BIN"

  cat > "$KEYLOAD_UNIT" <<EOF
[Unit]
Description=Load Keylime OpenStack IMA/EVM public key
DefaultDependencies=no
After=local-fs.target sys-kernel-security.mount systemd-modules-load.service
Before=basic.target docker.service containerd.service libvirtd.service virtqemud.service nova-compute.service
ConditionPathExists=${PUBLIC_DER}

[Service]
Type=oneshot
ExecStart=${KEYLOAD_BIN}
RemainAfterExit=yes

[Install]
WantedBy=sysinit.target
EOF

  systemctl daemon-reload
  systemctl enable keylime-node-evm-keyload.service
  echo "installed helper: $MAIN_BIN"
  echo "installed key loader: $KEYLOAD_UNIT"
  echo "run now: systemctl start keylime-node-evm-keyload.service"
}

cmd_write_default_path_list() {
  need_root
  install -d -m 0755 "$INSTALL_ROOT"
  : > "$PATH_LIST"
  for path in "${DEFAULT_PROTECTED_PATHS[@]}"; do
    printf '%s\n' "$path" >> "$PATH_LIST"
  done
  chmod 0644 "$PATH_LIST"
  echo "wrote protected path list: $PATH_LIST"
}

cmd_sign_paths() {
  need_root
  require_cmd evmctl

  local path_list="$PATH_LIST"
  local private_key="$PRIVATE_KEY"
  local cert_pem="$CERT_PEM"
  local dry_run="false"

  while [ "$#" -gt 0 ]; do
    case "$1" in
      --path-list)
        path_list="${2:-}"
        shift 2
        ;;
      --key)
        private_key="${2:-}"
        shift 2
        ;;
      --cert)
        cert_pem="${2:-}"
        shift 2
        ;;
      --dry-run)
        dry_run="true"
        shift
        ;;
      *)
        echo "unknown sign-paths option: $1" >&2
        return 2
        ;;
    esac
  done

  if [ ! -f "$private_key" ]; then
    echo "private key not found: $private_key" >&2
    return 1
  fi
  if [ ! -f "$path_list" ]; then
    echo "path list not found: $path_list" >&2
    return 1
  fi

  local ima_extra=()
  local evm_extra=()
  if [ -n "${KEYLIME_NODE_EVM_IMA_SIGN_EXTRA:-}" ]; then
    read -r -a ima_extra <<< "${KEYLIME_NODE_EVM_IMA_SIGN_EXTRA}"
  fi
  if [ -n "${KEYLIME_NODE_EVM_EVM_SIGN_EXTRA:-}" ]; then
    read -r -a evm_extra <<< "${KEYLIME_NODE_EVM_EVM_SIGN_EXTRA}"
  fi
  if [ -f "$cert_pem" ] && [ "${KEYLIME_NODE_EVM_USE_CERT_ARG:-false}" = "true" ]; then
    ima_extra+=(--cert "$cert_pem")
    evm_extra+=(--cert "$cert_pem")
  fi

  while IFS= read -r path; do
    is_usable_path_line "$path" || continue
    sign_one_path "$path" "$private_key" "$dry_run" "${ima_extra[@]}" -- "${evm_extra[@]}"
  done < "$path_list"
}

sign_one_path() {
  local path="$1"
  local private_key="$2"
  local dry_run="$3"
  shift 3

  local ima_extra=()
  local evm_extra=()
  local seen_separator="false"
  while [ "$#" -gt 0 ]; do
    if [ "$1" = "--" ]; then
      seen_separator="true"
      shift
      continue
    fi
    if [ "$seen_separator" = "true" ]; then
      evm_extra+=("$1")
    else
      ima_extra+=("$1")
    fi
    shift
  done

  if [ ! -e "$path" ]; then
    echo "skip missing: $path"
    return 0
  fi

  local target="$path"
  if [ -L "$target" ]; then
    target="$(readlink -f "$target")"
  fi
  if [ ! -f "$target" ]; then
    echo "skip non-regular: $path"
    return 0
  fi

  echo "signing: $target"
  if [ "$dry_run" = "true" ]; then
    printf '  evmctl ima_sign --key %q --hashalgo sha256' "$private_key"
    printf ' %q' "${ima_extra[@]}" "$target"
    printf '\n'
    printf '  evmctl sign --imahash --portable --key %q' "$private_key"
    printf ' %q' "${evm_extra[@]}" "$target"
    printf '\n'
    return 0
  fi

  evmctl ima_sign --key "$private_key" --hashalgo sha256 "${ima_extra[@]}" "$target"
  if ! evmctl sign --imahash --portable --key "$private_key" "${evm_extra[@]}" "$target"; then
    evmctl sign --imahash --key "$private_key" "${evm_extra[@]}" "$target"
  fi
  print_xattr_state "$target"
}

cmd_verify_paths() {
  local path_list="$PATH_LIST"
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --path-list)
        path_list="${2:-}"
        shift 2
        ;;
      *)
        echo "unknown verify-paths option: $1" >&2
        return 2
        ;;
    esac
  done

  if [ ! -f "$path_list" ]; then
    echo "path list not found: $path_list" >&2
    return 1
  fi

  while IFS= read -r path; do
    is_usable_path_line "$path" || continue
    print_xattr_state "$path"
  done < "$path_list"
}

print_xattr_state() {
  local path="$1"
  python3 -c '
import os
import sys

path = sys.argv[1]
if not os.path.exists(path):
    print(f"{path}: missing")
    raise SystemExit(0)
if os.path.islink(path):
    path = os.path.realpath(path)
if not os.path.isfile(path):
    print(f"{path}: non-regular")
    raise SystemExit(0)
parts = []
for attr in ("security.ima", "security.evm"):
    try:
        value = os.getxattr(path, attr)
    except OSError as exc:
        parts.append(f"{attr}=missing:{exc.errno}")
    else:
        parts.append(f"{attr}=present:{len(value)}")
print(f"{path}: " + " ".join(parts))
' "$path"
}

ensure_path_list_exists() {
  local quiet="${1:-}"
  if [ -f "$PATH_LIST" ]; then
    return 0
  fi
  if [ "$quiet" != "quiet" ]; then
    echo "path list not found: $PATH_LIST" >&2
  fi
  return 1
}

is_usable_path_line() {
  local line="$1"
  [ -n "$line" ] || return 1
  case "$line" in
    \#*) return 1 ;;
    *) return 0 ;;
  esac
}

cmd_print_boot_args() {
  cat <<EOF
Stage 1, audit-only appraisal:
  ${DEFAULT_BOOT_ARGS_LOG}

Stage 2, enforcement after all required files are signed and probe is green:
  ${DEFAULT_BOOT_ARGS_ENFORCE}

Ubuntu csri8/csri9:
  cp -a /etc/default/grub /root/grub.before-keylime-evm
  Edit /etc/default/grub so GRUB_CMDLINE_LINUX contains one ima_policy argument:
    ${DEFAULT_BOOT_ARGS_LOG}
  update-grub
  reboot

Anolis/RHEL-family hygon22:
  grubby --update-kernel=ALL --remove-args="ima_policy ima_appraise"
  grubby --update-kernel=ALL --args="${DEFAULT_BOOT_ARGS_LOG}"
  reboot

Rollback before enforcement:
  Ubuntu: remove the added arguments from /etc/default/grub, run update-grub, reboot.
  Anolis/RHEL-family: grubby --update-kernel=ALL --remove-args="ima_policy ima_appraise", reboot.
EOF
}

main "$@"
