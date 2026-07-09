const { createApp } = Vue;

const LEVEL_CLASS = {
  ok: "ok",
  warn: "warn",
  bad: "bad",
  info: "warn"
};

function getLayer(node, key) {
  return (node && node.trust_layers && node.trust_layers[key]) || {};
}

function getPolicy(node) {
  return (node && node.trust_layers && node.trust_layers.policy) || {};
}

function isRuntimePolicy(policy) {
  return policy && (policy.module_key === "runtime" || policy.type === "ima_runtime" || policy.module === "runtime_integrity");
}

function isBootPolicy(policy) {
  return policy && !isRuntimePolicy(policy);
}

function cloneRuntimePolicy(policy = {}) {
  return {
    id: policy.id || "",
    name: policy.name || "",
    description: policy.description || "",
    type: "ima_runtime",
    module: "runtime_integrity",
    runtime_policy_name: policy.runtime_policy_name || policy.id || "",
    runtime_policy_path: policy.runtime_policy_path || "",
    protected_paths: policy.protected_paths || ["/opt/keylime-cloud-integrity/cloud-runtime-guard.sh"],
    excludes: policy.excludes || ["^(?!(boot_aggregate|/opt/keylime-cloud-integrity/cloud-runtime-guard.sh)$).*"]
  };
}

function slugify(value) {
  return String(value || "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_.-]+/g, "-")
    .replace(/^-+|-+$/g, "") || "runtime-policy";
}

createApp({
  data() {
    return {
      view: "overview",
      policyModule: "boot",
      adminToken: "",
      refreshSeconds: 5,
      status: null,
      policies: null,
      notice: { kind: "", text: "" },
      busy: { status: false, policies: false, action: false },
      timer: null,
      nodeSearch: "",
      nodeFilter: "all",
      selectedNodeHost: "",
      selectedBootPolicyId: "",
      selectedRuntimePolicyId: "",
      selectedBootHosts: [],
      selectedRuntimeHosts: [],
      applyResults: [],
      runtimeForm: cloneRuntimePolicy()
    };
  },
  computed: {
    pageTitle() {
      if (this.view === "nodes") return "计算节点可信状态";
      if (this.view === "policies") return "Keylime 策略工作台";
      return "可信云运行态总览";
    },
    nodes() {
      return (this.status && this.status.nodes) || [];
    },
    summary() {
      return (this.status && this.status.summary) || {};
    },
    summaryText() {
      return this.summary.text || "等待状态";
    },
    checkedAtText() {
      if (!this.status || !this.status.checked_at_utc) return "尚未刷新";
      return `最近刷新：${this.status.checked_at_utc}`;
    },
    filteredNodes() {
      const q = this.nodeSearch.toLowerCase();
      return this.nodes.filter((node) => {
        const text = [node.host, node.ip, node.agent?.uuid, node.decision?.result].join(" ").toLowerCase();
        const matchesSearch = !q || text.includes(q);
        const code = node.conclusion?.code || "";
        const matchesFilter =
          this.nodeFilter === "all" ||
          (this.nodeFilter === "trusted" && code === "TRUSTED") ||
          (this.nodeFilter === "untrusted" && code !== "TRUSTED" && code !== "NO_KEYLIME_AGENT") ||
          (this.nodeFilter === "no_agent" && code === "NO_KEYLIME_AGENT");
        return matchesSearch && matchesFilter;
      });
    },
    selectedNode() {
      return this.nodes.find((node) => node.host === this.selectedNodeHost) || this.nodes[0] || null;
    },
    layerKeys() {
      return ["keylime", "boot", "runtime", "openstack"];
    },
    focusItems() {
      return this.nodes
        .map((node) => ({ host: node.host, text: this.recommendation(node), level: node.conclusion?.level }))
        .filter((item) => item.level !== "ok" || item.text !== "可信链路完整，可进入调度验证。")
        .slice(0, 6);
    },
    policyStorePath() {
      return (this.policies && this.policies.store_path) || "-";
    },
    allPolicies() {
      return (this.policies && this.policies.policies) || [];
    },
    bootPolicies() {
      return this.allPolicies.filter(isBootPolicy);
    },
    runtimePolicies() {
      return this.allPolicies.filter(isRuntimePolicy);
    },
    policyNodes() {
      return (this.policies && this.policies.nodes) || [];
    },
    baseline() {
      return (this.policies && this.policies.baseline) || {};
    },
    baselineText() {
      if (!this.baseline.ok) return this.baseline.message || "未读取到 TPM evidence baseline";
      return `${this.baseline.checked_at_utc || "-"} / ${this.baseline.nodes?.length || 0} 个节点`;
    },
    runtimeSummaryText() {
      const ready = this.policyNodes.filter((node) => getPolicy(node).has_runtime_policy === true).length;
      return `${ready}/${this.policyNodes.length} 个节点显示 runtime policy 已绑定`;
    }
  },
  watch: {
    refreshSeconds() {
      this.startTimer();
    },
    "runtimeForm.name"(value) {
      if (!this.runtimeForm.id) {
        this.runtimeForm.id = slugify(value);
      }
      if (!this.runtimeForm.runtime_policy_name) {
        this.runtimeForm.runtime_policy_name = this.runtimeForm.id;
      }
    }
  },
  mounted() {
    this.refreshAll();
    this.startTimer();
  },
  beforeUnmount() {
    if (this.timer) clearInterval(this.timer);
  },
  methods: {
    levelClass(level) {
      return LEVEL_CLASS[level] || "warn";
    },
    layerText(node, key) {
      return getLayer(node, key).text || "-";
    },
    layerDetail(node, key) {
      return getLayer(node, key).detail || "";
    },
    layerLevel(node, key) {
      return this.levelClass(getLayer(node, key).level);
    },
    layerLabel(key) {
      return { keylime: "Keylime", boot: "PCR7 启动", runtime: "PCR10 / IMA", openstack: "OpenStack" }[key] || key;
    },
    policyOf(node) {
      return getPolicy(node);
    },
    boolText(value) {
      if (value === true) return "存在";
      if (value === false) return "不存在";
      return "未知";
    },
    serviceText(node) {
      const service = node.service || {};
      return `${service.status || "-"} / ${service.state || "-"}`;
    },
    runtimePolicyText(node) {
      const value = getPolicy(node).has_runtime_policy;
      if (value === true) return "已绑定";
      if (value === false) return "未绑定";
      return "未知";
    },
    attestationAge(node) {
      const age = node.decision?.last_successful_attestation_age_seconds;
      return age === null || age === undefined ? "-" : `${age}s`;
    },
    selectNode(host) {
      this.selectedNodeHost = host;
    },
    recommendation(node) {
      const decision = node.decision || {};
      const policy = getPolicy(node);
      if (!node.agent?.configured && node.placement?.trait_present) {
        return "OpenStack 已有可信 trait，但 Keylime inventory 未识别该节点。";
      }
      if (!node.agent?.configured) {
        return "该节点尚未纳入 Keylime agent inventory。";
      }
      if (decision.result !== "PASS_FRESH") {
        return decision.reason || "保持隔离，等待 attestation 恢复。";
      }
      if (policy.boot_pcr7_enforced && !policy.has_runtime_policy) {
        return "PCR7 已通过，运行时完整性策略尚未绑定。";
      }
      if (policy.has_runtime_policy && node.placement?.trait_present && node.service?.status === "enabled") {
        return "可信链路完整，可进入调度验证。";
      }
      if (node.service?.status !== "enabled") {
        return "节点可信，但 nova-compute 仍处于禁用状态。";
      }
      return "状态正常，继续观察同步结果。";
    },
    baselinePcr7(host) {
      const node = (this.baseline.nodes || []).find((item) => item.host === host);
      return node ? node.pcr7 || "-" : "-";
    },
    baselineEvidence(host) {
      const node = (this.baseline.nodes || []).find((item) => item.host === host);
      if (!node) return "-";
      return Number(node.sha256_pcr_count || 0) >= 8 ? "完整" : "缺失";
    },
    showNotice(kind, text) {
      this.notice = { kind, text };
      if (text) {
        setTimeout(() => {
          if (this.notice.text === text) this.notice = { kind: "", text: "" };
        }, 7000);
      }
    },
    requestHeaders() {
      const headers = { "Content-Type": "application/json" };
      if (this.adminToken) headers["X-Admin-Token"] = this.adminToken;
      return headers;
    },
    async requestJson(path, options = {}) {
      const response = await fetch(path, {
        ...options,
        headers: { ...this.requestHeaders(), ...(options.headers || {}) }
      });
      const data = await response.json();
      if (!response.ok || data.ok === false) {
        throw new Error(data.error || `HTTP ${response.status}`);
      }
      return data;
    },
    async refreshStatus(force = false) {
      this.busy.status = true;
      try {
        this.status = await this.requestJson(`/api/status${force ? "?force=1" : ""}`);
        if (!this.selectedNodeHost && this.nodes.length) this.selectedNodeHost = this.nodes[0].host;
      } catch (error) {
        this.showNotice("bad", error.message);
      } finally {
        this.busy.status = false;
      }
    },
    async refreshPolicies() {
      this.busy.policies = true;
      try {
        this.policies = await this.requestJson(`/api/policies?ts=${Date.now()}`);
        if (!this.selectedBootPolicyId && this.bootPolicies.length) this.selectedBootPolicyId = this.bootPolicies[0].id;
        if (!this.selectedRuntimePolicyId && this.runtimePolicies.length) this.selectedRuntimePolicyId = this.runtimePolicies[0].id;
      } catch (error) {
        this.showNotice("bad", error.message);
      } finally {
        this.busy.policies = false;
      }
    },
    async refreshAll() {
      await this.refreshStatus(false);
      await this.refreshPolicies();
    },
    startTimer() {
      if (this.timer) clearInterval(this.timer);
      this.timer = setInterval(() => this.refreshStatus(false), this.refreshSeconds * 1000);
    },
    async importBaselinePolicies() {
      this.busy.action = true;
      try {
        const data = await this.requestJson("/api/policies/import-baseline", {
          method: "POST",
          body: JSON.stringify({ bind_mode: "pcr7" })
        });
        this.showNotice("ok", `已导入 ${data.rendered.length} 项 PCR 策略`);
        await this.refreshPolicies();
      } catch (error) {
        this.showNotice("bad", error.message);
      } finally {
        this.busy.action = false;
      }
    },
    selectedHostsFor(moduleKey) {
      return moduleKey === "runtime" ? this.selectedRuntimeHosts : this.selectedBootHosts;
    },
    selectedPolicyFor(moduleKey) {
      return moduleKey === "runtime" ? this.selectedRuntimePolicyId : this.selectedBootPolicyId;
    },
    async applyPolicy(moduleKey, all) {
      const policyId = this.selectedPolicyFor(moduleKey);
      const hosts = this.selectedHostsFor(moduleKey);
      if (!policyId) {
        this.showNotice("warn", "请选择策略");
        return;
      }
      if (!all && !hosts.length) {
        this.showNotice("warn", "请选择节点");
        return;
      }
      this.busy.action = true;
      try {
        const data = await this.requestJson("/api/policies/apply", {
          method: "POST",
          body: JSON.stringify({ policy_id: policyId, hosts, all, sync: true, sync_mode: "control-loop" })
        });
        this.applyResults = data.results || [];
        this.showNotice(data.ok ? "ok" : "warn", data.ok ? "策略下发完成" : "策略部分下发失败");
        await this.refreshAll();
      } catch (error) {
        this.showNotice("bad", error.message);
      } finally {
        this.busy.action = false;
      }
    },
    async applyBoundPolicies(moduleKey) {
      this.busy.action = true;
      try {
        const data = await this.requestJson("/api/policies/apply-bound", {
          method: "POST",
          body: JSON.stringify({ all: true, module: moduleKey, sync: true, sync_mode: "control-loop" })
        });
        this.applyResults = data.results || [];
        this.showNotice(data.ok ? "ok" : "warn", data.ok ? "绑定策略下发完成" : "绑定策略部分失败");
        await this.refreshAll();
      } catch (error) {
        this.showNotice("bad", error.message);
      } finally {
        this.busy.action = false;
      }
    },
    resetRuntimeForm() {
      this.runtimeForm = cloneRuntimePolicy();
    },
    editRuntimePolicy(policy) {
      this.runtimeForm = cloneRuntimePolicy(policy);
      this.policyModule = "runtime";
    },
    async saveRuntimePolicy() {
      if (!this.runtimeForm.name || !this.runtimeForm.runtime_policy_path) {
        this.showNotice("warn", "名称和 JSON 文件路径不能为空");
        return;
      }
      const payload = {
        ...this.runtimeForm,
        id: this.runtimeForm.id || slugify(this.runtimeForm.name),
        runtime_policy_name: this.runtimeForm.runtime_policy_name || this.runtimeForm.id || slugify(this.runtimeForm.name)
      };
      try {
        const data = await this.requestJson("/api/policies", {
          method: "POST",
          body: JSON.stringify(payload)
        });
        this.selectedRuntimePolicyId = data.policy.id;
        this.showNotice("ok", `已保存：${data.policy.name}`);
        await this.refreshPolicies();
      } catch (error) {
        this.showNotice("bad", error.message);
      }
    },
    async deletePolicy(policyId) {
      if (!policyId) return;
      this.busy.action = true;
      try {
        await this.requestJson(`/api/policies/${encodeURIComponent(policyId)}`, { method: "DELETE" });
        this.showNotice("ok", `已删除：${policyId}`);
        await this.refreshPolicies();
      } catch (error) {
        this.showNotice("bad", error.message);
      } finally {
        this.busy.action = false;
      }
    }
  }
}).mount("#app");
