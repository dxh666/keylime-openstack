export const LIST_PAGE_SIZES = [10, 20, 50];

const AUDIT_CATEGORIES = [
  { key: "all", label: "全部" },
  { key: "auth", label: "登录审计" },
  { key: "enrollment", label: "节点纳管" },
  { key: "verification", label: "可信验证" },
  { key: "dynamic", label: "动态度量" },
  { key: "policy", label: "策略操作" },
  { key: "environment", label: "环境同步" }
];

function auditDetails(event) {
  return event?.event_details || {};
}

function auditEventType(event) {
  return String(event?.event_type || "");
}

function auditLogType(event) {
  return String(auditDetails(event).log_type || "");
}

function auditMessage(event) {
  return String(event?.message || "");
}

function auditCategoryMatches(event, category) {
  const eventType = auditEventType(event);
  const logType = auditLogType(event);
  const message = auditMessage(event);

  if (category === "all") return true;
  if (category === "auth") return ["auth_login", "auth_logout"].includes(eventType);
  if (category === "enrollment") {
    return (
      ["trusted_node_registration_sync", "trusted_node_register", "trust_agent_evidence_collect"].includes(eventType) ||
      logType === "node_management" ||
      ["TRUST_AGENT_UNMANAGED", "trusted-root-agent-unmanaged"].includes(message)
    );
  }
  if (category === "dynamic") {
    return (
      eventType.startsWith("tpcm_dynamic_") ||
      ["opentcsm_evidence_collect", "opentcsm_access_check"].includes(eventType) ||
      ["dynamic_measurement", "tpcm_authorization"].includes(logType)
    );
  }
  if (category === "verification") {
    return (
      ["trusted_node_verify", "trust_decision", "keylime_evidence_collect", "host_integrity_evidence_collect"].includes(eventType) ||
      ["trust_verification", "measured_boot", "ima_runtime"].includes(logType)
    );
  }
  if (category === "policy") {
    return eventType.startsWith("policy_") && !eventType.startsWith("tpcm_dynamic_");
  }
  if (category === "environment") return eventType === "openstack_state_refresh";
  return false;
}

function normalizePageSize(value) {
  const parsed = Number(value);
  return LIST_PAGE_SIZES.includes(parsed) ? parsed : 20;
}

function pageInfo(total, state = {}) {
  const pageSize = normalizePageSize(state.pageSize);
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const page = Math.min(Math.max(Number(state.page) || 1, 1), totalPages);
  return {
    total,
    page,
    pageSize,
    totalPages,
    start: total ? (page - 1) * pageSize + 1 : 0,
    end: total ? Math.min(total, page * pageSize) : 0,
    hasPrev: page > 1,
    hasNext: page < totalPages
  };
}

function paginate(items, state) {
  const info = pageInfo(items.length, state);
  return items.slice((info.page - 1) * info.pageSize, info.page * info.pageSize);
}

function pageState(vm, key) {
  if (!vm.listPagination[key]) {
    vm.listPagination[key] = { page: 1, pageSize: 20 };
  }
  return vm.listPagination[key];
}

function listTotal(vm, key) {
  if (key === "alerts") return vm.alertRows.length;
  if (key === "tasks") return vm.visibleTasks.length;
  if (key === "audit") return vm.filteredAuditEvents.length;
  return 0;
}

export const listPageComputed = {
  auditCategoryTabs() {
    return AUDIT_CATEGORIES.map((item) => ({
      ...item,
      count: item.key === "all"
        ? this.auditEvents.length
        : this.auditEvents.filter((event) => auditCategoryMatches(event, item.key)).length
    }));
  },
  filteredAuditEvents() {
    return (this.auditEvents || []).filter((event) => auditCategoryMatches(event, this.activeAuditCategory));
  },
  alertPageInfo() {
    return pageInfo(this.alertRows.length, this.listPagination.alerts);
  },
  taskPageInfo() {
    return pageInfo(this.visibleTasks.length, this.listPagination.tasks);
  },
  auditPageInfo() {
    return pageInfo(this.filteredAuditEvents.length, this.listPagination.audit);
  },
  paginatedAlertRows() {
    return paginate(this.alertRows, this.listPagination.alerts);
  },
  paginatedVisibleTasks() {
    return paginate(this.visibleTasks, this.listPagination.tasks);
  },
  paginatedAuditEvents() {
    return paginate(this.filteredAuditEvents, this.listPagination.audit);
  }
};

export const listPageMethods = {
  changeListPage(key, page) {
    const state = pageState(this, key);
    state.page = pageInfo(listTotal(this, key), { ...state, page }).page;
  },
  changeListPageSize(key, pageSize) {
    const state = pageState(this, key);
    state.pageSize = normalizePageSize(pageSize);
    state.page = 1;
  },
  selectAuditCategory(key) {
    if (!AUDIT_CATEGORIES.some((item) => item.key === key)) return;
    this.activeAuditCategory = key;
    this.changeListPage("audit", 1);
  }
};
