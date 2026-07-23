import { displayMethods } from "./js/formatters.js";
import { dashboardComputed } from "./js/modules/dashboard.js?v=20260723-node-sync";
import { globalControlsComputed, globalControlsMethods } from "./js/modules/global-controls.js";
import { policyCenterComputed, policyCenterMethods } from "./js/modules/policy-center.js";
import { shellComputed, shellMethods } from "./js/modules/shell.js?v=20260723-refresh-ux";
import { tpcmDynamicComputed, tpcmDynamicMethods } from "./js/modules/tpcm-dynamic.js";
import { tokenDialogMethods } from "./js/modules/token-dialog.js";
import { nodeMethods } from "./js/modules/nodes.js?v=20260723-node-sync";
import {
  POLICY_TYPES,
  emptyDynamicForm,
  emptyPolicyForm
} from "./js/policies.js";

const bootStyle = document.createElement("style");
bootStyle.textContent = `
  body.auth-booting #app {
    display: block;
    min-height: 100vh;
    background: #f5f7fb;
  }

  body.auth-booting #app .login-screen {
    display: none;
  }

  .global-refresh-button {
    background: #ffffff !important;
    color: #1f2937 !important;
    border-left: 1px solid var(--line) !important;
  }

  .global-refresh-button:hover {
    background: #f8fafc !important;
    color: #1f2937 !important;
  }

  .global-refresh-button + .user-menu-wrap {
    border-left-color: var(--line);
  }
`;
document.head.appendChild(bootStyle);
document.body.classList.add("auth-booting");

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
    this.installRefreshControls();
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
