export const policyCenterComputed = {
  filteredPolicies() {
    return this.policies.filter((policy) => policy.policy_type === this.activePolicyType);
  },
  policyTargetNodes() {
    return this.computeInventory;
  },
  policyNamePlaceholder() {
    if (this.activePolicyType === "measured_boot") return "例如 compute-trusted-boot-v1";
    if (this.activePolicyType === "ima_runtime") return "例如 compute-ima-runtime-v1";
    return "例如 compute-policy-v1";
  },
};
