export const auditFormatters = {
  severityText(value) {
    const names = {
      info: "信息",
      warning: "提醒",
      error: "重要",
      critical: "重要"
    };
    return names[String(value || "").toLowerCase()] || value || "信息";
  },
  severityClass(value) {
    const normalized = String(value || "").toLowerCase();
    if (["error", "critical", "重要"].includes(normalized)) return "bad";
    if (["warning", "提醒"].includes(normalized)) return "warn";
    return "ok";
  },
  auditMessage(event) {
    const names = {
      policy_create: "新增策略",
      policy_update: "更新策略",
      policy_delete: "删除策略",
      policy_deploy_queued: "策略下发已入队",
      auth_login: "用户登录",
      auth_logout: "用户注销",
      trusted_node_registration_sync: "同步可信节点纳管",
      trusted_node_register: "更新可信节点纳管",
      trusted_node_verify: "可信节点验证",
      trust_decision: "可信状态判定",
      trust_agent_evidence_collect: "可信代理状态",
      keylime_evidence_collect: "采集 TPM 可信证据",
      openstack_state_refresh: "OpenStack 状态刷新",
      tpcm_dynamic_policy_save: "动态度量策略保存",
      tpcm_dynamic_policy_apply_queued: "动态度量策略生效已入队",
      tpcm_dynamic_policy_apply: "动态度量策略生效",
      tpcm_dynamic_global_switch: "动态度量全局控制",
      host_integrity_evidence_collect: "采集节点完整性证据",
      opentcsm_evidence_collect: "采集 TPCM 可信报告",
      opentcsm_access_check: "TPCM 接入检查"
    };
    return names[event.event_type] || event.message || event.event_type || "-";
  },
  auditDetails(event) {
    return event?.event_details || {};
  },
  auditType(event) {
    const details = this.auditDetails(event);
    if (details.log_type === "dynamic_measurement") return "动态度量日志";
    if (details.log_type === "tpcm_authorization") return "TPCM授权日志";
    if (details.log_type === "measured_boot") return "可信启动日志";
    if (details.log_type === "ima_runtime") return "IMA运行时日志";
    if (details.log_type === "node_management") return "节点纳管日志";
    if (details.log_type === "trust_verification") return "可信验证日志";
    if (details.log_type === "system_operation") return "系统操作日志";
    return this.auditMessage(event);
  },
  auditSubject(event) {
    const details = this.auditDetails(event);
    return details.subject_name || details.provider || "-";
  },
  auditObject(event) {
    const details = this.auditDetails(event);
    return details.object_name || event.target || "-";
  },
  auditHash(event) {
    const details = this.auditDetails(event);
    return details.measurement_baseline || details.hash || details.report_hash || details.evidence_sha256 || details.rendered_policy_sha256 || "";
  },
  auditHashText(event) {
    const value = this.auditHash(event);
    return value ? value.slice(0, 24) : "-";
  },
  auditOperation(event) {
    const details = this.auditDetails(event);
    return details.operation || this.auditMessage(event);
  },
  auditResult(event) {
    const details = this.auditDetails(event);
    if (details.result) return details.result;
    if (event.severity === "info") return "成功";
    if (event.severity === "warning") return "警告";
    return "失败";
  },
  auditResultClass(event) {
    const result = this.auditResult(event);
    if (["失败", "异常", "授权异常"].includes(result)) return "bad";
    if (["已入队", "处理中", "警告", "提醒"].includes(result)) return "warn";
    return this.severityClass(event.severity);
  },
};
