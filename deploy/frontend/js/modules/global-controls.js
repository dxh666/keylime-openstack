export const globalControlsComputed = {
  tpcmDynamicGlobalControl() {
    return this.globalControls.tpcm_dynamic_measurement || { enabled: true, source: "default", updated_at: null };
  },
  tpcmDynamicGlobalEnabled() {
    return this.tpcmDynamicGlobalControl.enabled !== false;
  },
  trustCapabilityItems() {
    const inventory = this.nodes?.length ? this.nodes : this.keylime.nodes || [];
    const tpcmNodes = inventory.filter((node) =>
      node.trust_managed !== false &&
      (node.trusted_root_type === "tpcm" || node.trust_agent_type === "opentcsm_tpcm")
    );
    const dynamicGlobalEnabled = this.tpcmDynamicGlobalEnabled;
    return [
      {
        key: "tpcm_dynamic",
        label: "TPCM 动态度量",
        enabled: dynamicGlobalEnabled,
        summary: dynamicGlobalEnabled ? "全局控制已开启" : "全局关闭，节点配置暂不生效",
        actionable: tpcmNodes.length > 0
      }
    ];
  },
};

export const globalControlsMethods = {
  toggleGlobalTrustCapability(item) {
    if (item.key !== "tpcm_dynamic") return;
    const nextEnabled = !item.enabled;
    this.requireToken({
      title: nextEnabled ? "确认开启集群动态度量" : "确认关闭集群动态度量",
      message: nextEnabled
        ? "将开启所有 TPCM 纳管节点的动态度量总开关，并使对应节点策略生效。"
        : "将关闭所有 TPCM 纳管节点的动态度量总开关，并使对应节点策略生效。",
      confirmText: nextEnabled ? "开启并生效" : "关闭并生效",
      action: async (token) => {
        await this.requestJson("/api/policies/tpcm-dynamic/global-switch", {
          method: "POST",
          body: JSON.stringify({ enabled: nextEnabled })
        }, token);
        this.showNotice("ok", nextEnabled ? "集群动态度量开启任务已进入队列" : "集群动态度量关闭任务已进入队列");
        await this.refreshAll(false);
      }
    });
  },
};
