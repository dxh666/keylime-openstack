const { createApp } = Vue;

const emptyPolicyForm = () => ({
  id: null,
  name: "",
  policy_type: "ima_runtime",
  version: 1,
  status: "draft",
  hash_alg: "sha256",
  description: "",
  contentText: "{}",
  protectedPathsText: "",
  excludesText: ""
});

createApp({
  data() {
    return {
      view: "nodes",
      views: [
        { key: "nodes", label: "节点状态" },
        { key: "policies", label: "策略管理" }
      ],
      adminToken: "",
      busy: false,
      notice: { kind: "", text: "" },
      keylime: {},
      policies: [],
      policyForm: emptyPolicyForm(),
      timer: null
    };
  },
  computed: {
    currentTitle() {
      return (this.views.find((item) => item.key === this.view) || {}).label || "节点状态";
    },
    selectedPolicyId() {
      return this.policyForm.id ? `#${this.policyForm.id}` : "新增";
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
      return value ? "是" : "否";
    },
    trustText(value) {
      if (value === true) return "可信";
      if (value === false) return "不可信";
      return "未知";
    },
    gateText(value) {
      if (value === true) return "通过";
      if (value === false) return "失败";
      return "未知";
    },
    stateText(value) {
      const normalized = String(value || "").toLowerCase();
      const names = {
        pass: "通过",
        fail: "失败",
        missing: "缺失",
        unknown: "未知",
        none: "-"
      };
      return names[normalized] || value || "-";
    },
    ageText(value) {
      if (value === null || value === undefined) return "-";
      return `${value} 秒`;
    },
    policyTypeText(value) {
      const names = {
        tpm_pcr: "TPM 启动策略",
        ima_runtime: "IMA 运行时策略",
        evm: "EVM 策略"
      };
      return names[value] || value || "-";
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
        // 生产环境通常需要管理令牌，首次加载失败不影响只读查看。
      }
    },
    async refreshAll(showBusy = true) {
      if (showBusy) this.busy = true;
      try {
        const [keylime, policies] = await Promise.all([
          this.requestJson("/api/keylime/check"),
          this.requestJson("/api/policies")
        ]);
        this.keylime = keylime;
        this.policies = policies;
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
        this.showNotice("ok", `同步完成，任务编号：#${data.task_id}`);
        await this.refreshAll(false);
      } catch (error) {
        this.showNotice("bad", error.message);
      } finally {
        this.busy = false;
      }
    },
    selectPolicy(policy) {
      this.policyForm = {
        id: policy.id,
        name: policy.name || "",
        policy_type: policy.policy_type || "ima_runtime",
        version: policy.version || 1,
        status: policy.status || "draft",
        hash_alg: policy.hash_alg || "sha256",
        description: policy.description || "",
        contentText: JSON.stringify(policy.content || {}, null, 2),
        protectedPathsText: (policy.protected_paths || []).join("\n"),
        excludesText: (policy.excludes || []).join("\n")
      };
      this.view = "policies";
    },
    newPolicy() {
      this.policyForm = emptyPolicyForm();
    },
    parseLines(text) {
      return String(text || "")
        .split(/\r?\n/)
        .map((item) => item.trim())
        .filter(Boolean);
    },
    policyPayload() {
      let content = {};
      try {
        content = JSON.parse(this.policyForm.contentText || "{}");
      } catch (_error) {
        throw new Error("策略内容必须是合法 JSON");
      }
      return {
        name: this.policyForm.name.trim(),
        policy_type: this.policyForm.policy_type,
        version: Number(this.policyForm.version || 1),
        status: this.policyForm.status,
        hash_alg: this.policyForm.hash_alg,
        description: this.policyForm.description,
        content,
        protected_paths: this.parseLines(this.policyForm.protectedPathsText),
        excludes: this.parseLines(this.policyForm.excludesText),
        source: {}
      };
    },
    async savePolicy() {
      if (!this.adminToken) {
        this.showNotice("bad", "请先填写管理令牌");
        return;
      }
      this.busy = true;
      try {
        const payload = this.policyPayload();
        const path = this.policyForm.id ? `/api/policies/${this.policyForm.id}` : "/api/policies";
        const method = this.policyForm.id ? "PUT" : "POST";
        const saved = await this.requestJson(path, {
          method,
          body: JSON.stringify(payload)
        });
        this.showNotice("ok", this.policyForm.id ? "策略已更新" : "策略已创建");
        await this.refreshAll(false);
        this.selectPolicy(saved);
      } catch (error) {
        this.showNotice("bad", error.message);
      } finally {
        this.busy = false;
      }
    },
    async deletePolicy(policy) {
      if (!this.adminToken) {
        this.showNotice("bad", "请先填写管理令牌");
        return;
      }
      if (!confirm(`确认删除策略「${policy.name}」？`)) return;
      this.busy = true;
      try {
        await this.requestJson(`/api/policies/${policy.id}`, { method: "DELETE" });
        this.showNotice("ok", "策略已删除");
        if (this.policyForm.id === policy.id) this.newPolicy();
        await this.refreshAll(false);
      } catch (error) {
        this.showNotice("bad", error.message);
      } finally {
        this.busy = false;
      }
    }
  }
}).mount("#app");
