# Operational Scripts

This directory is reserved for parameterized scripts that are safe to use as
current operational entry points. Host-specific one-off scripts and deferred
experiments should live under `deploy/examples/`.

## Primary Control Plane

- `keylime-openstack-compose-deploy.sh`: build, migrate, start, and health-check
  the FastAPI/PostgreSQL trust plane.
- `keylime-openstackctl`: thin CLI wrapper for the installed Python package.
- `keylime-openstack-capability-check.sh`: post-deployment capability check.

## Node Inventory And Agents

- `keylime-agent-inventory-refresh.sh`: refresh TPM agent inventory.
- `keylime-agent-container-restart.sh`: generic TPM agent container restart helper.
- `keylime-host-integrity-probe.py`: host integrity evidence probe.

## TPM Trust Policies

- `keylime-only-attestation-check.sh`: TPM-only verification gate.
- `keylime-only-attestation-repair.sh`: IMA runtime repair workflow for TPM nodes.
- `keylime-tpm-evidence-audit.sh`: TPM/PCR evidence diagnostics.
- `keylime-tpm-pcr-policy-render-from-baseline.sh`: render TPM PCR policy from evidence.
- `keylime-tpm-pcr-policy-apply.sh`: apply TPM PCR policy.

## IMA Runtime Policies

- `keylime-ima-runtime-policy-generate.sh`: generate IMA runtime policy.
- `keylime-ima-runtime-policy-register.sh`: register IMA runtime policy.
- `keylime-ima-runtime-policy-apply.sh`: apply IMA runtime policy.
- `keylime-ima-runtime-policy-refresh.sh`: generate, register, apply, and re-check.
- `keylime-ima-runtime-policy-diff.sh`: compare live measurements with a bound policy.
- `keylime-ima-runtime-evidence-audit.sh`: IMA evidence diagnostics.

## TPCM / OpenTCSM

- `opentcsm-evidence-collect.sh`: collect OpenTCSM/Hygon TPCM evidence for a node.

## OpenStack Enforcement And Legacy Shell Loop

These remain for compatibility with the shell/systemd lab control loop. The
FastAPI/PostgreSQL control plane is the primary product path.

- `keylime-placement-sync.sh`
- `keylime-placement-sync-locked.sh`
- `keylime-sync-control-loop.sh`
- `keylime-nova-compute-quarantine.sh`
- `keylime-vm-risk-marker.sh`
- `keylime-openstack-trusted-flavor-setup.sh`
- `keylime-openstack-control-plane-install.sh`

## Not Current Operational Entry Points

- Host-specific csri9 helpers are archived in `deploy/examples/legacy/`.
- EVM/IMA appraisal helpers are archived in `deploy/examples/experimental/evm/`
  because EVM is deferred in the current product scope.
