import { displayMethods } from "./js/formatters.js?v=20260723-list-pages";
import { dashboardComputed } from "./js/modules/dashboard.js?v=20260723-audit-task-ux";
import { globalControlsComputed, globalControlsMethods } from "./js/modules/global-controls.js";
import { LIST_PAGE_SIZES, listPageComputed, listPageMethods } from "./js/modules/list-pages.js?v=20260723-list-pages";
import { policyCenterComputed, policyCenterMethods } from "./js/modules/policy-center.js";
import { shellComputed, shellMethods } from "./js/modules/shell.js?v=20260723-json-errors";
import { tpcmDynamicComputed, tpcmDynamicMethods } from "./js/modules/tpcm-dynamic.js";
import { tokenDialogMethods } from "./js/modules/token-dialog.js";
import { nodeMethods } from "./js/modules/nodes.js?v=20260723-trust-profile";
import { emptyTrustProfileForm, trustProfileMethods } from "./js/modules/trust-profile.js?v=20260723-trust-profile";
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
      activeAuditCategory: "all",
      activeDynamicNodeId: null,
      dynamicNodeSearch: "",
      dynamicNodeStatusFilter: "all",
      listPageSizes: LIST_PAGE_SIZES,
      listPagination: {
        alerts: { page: 1, pageSize: 20 },
        tasks: { page: 1, pageSize: 20 },
        audit: { page: 1, pageSize: 20 }
      },
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
      trustProfileDialog: {
        open: false,
        node: null,
        form: emptyTrustProfileForm()
      },
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
    ...listPageComputed,
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
    ...listPageMethods,
    ...tpcmDynamicMethods,
    ...nodeMethods,
    ...trustProfileMethods,
    ...policyCenterMethods,
    ...tokenDialogMethods,
    ...globalControlsMethods,
  }
}).mount("#app");
