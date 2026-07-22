# Documentation Index

Use this index as the product-oriented entry point. Older case records remain
available, but new development should link to the active documents below first.

## Active Product Documents

- `keylime_openstack_production_deployment.md`: deployment and operations.
- `keylime_openstack_production_trust_plane_design.md`: control-plane design.
- `keylime_openstack_productization_plan.md`: productization boundary and roadmap.
- `opentcsm_hygon_tpcm_integration.md`: TPCM/OpenTCSM integration.
- `keylime_measured_boot_ima_ansible_management.md`: TPM measured boot and IMA policy operations.

## Operational References

- `keylime_agent_inventory_auto_discovery.md`
- `keylime_first_attestation_gate.md`
- `keylime_openstack_integrated_capability_audit.md`
- `keylime_openstack_systemd_timer_and_productionization.md`

## Historical Lab Records

The `case*`, `phase*`, and `current_issue*` documents are historical lab
records. They may reference archived scripts under `deploy/examples/legacy/` or
deferred experiments under `deploy/examples/experimental/`.

## Deferred Scope

- `keylime_openstack_node_evm_appraisal_enablement.md`
- `keylime_openstack_hygon22_experimental_kernel.md`

EVM is intentionally not part of the current productized structure. Keep future
EVM work behind a separate feature boundary.

## Documentation Rules For New Work

- Add product behavior and operations to active product documents first.
- Keep one-off command transcripts as historical case records.
- Do not add host-specific operational procedures without a parameterized script.
- Prefer TPM/TPCM trusted-root terminology in product-facing text; keep
  Keylime/OpenTCSM names for adapter or implementation details.
