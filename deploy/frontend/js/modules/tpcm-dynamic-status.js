export const tpcmDynamicStatusMethods = {
  dynamicNodeSwitchText(policy) {
    if (!policy) return "未配置";
    const content = policy.content || {};
    const enabled = content.node_dynamic_measure_enabled !== false && content.dynamic_measure_required !== false;
    return enabled ? "开启" : "关闭";
  },
  dynamicNodeSwitchClass(policy) {
    if (!policy) return "warn";
    const content = policy.content || {};
    const enabled = content.node_dynamic_measure_enabled !== false && content.dynamic_measure_required !== false;
    return enabled ? "ok" : "bad";
  },
  dynamicPolicyStateText(state) {
    const names = {
      applied: "一致",
      global_disabled: "暂不生效",
      queued: "待生效",
      applying: "生效中",
      failed: "生效失败",
      not_deployed: "待生效",
      not_configured: "未配置",
      superseded: "已替换",
      mixed: "状态不一致"
    };
    return names[state] || state || "-";
  },
  dynamicPolicyStateClass(state) {
    if (state === "applied") return "ok";
    if (state === "failed") return "bad";
    return "warn";
  },
  dynamicBindingAuthorization(binding) {
    if (!binding) return { text: "未检测", state: "warn", key: "unknown" };
    const details = binding.binding_details || {};
    const status = details.policy_apply_authorization_status || "";
    if (status === "normal") return { text: "正常", state: "ok", key: "normal" };
    if (status === "missing") return { text: "未配置", state: "bad", key: "missing" };
    if (status === "invalid") return { text: "材料异常", state: "bad", key: "invalid" };
    if (status === "rejected") return { text: "授权异常", state: "bad", key: "rejected" };
    if (binding.application_status === "applied") return { text: "正常", state: "ok", key: "normal" };
    if (binding.application_status === "queued" || binding.application_status === "applying") {
      return { text: "检测中", state: "warn", key: "checking" };
    }
    if (binding.application_status === "failed") {
      const error = String(binding.last_error || "");
      const rejected = error.includes("ret: 152") || error.includes("0x98");
      return { text: rejected ? "授权异常" : "生效异常", state: "bad", key: rejected ? "rejected" : "failed" };
    }
    return { text: "未检测", state: "warn", key: "unknown" };
  },
  dynamicLastApplyResult(binding) {
    if (!binding) {
      return { text: "未配置", state: "warn", summary: "尚未保存节点动态度量策略。" };
    }
    const details = binding.binding_details || {};
    const result = String(details.policy_last_result || "").toLowerCase();
    if (result === "success" || binding.application_status === "applied") {
      return {
        text: "成功",
        state: "ok",
        summary: details.policy_last_result_summary || "策略已生效。"
      };
    }
    if (binding.application_status === "queued" || binding.application_status === "applying") {
      return { text: "待生效", state: "warn", summary: "策略正在等待后台任务处理。" };
    }
    if (result === "failed" || binding.application_status === "failed") {
      return {
        text: "失败",
        state: "bad",
        summary: details.policy_apply_error_summary || details.policy_last_result_summary || binding.last_error || "策略生效失败。"
      };
    }
    return { text: "未生效", state: "warn", summary: "尚未完成策略生效。" };
  },
  dynamicNodePolicySourceText(policy) {
    if (!policy) return "未配置";
    const state = this.bindingState(policy);
    return state === "applied" ? "节点策略" : "节点策略（未生效）";
  },
  dynamicObjectApplyState({ current, globalEnabled, nodeEnabled, targetEnabled, targetInterval, bindingState }) {
    if (this.dynamicFormDirty) return { text: "待保存", state: "warn" };
    if (!globalEnabled) return { text: "暂不生效", state: "warn" };
    if (!nodeEnabled) {
      if (!current) return { text: "一致", state: "ok" };
      if (bindingState === "failed") return { text: "生效失败", state: "bad" };
      return { text: "待关闭", state: "warn" };
    }
    if (!targetEnabled) {
      if (!current) return { text: "一致", state: "ok" };
      if (bindingState === "failed") return { text: "生效失败", state: "bad" };
      return { text: "待生效", state: "warn" };
    }
    if (current && Number(current.interval_milli || 0) === Number(targetInterval)) {
      return { text: "一致", state: "ok" };
    }
    if (bindingState === "failed") return { text: "生效失败", state: "bad" };
    if (bindingState === "queued" || bindingState === "applying") return { text: "生效中", state: "warn" };
    return { text: "待生效", state: "warn" };
  },
  dynamicObjectStateText(state) {
    if (state === "applied") return "已生效";
    if (state === "queued" || state === "applying") return "生效中";
    if (state === "failed") return "生效失败";
    if (state === "mixed") return "状态不一致";
    return "未生效";
  },
};
