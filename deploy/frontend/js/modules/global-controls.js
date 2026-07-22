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
