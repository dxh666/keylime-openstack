import { nodeDetailMethods } from "./node-detail.js";

export const nodeMethods = {
  ...nodeDetailMethods,
  async refreshNodeStatus() {
    await this.refreshAll(true);
    if (!this.apiError) this.showNotice("ok", "节点状态已刷新。");
  },
  async refreshComputeNodeStatus(row) {
    if (row?.trustAgentType === "opentcsm_tpcm") {
      await this.collectOpenTcsmStatus(row);
      return;
    }
    await this.refreshNodeStatus();
  },
  async collectOpenTcsmStatus(row) {
    const host = encodeURIComponent(row?.host || row?.rawNode?.hostname || "");
    if (!host) return this.showNotice("bad", "节点名称不能为空。");
    this.busy = true;
    try {
      await this.requestJson(`/api/nodes/${host}/opentcsm-collect`, { method: "POST" });
      await this.refreshAll(false);
      this.showNotice("ok", "可信状态已刷新。");
    } catch (error) {
      this.showNotice("bad", error.message);
    } finally {
      this.busy = false;
    }
  },
  async runOpenTcsmAccessCheck(row) {
    const hostname = row?.host || row?.rawNode?.hostname || "";
    const host = encodeURIComponent(hostname);
    if (!host) return this.showNotice("bad", "节点名称不能为空。");
    this.busy = true;
    try {
      const result = await this.requestJson(`/api/nodes/${host}/opentcsm-access-check`, { method: "POST" });
      this.opentcsmAccessChecks = {
        ...this.opentcsmAccessChecks,
        [hostname]: result
      };
      await this.refreshAll(false);
      const freshRow = this.computeRows.find((item) => item.host === hostname) || row;
      this.openComputeDetail(freshRow);
      this.showNotice(result.ok ? "ok" : "bad", result.summary || "OpenTCSM 接入检查已完成。");
    } catch (error) {
      this.showNotice("bad", error.message);
    } finally {
      this.busy = false;
    }
  },
  osText(kernel) {
    const value = String(kernel || "");
    if (value.includes("generic")) return "Ubuntu Server";
    if (value.includes("an23")) return "Anolis OS 23";
    return "-";
  },
  nodeOsText(node) {
    return node?.facts?.os || this.osText(node?.facts?.kernel) || "-";
  },
  nodeCpuText(node) {
    return (
      node?.hardware_profile?.model ||
      node?.facts?.cpu_model ||
      node?.facts?.cpu_vendor ||
      "-"
    );
  },
  isOpenTcsmNode(row, node, keylimeNode) {
    return (
      row?.trustAgentType === "opentcsm_tpcm" ||
      node?.trust_agent_type === "opentcsm_tpcm" ||
      keylimeNode?.trust_agent_type === "opentcsm_tpcm"
    );
  },
  keylimeManagedText(node, keylimeNode) {
    const agentType = node?.trust_agent_type || keylimeNode?.trust_agent_type || "unmanaged";
    const managed = node?.trust_managed ?? keylimeNode?.trust_managed ?? agentType !== "unmanaged";
    if (!managed || agentType === "unmanaged" || keylimeNode?.status === "unmanaged") return "未纳管";
    if (agentType === "opentcsm_tpcm") {
      if (keylimeNode.status === "collected") return "已纳管";
      return "待上报";
    }
    if (keylimeNode.status === "error") return "纳管异常";
    if (keylimeNode.status === "collected") return "已纳管";
    if (node.keylime_agent_uuid) return "待验证";
    return "未纳管";
  },
  keylimeManagedClass(node, keylimeNode) {
    const agentType = node?.trust_agent_type || keylimeNode?.trust_agent_type || "unmanaged";
    const managed = node?.trust_managed ?? keylimeNode?.trust_managed ?? agentType !== "unmanaged";
    if (!managed || agentType === "unmanaged" || keylimeNode?.status === "unmanaged") return "warn";
    if (agentType === "opentcsm_tpcm") {
      if (keylimeNode.status === "collected") return "ok";
      return "warn";
    }
    if (keylimeNode.status === "error") return "bad";
    if (keylimeNode.status === "collected") return "ok";
    if (node.keylime_agent_uuid) return "warn";
    return "bad";
  },
  trustAgentName(node, keylimeNode) {
    const agentType = node?.trust_agent_type || keylimeNode?.trust_agent_type || "unmanaged";
    const managed = node?.trust_managed ?? keylimeNode?.trust_managed ?? agentType !== "unmanaged";
    if (!managed || agentType === "unmanaged") return "未纳管";
    return node.trust_agent_name || keylimeNode.trust_agent_name || "可信代理";
  },
  trustedRoot(node, keylimeNode) {
    return node.trusted_root || keylimeNode.trusted_root || "unknown";
  },
  agentUuidText(node, keylimeNode) {
    const agentType = node?.trust_agent_type || keylimeNode?.trust_agent_type || "unmanaged";
    const managed = node?.trust_managed ?? keylimeNode?.trust_managed ?? agentType !== "unmanaged";
    if (!managed) return "-";
    if (agentType === "opentcsm_tpcm") return "不适用";
    return node.keylime_agent_uuid || keylimeNode.agent_uuid || "-";
  },
  agentPortText(node) {
    if (node.trust_managed === false) return "-";
    if (node.trust_agent_type === "opentcsm_tpcm") return "不适用";
    return node.keylime_agent_port || "-";
  },
  proofTime(node) {
    return this.formatTime(this.timestampFromSeconds(node.last_successful_attestation || node.last_received_quote));
  },
};
