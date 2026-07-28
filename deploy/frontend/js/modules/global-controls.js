const GLOBAL_POLICY_CONTROLS = [
  {
    key: "boot_measure_on",
    label: "TPCM 启动度量",
    control: "toggle",
    onText: "硬件启动度量已开启",
    offText: "硬件启动度量未开启"
  },
  {
    key: "boot_control",
    label: "TPCM 启动控制",
    control: "toggle",
    risk: "high",
    onText: "启动控制已开启",
    offText: "启动控制未开启"
  },
  {
    key: "dynamic_measure_on",
    label: "TPCM 动态度量",
    control: "toggle",
    onText: "硬件动态度量已开启",
    offText: "硬件动态度量未开启"
  },
  {
    key: "dmeasure_max_busy_delay",
    label: "动态忙等待上限",
    control: "number",
    min: 60,
    max: 86400,
    unit: "秒"
  }
];

export const globalControlsComputed = {
  tpcmDynamicGlobalControl() {
    return this.globalControls.tpcm_dynamic_measurement || { enabled: true, source: "default", updated_at: null };
  },
  tpcmDynamicGlobalEnabled() {
    return this.tpcmDynamicGlobalControl.enabled !== false;
  },
  tpcmGlobalPolicyControl() {
    return this.globalControls.tpcm_global_policy || { ok: true, nodes_total: 0, fields: {}, nodes: [] };
  },
  trustCapabilityItems() {
    const policy = this.tpcmGlobalPolicyControl;
    const fields = policy.fields || {};
    const nodesTotal = policy.nodes_total || 0;
    return GLOBAL_POLICY_CONTROLS.map((control) => {
      const field = fields[control.key] || {};
      const value = field.value;
      const knownNodes = field.known_nodes ?? 0;
      const enabled = value === true;
      const mixed = value === null || value === undefined;
      const summary = this.globalPolicySummary(control, field, nodesTotal, knownNodes);
      return {
        ...control,
        enabled,
        mixed,
        value,
        writable: field.writable !== false,
        actionable: nodesTotal > 0 && field.writable !== false,
        summary
      };
    });
  },
};

export const globalControlsMethods = {
  syncGlobalPolicyDrafts() {
    const fields = this.tpcmGlobalPolicyControl.fields || {};
    for (const control of GLOBAL_POLICY_CONTROLS) {
      if (control.control !== "number") continue;
      const field = fields[control.key] || {};
      if (field.value === null || field.value === undefined) continue;
      if (this.globalPolicyDrafts[control.key] === undefined) {
        this.globalPolicyDrafts = {
          ...this.globalPolicyDrafts,
          [control.key]: field.value
        };
      }
    }
  },
  globalPolicySummary(control, field, nodesTotal, knownNodes) {
    if (!nodesTotal) return "暂无已纳管 TPCM 计算节点";
    const coverage = `已采集 ${knownNodes}/${nodesTotal} 个节点`;
    if (control.control === "number") {
      const value = field.value ?? "-";
      const unit = field.unit || control.unit || "";
      return `${coverage}，当前值 ${value}${unit}`;
    }
    if (field.value === true) return `${coverage}，${control.onText}`;
    if (field.value === false) return `${coverage}，${control.offText}`;
    return `${coverage}，各节点状态不一致或未知`;
  },
  toggleGlobalTrustCapability(item) {
    if (!item.actionable || item.control !== "toggle") return;
    const nextEnabled = !item.enabled;
    const highRisk = item.risk === "high";
    this.requireToken({
      title: nextEnabled ? `确认开启${item.label}` : `确认关闭${item.label}`,
      message: highRisk
        ? "启动控制可能影响节点下一次启动可信判定，请确认启动基线已经建立并完成重启验证。"
        : "该操作会对所有已纳管 TPCM 计算节点下发全局策略控制。",
      confirmText: nextEnabled ? "开启并下发" : "关闭并下发",
      action: async (token) => {
        if (item.key === "dynamic_measure_on") {
          await this.requestJson("/api/policies/tpcm-dynamic/global-switch", {
            method: "POST",
            body: JSON.stringify({ enabled: nextEnabled })
          }, token);
        } else {
          await this.requestJson("/api/system/tpcm/global-policy", {
            method: "POST",
            body: JSON.stringify({ fields: { [item.key]: nextEnabled } })
          }, token);
        }
        this.showNotice("ok", `${item.label}下发任务已进入队列`);
        await this.refreshAll(false);
      }
    });
  },
  applyGlobalPolicyNumber(item) {
    if (!item.actionable || item.control !== "number") return;
    const value = Number(this.globalPolicyDrafts[item.key]);
    if (!Number.isInteger(value)) return this.showNotice("bad", "请输入整数。");
    if (item.min !== undefined && value < item.min) return this.showNotice("bad", `取值不能小于 ${item.min}。`);
    if (item.max !== undefined && value > item.max) return this.showNotice("bad", `取值不能大于 ${item.max}。`);
    this.requireToken({
      title: `确认设置${item.label}`,
      message: "该操作会对所有已纳管 TPCM 计算节点下发全局策略控制。",
      confirmText: "设置并下发",
      action: async (token) => {
        await this.requestJson("/api/system/tpcm/global-policy", {
          method: "POST",
          body: JSON.stringify({ fields: { [item.key]: value } })
        }, token);
        this.showNotice("ok", `${item.label}下发任务已进入队列`);
        await this.refreshAll(false);
      }
    });
  },
};
