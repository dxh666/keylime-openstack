export const openStackFormatters = {
  openStackComputeText(state) {
    if (!state) return "未知";
    const serviceStatus = String(state.service_status || "").toLowerCase();
    const serviceState = String(state.service_state || "").toLowerCase();
    if (serviceStatus === "enabled" && serviceState === "up") return "在线";
    if (serviceStatus === "disabled" || serviceState === "down") return "离线";
    return "未知";
  },
  openStackComputeClass(state) {
    if (!state) return "warn";
    const serviceStatus = String(state.service_status || "").toLowerCase();
    const serviceState = String(state.service_state || "").toLowerCase();
    if (serviceStatus === "enabled" && serviceState === "up") return "ok";
    if (serviceStatus === "disabled" || serviceState === "down") return "bad";
    return "warn";
  },
};
