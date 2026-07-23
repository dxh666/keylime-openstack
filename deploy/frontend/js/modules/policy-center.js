import { emptyPolicyForm } from "../policies.js?v=20260723-trusted-boot";

export const policyCenterComputed = {
  filteredPolicies() {
    return this.policies.filter((policy) => policy.policy_type === this.activePolicyType);
  },
  policyTargetNodes() {
    return this.computeInventory.filter((node) => {
      const profile = node.trusted_node_profile || node;
      const capabilities = profile.capabilities || node.capabilities || {};
      if (this.activePolicyType === "measured_boot") {
        const rootType = this.createForm.trustedRootType || "tpm";
        return (
          profile.trust_managed === true &&
          profile.trusted_root_type === rootType &&
          capabilities.trusted_boot === true
        );
      }
      if (this.activePolicyType === "ima_runtime") {
        return (
          profile.trust_managed === true &&
          profile.trusted_root_type === "tpm" &&
          capabilities.ima_runtime === true
        );
      }
      return profile.trust_managed === true;
    });
  },
  policyNamePlaceholder() {
    if (this.activePolicyType === "measured_boot") {
      return this.createForm.trustedRootType === "tpcm"
        ? "例如 tpcm-trusted-boot-hygon23"
        : "例如 tpm-trusted-boot-compute";
    }
    if (this.activePolicyType === "ima_runtime") return "例如 compute-ima-runtime-v1";
    return "例如 compute-policy-v1";
  },
};

export const policyCenterMethods = {
  openCreatePolicy() {
    if (!this.currentPolicyType.creatable) return;
    this.createForm = emptyPolicyForm(this.activePolicyType);
    this.createDialogOpen = true;
    this.$nextTick(() => {
      const input = document.querySelector(".policy-create-dialog input");
      if (input) input.focus();
    });
  },
  closeCreatePolicy() {
    if (!this.busy) this.createDialogOpen = false;
  },
  viewPolicy(policy) {
    this.detailPolicy = policy;
  },
  closePolicyDetail() {
    this.detailPolicy = null;
  },
  changeTrustedBootRootType(value) {
    this.createForm.trustedRootType = value;
    this.createForm.targetNodeIds = [];
  },
  policyPayload() {
    const name = this.createForm.name.trim();
    if (!name) throw new Error("策略名称不能为空");
    if (!this.createForm.targetNodeIds.length) throw new Error("请至少选择一个节点");
    let content;
    let excludes = [];
    if (this.activePolicyType === "measured_boot") {
      if (this.createForm.trustedRootType === "tpcm") {
        content = {
          trusted_root_type: "tpcm",
          policy_scope: "trusted_boot",
          evidence_type: "tpcm_boot_measurement",
          baseline_generation: "auto_collect_tpcm_boot_measurement",
          boot_measure_required: true,
          minimum_boot_references: Number(this.createForm.tpcmMinimumBootReferences || 1),
          require_clean_trust_report: this.createForm.tpcmRequireCleanTrustReport,
          require_trust_status: "trusted",
          require_trust_report_eval: 100,
          tpcm_apply_mode: this.createForm.tpcmWriteEnabled ? "tpcm_write" : "management_baseline",
          tpcm_write_enabled: this.createForm.tpcmWriteEnabled,
          auth_material_ref: String(this.createForm.tpcmAuthRef || "bmeasure-uid").trim(),
          keylime_artifact: "opentcsm_tpcm_boot_policy",
          adapter_artifact: "opentcsm_tpcm_boot_policy"
        };
      } else {
        content = {
          trusted_root_type: "tpm",
          policy_scope: "trusted_boot",
          pcrs: this.createForm.pcrs.map(Number).sort((a, b) => a - b),
          fallback_pcrs: [7],
          secure_boot_required: this.createForm.secureBootRequired,
          reference_state_mode: "collect_from_node",
          event_log_fallback: "pcr_quote",
          baseline_generation: "auto_collect_tpm_event_log",
          keylime_artifact: "measured_boot_refstate_or_tpm_policy"
        };
      }
    } else if (this.activePolicyType === "ima_runtime") {
      content = {
        node_ima_policy: this.createForm.nodeImaPolicy,
        runtime_policy_generation: "keylime-policy-from-measurements",
        baseline_generation: "auto_collect_ima_measurements",
        keylime_artifact: "runtime_policy",
        reboot_after_apply: this.createForm.rebootAfterApply
      };
      excludes = this.parseLines(this.createForm.excludesText);
    } else {
      throw new Error("该策略类型暂未启用");
    }
    return {
      name,
      policy_type: this.activePolicyType,
      version: 1,
      status: "active",
      hash_alg: "sha256",
      description: this.createForm.description.trim(),
      content,
      protected_paths: [],
      excludes,
      source: {
        baseline_source: "target_node",
        generation_mode: "auto_collect",
        trusted_root_type: content.trusted_root_type,
        keylime_artifact: content.keylime_artifact,
        adapter_artifact: content.adapter_artifact || content.keylime_artifact
      },
      target_node_ids: this.createForm.targetNodeIds.map(Number),
      deploy_now: true
    };
  },
  createPolicy() {
    try {
      const payload = this.policyPayload();
      this.requireToken({
        title: "确认新增策略",
        message: `将创建${this.currentPolicyType.label}「${payload.name}」，并从目标节点自动采集基线后应用到对应可信能力。`,
        confirmText: "生成并下发",
        action: async (token) => {
          await this.requestJson("/api/policies", {
            method: "POST",
            body: JSON.stringify(payload)
          }, token);
          this.showNotice("ok", "策略已创建并进入下发队列");
          this.createDialogOpen = false;
          await this.refreshAll(false);
        }
      });
    } catch (error) {
      this.showNotice("bad", error.message);
    }
  },
  deletePolicy(policy) {
    this.requireToken({
      title: "确认删除策略",
      message: `将删除策略「${policy.name}」。`,
      confirmText: "确认删除",
      action: async (token) => {
        await this.requestJson(`/api/policies/${policy.id}`, { method: "DELETE" }, token);
        this.showNotice("ok", "策略已删除");
        if (this.detailPolicy && this.detailPolicy.id === policy.id) this.closePolicyDetail();
        await this.refreshAll(false);
      }
    });
  },
  deployPolicy(policy, binding = null) {
    const targetText = binding ? `${policy.name} / ${binding.target_name}` : policy.name;
    const endpoint = binding
      ? `/api/policies/${policy.id}/bindings/${binding.id}/deploy`
      : `/api/policies/${policy.id}/deploy`;
    this.requireToken({
      title: "确认更新策略基线",
      message: `将重新采集「${targetText}」的节点证据，生成策略并下发到对应可信代理。`,
      confirmText: "确认更新",
      action: async (token) => {
        await this.requestJson(endpoint, { method: "POST" }, token);
        this.showNotice("ok", "策略基线更新任务已进入队列");
        this.detailPolicy = null;
        await this.refreshAll(false);
      }
    });
  },
  applyTpcmBootHardware(policy, binding) {
    if (!binding) return;
    const targetText = `${policy.name} / ${binding.target_name}`;
    this.requireToken({
      title: "确认下发 TPCM 可信启动硬件策略",
      message: `将把「${targetText}」的可信启动基线写入 TPCM 启动参考值，并开启启动度量控制。`,
      confirmText: "下发到 TPCM",
      action: async (token) => {
        const result = await this.requestJson(
          `/api/policies/${policy.id}/bindings/${binding.id}/tpcm-boot/hardware-apply`,
          { method: "POST" },
          token
        );
        if (result.ok === false) {
          const summary = result.details?.tpcm_write_error_summary || result.error || "TPCM 可信启动硬件下发失败";
          throw new Error(summary);
        }
        this.showNotice("ok", "TPCM 可信启动硬件下发完成");
        await this.refreshAll(false);
        const refreshed = this.policies.find((item) => item.id === policy.id);
        this.detailPolicy = refreshed || null;
      }
    });
  },
};
