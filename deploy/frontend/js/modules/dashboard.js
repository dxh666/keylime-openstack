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
      return {
        id: node.id,
        host: node.hostname,
        rawNode: node,
        keylimeNode,
        trustAgentType: node.trust_agent_type || keylimeNode.trust_agent_type || "keylime",
        trustAgentName: node.trust_agent_name || keylimeNode.trust_agent_name || "Keylime Agent",
        trustedRoot: node.trusted_root || keylimeNode.trusted_root || "TPM 2.0",
        managementIp: this.primaryController.management_ip || "-",
        ownIp: node.management_ip || node.keylime_agent_ip || keylimeNode.agent_ip || "-",
        openstackText: this.openStackComputeText(node.openstack_state),
        openstackClass: this.openStackComputeClass(node.openstack_state),
        managed: this.keylimeManagedText(node, keylimeNode),
        managedClass: this.keylimeManagedClass(node, keylimeNode),
        trusted: keylimeNode.trusted,
        trustText: this.trustText(keylimeNode.trusted),
        trustClass: keylimeNode.trusted === true ? "ok" : keylimeNode.trusted === false ? "bad" : "warn",
        attestationTime: this.proofTime(keylimeNode),
        reason: keylimeNode.reason || keylimeNode.last_event_id || "-"
      };
    });
  },
  dashboardSummary() {
    const total = this.computeRows.length || this.keylime.nodes_total || 0;
    const trusted = this.computeRows.filter((node) => node.trusted === true).length || this.keylime.nodes_trusted || 0;
    const untrusted = Math.max(total - trusted, 0);
    const managed = this.computeRows.filter((node) => node.managedClass === "ok").length;
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
      .filter((node) => node.trusted === false || node.status === "error")
      .map((node) => ({
        target: node.host || node.agent_uuid || "-",
        severity: node.status === "error" ? "重要" : "提醒",
        message: node.reason || node.last_event_id || "可信状态未通过",
        remediation: node.remediation?.summary || "-"
      }));
  },
  keylimeStatusClass() {
    if (this.keylimeError) return "bad";
    if (this.keylime.ok === true) return "ok";
    if (this.keylime.ok === false) return "bad";
    return "warn";
  }
};
