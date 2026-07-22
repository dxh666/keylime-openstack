export const globalControlsComputed = {
  tpcmDynamicGlobalControl() {
    return this.globalControls.tpcm_dynamic_measurement || { enabled: true, source: "default", updated_at: null };
  },
  tpcmDynamicGlobalEnabled() {
    return this.tpcmDynamicGlobalControl.enabled !== false;
  },
  trustCapabilityItems() {
    const caps = this.keylime.trust_capabilities || {};
    const tpcmNodes = (this.keylime.nodes || []).filter((node) =>
      node.trust_agent_type === "opentcsm_tpcm" || node.trust_agent_name === "OpenTCSM"
    );
    const dynamicGlobalEnabled = this.tpcmDynamicGlobalEnabled;
    return [
      {
        key: "tpcm_dynamic",
        label: "TPCM 动态度量",
        enabled: dynamicGlobalEnabled,
        summary: dynamicGlobalEnabled ? "全局控制已开启" : "全局关闭，节点配置暂不生效",
        actionable: tpcmNodes.length > 0
      },
      { key: "boot", label: "可信启动", enabled: caps.boot === true },
      { key: "ima", label: "IMA 运行时", enabled: caps.ima === true },
      { key: "evm", label: "EVM", enabled: caps.evm === true },
      { key: "openstack_service", label: "OpenStack 服务状态", enabled: caps.openstack_service === true }
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
        ? "将开启所有 OpenTCSM 节点的动态度量总开关，并使对应节点策略生效。"
        : "将关闭所有 OpenTCSM 节点的动态度量总开关，并使对应节点策略生效。",
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
