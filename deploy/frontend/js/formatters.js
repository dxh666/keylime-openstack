import { DYNAMIC_MEASUREMENT_OBJECTS } from "./policies.js";

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
      tpcm_dynamic_policy_save: "动态度量策略保存",
      tpcm_dynamic_policy_apply_queued: "动态度量策略生效已入队",
      tpcm_dynamic_policy_apply: "动态度量策略生效",
      tpcm_dynamic_global_switch: "动态度量全局控制",
      host_integrity_evidence_collect: "采集节点完整性证据",
      opentcsm_evidence_collect: "采集 TPCM 可信报告",
      opentcsm_access_check: "OpenTCSM 接入检查"
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
    if (event.severity === "warning") return "异常";
    return "失败";
  },
  auditResultClass(event) {
    const result = this.auditResult(event);
    if (["失败", "异常", "授权异常"].includes(result)) return "bad";
    if (["已入队", "处理中"].includes(result)) return "warn";
    return this.severityClass(event.severity);
  },
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
  parseLines(text) {
    return String(text || "").split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
  },
  policyArtifactText(policy) {
    const artifact = policy?.source?.keylime_artifact || policy?.content?.keylime_artifact || "";
    if (artifact === "measured_boot_refstate") return "可信启动参考状态";
    if (artifact === "measured_boot_refstate_or_tpm_policy") return "可信启动策略";
    if (artifact === "tpm_pcr_quote_policy") return "TPM PCR Quote 策略";
    if (artifact === "runtime_policy") return "IMA 运行时策略";
    if (artifact === "opentcsm_dynamic_measurement_policy") return "TPCM 动态度量策略";
    return artifact || "-";
  },
  policyDynamicObjectsText(policy) {
    const names = {
      kernel_section: "内核代码段",
      syscall_table: "系统调用表",
      idt_table: "中断描述符表"
    };
    const nodeEnabled = policy?.content?.node_dynamic_measure_enabled !== false && policy?.content?.dynamic_measure_required !== false;
    const configs = policy?.content?.environment_object_configs || {};
    if (Object.keys(configs).length) {
      const objectText = DYNAMIC_MEASUREMENT_OBJECTS.map((item) => {
        const config = configs[item.key] || {};
        const state = config.enabled ? "开启" : "关闭";
        const interval = config.enabled ? ` / ${config.interval_milli || 60000} ms` : "";
        return `${item.label}：${state}${interval}`;
      }).join("、");
      return `${nodeEnabled ? "节点总开关：开启" : "节点总开关：关闭"}；${objectText}`;
    }
    const objects = policy?.content?.environment_objects || ["kernel_section", "syscall_table", "idt_table"];
    return objects.length ? objects.map((item) => names[item] || item).join("、") : "-";
  },
  policyDynamicIntervalText(policy) {
    const configs = policy?.content?.environment_object_configs || {};
    const intervals = [...new Set(
      Object.values(configs)
        .filter((config) => config?.enabled)
        .map((config) => `${config.interval_milli || 60000} ms`)
    )];
    if (intervals.length) return intervals.join("、");
    return `${policy?.content?.environment_interval_milli ?? 60000} ms`;
  },
  bindingNames(policy) {
    return (policy.bindings || []).map((item) => item.target_name).join("、") || "-";
  },
  bindingState(policy) {
    const states = [...new Set((policy.bindings || []).map((item) => item.application_status))];
    return states.length === 1 ? states[0] : states.length ? "mixed" : "not_deployed";
  },
  bindingStateText(policy) {
    const state = this.bindingState(policy);
    return state === "mixed" ? "状态不一致" : this.deploymentText(state);
  },
  policyEffectiveVersion(policy) {
    const applied = (policy.bindings || [])
      .filter((binding) => binding.application_status === "applied" && binding.applied_at)
      .sort((a, b) => new Date(b.applied_at).getTime() - new Date(a.applied_at).getTime());
    if (!applied.length) return "-";
    return this.formatTime(applied[0].applied_at);
  },
  policyPcrText(policy) {
    const pcrs = policy?.content?.pcrs || [];
    return pcrs.length ? pcrs.map((pcr) => `PCR${pcr}`).join("、") : "PCR0-7";
  },
  policyFallbackPcrText(policy) {
    const pcrs = policy?.content?.fallback_pcrs || [7];
    return pcrs.length ? pcrs.map((pcr) => `PCR${pcr}`).join("、") : "PCR7";
  },
  policySecureBootText(policy) {
    return policy?.content?.secure_boot_required === false ? "不强制" : "要求启用";
  },
  bindingEvidenceText(binding) {
    const keylimePolicy = binding?.keylime_policy || {};
    const evidenceType = keylimePolicy.evidence_type || "";
    const evidenceName = evidenceType === "tpm_event_log"
      ? "TPM Event Log"
      : evidenceType === "tpm_pcr_quote"
        ? "TPM PCR Quote"
      : evidenceType === "ima_measurement_list"
        ? "IMA 度量列表"
      : evidenceType === "tpcm_dynamic_measurement"
        ? "TPCM 动态度量"
        : "节点证据";
    const evidenceHash = keylimePolicy.evidence_sha256 || "";
    const policyHash = keylimePolicy.content_sha256 || "";
    if (evidenceHash && policyHash) {
      return `${evidenceName} ${evidenceHash.slice(0, 12)} / 策略 ${policyHash.slice(0, 12)}`;
    }
    if (evidenceHash) return `${evidenceName} ${evidenceHash.slice(0, 12)}`;
    if (policyHash) return `策略 ${policyHash.slice(0, 12)}`;
    return "-";
  },
  canDeployPolicy(policy) {
    const retryable = new Set(["not_deployed", "failed", "awaiting_reboot"]);
    return (policy.bindings || []).some((binding) => retryable.has(binding.application_status));
  },
  deployActionText(policy, scope = "") {
    if (policy?.policy_type === "tpcm_dynamic_measurement") {
      return scope === "node" ? "下发此节点策略" : scope === "all" ? "下发全部节点策略" : "下发策略";
    }
    return scope === "node" ? "更新此节点基线" : scope === "all" ? "更新全部节点基线" : "更新基线";
  },
  prettyJson(value) {
    return JSON.stringify(value || {}, null, 2);
  },
  listText(items) {
    return (items || []).length ? items.join("\n") : "-";
  }
};
