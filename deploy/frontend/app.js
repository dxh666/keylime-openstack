import { displayMethods } from "./js/formatters.js";
import { dashboardComputed } from "./js/modules/dashboard.js";
import { globalControlsComputed } from "./js/modules/global-controls.js";
import { policyCenterComputed } from "./js/modules/policy-center.js";
import { shellComputed, shellMethods } from "./js/modules/shell.js";
import { tpcmDynamicComputed, tpcmDynamicMethods } from "./js/modules/tpcm-dynamic.js";
import {
  POLICY_TYPES,
  emptyDynamicForm,
  emptyPolicyForm
} from "./js/policies.js";

const { createApp } = window.Vue;

createApp({
  data() {
    return {
      view: "dashboard",
      expandedGroups: {
        nodes: true,
        policies: true,
        dynamic_policies: true
      },
      policyTypes: POLICY_TYPES,
      activePolicyType: "measured_boot",
      activeDynamicNodeId: null,
      dynamicNodeSearch: "",
      dynamicNodeStatusFilter: "all",
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
      globalControls: {
        tpcm_dynamic_measurement: { enabled: true, source: "default", updated_at: null }
      },
      nodes: [],
      policies: [],
      auditEvents: [],
      tasks: [],
      opentcsmAccessChecks: {},
      createDialogOpen: false,
      createForm: emptyPolicyForm(),
      dynamicForm: emptyDynamicForm(),
      dynamicFormPolicyId: null,
      dynamicFormDirty: false,
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
    ...shellComputed,
    ...dashboardComputed,
    ...tpcmDynamicComputed,
    ...policyCenterComputed,
    ...globalControlsComputed
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
    ...displayMethods,
    ...shellMethods,
    ...tpcmDynamicMethods,
    async refreshNodeStatus() {
      await this.refreshAll(true);
      if (!this.apiError) this.showNotice("ok", "节点状态已刷新。");
    },
    async refreshComputeNodeStatus(row) {
      if (row?.trustAgentType === "opentcsm_tpcm") {
        await this.collectOpenTcsmStatus(row);
        return;
      }
      await this.refreshNodeStatus();
    },
    async collectOpenTcsmStatus(row) {
      const host = encodeURIComponent(row?.host || row?.rawNode?.hostname || "");
      if (!host) return this.showNotice("bad", "节点名称不能为空。");
      this.busy = true;
      try {
        await this.requestJson(`/api/nodes/${host}/opentcsm-collect`, { method: "POST" });
        await this.refreshAll(false);
        this.showNotice("ok", "可信状态已刷新。");
      } catch (error) {
        this.showNotice("bad", error.message);
      } finally {
        this.busy = false;
      }
    },
    async runOpenTcsmAccessCheck(row) {
      const hostname = row?.host || row?.rawNode?.hostname || "";
      const host = encodeURIComponent(hostname);
      if (!host) return this.showNotice("bad", "节点名称不能为空。");
      this.busy = true;
      try {
        const result = await this.requestJson(`/api/nodes/${host}/opentcsm-access-check`, { method: "POST" });
        this.opentcsmAccessChecks = {
          ...this.opentcsmAccessChecks,
          [hostname]: result
        };
        await this.refreshAll(false);
        const freshRow = this.computeRows.find((item) => item.host === hostname) || row;
        this.openComputeDetail(freshRow);
        this.showNotice(result.ok ? "ok" : "bad", result.summary || "OpenTCSM 接入检查已完成。");
      } catch (error) {
        this.showNotice("bad", error.message);
      } finally {
        this.busy = false;
      }
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
      if (this.isOpenTcsmNode(row, node, keylimeNode)) {
        const report = keylimeNode.trust_report || {};
        const history = keylimeNode.trust_report_history || [];
        sections.push({
          title: "OpenTCSM 采集健康",
          items: [
            { label: "采集状态", value: this.collectionStatusText(keylimeNode.status), state: this.collectionStatusClass(keylimeNode.status) },
            { label: "证据有效性", value: this.evidenceFreshText(keylimeNode.evidence_fresh), state: this.evidenceFreshClass(keylimeNode.evidence_fresh) },
            { label: "最近采集", value: this.formatTime(report.collected_at) },
            { label: "有效期", value: this.formatTime(report.valid_until) },
            { label: "异常信息", value: this.collectionErrorText(keylimeNode, report) }
          ]
        });
        const accessCheck = this.opentcsmAccessChecks[row.host || node.hostname];
        if (accessCheck) {
          sections.push({
            title: "OpenTCSM 接入检查",
            items: this.opentcsmAccessCheckItems(accessCheck)
          });
        }
        sections.push({
          title: "TPCM 能力状态",
          items: [
            { label: "可信根", value: report.trust_root || row.trustedRoot || "-" },
            { label: "TPCM ID", value: report.tpcm_id || "-" },
            { label: "启动度量", value: this.enabledText(report.boot_measure_on) },
            { label: "动态度量", value: this.enabledText(report.dynamic_measure_on) },
            { label: "启动基线数量", value: this.countText(report.boot_measure_ref_number) },
            { label: "动态基线数量", value: this.countText(report.dynamic_measure_ref_number) },
            { label: "动态度量次数", value: this.countText(report.dmeasure_times) },
            { label: "控制策略指纹", value: this.shortHash(report.global_control_policy_sha256) }
          ]
        });
        sections.push({
          title: "TPCM 可信报告",
          items: [
            { label: "报告状态", value: this.trustStatusText(report.trust_status), state: report.trusted === true ? "ok" : report.trusted === false ? "bad" : "warn" },
            { label: "可信报告评分", value: this.reportEvalText(report.trust_report_eval) },
            { label: "失败计数", value: report.failure_count ?? "-" },
            { label: "失败项", value: this.failureText(report.trust_report_failures) }
          ]
        });
        sections.push({
          title: "TPCM 可信报告历史",
          items: history.length
            ? history.map((item, index) => ({
                label: `${index + 1}. ${this.formatTime(item.collected_at)}`,
                value: this.tpcmHistoryText(item)
              }))
            : [{ label: "历史记录", value: "暂无" }]
        });
      }
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
    isOpenTcsmNode(row, node, keylimeNode) {
      return (
        row?.trustAgentType === "opentcsm_tpcm" ||
        node?.trust_agent_type === "opentcsm_tpcm" ||
        keylimeNode?.trust_agent_type === "opentcsm_tpcm"
      );
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
    policyPayload() {
      const name = this.createForm.name.trim();
      if (!name) throw new Error("策略名称不能为空");
      if (!this.createForm.targetNodeIds.length) throw new Error("请至少选择一个节点");
      let content;
      let excludes = [];
      if (this.activePolicyType === "measured_boot") {
        content = {
          pcrs: this.createForm.pcrs.map(Number).sort((a, b) => a - b),
          fallback_pcrs: [7],
          secure_boot_required: this.createForm.secureBootRequired,
          reference_state_mode: "collect_from_node",
          event_log_fallback: "pcr_quote",
          baseline_generation: "auto_collect_tpm_event_log",
          keylime_artifact: "measured_boot_refstate_or_tpm_policy"
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
    toggleGlobalTrustCapability(item) {
      if (item.key !== "tpcm_dynamic") return;
      const nextEnabled = !item.enabled;
      this.requireToken({
        title: nextEnabled ? "确认开启集群动态度量" : "确认关闭集群动态度量",
        message: nextEnabled
          ? "将开启所有 OpenTCSM 节点的动态度量总开关，并使对应节点策略生效。"
          : "将关闭所有 OpenTCSM 节点的动态度量总开关，并使对应节点策略生效。",
        confirmText: nextEnabled ? "开启并生效" : "关闭并生效",
        action: async (token) => {
          await this.requestJson("/api/policies/tpcm-dynamic/global-switch", {
            method: "POST",
            body: JSON.stringify({ enabled: nextEnabled })
          }, token);
          this.showNotice("ok", nextEnabled ? "集群动态度量开启任务已进入队列" : "集群动态度量关闭任务已进入队列");
          await this.refreshAll(false);
        }
      });
    },
  }
}).mount("#app");
