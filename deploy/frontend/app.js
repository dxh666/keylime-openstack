const { createApp } = Vue;

createApp({
  data() {
    return {
      view: "overview",
      views: [
        { key: "overview", label: "Overview" },
        { key: "keylime", label: "Keylime" },
        { key: "nodes", label: "Nodes" },
        { key: "policies", label: "Policies" },
        { key: "tasks", label: "Tasks" },
        { key: "audit", label: "Audit" }
      ],
      adminToken: "",
      busy: false,
      notice: { kind: "", text: "" },
      overview: {},
      keylime: {},
      nodes: [],
      policies: [],
      tasks: [],
      audit: [],
      timer: null
    };
  },
  computed: {
    currentTitle() {
      return (this.views.find((item) => item.key === this.view) || {}).label || "Overview";
    }
  },
  mounted() {
    this.bootstrap();
    this.refreshAll();
    this.timer = setInterval(() => this.refreshAll(false), 10000);
  },
  beforeUnmount() {
    if (this.timer) clearInterval(this.timer);
  },
  methods: {
    yesNo(value) {
      return value ? "yes" : "no";
    },
    statusClass(value) {
      if (value === true || value === "PASS" || value === "pass" || value === "TRUSTED") return "ok";
      if (value === false || value === "FAIL" || value === "fail") return "bad";
      return "warn";
    },
    ageText(value) {
      if (value === null || value === undefined) return "-";
      return `${value}s`;
    },
    gateText(value) {
      if (value === true) return "PASS";
      if (value === false) return "FAIL";
      return "UNKNOWN";
    },
    headers() {
      const headers = { "Content-Type": "application/json" };
      if (this.adminToken) headers["X-Admin-Token"] = this.adminToken;
      return headers;
    },
    async requestJson(path, options = {}) {
      const response = await fetch(path, {
        ...options,
        headers: { ...this.headers(), ...(options.headers || {}) }
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || data.error || `HTTP ${response.status}`);
      }
      return data;
    },
    showNotice(kind, text) {
      this.notice = { kind, text };
      if (text) {
        setTimeout(() => {
          if (this.notice.text === text) this.notice = { kind: "", text: "" };
        }, 6000);
      }
    },
    async bootstrap() {
      try {
        await this.requestJson("/api/bootstrap", { method: "POST", body: "{}" });
      } catch (_error) {
        // Bootstrap may require an admin token in hardened deployments.
      }
    },
    async refreshAll(showBusy = true) {
      if (showBusy) this.busy = true;
      try {
        const [overview, keylime, nodes, policies, tasks, audit] = await Promise.all([
          this.requestJson("/api/overview"),
          this.requestJson("/api/keylime/check"),
          this.requestJson("/api/nodes"),
          this.requestJson("/api/policies"),
          this.requestJson("/api/tasks"),
          this.requestJson("/api/audit")
        ]);
        this.overview = overview;
        this.keylime = keylime;
        this.nodes = nodes;
        this.policies = policies;
        this.tasks = tasks;
        this.audit = audit;
      } catch (error) {
        this.showNotice("bad", error.message);
      } finally {
        if (showBusy) this.busy = false;
      }
    },
    async runSync() {
      this.busy = true;
      try {
        const data = await this.requestJson("/api/tasks/sync", { method: "POST", body: "{}" });
        this.showNotice("ok", `Sync task completed: #${data.task_id}`);
        await this.refreshAll(false);
      } catch (error) {
        this.showNotice("bad", error.message);
      } finally {
        this.busy = false;
      }
    }
  }
}).mount("#app");
