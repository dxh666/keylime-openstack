import { VIEW_TITLES } from "../policies.js";

export const shellComputed = {
  currentPolicyType() {
    return this.policyTypes.find((item) => item.key === this.activePolicyType) || this.policyTypes[0];
  },
  currentTitle() {
    if (this.view === "policies") return this.currentPolicyType.label;
    return VIEW_TITLES[this.view] || "管理控制台";
  },
  currentTimeText() {
    return this.currentTime.toLocaleString("zh-CN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false
    });
  },
};
