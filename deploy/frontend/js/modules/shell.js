import { requestJson as apiRequestJson, jsonHeaders } from "../api.js?v=20260723-json-errors";
import { VIEW_TITLES } from "../policies.js";

const DEFAULT_VIEW = "dashboard";
const ROUTABLE_VIEWS = new Set([
  "dashboard",
  "control_nodes",
  "compute_nodes",
  "policies",
  "environment_dynamic_policy",
  "global_policy",
  "alerts",
  "tasks",
  "audit"
]);

function routeFromHash(policyTypes = []) {
  const hash = decodeURIComponent(window.location.hash || "").replace(/^#\/?/, "").trim();
  if (!hash) return { view: DEFAULT_VIEW, policyType: "" };
  const [rawView, rawParam] = hash.split("/");
  if (rawView === "policies") {
    const fallback = policyTypes[0]?.key || "measured_boot";
    const validPolicyType = policyTypes.some((item) => item.key === rawParam) ? rawParam : fallback;
    return { view: "policies", policyType: validPolicyType };
  }
  if (ROUTABLE_VIEWS.has(rawView)) return { view: rawView, policyType: "" };
  return { view: DEFAULT_VIEW, policyType: "" };
}

function hashForView(view, policyType) {
  if (view === "policies") return `#/policies/${policyType || "measured_boot"}`;
  return `#/${ROUTABLE_VIEWS.has(view) ? view : DEFAULT_VIEW}`;
}

export const shellComputed = {
  currentPolicyType() {
    return this.policyTypes.find((item) => item.key === this.activePolicyType) || this.policyTypes[0];
  },
  currentTitle() {
    if (this.view === "policies") return this.currentPolicyType.label;
    return VIEW_TITLES[this.view] || "管理控制台";
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
  currentUserName() {
    return this.currentUser?.display_name || this.currentUser?.username || "admin";
  },
};

export const shellMethods = {
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
    this.activateView(view);
  },
  activateView(view, { updateRoute = true } = {}) {
    this.closeUserMenu();
    this.closeNodeDetail();
    this.closeTrustProfileDialog?.();
    this.view = ROUTABLE_VIEWS.has(view) ? view : DEFAULT_VIEW;
    this.detailPolicy = null;
    if (this.groupActive("nodes")) this.expandedGroups.nodes = true;
    if (this.groupActive("policies")) this.expandedGroups.policies = true;
    if (this.view === "environment_dynamic_policy") {
      this.expandedGroups.dynamic_policies = true;
      this.ensureDynamicNodeSelection();
      this.syncDynamicFormFromPolicy(true);
    }
    if (updateRoute) this.syncRouteToHash();
  },
  toggleGroup(group) {
    this.expandedGroups[group] = !this.expandedGroups[group];
  },
  groupActive(group) {
    if (group === "nodes") return ["control_nodes", "compute_nodes"].includes(this.view);
    if (group === "policies") return ["policies", "environment_dynamic_policy"].includes(this.view);
    if (group === "dynamic_policies") return this.view === "environment_dynamic_policy";
    return false;
  },
  selectPolicyType(policyType) {
    this.view = "policies";
    this.activePolicyType = policyType;
    this.detailPolicy = null;
    this.closeNodeDetail();
    this.expandedGroups.policies = true;
    this.syncRouteToHash();
  },
  selectEnvironmentDynamicPolicy() {
    this.activateView("environment_dynamic_policy", { updateRoute: false });
    this.expandedGroups.policies = true;
    this.expandedGroups.dynamic_policies = true;
    this.ensureDynamicNodeSelection();
    this.syncDynamicFormFromPolicy(true);
    this.syncRouteToHash();
  },
  applyRouteFromHash() {
    const route = routeFromHash(this.policyTypes);
    if (route.view === "policies" && route.policyType) {
      this.activePolicyType = route.policyType;
    }
    this.activateView(route.view, { updateRoute: false });
  },
  syncRouteToHash(replace = false) {
    const nextHash = hashForView(this.view, this.activePolicyType);
    if (window.location.hash === nextHash) return;
    const url = `${window.location.pathname}${window.location.search}${nextHash}`;
    if (replace) {
      window.history.replaceState(null, "", url);
    } else {
      window.history.pushState(null, "", url);
    }
  },
  headers(token = "") {
    return jsonHeaders(token);
  },
  async requestJson(path, options = {}, token = "") {
    try {
      return await apiRequestJson(path, options, token);
    } catch (error) {
      if (error.status === 401) this.handleUnauthorized();
      throw error;
    }
  },
  async initializeSession() {
    this.loading = true;
    try {
      const result = await apiRequestJson("/api/auth/me");
      this.authChecked = true;
      this.authenticated = result.authenticated === true;
      this.currentUser = result.user || null;
      if (this.authenticated) {
        await this.refreshAll();
      } else {
        this.resetApplicationState();
      }
    } catch (error) {
      this.authChecked = true;
      this.authenticated = false;
      this.currentUser = null;
      this.resetApplicationState();
    } finally {
      this.loading = false;
    }
  },
  async loginSession() {
    this.loginError = "";
    this.busy = true;
    try {
      const result = await apiRequestJson("/api/auth/login", {
        method: "POST",
        body: JSON.stringify(this.loginForm)
      });
      this.authenticated = result.authenticated === true;
      this.currentUser = result.user || null;
      this.loginForm.password = "";
      await this.refreshAll();
    } catch (error) {
      this.loginError = error.message;
    } finally {
      this.busy = false;
      this.authChecked = true;
    }
  },
  handleUnauthorized() {
    this.authenticated = false;
    this.currentUser = null;
    this.authChecked = true;
    this.resetApplicationState();
  },
  resetApplicationState() {
    this.dashboard = null;
    this.overview = null;
    this.keylime = { ok: null, nodes: [], nodes_total: 0, nodes_trusted: 0, trust_capabilities: {} };
    this.globalControls = {
      tpcm_dynamic_measurement: { enabled: true, source: "default", updated_at: null },
      tpcm_global_policy: { ok: true, nodes_total: 0, fields: {}, nodes: [] }
    };
    this.globalPolicyDrafts = {};
    this.nodes = [];
    this.policies = [];
    this.auditEvents = [];
    this.tasks = [];
    this.opentcsmAccessChecks = {};
    this.detailPolicy = null;
    this.detailNode = null;
    this.closeTrustProfileDialog?.();
  },
  refreshRequestsForView(view = this.view) {
    if (view === "dashboard") {
      return {
        health: this.requestJson("/api/health"),
        dashboard: this.requestJson("/api/dashboard"),
        overview: this.requestJson("/api/overview"),
        keylime: this.requestJson("/api/trust/check"),
        nodes: this.requestJson("/api/nodes"),
        audit: this.requestJson("/api/audit?limit=200"),
        tasks: this.requestJson("/api/tasks?limit=100")
      };
    }
    if (view === "control_nodes") {
      return {
        dashboard: this.requestJson("/api/dashboard"),
        nodes: this.requestJson("/api/nodes")
      };
    }
    if (view === "compute_nodes") {
      return {
        keylime: this.requestJson("/api/trust/check"),
        globalTpcmDynamic: this.requestJson("/api/policies/tpcm-dynamic/global-switch"),
        globalTpcmPolicy: this.requestJson("/api/system/tpcm/global-policy"),
        nodes: this.requestJson("/api/nodes")
      };
    }
    if (view === "policies") {
      return {
        nodes: this.requestJson("/api/nodes"),
        policies: this.requestJson("/api/policies")
      };
    }
    if (view === "environment_dynamic_policy") {
      return {
        keylime: this.requestJson("/api/trust/check"),
        globalTpcmDynamic: this.requestJson("/api/policies/tpcm-dynamic/global-switch"),
        nodes: this.requestJson("/api/nodes"),
        policies: this.requestJson("/api/policies")
      };
    }
    if (view === "global_policy") {
      return {
        overview: this.requestJson("/api/overview"),
        globalTpcmDynamic: this.requestJson("/api/policies/tpcm-dynamic/global-switch"),
        globalTpcmPolicy: this.requestJson("/api/system/tpcm/global-policy"),
        nodes: this.requestJson("/api/nodes")
      };
    }
    if (view === "alerts") {
      return {
        keylime: this.requestJson("/api/trust/check"),
        nodes: this.requestJson("/api/nodes"),
        audit: this.requestJson("/api/audit?limit=200")
      };
    }
    if (view === "tasks") {
      return { tasks: this.requestJson("/api/tasks?limit=100") };
    }
    if (view === "audit") {
      return { audit: this.requestJson("/api/audit?limit=200") };
    }
    return this.refreshRequestsForView(DEFAULT_VIEW);
  },
  async refreshAll(showBusy = true) {
    if (!this.authenticated) {
      this.loading = false;
      return;
    }
    return this.refreshDataSet({
      health: this.requestJson("/api/health"),
      dashboard: this.requestJson("/api/dashboard"),
      overview: this.requestJson("/api/overview"),
      keylime: this.requestJson("/api/trust/check"),
      globalTpcmDynamic: this.requestJson("/api/policies/tpcm-dynamic/global-switch"),
      globalTpcmPolicy: this.requestJson("/api/system/tpcm/global-policy"),
      nodes: this.requestJson("/api/nodes"),
      policies: this.requestJson("/api/policies"),
      audit: this.requestJson("/api/audit?limit=200"),
      tasks: this.requestJson("/api/tasks?limit=100")
    }, showBusy);
  },
  async refreshCurrentView(showBusy = true) {
    if (!this.authenticated) {
      this.loading = false;
      return;
    }
    const ok = await this.refreshDataSet(this.refreshRequestsForView(), showBusy);
    if (showBusy && ok) this.showNotice("ok", "当前页面数据已刷新。");
  },
  async refreshDataSet(requests, showBusy = true) {
    if (showBusy) this.busy = true;
    this.loading = true;
    try {
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
        if (key === "globalTpcmDynamic" && value) {
          this.globalControls.tpcm_dynamic_measurement = value;
        }
        if (key === "globalTpcmPolicy" && value) {
          this.globalControls.tpcm_global_policy = value;
          this.syncGlobalPolicyDrafts?.();
        }
        if (key === "policies" && value) this.policies = value;
        if (key === "audit" && value) this.auditEvents = value;
        if (key === "tasks" && value) this.tasks = value;
      }
      this.ensureDynamicNodeSelection();
      this.syncDynamicFormFromPolicy();
      const failed = entries.filter(([, , error]) => error);
      this.apiError = failed.length
        ? failed.map(([key, , error]) => `${key}: ${error.message}`).join("；")
        : "";
      if (showBusy && this.apiError) this.showNotice("bad", this.apiError);
      return failed.length === 0;
    } finally {
      this.loading = false;
      if (showBusy) this.busy = false;
    }
  },
  showNotice(kind, text) {
    this.notice = { kind, text };
    if (text) {
      setTimeout(() => {
        if (this.notice.text === text) this.notice = { kind: "", text: "" };
      }, 6000);
    }
  },
  async logoutSession() {
    this.closeUserMenu();
    try {
      await apiRequestJson("/api/auth/logout", { method: "POST" });
    } catch (error) {
      // Clear local state even when the server session is already gone.
    }
    this.handleUnauthorized();
  },
};
