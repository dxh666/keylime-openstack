const { createApp } = Vue;

const POLICY_TYPES = [
  { key: "measured_boot", label: "可信启动", creatable: true },
  { key: "ima_runtime", label: "IMA 运行时策略", creatable: true }
];

const DEFAULT_IMA_POLICY = `dont_measure fsmagic=0x9fa0
dont_measure fsmagic=0x62656572
dont_measure fsmagic=0x64626720
dont_measure fsmagic=0x1021994
dont_measure fsmagic=0x73636673
dont_measure fsmagic=0x27e0eb
measure func=KEY_CHECK keyrings=.ima
measure func=BPRM_CHECK mask=MAY_EXEC
measure func=MMAP_CHECK mask=MAY_EXEC
measure func=MODULE_CHECK
measure func=FIRMWARE_CHECK
measure func=POLICY_CHECK`;

const DEFAULT_IMA_EXCLUDES = `^/var/lib/docker/containers/[0-9a-f]+/\\.tmp-config\\.v2\\.json.*$
^/var/lib/docker/containers/[0-9a-f]+/\\.tmp-hostconfig\\.json.*$
^/var/lib/docker/containers/[0-9a-f]+/[0-9a-f]+-json\\.log.*$
^/var/lib/docker/network/files/local-kv\\.db$
^/tmp/tmp[A-Za-z0-9._-]+$
^/var/log/journal/[0-9a-f]+/.*\\.journal$
^/var/lib/docker/.*$
^/var/lib/containerd/.*$
^/run/.*$
^/var/run/.*$
^/tmp/.*$
^/var/tmp/.*$
^/var/log/.*$
^/dev/.*$
^/etc/mtab$
^/root/\\.ansible/tmp/.*$
^/home/[^/]+/\\.ansible/tmp/.*$
^/var/lib/apt/.*$
^/var/cache/apt/.*$
^/var/lib/ubuntu-advantage/apt-esm/.*$
^/var/lib/update-notifier/.*$
^/var/lib/landscape/.*$
^/var/lib/openvswitch/.*\\.db$
^/run_command$
^/.*/__pycache__/.*\\.pyc$
^/usr/lib/python[0-9.]+/.*/__pycache__/.*\\.pyc$
^/usr/lib/python3/dist-packages/.*/__pycache__/.*\\.pyc$`;

const emptyPolicyForm = (policyType = "measured_boot") => ({
  name: "",
  description: "",
  targetNodeIds: [],
  pcrs: [0, 1, 2, 3, 4, 5, 6, 7],
  secureBootRequired: true,
  nodeImaPolicy: DEFAULT_IMA_POLICY,
  excludesText: DEFAULT_IMA_EXCLUDES,
  rebootAfterApply: false,
  policy_type: policyType
});

const viewTitles = {
  dashboard: "首页",
  control_nodes: "控制节点",
  compute_nodes: "计算节点",
  policies: "策略管理",
  global_policy: "全局策略控制",
  alerts: "告警中心",
  tasks: "任务中心",
  audit: "审计日志",
  settings: "系统设置"
};

createApp({
  data() {
    return {
      view: "dashboard",
      expandedGroups: {
        nodes: true,
        policies: true
      },
      policyTypes: POLICY_TYPES,
      activePolicyType: "measured_boot",
      busy: false,
      loading: true,
      apiError: "",
      keylimeError: "",
      notice: { kind: "", text: "" },
      userMenuOpen: false,
      health: null,
      dashboard: null,
      overview: null,
      keylime: { ok: null, nodes: [], nodes_total: 0, nodes_trusted: 0, trust_capabilities: {} },
      nodes: [],
      policies: [],
      auditEvents: [],
      tasks: [],
      createDialogOpen: false,
      createForm: emptyPolicyForm(),
      detailPolicy: null,
      detailNode: null,
      tokenDialog: {
        open: false,
        title: "",
        message: "",
        confirmText: "确认",
        token: "",
        action: null
      },
      timer: null,
      clockTimer: null,
      currentTime: new Date()
    };
  },
  computed: {
    currentPolicyType() {
      return this.policyTypes.find((item) => item.key === this.activePolicyType) || this.policyTypes[0];
    },
    currentTitle() {
      if (this.view === "policies") return this.currentPolicyType.label;
      return viewTitles[this.view] || "管理控制台";
    },
    policyGenerationTitle() {
      return this.activePolicyType === "measured_boot"
        ? "自动采集 TPM 启动基线"
        : "自动采集 IMA 运行基线";
    },
    policyGenerationSummary() {
      return this.activePolicyType === "measured_boot"
        ? "生成可信启动参考状态"
        : "生成 IMA 运行时策略";
    },
    currentTimeText() {
      return this.currentTime.toLocaleString("zh-CN", {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false
      });
    },
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
    filteredPolicies() {
      return this.policies.filter((policy) => policy.policy_type === this.activePolicyType);
    },
    trustCapabilityItems() {
      const caps = this.keylime.trust_capabilities || {};
      return [
        { key: "boot", label: "可信启动", enabled: caps.boot === true },
        { key: "ima", label: "IMA 运行时", enabled: caps.ima === true },
        { key: "evm", label: "EVM", enabled: caps.evm === true },
        { key: "openstack_service", label: "OpenStack 服务状态", enabled: caps.openstack_service === true }
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
    policyNamePlaceholder() {
      return this.activePolicyType === "measured_boot"
        ? "例如 compute-trusted-boot-v1"
        : "例如 compute-ima-runtime-v1";
    },
    keylimeStatusClass() {
      if (this.keylimeError) return "bad";
      if (this.keylime.ok === true) return "ok";
      if (this.keylime.ok === false) return "bad";
      return "warn";
    }
  },
  mounted() {
    this.updateClock();
    this.refreshAll();
    this.timer = setInterval(() => this.refreshAll(false), 10000);
    this.clockTimer = setInterval(() => this.updateClock(), 1000);
    window.addEventListener("click", this.closeUserMenu);
  },
  beforeUnmount() {
    if (this.timer) clearInterval(this.timer);
    if (this.clockTimer) clearInterval(this.clockTimer);
    window.removeEventListener("click", this.closeUserMenu);
  },
  methods: {
    updateClock() {
      this.currentTime = new Date();
    },
    toggleUserMenu() {
      this.userMenuOpen = !this.userMenuOpen;
    },
    closeUserMenu() {
      this.userMenuOpen = false;
    },
    selectView(view) {
      this.closeUserMenu();
      this.closeNodeDetail();
      this.view = view;
      this.detailPolicy = null;
    },
    toggleGroup(group) {
      this.expandedGroups[group] = !this.expandedGroups[group];
    },
    groupActive(group) {
      if (group === "nodes") return ["control_nodes", "compute_nodes"].includes(this.view);
      if (group === "policies") return this.view === "policies";
      return false;
    },
    selectPolicyType(policyType) {
      this.view = "policies";
      this.activePolicyType = policyType;
      this.detailPolicy = null;
      this.closeNodeDetail();
      this.expandedGroups.policies = true;
    },
    headers(token = "") {
      const headers = { "Content-Type": "application/json" };
      if (token) headers["X-Admin-Token"] = token;
      return headers;
    },
    async requestJson(path, options = {}, token = "") {
      const response = await fetch(path, {
        ...options,
        headers: { ...this.headers(token), ...(options.headers || {}) }
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || data.error || `HTTP ${response.status}`);
      return data;
    },
    async refreshAll(showBusy = true) {
      if (showBusy) this.busy = true;
      this.loading = true;
      const requests = {
        health: this.requestJson("/api/health"),
        dashboard: this.requestJson("/api/dashboard"),
        overview: this.requestJson("/api/overview"),
        keylime: this.requestJson("/api/keylime/check"),
        nodes: this.requestJson("/api/nodes"),
        policies: this.requestJson("/api/policies"),
        audit: this.requestJson("/api/audit?limit=20"),
        tasks: this.requestJson("/api/tasks?limit=20")
      };
      const entries = await Promise.all(
        Object.entries(requests).map(async ([key, promise]) => {
          try {
            return [key, await promise, null];
          } catch (error) {
            return [key, null, error];
          }
        })
      );
      for (const [key, value, error] of entries) {
        if (key === "health" && value) this.health = value;
        if (key === "dashboard" && value) this.dashboard = value;
        if (key === "overview" && value) this.overview = value;
        if (key === "keylime") {
          if (value) {
            this.keylime = value;
            this.keylimeError = "";
          } else if (error) {
            this.keylimeError = error.message;
          }
        }
        if (key === "nodes" && value) this.nodes = value;
        if (key === "policies" && value) this.policies = value;
        if (key === "audit" && value) this.auditEvents = value;
        if (key === "tasks" && value) this.tasks = value;
      }
      const failed = entries.filter(([, , error]) => error);
      this.apiError = failed.length ? failed.map(([key, , error]) => `${key}: ${error.message}`).join("；") : "";
      if (showBusy && this.apiError) this.showNotice("bad", this.apiError);
      this.loading = false;
      if (showBusy) this.busy = false;
    },
    showNotice(kind, text) {
      this.notice = { kind, text };
      if (text) {
        setTimeout(() => {
          if (this.notice.text === text) this.notice = { kind: "", text: "" };
        }, 6000);
      }
    },
    logoutSession() {
      this.closeUserMenu();
      this.showNotice("ok", "已退出当前前端会话，后续管理操作仍需重新输入管理令牌。");
    },
    async refreshNodeStatus() {
      await this.refreshAll(true);
      if (!this.apiError) this.showNotice("ok", "节点状态已刷新。");
    },
    openControllerDetail(node) {
      this.detailNode = {
        title: node.hostname || "控制节点",
        subtitle: "控制节点详情",
        sections: [
          {
            title: "基本信息",
            items: [
              { label: "节点名称", value: node.hostname || "-" },
              { label: "IP 地址", value: node.management_ip || "-" },
              { label: "操作系统", value: this.nodeOsText(node) },
              { label: "内核版本", value: node.facts?.kernel || "-" },
              { label: "CPU", value: this.nodeCpuText(node) },
              { label: "CPU 核心数", value: node.facts?.cpu_count || "-" }
            ]
          },
          {
            title: "控制面运行状况",
            items: this.controlRuntimeItems.map((item) => ({
              label: item.name,
              value: item.status,
              state: item.state
            }))
          }
        ]
      };
    },
    openComputeDetail(row) {
      const node = row.rawNode || {};
      const keylimeNode = row.keylimeNode || {};
      const remediation = keylimeNode.remediation || {};
      const sections = [
        {
          title: "基本信息",
          items: [
            { label: "节点名称", value: row.host || node.hostname || "-" },
            { label: "Hypervisor 名称", value: node.hypervisor_name || row.host || "-" },
            { label: "管理节点 IP", value: row.managementIp || "-" },
            { label: "自身 IP", value: row.ownIp || "-" },
            { label: "操作系统", value: this.nodeOsText(node) },
            { label: "CPU", value: this.nodeCpuText(node) }
          ]
        },
        {
          title: "OpenStack 计算服务",
          items: [
            { label: "服务名称", value: node.openstack_state?.service_binary || "nova-compute" },
            { label: "服务状态", value: row.openstackText, state: row.openstackClass },
            { label: "status", value: node.openstack_state?.service_status || "-" },
            { label: "state", value: node.openstack_state?.service_state || "-" },
            { label: "更新时间", value: this.formatTime(node.openstack_state?.updated_at) }
          ]
        },
        {
          title: "可信代理",
          items: [
            { label: "纳管状态", value: row.managed, state: row.managedClass },
            { label: "代理类型", value: row.trustAgentName || this.trustAgentName(node, keylimeNode) },
            { label: "可信根", value: row.trustedRoot || this.trustedRoot(node, keylimeNode) },
            { label: "Agent UUID", value: this.agentUuidText(node, keylimeNode) },
            { label: "Agent IP", value: node.keylime_agent_ip || keylimeNode.agent_ip || node.management_ip || "-" },
            { label: "端口", value: this.agentPortText(node) },
            { label: "证明状态", value: keylimeNode.attestation_status || "-" },
            { label: "运行状态", value: keylimeNode.operational_state ?? "-" },
            { label: "最近事件", value: keylimeNode.last_event_id || "-" }
          ]
        },
        {
          title: "可信状态",
          items: [
            { label: "可信状态", value: row.trustText, state: row.trustClass },
            { label: "可信启动", value: this.stateText(keylimeNode.evidence?.boot), state: this.evidenceClass(keylimeNode.evidence?.boot) },
            { label: "IMA 运行时", value: this.stateText(keylimeNode.evidence?.runtime), state: this.evidenceClass(keylimeNode.evidence?.runtime) },
            { label: "最近证明时间", value: row.attestationTime },
            { label: "原因", value: row.reason || "-" }
          ]
        }
      ];
      if (remediation.summary) {
        sections.push({
          title: "修复建议",
          items: [
            { label: "类别", value: remediation.category || "-" },
            { label: "建议", value: remediation.summary },
            { label: "命令", value: this.listText(remediation.next_commands || []) }
          ]
        });
      }
      this.detailNode = {
        title: row.host || node.hostname || "计算节点",
        subtitle: "计算节点详情",
        sections
      };
    },
    closeNodeDetail() {
      this.detailNode = null;
    },
    osText(kernel) {
      const value = String(kernel || "");
      if (value.includes("generic")) return "Ubuntu Server";
      if (value.includes("an23")) return "Anolis OS 23";
      return "-";
    },
    nodeOsText(node) {
      return node?.facts?.os || this.osText(node?.facts?.kernel) || "-";
    },
    nodeCpuText(node) {
      return (
        node?.hardware_profile?.model ||
        node?.facts?.cpu_model ||
        node?.facts?.cpu_vendor ||
        "-"
      );
    },
    yesNo(value) {
      return value ? "是" : "否";
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
    keylimeManagedText(node, keylimeNode) {
      if ((node.trust_agent_type || keylimeNode.trust_agent_type) === "opentcsm_tpcm") {
        if (keylimeNode.status === "collected") return "已纳管";
        return "待上报";
      }
      if (keylimeNode.status === "error") return "纳管异常";
      if (keylimeNode.status === "collected") return "已纳管";
      if (node.keylime_agent_uuid) return "待验证";
      return "未纳管";
    },
    keylimeManagedClass(node, keylimeNode) {
      if ((node.trust_agent_type || keylimeNode.trust_agent_type) === "opentcsm_tpcm") {
        if (keylimeNode.status === "collected") return "ok";
        return "warn";
      }
      if (keylimeNode.status === "error") return "bad";
      if (keylimeNode.status === "collected") return "ok";
      if (node.keylime_agent_uuid) return "warn";
      return "bad";
    },
    trustAgentName(node, keylimeNode) {
      return node.trust_agent_name || keylimeNode.trust_agent_name || "Keylime Agent";
    },
    trustedRoot(node, keylimeNode) {
      return node.trusted_root || keylimeNode.trusted_root || "TPM 2.0";
    },
    agentUuidText(node, keylimeNode) {
      if ((node.trust_agent_type || keylimeNode.trust_agent_type) === "opentcsm_tpcm") return "不适用";
      return node.keylime_agent_uuid || keylimeNode.agent_uuid || "-";
    },
    agentPortText(node) {
      if (node.trust_agent_type === "opentcsm_tpcm") return "不适用";
      return node.keylime_agent_port || "-";
    },
    proofTime(node) {
      return this.formatTime(this.timestampFromSeconds(node.last_successful_attestation || node.last_received_quote));
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
        host_integrity_evidence_collect: "采集节点完整性证据"
      };
      return names[event.event_type] || event.message || event.event_type || "-";
    },
    taskTypeText(value) {
      const names = {
        sync: "可信状态同步",
        policy_deploy: "策略下发"
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
    openCreatePolicy() {
      if (!this.currentPolicyType.creatable) return;
      this.createForm = emptyPolicyForm(this.activePolicyType);
      this.createDialogOpen = true;
      this.$nextTick(() => {
        const input = document.querySelector(".policy-create-dialog input");
        if (input) input.focus();
      });
    },
    closeCreatePolicy() {
      if (!this.busy) this.createDialogOpen = false;
    },
    viewPolicy(policy) {
      this.detailPolicy = policy;
    },
    closePolicyDetail() {
      this.detailPolicy = null;
    },
    parseLines(text) {
      return String(text || "").split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
    },
    policyPayload() {
      const name = this.createForm.name.trim();
      if (!name) throw new Error("策略名称不能为空");
      if (!this.createForm.targetNodeIds.length) throw new Error("请至少选择一个节点");
      let content;
      let excludes = [];
      if (this.activePolicyType === "measured_boot") {
        content = {
          pcrs: this.createForm.pcrs.map(Number).sort((a, b) => a - b),
          secure_boot_required: this.createForm.secureBootRequired,
          reference_state_mode: "collect_from_node",
          baseline_generation: "auto_collect_tpm_event_log",
          keylime_artifact: "measured_boot_refstate"
        };
      } else if (this.activePolicyType === "ima_runtime") {
        content = {
          node_ima_policy: this.createForm.nodeImaPolicy,
          runtime_policy_generation: "keylime-policy-from-measurements",
          baseline_generation: "auto_collect_ima_measurements",
          keylime_artifact: "runtime_policy",
          reboot_after_apply: this.createForm.rebootAfterApply
        };
        excludes = this.parseLines(this.createForm.excludesText);
      } else {
        throw new Error("该策略类型暂未启用");
      }
      return {
        name,
        policy_type: this.activePolicyType,
        version: 1,
        status: "active",
        hash_alg: "sha256",
        description: this.createForm.description.trim(),
        content,
        protected_paths: [],
        excludes,
        source: {
          baseline_source: "target_node",
          generation_mode: "auto_collect",
          keylime_artifact: content.keylime_artifact
        },
        target_node_ids: this.createForm.targetNodeIds.map(Number),
        deploy_now: true
      };
    },
    policyBaselineText(policy) {
      const policyType = String(policy.policy_type || "");
      if (policyType === "measured_boot") return "TPM 启动基线";
      if (policyType === "ima_runtime") return "IMA 运行基线";
      return "-";
    },
    policyGenerationModeText(policy) {
      const mode = policy?.source?.generation_mode || policy?.content?.baseline_generation || "";
      if (String(mode).includes("auto") || String(mode).includes("collect")) return "自动采集";
      return mode || "-";
    },
    policyArtifactText(policy) {
      const artifact = policy?.source?.keylime_artifact || policy?.content?.keylime_artifact || "";
      if (artifact === "measured_boot_refstate") return "可信启动参考状态";
      if (artifact === "runtime_policy") return "IMA 运行时策略";
      return artifact || "-";
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
    policySecureBootText(policy) {
      return policy?.content?.secure_boot_required === false ? "不强制" : "要求启用";
    },
    bindingEvidenceText(binding) {
      const keylimePolicy = binding?.keylime_policy || {};
      const evidenceType = keylimePolicy.evidence_type || "";
      const evidenceName = evidenceType === "tpm_event_log"
        ? "TPM Event Log"
        : evidenceType === "ima_measurement_list"
          ? "IMA 度量列表"
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
    requireToken({ title, message, confirmText, action }) {
      this.tokenDialog = { open: true, title, message, confirmText, token: "", action };
      this.$nextTick(() => {
        const input = document.querySelector(".token-dialog input");
        if (input) input.focus();
      });
    },
    closeTokenDialog() {
      if (this.busy) return;
      this.resetTokenDialog();
    },
    resetTokenDialog() {
      this.tokenDialog.open = false;
      this.tokenDialog.token = "";
      this.tokenDialog.action = null;
    },
    async confirmTokenDialog() {
      const token = this.tokenDialog.token.trim();
      if (!token) return this.showNotice("bad", "请输入管理令牌");
      const action = this.tokenDialog.action;
      if (!action) return;
      this.busy = true;
      try {
        await action(token);
        this.resetTokenDialog();
      } catch (error) {
        this.showNotice("bad", error.message);
      } finally {
        this.busy = false;
      }
    },
    createPolicy() {
      try {
        const payload = this.policyPayload();
        this.requireToken({
          title: "确认新增策略",
        message: `将创建${this.currentPolicyType.label}「${payload.name}」，并从目标节点自动采集基线后下发到对应可信代理。`,
          confirmText: "生成并下发",
          action: async (token) => {
            await this.requestJson("/api/policies", {
              method: "POST",
              body: JSON.stringify(payload)
            }, token);
            this.showNotice("ok", "策略已创建并进入下发队列");
            this.createDialogOpen = false;
            await this.refreshAll(false);
          }
        });
      } catch (error) {
        this.showNotice("bad", error.message);
      }
    },
    deletePolicy(policy) {
      this.requireToken({
        title: "确认删除策略",
        message: `将删除策略「${policy.name}」。`,
        confirmText: "确认删除",
        action: async (token) => {
          await this.requestJson(`/api/policies/${policy.id}`, { method: "DELETE" }, token);
          this.showNotice("ok", "策略已删除");
          if (this.detailPolicy && this.detailPolicy.id === policy.id) this.closePolicyDetail();
          await this.refreshAll(false);
        }
      });
    },
    deployPolicy(policy, binding = null) {
      const targetText = binding ? `${policy.name} / ${binding.target_name}` : policy.name;
      const endpoint = binding
        ? `/api/policies/${policy.id}/bindings/${binding.id}/deploy`
        : `/api/policies/${policy.id}/deploy`;
      this.requireToken({
        title: "确认更新策略基线",
        message: `将重新采集「${targetText}」的节点证据，生成策略并下发到对应可信代理。`,
        confirmText: "确认更新",
        action: async (token) => {
          await this.requestJson(endpoint, { method: "POST" }, token);
          this.showNotice("ok", "策略基线更新任务已进入队列");
          this.detailPolicy = null;
          await this.refreshAll(false);
        }
      });
    },
    prettyJson(value) {
      return JSON.stringify(value || {}, null, 2);
    },
    listText(items) {
      return (items || []).length ? items.join("\n") : "-";
    }
  }
}).mount("#app");
