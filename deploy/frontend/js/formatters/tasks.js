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
  taskTargetText(task) {
    if (task?.target) return task.target;
    if (task?.task_type === "sync" && task?.requested_by === "worker") return "后台同步";
    if (task?.task_type === "sync") return "全部计算节点";
    if (task?.task_type === "policy_deploy") return "策略目标节点";
    return "-";
  },
  taskSummaryText(task) {
    if (task?.error) return task.error;
    if (task?.task_type === "sync" && task?.requested_by === "worker") {
      if (task.status === "failed") return "后台可信状态同步失败";
      return "系统周期同步，已从任务列表折叠";
    }
    if (task?.task_type === "sync") return "同步 OpenStack 节点、可信代理与验证结果";
    if (task?.task_type === "policy_deploy") {
      const policyType = task.result?.policy_type || task.task_args?.policy_type || "";
      if (policyType === "measured_boot") return "应用可信启动策略并绑定节点基线";
      if (policyType === "tpcm_dynamic_measurement") return "应用环境动态度量策略";
      return "下发可信策略到目标节点";
    }
    if (task?.task_type === "opentcsm_collect") return "采集 TPCM 可信报告";
    return "-";
  },
};
