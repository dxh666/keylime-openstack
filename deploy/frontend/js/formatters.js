import { policyFormatters } from "./formatters/policies.js";
import { trustFormatters } from "./formatters/trust.js";
import { auditFormatters } from "./formatters/audit.js";
import { taskFormatters } from "./formatters/tasks.js";

export const displayMethods = {
  ...trustFormatters,
  statusText(value) {
    const names = {
      active: "启用",
      draft: "草稿",
      disabled: "停用"
    };
    return names[value] || value || "-";
  },
  statusClass(value) {
    if (value === "active") return "ok";
    if (value === "disabled") return "bad";
    return "warn";
  },
  deploymentText(value) {
    const names = {
      queued: "等待下发",
      applying: "下发中",
      applied: "已下发",
      external_pending: "外部代理待接入",
      awaiting_reboot: "等待重启",
      failed: "下发失败",
      superseded: "已替换",
      not_deployed: "未下发"
    };
    return names[value] || value || "-";
  },
  deploymentClass(value) {
    if (value === "applied") return "ok";
    if (value === "failed") return "bad";
    return "warn";
  },
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
  timestampFromSeconds(value) {
    if (!value) return "";
    const seconds = Number(value);
    if (!Number.isFinite(seconds) || seconds <= 0) return "";
    return new Date(seconds * 1000).toISOString();
  },
  formatTime(value) {
    if (!value) return "-";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "-";
    return date.toLocaleString("zh-CN", { hour12: false });
  },
  ...auditFormatters,
  ...taskFormatters,
  ...policyFormatters,
  prettyJson(value) {
    return JSON.stringify(value || {}, null, 2);
  },
  listText(items) {
    return (items || []).length ? items.join("\n") : "-";
  }
};
