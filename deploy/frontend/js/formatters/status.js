export const statusFormatters = {
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
};
