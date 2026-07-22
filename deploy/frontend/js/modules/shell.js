import { requestJson as apiRequestJson, jsonHeaders } from "../api.js";
import { VIEW_TITLES } from "../policies.js";

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
  },
  selectEnvironmentDynamicPolicy() {
    this.selectView("environment_dynamic_policy");
    this.expandedGroups.policies = true;
    this.expandedGroups.dynamic_policies = true;
    this.ensureDynamicNodeSelection();
    this.syncDynamicFormFromPolicy(true);
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
      tpcm_dynamic_measurement: { enabled: true, source: "default", updated_at: null }
    };
    this.nodes = [];
    this.policies = [];
    this.auditEvents = [];
    this.tasks = [];
    this.opentcsmAccessChecks = {};
    this.detailPolicy = null;
    this.detailNode = null;
  },
  async refreshAll(showBusy = true) {
    if (!this.authenticated) {
      this.loading = false;
      return;
    }
    if (showBusy) this.busy = true;
    this.loading = true;
    const requests = {
      health: this.requestJson("/api/health"),
      dashboard: this.requestJson("/api/dashboard"),
      overview: this.requestJson("/api/overview"),
      keylime: this.requestJson("/api/trust/check"),
      globalTpcmDynamic: this.requestJson("/api/policies/tpcm-dynamic/global-switch"),
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
      if (key === "globalTpcmDynamic" && value) {
        this.globalControls.tpcm_dynamic_measurement = value;
      }
      if (key === "policies" && value) this.policies = value;
      if (key === "audit" && value) this.auditEvents = value;
      if (key === "tasks" && value) this.tasks = value;
    }
    this.ensureDynamicNodeSelection();
    this.syncDynamicFormFromPolicy();
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
