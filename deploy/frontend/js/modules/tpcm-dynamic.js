import { DYNAMIC_MEASUREMENT_OBJECTS, emptyDynamicForm } from "../policies.js";

export const tpcmDynamicComputed = {
  dynamicTargetNodes() {
    return this.computeInventory.filter((node) => node.trust_agent_type === "opentcsm_tpcm");
  },
  selectedDynamicNode() {
    if (!this.dynamicTargetNodes.length) return null;
    const selectedId = Number(this.activeDynamicNodeId);
    return this.dynamicTargetNodes.find((node) => Number(node.id) === selectedId) || this.dynamicTargetNodes[0];
  },
  selectedDynamicNodeId() {
    return this.selectedDynamicNode ? Number(this.selectedDynamicNode.id) : null;
  },
  selectedDynamicComputeRow() {
    const selectedId = this.selectedDynamicNodeId;
    if (!selectedId) return null;
    return this.computeRows.find((row) => Number(row.id) === selectedId) || null;
  },
  selectedDynamicKeylimeNode() {
    const node = this.selectedDynamicNode || {};
    return (
      this.keylimeNodesByHost.get(node.hostname) ||
      this.keylimeNodesByUuid.get(node.keylime_agent_uuid) ||
      {}
    );
  },
  selectedDynamicReport() {
    return this.selectedDynamicKeylimeNode.trust_report || {};
  },
  dynamicCurrentObjects() {
    const items = new Map();
    for (const item of this.selectedDynamicReport.dmeasure_policy || []) {
      if (item?.object) items.set(item.object, item);
    }
    return items;
  },
  dynamicMeasurementPolicies() {
    return this.policies
      .filter((policy) => policy.policy_type === "tpcm_dynamic_measurement")
      .sort((a, b) => Number(b.id || 0) - Number(a.id || 0));
  },
  dynamicMeasurementPolicy() {
    const selectedId = this.selectedDynamicNodeId;
    if (!selectedId) return null;
    return this.dynamicMeasurementPolicies.find((policy) => {
      const bindings = policy.bindings || [];
      return bindings.length === 1 && Number(bindings[0].target_id) === selectedId;
    }) || null;
  },
  dynamicMeasurementAppliedPolicy() {
    const selectedId = this.selectedDynamicNodeId;
    if (!selectedId) return null;
    return this.dynamicMeasurementPolicies.find((policy) =>
      (policy.bindings || []).some((binding) =>
        Number(binding.target_id) === selectedId &&
        binding.active !== false &&
        binding.application_status === "applied"
      )
    ) || null;
  },
  dynamicMeasurementSourcePolicy() {
    return this.dynamicMeasurementAppliedPolicy || this.dynamicMeasurementPolicy;
  },
  dynamicSelectedPolicyBinding() {
    const policy = this.dynamicMeasurementPolicy;
    const selectedId = this.selectedDynamicNodeId;
    if (!policy || !selectedId) return null;
    return this.dynamicPolicyBinding(policy, selectedId);
  },
  dynamicPolicySourceText() {
    if (this.dynamicMeasurementAppliedPolicy) return "节点策略";
    if (this.dynamicMeasurementPolicy) return "节点策略（未生效）";
    return "未配置";
  },
  dynamicPolicySourceClass() {
    if (this.dynamicMeasurementAppliedPolicy) return "ok";
    if (this.dynamicMeasurementPolicy) return "warn";
    return "warn";
  },
  dynamicApplyAuthorization() {
    return this.dynamicBindingAuthorization(this.dynamicSelectedPolicyBinding);
  },
  dynamicStatusItems() {
    const report = this.selectedDynamicReport;
    const auth = this.dynamicApplyAuthorization;
    const globalEnabled = this.tpcmDynamicGlobalEnabled;
    const lastResult = this.dynamicLastApplyResult(this.dynamicSelectedPolicyBinding);
    return [
      {
        label: "TPCM 动态度量全局控制",
        value: globalEnabled ? "开启" : "全局关闭，节点配置暂不生效",
        state: globalEnabled ? "ok" : "warn"
      },
      {
        label: "策略生效授权状态",
        value: auth.text,
        state: auth.state
      },
      {
        label: "策略来源",
        value: this.dynamicPolicySourceText,
        state: this.dynamicPolicySourceClass
      },
      {
        label: "最近生效结果",
        value: globalEnabled ? lastResult.text : "暂不生效",
        state: globalEnabled ? lastResult.state : "warn"
      },
      {
        label: "结果说明",
        value: globalEnabled ? lastResult.summary : "全局关闭时保留节点配置，但不会下发到节点。"
      },
      {
        label: "最近采集时间",
        value: this.formatTime(report.collected_at)
      }
    ];
  },
  dynamicNodeRows() {
    return this.dynamicTargetNodes.map((node) => {
      const policy = this.dynamicMeasurementPolicies.find((item) => {
        const bindings = item.bindings || [];
        return bindings.length === 1 && Number(bindings[0].target_id) === Number(node.id);
      });
      const binding = policy ? this.dynamicPolicyBinding(policy, node.id) : null;
      const auth = this.dynamicBindingAuthorization(binding);
      const state = policy ? this.bindingState(policy) : "not_configured";
      const effectiveState = !this.tpcmDynamicGlobalEnabled && policy ? "global_disabled" : state;
      return {
        id: node.id,
        hostname: node.hostname,
        ip: node.management_ip || node.keylime_agent_ip || "-",
        selected: Number(node.id) === this.selectedDynamicNodeId,
        nodeSwitchText: this.dynamicNodeSwitchText(policy),
        nodeSwitchClass: this.dynamicNodeSwitchClass(policy),
        sourceText: policy ? this.dynamicNodePolicySourceText(policy) : "未配置",
        sourceClass: policy ? this.dynamicPolicyStateClass(state) : "warn",
        stateText: policy ? this.dynamicPolicyStateText(effectiveState) : "未配置",
        stateClass: policy ? this.dynamicPolicyStateClass(effectiveState) : "warn",
        stateKey: effectiveState,
        authText: auth.text,
        authClass: auth.state,
        authKey: auth.key,
        effectiveAt: policy ? this.policyEffectiveVersion(policy) : "-"
      };
    });
  },
  filteredDynamicNodeRows() {
    const keyword = this.dynamicNodeSearch.trim().toLowerCase();
    return this.dynamicNodeRows.filter((node) => {
      const searchable = `${node.hostname} ${node.ip}`.toLowerCase();
      if (keyword && !searchable.includes(keyword)) return false;
      if (this.dynamicNodeStatusFilter === "all") return true;
      if (this.dynamicNodeStatusFilter === "auth_error") return node.authKey === "rejected" || node.authKey === "invalid" || node.authKey === "missing";
      return node.stateKey === this.dynamicNodeStatusFilter;
    });
  },
  dynamicObjectRows() {
    const policy = this.dynamicMeasurementPolicy;
    const bindingState = policy ? this.bindingState(policy) : "not_deployed";
    const currentObjects = this.dynamicCurrentObjects;
    const nodeEnabled = this.dynamicForm.nodeEnabled === true;
    const globalEnabled = this.tpcmDynamicGlobalEnabled;
    return DYNAMIC_MEASUREMENT_OBJECTS.map((item) => {
      const config = this.dynamicForm.objects[item.key] || { enabled: false, intervalMilli: 60000 };
      const current = currentObjects.get(item.key);
      const targetEnabled = nodeEnabled && config.enabled === true;
      const targetInterval = Number(config.intervalMilli || 60000);
      const state = this.dynamicObjectApplyState({
        current,
        globalEnabled,
        nodeEnabled,
        targetEnabled,
        targetInterval,
        bindingState
      });
      return {
        ...item,
        enabled: config.enabled,
        intervalMilli: config.intervalMilli,
        currentText: current ? "已开启" : "未开启",
        currentClass: current ? "ok" : "warn",
        currentIntervalText: current ? `${current.interval_milli ?? "-"} ms` : "-",
        targetText: targetEnabled ? "开启" : "关闭",
        targetClass: targetEnabled ? "ok" : "warn",
        stateText: state.text,
        stateClass: state.state,
        effectiveAt: policy ? this.policyEffectiveVersion(policy) : "-"
      };
    });
  },
};

export const tpcmDynamicMethods = {
  ensureDynamicNodeSelection() {
    const nodes = this.dynamicTargetNodes;
    if (!nodes.length) {
      this.activeDynamicNodeId = null;
      return;
    }
    if (!nodes.some((node) => Number(node.id) === Number(this.activeDynamicNodeId))) {
      this.activeDynamicNodeId = Number(nodes[0].id);
    }
  },
  selectDynamicNode(row) {
    const nodeId = Number(row?.id);
    if (!nodeId || nodeId === Number(this.activeDynamicNodeId)) return;
    if (this.dynamicFormDirty) {
      this.showNotice("warn", "当前节点配置尚未保存，请先保存并生效后再切换节点。");
      return;
    }
    this.activeDynamicNodeId = nodeId;
    this.syncDynamicFormFromPolicy(true);
  },
  syncDynamicFormFromPolicy(force = false) {
    if (this.dynamicFormDirty && !force) return;
    this.ensureDynamicNodeSelection();
    const policy = this.dynamicMeasurementSourcePolicy;
    const form = emptyDynamicForm();
    if (policy) {
      const content = policy.content || {};
      const objectConfigs = content.environment_object_configs || {};
      const legacyObjects = new Set(content.environment_objects || DYNAMIC_MEASUREMENT_OBJECTS.map((item) => item.key));
      const defaultInterval = Number(content.environment_interval_milli || 60000);
      form.nodeEnabled = content.node_dynamic_measure_enabled !== false && content.dynamic_measure_required !== false;
      for (const item of DYNAMIC_MEASUREMENT_OBJECTS) {
        const config = objectConfigs[item.key] || {};
        form.objects[item.key] = {
          enabled: Object.prototype.hasOwnProperty.call(objectConfigs, item.key)
            ? config.enabled === true
            : legacyObjects.has(item.key),
          intervalMilli: Number(config.interval_milli || config.intervalMilli || defaultInterval || 60000)
        };
      }
      this.dynamicFormPolicyId = this.dynamicMeasurementPolicy?.id || null;
    } else {
      this.dynamicFormPolicyId = null;
    }
    this.dynamicForm = form;
    this.dynamicFormDirty = false;
  },
  markDynamicFormDirty() {
    this.dynamicFormDirty = true;
  },
  dynamicPolicyBinding(policy, nodeId) {
    if (!policy || !nodeId) return null;
    return (policy.bindings || []).find((binding) => Number(binding.target_id) === Number(nodeId)) || null;
  },
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
  async refreshSelectedDynamicNode() {
    const row = this.selectedDynamicComputeRow;
    if (!row) return this.showNotice("bad", "请先选择节点。");
    await this.collectOpenTcsmStatus(row);
  },
  dynamicMeasurementPayload() {
    const selectedNode = this.selectedDynamicNode;
    if (!selectedNode) throw new Error("请先选择一个目标节点");
    const objectConfigs = {};
    const nodeEnabled = this.dynamicForm.nodeEnabled === true;
    for (const item of DYNAMIC_MEASUREMENT_OBJECTS) {
      const config = this.dynamicForm.objects[item.key] || {};
      const interval = Number(config.intervalMilli || 60000);
      if (!Number.isFinite(interval) || interval < 1000 || interval > 86400000) {
        throw new Error(`${item.label} 的检测周期必须在 1000 到 86400000 毫秒之间`);
      }
      objectConfigs[item.key] = {
        enabled: config.enabled === true,
        interval_milli: interval
      };
    }
    const enabledObjects = Object.entries(objectConfigs)
      .filter(([, config]) => nodeEnabled && config.enabled)
      .map(([name]) => name);
    const defaultInterval = enabledObjects.length
      ? objectConfigs[enabledObjects[0]].interval_milli
      : 60000;
    const existing = this.dynamicMeasurementPolicy;
    return {
      name: existing?.name || `${selectedNode.hostname} 环境动态度量策略`,
      policy_type: "tpcm_dynamic_measurement",
      version: existing?.version || 1,
      status: "active",
      hash_alg: "sha256",
      description: existing?.description || `${selectedNode.hostname} 的 TPCM 环境动态度量对象配置`,
      content: {
        policy_scope: "tpcm_dynamic_measurement",
        node_dynamic_measure_enabled: nodeEnabled,
        dynamic_measure_required: nodeEnabled,
        environment_object_configs: objectConfigs,
        environment_objects: enabledObjects,
        environment_interval_milli: defaultInterval,
        delete_unmanaged_objects: false,
        keylime_artifact: "opentcsm_dynamic_measurement_policy"
      },
      protected_paths: [],
      excludes: [],
      source: {
        executor: "ansible",
        managed_by: "keylime-openstack",
        scope: "node",
        node: selectedNode.hostname
      },
      target_node_ids: [Number(selectedNode.id)],
      deploy_now: this.tpcmDynamicGlobalEnabled
    };
  },
  saveDynamicMeasurementConfig() {
    let payload;
    try {
      payload = this.dynamicMeasurementPayload();
    } catch (error) {
      this.showNotice("bad", error.message);
      return;
    }
    const existing = this.dynamicMeasurementPolicy;
    const globalEnabled = this.tpcmDynamicGlobalEnabled;
    this.requireToken({
      title: "确认保存动态度量配置",
      message: globalEnabled
        ? "将保存动态度量对象配置，并使配置在目标节点上生效。"
        : "将保存动态度量对象配置；当前全局控制关闭，节点配置暂不生效。",
      confirmText: globalEnabled ? "保存并生效" : "保存配置",
      action: async (token) => {
        const path = existing ? `/api/policies/${existing.id}` : "/api/policies";
        const method = existing ? "PUT" : "POST";
        await this.requestJson(path, {
          method,
          body: JSON.stringify(payload)
        }, token);
        this.dynamicFormDirty = false;
        this.showNotice("ok", globalEnabled ? "动态度量配置已进入生效流程" : "动态度量配置已保存");
        await this.refreshAll(false);
        this.syncDynamicFormFromPolicy(true);
      }
    });
  },
};
