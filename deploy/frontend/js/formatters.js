import { policyFormatters } from "./formatters/policies.js";
import { auditFormatters } from "./formatters/audit.js";
import { taskFormatters } from "./formatters/tasks.js";

export const displayMethods = {
  yesNo(value) {
    return value ? "是" : "否";
  },
  enabledText(value) {
    if (value === true) return "已开启";
    if (value === false) return "未开启";
    return "-";
  },
  trustStatusText(value) {
    const normalized = String(value || "").toLowerCase();
    if (normalized === "trusted") return "可信";
    if (normalized === "untrusted") return "不可信";
    return "未知";
  },
  reportEvalText(value) {
    if (value === null || value === undefined || value === "") return "-";
    return `${value}`;
  },
  shortHash(value) {
    const text = String(value || "");
    return text ? text.slice(0, 16) : "-";
  },
  countText(value) {
    if (value === null || value === undefined || value === "") return "-";
    return `${value}`;
  },
  failureText(value) {
    const entries = Object.entries(value || {});
    if (!entries.length) return "无";
    return entries.map(([key, item]) => `${key}: ${item}`).join("\n");
  },
  collectionStatusText(value) {
    const normalized = String(value || "").toLowerCase();
    if (normalized === "collected") return "正常";
    if (normalized === "pending") return "等待采集";
    if (normalized === "error") return "采集异常";
    return "未知";
  },
  collectionStatusClass(value) {
    const normalized = String(value || "").toLowerCase();
    if (normalized === "collected") return "ok";
    if (normalized === "error") return "bad";
    return "warn";
  },
  evidenceFreshText(value) {
    const bootFresh = value?.boot === true;
    const runtimeFresh = value?.runtime === true;
    if (bootFresh && runtimeFresh) return "有效";
    if (value?.boot === false || value?.runtime === false) return "已过期";
    return "未知";
  },
  evidenceFreshClass(value) {
    const text = this.evidenceFreshText(value);
    if (text === "有效") return "ok";
    if (text === "已过期") return "bad";
    return "warn";
  },
  collectionErrorText(keylimeNode, report) {
    const errors = report.errors || [];
    if (errors.length) return this.listText(errors);
    if (keylimeNode.status === "error") return keylimeNode.reason || "采集异常";
    return "无";
  },
  tpcmHistoryText(item) {
    const parts = [
      `可信状态：${this.trustResultText(item.trusted)}`,
      `启动度量：${this.stateText(item.boot_status || item.status)}`,
      `动态度量：${this.stateText(item.dynamic_measurement_status)}`,
      `失败计数：${item.failure_count ?? "-"}`,
      `报告哈希：${this.shortHash(item.trust_report_sha256)}`
    ];
    return parts.join("\n");
  },
  opentcsmAccessCheckItems(result) {
    const items = [
      {
        label: "检查结论",
        value: result?.ok ? "通过" : "未通过",
        state: result?.ok ? "ok" : "bad"
      },
      { label: "检查时间", value: this.formatTime(result?.checked_at_utc) }
    ];
    for (const check of result?.checks || []) {
      const parts = [
        this.accessStatusText(check.status),
        check.summary,
        check.detail
      ].filter(Boolean);
      items.push({
        label: check.name || "检查项",
        value: parts.join("\n")
      });
    }
    return items;
  },
  accessStatusText(value) {
    const normalized = String(value || "").toLowerCase();
    if (normalized === "pass") return "通过";
    if (normalized === "fail") return "失败";
    return "未知";
  },
  trustResultText(value) {
    if (value === true) return "可信";
    if (value === false) return "不可信";
    return "未知";
  },
  trustText(value) {
    if (value === true) return "可信";
    if (value === false) return "不可信";
    return "未知";
  },
  stateText(value) {
    const normalized = String(value || "").toLowerCase();
    const names = {
      pass: "通过",
      fail: "失败",
      missing: "缺失",
      unknown: "未知",
      none: "-",
      collected: "已采集",
      error: "异常"
    };
    return names[normalized] || value || "-";
  },
  evidenceClass(value) {
    const normalized = String(value || "").toLowerCase();
    if (normalized === "pass") return "ok";
    if (normalized === "fail") return "bad";
    return "warn";
  },
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
