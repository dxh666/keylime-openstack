export const taskFormatters = {
  taskTypeText(value) {
    const names = {
      sync: "可信状态同步",
      policy_deploy: "策略下发",
      opentcsm_collect: "采集 TPCM 可信报告"
    };
    return names[value] || value || "任务";
  },
  taskStatusText(value) {
    const names = {
      queued: "等待执行",
      running: "执行中",
      success: "成功",
      failed: "失败"
    };
    return names[value] || value || "-";
  },
  taskSeverity(value) {
    return value === "failed" ? "重要" : value === "running" || value === "queued" ? "提醒" : "信息";
  },
  taskSeverityClass(value) {
    return value === "failed" ? "bad" : value === "running" || value === "queued" ? "warn" : "ok";
  },
};
