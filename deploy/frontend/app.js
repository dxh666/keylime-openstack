import { displayMethods } from "./js/formatters.js?v=20260723-audit-task-ux";
import { dashboardComputed } from "./js/modules/dashboard.js?v=20260723-audit-task-ux";
import { globalControlsComputed, globalControlsMethods } from "./js/modules/global-controls.js";
import { policyCenterComputed, policyCenterMethods } from "./js/modules/policy-center.js";
import { shellComputed, shellMethods } from "./js/modules/shell.js?v=20260723-product-closure";
import { tpcmDynamicComputed, tpcmDynamicMethods } from "./js/modules/tpcm-dynamic.js";
import { tokenDialogMethods } from "./js/modules/token-dialog.js";
import { nodeMethods } from "./js/modules/nodes.js?v=20260723-product-closure";
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
      authChecked: false,
      authenticated: false,
      currentUser: null,
      loginForm: {
        username: "admin",
        password: ""
      },
      loginError: "",
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
        requireToken: false,
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
    this.applyRouteFromHash();
    this.syncRouteToHash(true);
    this.initializeSession();
    this.timer = setInterval(() => {
      if (this.authenticated) this.refreshCurrentView(false);
    }, 10000);
    this.clockTimer = setInterval(() => this.updateClock(), 1000);
    window.addEventListener("hashchange", this.applyRouteFromHash);
    window.addEventListener("popstate", this.applyRouteFromHash);
    window.addEventListener("click", this.closeUserMenu);
  },
  beforeUnmount() {
    if (this.timer) clearInterval(this.timer);
    if (this.clockTimer) clearInterval(this.clockTimer);
    window.removeEventListener("hashchange", this.applyRouteFromHash);
    window.removeEventListener("popstate", this.applyRouteFromHash);
    window.removeEventListener("click", this.closeUserMenu);
  },
  methods: {
    ...displayMethods,
    ...shellMethods,
    ...tpcmDynamicMethods,
    ...nodeMethods,
    ...policyCenterMethods,
    ...tokenDialogMethods,
    ...globalControlsMethods,
  }
}).mount("#app");
