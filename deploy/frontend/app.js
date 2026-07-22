import { displayMethods } from "./js/formatters.js";
import { dashboardComputed } from "./js/modules/dashboard.js";
import { globalControlsComputed } from "./js/modules/global-controls.js";
import { policyCenterComputed, policyCenterMethods } from "./js/modules/policy-center.js";
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
    ...policyCenterMethods,
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
