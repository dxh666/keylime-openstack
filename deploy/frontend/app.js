const { createApp } = Vue;

const POLICY_TYPES = [
  { key: "measured_boot", label: "可信启动", creatable: true },
  { key: "ima_runtime", label: "IMA 运行时策略", creatable: true },
  { key: "evm", label: "EVM 策略", creatable: false }
];

const DEFAULT_IMA_POLICY = `dont_measure fsmagic=0x9fa0
dont_measure fsmagic=0x62656572
dont_measure fsmagic=0x64626720
dont_measure fsmagic=0x1021994
dont_measure fsmagic=0x73636673
dont_measure fsmagic=0x27e0eb
measure func=KEY_CHECK keyrings=.ima
measure func=BPRM_CHECK mask=MAY_EXEC
measure func=MMAP_CHECK mask=MAY_EXEC
measure func=MODULE_CHECK
measure func=FIRMWARE_CHECK
measure func=POLICY_CHECK`;

const DEFAULT_IMA_EXCLUDES = `^/var/lib/docker/containers/[0-9a-f]+/\\.tmp-config\\.v2\\.json.*$
^/var/lib/docker/containers/[0-9a-f]+/\\.tmp-hostconfig\\.json.*$
^/var/lib/docker/containers/[0-9a-f]+/[0-9a-f]+-json\\.log.*$
^/var/lib/docker/network/files/local-kv\\.db$
^/tmp/tmp[A-Za-z0-9._-]+$
^/var/log/journal/[0-9a-f]+/.*\\.journal$
^/var/lib/docker/.*$
^/var/lib/containerd/.*$
^/run/.*$
^/var/run/.*$
^/tmp/.*$
^/var/tmp/.*$
^/var/log/.*$
^/dev/.*$
^/etc/mtab$
^/root/\\.ansible/tmp/.*$
^/home/[^/]+/\\.ansible/tmp/.*$
^/var/lib/apt/.*$
^/var/cache/apt/.*$
^/var/lib/ubuntu-advantage/apt-esm/.*$
^/var/lib/update-notifier/.*$
^/var/lib/landscape/.*$
^/var/lib/openvswitch/.*\\.db$
^/run_command$
^/.*/__pycache__/.*\\.pyc$
^/usr/lib/python[0-9.]+/.*/__pycache__/.*\\.pyc$
^/usr/lib/python3/dist-packages/.*/__pycache__/.*\\.pyc$`;

const emptyPolicyForm = (policyType = "measured_boot") => ({
  name: "",
  description: "",
  targetNodeIds: [],
  pcrs: [0, 1, 2, 3, 4, 5, 6, 7],
  secureBootRequired: true,
  nodeImaPolicy: DEFAULT_IMA_POLICY,
  excludesText: DEFAULT_IMA_EXCLUDES,
  rebootAfterApply: false,
  policy_type: policyType
});

createApp({
  data() {
    return {
      view: "nodes",
      policyTypes: POLICY_TYPES,
      activePolicyType: "measured_boot",
      busy: false,
      loading: true,
      keylimeError: "",
      notice: { kind: "", text: "" },
      keylime: { ok: null, nodes: [], nodes_total: 0, nodes_trusted: 0, trust_capabilities: {} },
      nodes: [],
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
      return this.view === "nodes" ? "节点状态" : this.currentPolicyType.label;
    },
    keylimeStatusClass() {
      if (this.keylimeError) return "bad";
      if (this.keylime.ok === true) return "ok";
      if (this.keylime.ok === false) return "bad";
      return "warn";
    },
    keylimeStatusText() {
      if (this.keylimeError) return "接口异常";
      if (this.loading) return "检查中";
      return this.gateText(this.keylime.ok);
    },
    policyNamePlaceholder() {
      return this.activePolicyType === "measured_boot"
        ? "例如 compute-measured-boot-v1"
        : "例如 compute-ima-runtime-v1";
    },
    filteredPolicies() {
      return this.policies.filter((policy) => policy.policy_type === this.activePolicyType);
    },
    trustCapabilityItems() {
      const caps = this.keylime.trust_capabilities || {};
      return [
        { key: "boot", label: "可信启动", enabled: caps.boot === true },
        { key: "ima", label: "IMA 运行时度量", enabled: caps.ima === true },
        { key: "evm", label: "EVM 完整性保护", enabled: caps.evm === true },
        { key: "openstack_service", label: "OpenStack 服务状态", enabled: caps.openstack_service === true }
      ];
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
    selectPolicyType(policyType) {
      this.view = "policies";
      this.activePolicyType = policyType;
      this.detailPolicy = null;
    },
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
      if (value === false) return "未通过";
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
        active: "启用",
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
    deploymentText(value) {
      const names = {
        queued: "等待下发",
        applying: "下发中",
        applied: "已下发",
        awaiting_reboot: "等待重启",
        failed: "下发失败",
        superseded: "已替换",
        not_deployed: "未下发"
      };
      return names[value] || value || "-";
    },
    deploymentClass(value) {
      if (value === "applied") return "ok";
      if (value === "failed") return "bad";
      return "warn";
    },
    ageText(value) {
      if (value === null || value === undefined) return "-";
      return `${value} 秒`;
    },
    enabledCapabilityNames(node = null) {
      const caps = (node && node.trust_capabilities) || this.keylime.trust_capabilities || {};
      const names = [
        ["boot", "可信启动"],
        ["ima", "IMA"],
        ["evm", "EVM"],
        ["openstack_service", "OpenStack 服务"]
      ];
      const enabled = names.filter(([key]) => caps[key] === true).map(([, label]) => label);
      return enabled.length ? enabled.join("、") : "未启用";
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
      if (!response.ok) throw new Error(data.detail || data.error || `HTTP ${response.status}`);
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
      if (showBusy && this.keylime.ok === null) this.loading = true;
      try {
        const [keylime, nodes, policies] = await Promise.all([
          this.requestJson("/api/keylime/check"),
          this.requestJson("/api/nodes"),
          this.requestJson("/api/policies")
        ]);
        this.keylime = keylime;
        this.keylimeError = "";
        this.nodes = nodes.filter((node) => node.role === "compute" && node.enabled);
        this.policies = policies;
      } catch (error) {
        this.keylimeError = error.message;
        this.showNotice("bad", error.message);
      } finally {
        this.loading = false;
        if (showBusy) this.busy = false;
      }
    },
    openCreatePolicy() {
      if (!this.currentPolicyType.creatable) return;
      this.createForm = emptyPolicyForm(this.activePolicyType);
      this.createDialogOpen = true;
      this.$nextTick(() => {
        const input = document.querySelector(".policy-create-dialog input");
        if (input) input.focus();
      });
    },
    closeCreatePolicy() {
      if (!this.busy) this.createDialogOpen = false;
    },
    viewPolicy(policy) {
      this.detailPolicy = policy;
    },
    closePolicyDetail() {
      this.detailPolicy = null;
    },
    parseLines(text) {
      return String(text || "").split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
    },
    policyPayload() {
      const name = this.createForm.name.trim();
      if (!name) throw new Error("策略名称不能为空");
      if (!this.createForm.targetNodeIds.length) throw new Error("请至少选择一个节点");
      let content;
      let excludes = [];
      if (this.activePolicyType === "measured_boot") {
        content = {
          policy_engine: "example",
          pcrs: this.createForm.pcrs.map(Number).sort((a, b) => a - b),
          secure_boot_required: this.createForm.secureBootRequired,
          reference_state_mode: "collect_from_node"
        };
      } else if (this.activePolicyType === "ima_runtime") {
        content = {
          node_ima_policy: this.createForm.nodeImaPolicy,
          runtime_policy_generation: "keylime-policy-from-measurements",
          reboot_after_apply: this.createForm.rebootAfterApply
        };
        excludes = this.parseLines(this.createForm.excludesText);
      } else {
        throw new Error("EVM 策略新增尚未启用");
      }
      return {
        name,
        policy_type: this.activePolicyType,
        version: 1,
        status: "active",
        hash_alg: "sha256",
        description: this.createForm.description.trim(),
        content,
        protected_paths: [],
        excludes,
        source: {},
        target_node_ids: this.createForm.targetNodeIds.map(Number),
        deploy_now: true
      };
    },
    bindingNames(policy) {
      return (policy.bindings || []).map((item) => item.target_name).join("、") || "-";
    },
    bindingState(policy) {
      const states = [...new Set((policy.bindings || []).map((item) => item.application_status))];
      return states.length === 1 ? states[0] : states.length ? "mixed" : "not_deployed";
    },
    bindingStateText(policy) {
      const state = this.bindingState(policy);
      return state === "mixed" ? "状态不一致" : this.deploymentText(state);
    },
    canDeployPolicy(policy) {
      const retryable = new Set(["not_deployed", "failed", "awaiting_reboot"]);
      return (policy.bindings || []).some((binding) => retryable.has(binding.application_status));
    },
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
    createPolicy() {
      try {
        const payload = this.policyPayload();
        this.requireToken({
          title: "确认新增策略",
          message: `将创建${this.currentPolicyType.label}「${payload.name}」并下发至所选节点。`,
          confirmText: "确认新增",
          action: async (token) => {
            await this.requestJson("/api/policies", {
              method: "POST",
              body: JSON.stringify(payload)
            }, token);
            this.showNotice("ok", "策略已创建并进入下发队列");
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
        message: `将删除策略「${policy.name}」。`,
        confirmText: "确认删除",
        action: async (token) => {
          await this.requestJson(`/api/policies/${policy.id}`, { method: "DELETE" }, token);
          this.showNotice("ok", "策略已删除");
          if (this.detailPolicy && this.detailPolicy.id === policy.id) this.closePolicyDetail();
          await this.refreshAll(false);
        }
      });
    },
    deployPolicy(policy) {
      this.requireToken({
        title: "确认下发策略",
        message: `将重新下发策略「${policy.name}」。`,
        confirmText: "确认下发",
        action: async (token) => {
          await this.requestJson(`/api/policies/${policy.id}/deploy`, { method: "POST" }, token);
          this.showNotice("ok", "策略已进入下发队列");
          this.detailPolicy = null;
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
