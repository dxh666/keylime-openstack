const EVENT_TEXT = {
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
  tpcm_global_policy_apply_queued: "TPCM 全局策略下发已入队",
  tpcm_global_policy_apply: "TPCM 全局策略下发",
  tpcm_boot_hardware_apply: "TPCM 可信启动硬件下发",
  host_integrity_evidence_collect: "采集节点完整性证据",
  opentcsm_evidence_collect: "采集 TPCM 可信报告",
  opentcsm_access_check: "TPCM 接入检查"
};

const MESSAGE_TEXT = {
  "trusted node registrations synchronized": "同步可信节点纳管",
  "trusted node verification completed": "可信节点验证完成",
  "trusted node verification failed": "可信节点验证失败",
  "trusted node registration updated": "可信节点纳管配置已更新",
  "collected OpenTCSM/Hygon TPCM evidence from node": "采集 TPCM 可信报告",
  "no evidence reported by opentcsm_tpcm": "TPCM 可信代理尚未上报证据",
  "no evidence reported by keylime": "TPM 可信代理尚未上报证据",
  TRUST_AGENT_UNMANAGED: "计算节点未纳入可信代理纳管",
  "trusted-root-agent-unmanaged": "计算节点未纳入可信代理纳管",
  "waiting-for-external-trust-agent-evidence": "可信代理证据尚未采集",
  WAITING_FOR_IMA_MISSING: "IMA 运行时策略未绑定或未下发",
  WAITING_FOR_BOOT_MISSING: "可信启动证据尚未采集",
  WAITING_FOR_BOOT_FAIL: "可信启动证据未通过",
  WAITING_FOR_BOOT_UNCONFIGURED: "可信启动策略未生效",
  WAITING_FOR_BOOT_STALE: "可信启动证据已过期",
  WAITING_FOR_IMA_FAIL: "IMA 运行时证据未通过",
  WAITING_FOR_TPCM_DYNAMIC_MEASUREMENT: "环境动态度量证据尚未采集",
  WAITING_FOR_TPCM_DYNAMIC_MEASUREMENT_MISSING: "环境动态度量证据尚未采集",
  WAITING_FOR_TPCM_DYNAMIC_MEASUREMENT_FAIL: "环境动态度量证据未通过",
  WAITING_FOR_TPCM_DYNAMIC_MEASUREMENT_UNCONFIGURED: "环境动态度量策略未生效",
  WAITING_FOR_TPCM_DYNAMIC_MEASUREMENT_DISABLED: "环境动态度量策略已关闭",
  WAITING_FOR_TPCM_DYNAMIC_MEASUREMENT_STALE: "环境动态度量证据已过期",
  WAITING_FOR_BOOT_FAIL_IMA_FAIL: "可信启动或 IMA 运行时证据未通过",
  WAITING_FOR_BOOT_FAIL_IMA_MISSING: "可信启动未通过，IMA 运行时策略未绑定或未下发",
  WAITING_FOR_BOOT_MISSING_IMA_MISSING: "可信启动与 IMA 运行时证据尚未完备",
  WAITING_FOR_EVM: "EVM 可信能力尚未启用",
  "login succeeded": "用户登录成功",
  "login failed": "用户登录失败",
  "logout succeeded": "用户注销成功",
  "created trust policy": "新增可信策略",
  "updated trust policy": "更新可信策略",
  "deleted trust policy": "删除可信策略",
  "queued policy deployment": "策略下发已入队",
  "queued node policy deployment": "节点策略下发已入队",
  "TPCM trusted boot hardware apply succeeded": "TPCM 可信启动硬件下发成功",
  "TPCM trusted boot hardware apply failed": "TPCM 可信启动硬件下发失败",
  "TPCM trusted boot hardware apply precheck failed.": "TPCM 可信启动硬件下发预检失败",
  "TPCM boot authorization material is not configured.": "TPCM 启动度量授权材料未配置",
  "TPCM boot authorization material is invalid.": "TPCM 启动度量授权材料无效",
  "TPCM rejected the boot policy authorization material.": "TPCM 拒绝启动度量授权材料",
  "TPCM rejected the boot reference update. Check UID, key material, current TPCM policy and reference operation.": "TPCM 拒绝启动参考值写入，请检查 UID、授权密钥、当前 TPCM 策略和写入操作",
  "TPCM boot measurement control switch failed.": "TPCM 启动度量控制开关开启失败",
  "TPCM trusted boot hardware apply could not be verified.": "TPCM 可信启动硬件下发后验证失败",
  write_boot_references_and_enable_control: "写入启动参考值并开启控制"
};

const RESULT_TEXT = {
  success: "成功",
  succeeded: "成功",
  ok: "成功",
  pass: "通过",
  passed: "通过",
  fail: "失败",
  failed: "失败",
  error: "失败",
  warning: "警告",
  warn: "提醒",
  pending: "待处理",
  queued: "已入队",
  running: "处理中"
};

const LOG_TYPE_TEXT = {
  dynamic_measurement: "动态度量日志",
  tpcm_authorization: "TPCM 授权日志",
  trusted_boot: "可信启动日志",
  measured_boot: "可信启动日志",
  ima_runtime: "IMA 运行时日志",
  node_management: "节点纳管日志",
  trust_verification: "可信验证日志",
  system_operation: "系统操作日志",
  tpcm_global_policy: "TPCM 全局策略日志"
};

function translateMessage(value) {
  const raw = String(value || "").trim();
  return MESSAGE_TEXT[raw] || EVENT_TEXT[raw] || raw;
}

function translateResult(value) {
  const raw = String(value || "").trim();
  const key = raw.toLowerCase();
  return RESULT_TEXT[key] || raw;
}

function firstNonEmpty(...values) {
  for (const value of values) {
    if (value === null || value === undefined) continue;
    const text = String(value).trim();
    if (text) return text;
  }
  return "";
}

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
    return EVENT_TEXT[event.event_type] || translateMessage(event.message) || event.event_type || "-";
  },
  auditDetails(event) {
    return event?.event_details || {};
  },
  auditType(event) {
    const details = this.auditDetails(event);
    if (LOG_TYPE_TEXT[details.log_type]) return LOG_TYPE_TEXT[details.log_type];
    return this.auditMessage(event);
  },
  auditSubject(event) {
    const details = this.auditDetails(event);
    if (event.event_type === "trusted_node_registration_sync") return "可信代理";
    if (event.event_type === "auth_login" || event.event_type === "auth_logout") {
      return event.actor || details.subject_name || "admin";
    }
    return details.subject_name || details.provider || event.actor || "-";
  },
  auditObject(event) {
    const details = this.auditDetails(event);
    if (event.event_type === "trusted_node_registration_sync") return "计算节点";
    if (event.event_type === "auth_login" || event.event_type === "auth_logout") return "管理系统";
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
  auditSummary(event) {
    const details = this.auditDetails(event);
    return firstNonEmpty(
      details.reason,
      details.summary,
      details.message,
      details.error,
      details.warning,
      details.warning_reason,
      event.message,
      this.auditHash(event)
    );
  },
  auditSummaryText(event) {
    const value = this.auditSummary(event);
    if (!value) return "-";
    const translated = translateMessage(translateResult(value));
    return translated.length > 32 ? `${translated.slice(0, 32)}...` : translated;
  },
  auditOperation(event) {
    const details = this.auditDetails(event);
    return translateMessage(details.operation) || this.auditMessage(event);
  },
  auditResult(event) {
    const details = this.auditDetails(event);
    if (details.result) return translateResult(details.result);
    if (event.severity === "info") return "成功";
    if (event.severity === "warning") return "警告";
    return "失败";
  },
  auditResultClass(event) {
    const result = this.auditResult(event);
    if (["失败", "异常", "授权异常", "不通过"].includes(result)) return "bad";
    if (["已入队", "处理中", "警告", "提醒"].includes(result)) return "warn";
    return this.severityClass(event.severity);
  },
};
