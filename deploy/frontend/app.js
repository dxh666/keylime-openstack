const { createApp } = Vue;

const POLICY_TYPES = [
  { key: "tpm_pcr", label: "TPM 启动策略" },
  { key: "ima_runtime", label: "IMA 运行时策略" },
  { key: "evm", label: "EVM 策略" }
];

const emptyPolicyForm = (policyType = "tpm_pcr") => ({
  name: "",
  policy_type: policyType,
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
      policyTypes: POLICY_TYPES,
      activePolicyType: "tpm_pcr",
      busy: false,
      notice: { kind: "", text: "" },
      keylime: {},
      policies: [],
      createDialogOpen: false,
      createForm: emptyPolicyForm(),
      detailPolicy: null,
      tokenDialog: {
        open: false,
        title: "",
        message: "",
        confirmText: "确认",
        token: "",
        action: null
      },
      timer: null
    };
  },
  computed: {
    currentPolicyType() {
      return this.policyTypes.find((item) => item.key === this.activePolicyType) || this.policyTypes[0];
    },
    currentTitle() {
      return this.view === "nodes" ? "节点状态" : "策略管理";
    },
    filteredPolicies() {
      return this.policies.filter((policy) => policy.policy_type === this.activePolicyType);
    }
  },
  mounted() {
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
    statusText(value) {
      const names = {
        active: "生效",
        draft: "草稿",
        disabled: "停用"
      };
      return names[value] || value || "-";
    },
    statusClass(value) {
      if (value === "active") return "ok";
      if (value === "disabled") return "bad";
      return "warn";
    },
    ageText(value) {
      if (value === null || value === undefined) return "-";
      return `${value} 秒`;
    },
    headers(token = "") {
      const headers = { "Content-Type": "application/json" };
      if (token) headers["X-Admin-Token"] = token;
      return headers;
    },
    async requestJson(path, options = {}, token = "") {
      const response = await fetch(path, {
        ...options,
        headers: { ...this.headers(token), ...(options.headers || {}) }
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
    openCreatePolicy() {
      this.createForm = emptyPolicyForm(this.activePolicyType);
      this.createDialogOpen = true;
      this.$nextTick(() => {
        const input = document.querySelector(".policy-create-dialog input");
        if (input) input.focus();
      });
    },
    closeCreatePolicy() {
      if (this.busy) return;
      this.createDialogOpen = false;
    },
    viewPolicy(policy) {
      this.detailPolicy = policy;
    },
    closePolicyDetail() {
      this.detailPolicy = null;
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
        content = JSON.parse(this.createForm.contentText || "{}");
      } catch (_error) {
        throw new Error("策略内容必须是合法 JSON");
      }
      const name = this.createForm.name.trim();
      if (!name) throw new Error("策略名称不能为空");
      return {
        name,
        policy_type: this.activePolicyType,
        version: Number(this.createForm.version || 1),
        status: this.createForm.status,
        hash_alg: this.createForm.hash_alg || "sha256",
        description: this.createForm.description,
        content,
        protected_paths: this.parseLines(this.createForm.protectedPathsText),
        excludes: this.parseLines(this.createForm.excludesText),
        source: {}
      };
    },
    requireToken({ title, message, confirmText, action }) {
      this.tokenDialog = {
        open: true,
        title,
        message,
        confirmText,
        token: "",
        action
      };
      this.$nextTick(() => {
        const input = document.querySelector(".token-dialog input");
        if (input) input.focus();
      });
    },
    closeTokenDialog() {
      if (this.busy) return;
      this.tokenDialog.open = false;
      this.tokenDialog.token = "";
      this.tokenDialog.action = null;
    },
    async confirmTokenDialog() {
      const token = this.tokenDialog.token.trim();
      if (!token) {
        this.showNotice("bad", "请输入管理令牌");
        return;
      }
      const action = this.tokenDialog.action;
      if (!action) return;
      this.busy = true;
      try {
        await action(token);
        this.closeTokenDialog();
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
          message: `将创建${this.currentPolicyType.label}「${payload.name}」。`,
          confirmText: "确认新增",
          action: async (token) => {
            await this.requestJson("/api/policies", {
              method: "POST",
              body: JSON.stringify(payload)
            }, token);
            this.showNotice("ok", "策略已创建");
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
        message: `将删除策略「${policy.name}」。如果策略已经绑定到节点，后端会拒绝删除。`,
        confirmText: "确认删除",
        action: async (token) => {
          await this.requestJson(`/api/policies/${policy.id}`, { method: "DELETE" }, token);
          this.showNotice("ok", "策略已删除");
          if (this.detailPolicy && this.detailPolicy.id === policy.id) this.closePolicyDetail();
          await this.refreshAll(false);
        }
      });
    },
    prettyJson(value) {
      return JSON.stringify(value || {}, null, 2);
    },
    listText(items) {
      return (items || []).length ? items.join("\n") : "-";
    }
  }
}).mount("#app");
