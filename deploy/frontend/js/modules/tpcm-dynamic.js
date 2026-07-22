import { DYNAMIC_MEASUREMENT_OBJECTS, emptyDynamicForm } from "../policies.js";
import { tpcmDynamicStatusMethods } from "./tpcm-dynamic-status.js";

export { tpcmDynamicComputed } from "./tpcm-dynamic-computed.js";


export const tpcmDynamicMethods = {
  ...tpcmDynamicStatusMethods,
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
