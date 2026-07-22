import { displayMethods } from "./js/formatters.js";
import { dashboardComputed } from "./js/modules/dashboard.js";
import { globalControlsComputed } from "./js/modules/global-controls.js";
import { policyCenterComputed } from "./js/modules/policy-center.js";
import { shellComputed, shellMethods } from "./js/modules/shell.js";
import { tpcmDynamicComputed, tpcmDynamicMethods } from "./js/modules/tpcm-dynamic.js";
import { nodeMethods } from "./js/modules/nodes.js";
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
    ...nodeMethods,
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
