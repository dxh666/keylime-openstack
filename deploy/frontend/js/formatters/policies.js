import { DYNAMIC_MEASUREMENT_OBJECTS } from "../policies.js";

export const policyFormatters = {
  parseLines(text) {
    return String(text || "").split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
  },
  policyArtifactText(policy) {
    const artifact = policy?.source?.keylime_artifact || policy?.content?.keylime_artifact || "";
    if (artifact === "measured_boot_refstate") return "可信启动参考状态";
    if (artifact === "measured_boot_refstate_or_tpm_policy") return "可信启动策略";
    if (artifact === "tpm_pcr_quote_policy") return "TPM PCR Quote 策略";
    if (artifact === "runtime_policy") return "IMA 运行时策略";
    if (artifact === "opentcsm_dynamic_measurement_policy") return "TPCM 动态度量策略";
    return artifact || "-";
  },
  policyDynamicObjectsText(policy) {
    const names = {
      kernel_section: "内核代码段",
      syscall_table: "系统调用表",
      idt_table: "中断描述符表"
    };
    const nodeEnabled = policy?.content?.node_dynamic_measure_enabled !== false && policy?.content?.dynamic_measure_required !== false;
    const configs = policy?.content?.environment_object_configs || {};
    if (Object.keys(configs).length) {
      const objectText = DYNAMIC_MEASUREMENT_OBJECTS.map((item) => {
        const config = configs[item.key] || {};
        const state = config.enabled ? "开启" : "关闭";
        const interval = config.enabled ? ` / ${config.interval_milli || 60000} ms` : "";
        return `${item.label}：${state}${interval}`;
      }).join("、");
      return `${nodeEnabled ? "节点总开关：开启" : "节点总开关：关闭"}；${objectText}`;
    }
    const objects = policy?.content?.environment_objects || ["kernel_section", "syscall_table", "idt_table"];
    return objects.length ? objects.map((item) => names[item] || item).join("、") : "-";
  },
  policyDynamicIntervalText(policy) {
    const configs = policy?.content?.environment_object_configs || {};
    const intervals = [...new Set(
      Object.values(configs)
        .filter((config) => config?.enabled)
        .map((config) => `${config.interval_milli || 60000} ms`)
    )];
    if (intervals.length) return intervals.join("、");
    return `${policy?.content?.environment_interval_milli ?? 60000} ms`;
  },
  bindingNames(policy) {
    return (policy.bindings || []).map((item) => item.target_name).join("、") || "-";
  },
  bindingState(policy) {
    const states = [...new Set((policy.bindings || []).map((item) => item.application_status))];
    return states.length === 1 ? states[0] : states.length ? "mixed" : "not_deployed";
  },
  bindingStateText(policy) {
    const state = this.bindingState(policy);
    return state === "mixed" ? "状态不一致" : this.deploymentText(state);
  },
  policyEffectiveVersion(policy) {
    const applied = (policy.bindings || [])
      .filter((binding) => binding.application_status === "applied" && binding.applied_at)
      .sort((a, b) => new Date(b.applied_at).getTime() - new Date(a.applied_at).getTime());
    if (!applied.length) return "-";
    return this.formatTime(applied[0].applied_at);
  },
  policyPcrText(policy) {
    const pcrs = policy?.content?.pcrs || [];
    return pcrs.length ? pcrs.map((pcr) => `PCR${pcr}`).join("、") : "PCR0-7";
  },
  policyFallbackPcrText(policy) {
    const pcrs = policy?.content?.fallback_pcrs || [7];
    return pcrs.length ? pcrs.map((pcr) => `PCR${pcr}`).join("、") : "PCR7";
  },
  policySecureBootText(policy) {
    return policy?.content?.secure_boot_required === false ? "不强制" : "要求启用";
  },
  bindingEvidenceText(binding) {
    const keylimePolicy = binding?.keylime_policy || {};
    const evidenceType = keylimePolicy.evidence_type || "";
    const evidenceName = evidenceType === "tpm_event_log"
      ? "TPM Event Log"
      : evidenceType === "tpm_pcr_quote"
        ? "TPM PCR Quote"
      : evidenceType === "ima_measurement_list"
        ? "IMA 度量列表"
      : evidenceType === "tpcm_dynamic_measurement"
        ? "TPCM 动态度量"
        : "节点证据";
    const evidenceHash = keylimePolicy.evidence_sha256 || "";
    const policyHash = keylimePolicy.content_sha256 || "";
    if (evidenceHash && policyHash) {
      return `${evidenceName} ${evidenceHash.slice(0, 12)} / 策略 ${policyHash.slice(0, 12)}`;
    }
    if (evidenceHash) return `${evidenceName} ${evidenceHash.slice(0, 12)}`;
    if (policyHash) return `策略 ${policyHash.slice(0, 12)}`;
    return "-";
  },
  canDeployPolicy(policy) {
    const retryable = new Set(["not_deployed", "failed", "awaiting_reboot"]);
    return (policy.bindings || []).some((binding) => retryable.has(binding.application_status));
  },
  deployActionText(policy, scope = "") {
    if (policy?.policy_type === "tpcm_dynamic_measurement") {
      return scope === "node" ? "下发此节点策略" : scope === "all" ? "下发全部节点策略" : "下发策略";
    }
    return scope === "node" ? "更新此节点基线" : scope === "all" ? "更新全部节点基线" : "更新基线";
  },
};
