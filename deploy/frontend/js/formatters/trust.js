export const trustFormatters = {
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
  trustedRootTypeText(value) {
    const normalized = String(value || "").toLowerCase();
    if (normalized === "tpm") return "TPM";
    if (normalized === "tpcm") return "TPCM";
    return "unknown";
  },
  registrationStatusText(value) {
    const names = {
      discovered: "已发现",
      registered: "已注册",
      awaiting_policy: "待下发策略",
      verifier_enrolled: "验证器已纳管",
      verified: "已验证",
      untrusted: "验证异常",
      unmanaged: "未纳管",
      conflict: "纳管冲突"
    };
    return names[String(value || "").toLowerCase()] || value || "-";
  },
  registrationStatusClass(value) {
    const normalized = String(value || "").toLowerCase();
    if (["verified", "registered", "verifier_enrolled"].includes(normalized)) return "ok";
    if (["conflict", "untrusted"].includes(normalized)) return "bad";
    return "warn";
  },
  trustCapabilityText(capabilities) {
    const caps = capabilities || {};
    const labels = [];
    if (caps.trusted_boot === true) labels.push("可信启动");
    if (caps.ima_runtime === true) labels.push("IMA运行时");
    if (caps.tpcm_dynamic_measurement === true) labels.push("TPCM动态度量");
    return labels.length ? labels.join("、") : "-";
  },
  trustCapabilityItemText(item) {
    if (!item) return "-";
    const parts = [
      `状态：${this.stateText(item.status)}`,
      `策略：${item.policy_bound ? "已生效" : "未生效"}`,
      `能力：${item.policy_enabled ? "已开启" : "已关闭"}`,
      `证据：${item.provider || "-"} / ${item.evidence_type || "-"}`
    ];
    if (item.baseline?.content_sha256) {
      parts.push(`基线：${this.shortHash(item.baseline.content_sha256)}`);
    }
    if (item.baseline?.tpcm_write_status) {
      parts.push(`TPCM写入：${this.tpcmWriteStatusText(item.baseline.tpcm_write_status)}`);
    }
    if (item.reason) parts.push(`说明：${item.reason}`);
    return parts.join("\n");
  },
  tpcmWriteStatusText(value) {
    const names = {
      not_enabled: "未启用",
      authorization_missing: "授权材料缺失",
      reserved_not_executed: "已预留未执行",
      written: "已写入",
      failed: "写入失败"
    };
    return names[String(value || "")] || value || "-";
  },
  evidenceSummaryText(summary) {
    const evidence = summary?.evidence || {};
    const parts = [];
    if (evidence.boot) parts.push(`可信启动：${this.stateText(evidence.boot)}`);
    if (evidence.runtime) parts.push(`运行时：${this.stateText(evidence.runtime)}`);
    if (evidence.evm) parts.push(`EVM：${this.stateText(evidence.evm)}`);
    if (summary?.reason) parts.push(`原因：${summary.reason}`);
    return parts.length ? parts.join("\n") : "-";
  },
  bootMeasurementSummaryText(summary) {
    if (!summary || !Object.keys(summary).length) return "-";
    const parts = [
      `状态：${this.stateText(summary.status)}`,
      `开启：${this.enabledText(summary.enabled)}`,
      `记录数：${this.countText(summary.record_count)}`,
      `基线数：${this.countText(summary.reference_count)}`,
      `基线：${summary.baseline_ready === true ? "已就绪" : "待确认"}`
    ];
    if (summary.records_preview?.length) {
      parts.push(`记录预览：${summary.records_preview.join("、")}`);
    }
    if (summary.references_preview?.length) {
      parts.push(`参考值预览：${summary.references_preview.join("、")}`);
    }
    if (summary.records_sha256) parts.push(`记录哈希：${this.shortHash(summary.records_sha256)}`);
    if (summary.references_sha256) parts.push(`参考值哈希：${this.shortHash(summary.references_sha256)}`);
    return parts.join("\n");
  },
  dynamicMeasurementSummaryText(summary) {
    if (!summary || !Object.keys(summary).length) return "-";
    const parts = [
      `状态：${this.stateText(summary.status)}`,
      `开启：${this.enabledText(summary.enabled)}`,
      `对象数：${this.countText(summary.object_count)}`,
      `度量次数：${this.countText(summary.dmeasure_times)}`
    ];
    if (summary.policy_sha256) parts.push(`策略哈希：${this.shortHash(summary.policy_sha256)}`);
    return parts.join("\n");
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
      stale: "已过期",
      disabled: "已关闭",
      queued: "待生效",
      applying: "生效中",
      awaiting_reboot: "待重启",
      external_pending: "待外部生效",
      superseded: "已替换",
      unsupported: "不支持",
      unconfigured: "未配置",
      not_deployed: "未下发",
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
    if (["fail", "stale", "error"].includes(normalized)) return "bad";
    return "warn";
  },
};
