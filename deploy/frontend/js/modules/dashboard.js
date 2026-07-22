function alertMessageText(node) {
  if (node.trust_managed === false || node.status === "unmanaged") {
    return "计算节点未纳入可信代理纳管";
  }
  const reason = String(node.reason || node.last_event_id || "");
  const names = {
    TRUST_AGENT_UNMANAGED: "计算节点未纳入可信代理纳管",
    WAITING_FOR_IMA_MISSING: "IMA 运行时策略未绑定或未下发",
    WAITING_FOR_BOOT_MISSING: "可信启动证据尚未采集",
    WAITING_FOR_BOOT_FAIL: "可信启动证据未通过",
    WAITING_FOR_IMA_FAIL: "IMA 运行时证据未通过",
    WAITING_FOR_BOOT_FAIL_IMA_FAIL: "可信启动或 IMA 运行时证据未通过",
    WAITING_FOR_BOOT_FAIL_IMA_MISSING: "可信启动未通过，IMA 运行时策略未绑定或未下发",
    WAITING_FOR_BOOT_MISSING_IMA_MISSING: "可信启动与 IMA 运行时证据尚未完备",
    WAITING_FOR_EVM: "EVM 可信能力尚未启用"
  };
  return names[reason] || reason || "可信状态未通过";
}

function alertStateClass(node) {
  if (node.status === "error") return "bad";
  if (node.trust_managed === false || node.status === "unmanaged") return "warn";
  const reason = String(node.reason || node.last_event_id || "").toLowerCase();
  if (reason.includes("_fail") || reason.includes(".fail") || reason.includes("not_reachable")) {
    return "bad";
  }
  return "warn";
}

function alertSeverityText(node) {
  return alertStateClass(node) === "bad" ? "重要" : "提醒";
}

function alertRemediationText(node) {
  if (node.trust_managed === false || node.status === "unmanaged") {
    return "确认该 OpenStack 计算节点是否需要纳管；如需要，安装并配置 TPM/TPCM 可信代理后更新节点纳管配置。";
  }
  const summary = String(node.remediation?.summary || "");
  const reason = String(node.reason || node.last_event_id || "");
  const names = {
    "Compute node is not managed by a trusted-root agent.":
      "确认该 OpenStack 计算节点是否需要纳管；如需要，安装并配置 TPM/TPCM 可信代理后更新节点纳管配置。",
    "No IMA runtime policy is bound in Keylime verifier for this agent.":
      "TPM 可信验证器中未绑定该节点的 IMA 运行时策略；当前先保留，后续再处理策略下发与重启流程。",
    "IMA runtime policy does not match the live measurement list.":
      "IMA 运行时度量与已绑定策略不一致；比对实时度量后再重新生成或下发策略。",
    "IMA runtime evidence is not trusted; compare live measurements with the bound policy.":
      "IMA 运行时证据未通过；比对实时度量与已绑定策略。",
    "TPM/PCR boot evidence failed; do not refresh IMA runtime baseline first.":
      "TPM/PCR 可信启动证据未通过；先处理可信启动策略或启动证据。"
  };
  if (names[summary]) return names[summary];
  if (reason === "WAITING_FOR_IMA_MISSING") {
    return "TPM 可信验证器中未绑定该节点的 IMA 运行时策略；当前先保留，后续再处理策略下发与重启流程。";
  }
  return summary || "-";
}

export const dashboardComputed = {
  controllerNodes() {
    return this.nodes.filter((node) => node.role === "controller");
  },
  computeInventory() {
    return this.nodes.filter((node) => node.role === "compute" && node.enabled);
  },
  primaryController() {
    return this.controllerNodes[0] || {
      hostname: "csri10",
      management_ip: "172.31.100.10",
      role: "controller",
      facts: {}
    };
  },
  controlNodeInfo() {
    if (this.dashboard?.control_node) {
      const node = this.dashboard.control_node;
      return [
        { label: "主机名", value: node.hostname || "-" },
        { label: "IP 地址", value: node.management_ip || "-" },
        { label: "操作系统", value: node.operating_system || "-" },
        { label: "内核版本", value: node.kernel || "-" },
        { label: "CPU", value: node.cpu_model || "-" },
        { label: "CPU 核心数", value: node.cpu_count || "-" }
      ];
    }
    const node = this.primaryController;
    const facts = node.facts || {};
    const profile = node.hardware_profile || {};
    return [
      { label: "主机名", value: node.hostname || "-" },
      { label: "IP 地址", value: node.management_ip || "-" },
      { label: "操作系统", value: facts.os || this.osText(facts.kernel) },
      { label: "内核版本", value: facts.kernel || "-" },
      { label: "CPU", value: profile.model || facts.cpu_model || facts.cpu_vendor || "-" },
      { label: "CPU 核心数", value: facts.cpu_count || "-" }
    ];
  },
  controlRuntimeItems() {
    if (this.dashboard?.control_plane_status?.length) {
      return this.dashboard.control_plane_status;
    }
    return [
      {
        name: "管理 API",
        status: this.health?.ok ? "正常" : "待确认",
        state: this.health?.ok ? "ok" : "warn"
      },
      {
        name: "Keylime 接入",
        status: this.keylimeError ? "异常" : this.keylime.ok === null ? "检查中" : "正常",
        state: this.keylimeError ? "bad" : this.keylime.ok === null ? "warn" : "ok"
      },
      {
        name: "OpenStack 控制面",
        status: "待接入",
        state: "warn"
      },
      {
        name: "PostgreSQL",
        status: this.overview ? "正常" : "待确认",
        state: this.overview ? "ok" : "warn"
      }
    ];
  },
  onlineUsers() {
    return this.dashboard?.online_users?.length
      ? this.dashboard.online_users
      : [{ type: "HTTP", username: "admin", user_group: "Administrator", ip_address: "-" }];
  },
  keylimeNodesByHost() {
    const items = new Map();
    for (const node of this.keylime.nodes || []) {
      if (node.host) items.set(node.host, node);
    }
    return items;
  },
  keylimeNodesByUuid() {
    const items = new Map();
    for (const node of this.keylime.nodes || []) {
      if (node.agent_uuid) items.set(node.agent_uuid, node);
    }
    return items;
  },
  computeRows() {
    return this.computeInventory.map((node) => {
      const keylimeNode =
        this.keylimeNodesByHost.get(node.hostname) ||
        this.keylimeNodesByUuid.get(node.keylime_agent_uuid) ||
        {};
      const profile = node.trusted_node_profile || {};
      const capabilities = profile.capabilities || node.capabilities || {};
      const trustAgentType = node.trust_agent_type || keylimeNode.trust_agent_type || "unmanaged";
      const trustManaged = profile.trust_managed ?? node.trust_managed ?? keylimeNode.trust_managed ?? trustAgentType !== "unmanaged";
      const trustedRootType = profile.trusted_root_type || node.trusted_root_type || keylimeNode.trusted_root_type || "unknown";
      const trusted = keylimeNode.trusted ?? profile.last_evidence_summary?.trusted ?? null;
      const canCollectTpcmDynamic = (
        trustManaged === true &&
        trustedRootType === "tpcm" &&
        capabilities.tpcm_dynamic_measurement === true
      );
      const trustText = trustManaged ? this.trustText(trusted) : "未纳管";
      const trustClass = trustManaged
        ? trusted === true
          ? "ok"
          : trusted === false
            ? "bad"
            : "warn"
        : "warn";
      const proofTime = this.proofTime(keylimeNode);
      const profileTime = this.formatTime(profile.last_verified_at || node.last_verified_at);
      const attestationTime = trustManaged ? (proofTime !== "-" ? proofTime : profileTime) : "-";
      return {
        id: node.id,
        host: node.hostname,
        openstackComputeName: profile.openstack_compute_name || node.openstack_compute_name || node.hypervisor_name || node.hostname,
        rawNode: node,
        keylimeNode,
        trustAgentType,
        trustManaged,
        trustedRootType,
        canCollectTpcmDynamic,
        trustAgentName: node.trust_agent_name || keylimeNode.trust_agent_name || "Unmanaged",
        trustedRoot: node.trusted_root || keylimeNode.trusted_root || "unknown",
        managementIp: this.primaryController.management_ip || "-",
        ownIp: node.management_ip || node.keylime_agent_ip || keylimeNode.agent_ip || "-",
        openstackText: this.openStackComputeText(node.openstack_state, node),
        openstackClass: this.openStackComputeClass(node.openstack_state, node),
        managed: this.keylimeManagedText(node, keylimeNode),
        managedClass: this.keylimeManagedClass(node, keylimeNode),
        trusted,
        trustText,
        trustClass,
        attestationTime,
        reason: keylimeNode.reason || keylimeNode.last_event_id || "-"
      };
    });
  },
  dashboardSummary() {
    const total = this.computeRows.length || this.keylime.nodes_total || 0;
    const trusted = this.computeRows.filter((node) => node.trusted === true).length || this.keylime.nodes_trusted || 0;
    const untrusted = Math.max(total - trusted, 0);
    const managed = this.computeRows.filter((node) => node.trustManaged).length;
    return [
      { label: "计算节点", value: total, state: "" },
      { label: "可信节点", value: trusted, state: "ok" },
      { label: "异常节点", value: untrusted, state: untrusted ? "bad" : "ok" },
      { label: "已纳管节点", value: managed, state: "ok" }
    ];
  },
  recentEvents() {
    const items = [];
    for (const event of this.auditEvents.slice(0, 10)) {
      items.push({
        time: event.created_at,
        severity: this.severityText(event.severity),
        state: this.severityClass(event.severity),
        target: event.target || "-",
        message: this.auditMessage(event)
      });
    }
    for (const task of this.tasks.slice(0, 5)) {
      const time = task.finished_at || task.started_at;
      if (!time) continue;
      items.push({
        time,
        severity: this.taskSeverity(task.status),
        state: this.taskSeverityClass(task.status),
        target: task.target || task.task_type,
        message: `${this.taskTypeText(task.task_type)}：${this.taskStatusText(task.status)}`
      });
    }
    for (const node of this.keylime.nodes || []) {
      if (node.trusted === false) {
        items.push({
          time: this.timestampFromSeconds(node.last_received_quote),
          severity: "重要",
          state: "bad",
          target: node.host || node.agent_uuid || "-",
          message: `可信状态未通过：${node.reason || node.last_event_id || "原因待确认"}`
        });
      }
    }
    return items
      .filter((item) => item.time)
      .sort((a, b) => new Date(b.time).getTime() - new Date(a.time).getTime())
      .slice(0, 10);
  },
  alertRows() {
    return (this.keylime.nodes || [])
      .filter((node) => node.trust_managed === false || node.status === "unmanaged" || node.trusted === false || node.status === "error")
      .map((node) => ({
        target: node.host || node.agent_uuid || "-",
        severity: alertSeverityText(node),
        state: alertStateClass(node),
        message: alertMessageText(node),
        remediation: alertRemediationText(node)
      }));
  },
  keylimeStatusClass() {
    if (this.keylimeError) return "bad";
    if (this.keylime.ok === true) return "ok";
    if (this.keylime.ok === false) return "bad";
    return "warn";
  }
};
