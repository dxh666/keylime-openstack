import { DYNAMIC_MEASUREMENT_OBJECTS } from "../policies.js";

export const tpcmDynamicComputed = {
  dynamicTargetNodes() {
    return this.computeInventory.filter((node) => {
      const profile = node.trusted_node_profile || node;
      const capabilities = profile.capabilities || node.capabilities || {};
      return (
        profile.is_openstack_compute !== false &&
        profile.trust_managed === true &&
        profile.trusted_root_type === "tpcm" &&
        capabilities.tpcm_dynamic_measurement === true
      );
    });
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
