function normalizeTrustedRoot(value) {
  const normalized = String(value || "").trim().toLowerCase();
  if (normalized === "tpm" || normalized === "tpcm") return normalized;
  return "unknown";
}

function boolValue(value, fallback = false) {
  return value === undefined || value === null ? fallback : value === true;
}

function stringValue(value) {
  return String(value || "").trim();
}

function agentPort(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? Math.trunc(parsed) : 9002;
}

function adapterTypeForProfile(form) {
  if (!form.trustManaged) return "";
  if (form.trustedRootType === "tpm") return "keylime";
  if (form.trustedRootType === "tpcm") return "opentcsm";
  return "";
}

function defaultCapabilities(form) {
  if (!form.trustManaged) {
    return {
      trustedBoot: false,
      imaRuntime: false,
      tpcmDynamicMeasurement: false
    };
  }
  if (form.trustedRootType === "tpm") {
    return {
      trustedBoot: true,
      imaRuntime: true,
      tpcmDynamicMeasurement: false
    };
  }
  if (form.trustedRootType === "tpcm") {
    return {
      trustedBoot: true,
      imaRuntime: false,
      tpcmDynamicMeasurement: true
    };
  }
  return {
    trustedBoot: false,
    imaRuntime: false,
    tpcmDynamicMeasurement: false
  };
}

function apiCapabilities(form) {
  if (!form.trustManaged) return {};
  if (form.trustedRootType === "tpm") {
    return {
      trusted_boot: boolValue(form.capabilities.trustedBoot, true),
      ima_runtime: boolValue(form.capabilities.imaRuntime, true),
      tpcm_dynamic_measurement: false,
      evm: false
    };
  }
  if (form.trustedRootType === "tpcm") {
    return {
      trusted_boot: boolValue(form.capabilities.trustedBoot, true),
      ima_runtime: false,
      tpcm_dynamic_measurement: boolValue(form.capabilities.tpcmDynamicMeasurement, true),
      evm: false
    };
  }
  return {};
}

function endpointForProfile(form) {
  if (!form.trustManaged) return {};
  if (form.trustedRootType === "tpm") {
    return {
      host: stringValue(form.agentHost),
      port: agentPort(form.agentPort)
    };
  }
  if (form.trustedRootType === "tpcm") {
    return {
      transport: stringValue(form.agentTransport) || "ssh",
      host: stringValue(form.agentHost)
    };
  }
  return {};
}

function identityForProfile(form) {
  if (!form.trustManaged) return {};
  if (form.trustedRootType === "tpm") {
    return {
      keylime_agent_uuid: stringValue(form.tpmAgentUuid)
    };
  }
  if (form.trustedRootType === "tpcm") {
    return {
      tpcm_id: stringValue(form.tpcmId)
    };
  }
  return {};
}

export function emptyTrustProfileForm() {
  return {
    hostname: "",
    openstackComputeName: "",
    managementIp: "",
    isOpenStackCompute: true,
    trustManaged: false,
    trustedRootType: "unknown",
    agentHost: "",
    agentPort: 9002,
    agentTransport: "ssh",
    tpmAgentUuid: "",
    tpcmId: "",
    capabilities: {
      trustedBoot: false,
      imaRuntime: false,
      tpcmDynamicMeasurement: false
    }
  };
}

function trustProfileFormFromRow(row) {
  const node = row?.rawNode || row || {};
  const keylimeNode = row?.keylimeNode || {};
  const profile = node.trusted_node_profile || {};
  const endpoint = profile.agent_endpoint || node.agent_endpoint || {};
  const identity = profile.agent_identity || node.agent_identity || {};
  const capabilities = profile.capabilities || row?.capabilities || node.capabilities || {};
  const trustedRootType = normalizeTrustedRoot(
    profile.trusted_root_type || row?.trustedRootType || node.trusted_root_type
  );
  const trustManaged = boolValue(
    profile.trust_managed ?? row?.trustManaged ?? node.trust_managed,
    false
  );
  const form = {
    hostname: stringValue(row?.host || profile.hostname || node.hostname),
    openstackComputeName: stringValue(
      profile.openstack_compute_name ||
        row?.openstackComputeName ||
        node.openstack_compute_name ||
        node.hypervisor_name ||
        node.hostname
    ),
    managementIp: stringValue(
      profile.management_ip ||
        node.management_ip ||
        node.keylime_agent_ip ||
        row?.ownIp ||
        keylimeNode.agent_ip
    ),
    isOpenStackCompute: boolValue(
      profile.is_openstack_compute ?? (node.role ? node.role === "compute" : true),
      true
    ),
    trustManaged,
    trustedRootType,
    agentHost: stringValue(endpoint.host || node.keylime_agent_ip || node.management_ip || row?.ownIp),
    agentPort: agentPort(endpoint.port || node.keylime_agent_port || 9002),
    agentTransport: stringValue(endpoint.transport) || "ssh",
    tpmAgentUuid: stringValue(identity.keylime_agent_uuid || node.keylime_agent_uuid || keylimeNode.agent_uuid),
    tpcmId: stringValue(identity.tpcm_id || node.facts?.tpcm_id),
    capabilities: {
      trustedBoot: boolValue(capabilities.trusted_boot, trustManaged && trustedRootType !== "unknown"),
      imaRuntime: boolValue(capabilities.ima_runtime, trustManaged && trustedRootType === "tpm"),
      tpcmDynamicMeasurement: boolValue(
        capabilities.tpcm_dynamic_measurement,
        trustManaged && trustedRootType === "tpcm"
      )
    }
  };
  if (!capabilities || !Object.keys(capabilities).length) {
    form.capabilities = defaultCapabilities(form);
  }
  return form;
}

export const trustProfileMethods = {
  openTrustProfileDialog(row) {
    this.closeNodeDetail();
    this.trustProfileDialog = {
      open: true,
      node: row,
      form: trustProfileFormFromRow(row)
    };
  },
  closeTrustProfileDialog() {
    this.trustProfileDialog = {
      open: false,
      node: null,
      form: emptyTrustProfileForm()
    };
  },
  syncTrustProfileCapabilityDefaults() {
    const form = this.trustProfileDialog.form;
    form.capabilities = defaultCapabilities(form);
  },
  async saveTrustProfile() {
    const form = this.trustProfileDialog.form;
    const hostname = stringValue(form.hostname);
    if (!hostname) return this.showNotice("bad", "节点名称不能为空。");
    if (form.trustManaged && !["tpm", "tpcm"].includes(form.trustedRootType)) {
      return this.showNotice("bad", "已纳管节点必须选择 TPM 或 TPCM 可信根。");
    }
    if (form.trustManaged && !stringValue(form.agentHost)) {
      return this.showNotice("bad", "已纳管节点必须填写可信代理地址。");
    }

    this.busy = true;
    try {
      await this.requestJson(`/api/nodes/${encodeURIComponent(hostname)}/trusted-node-profile`, {
        method: "PUT",
        body: JSON.stringify({
          hostname,
          openstack_compute_name: stringValue(form.openstackComputeName) || hostname,
          management_ip: stringValue(form.managementIp),
          is_openstack_compute: form.isOpenStackCompute === true,
          trust_managed: form.trustManaged === true,
          trusted_root_type: form.trustedRootType,
          adapter_type: adapterTypeForProfile(form),
          agent_endpoint: endpointForProfile(form),
          agent_identity: identityForProfile(form),
          capabilities: apiCapabilities(form)
        })
      });
      this.closeTrustProfileDialog();
      await this.refreshAll(false);
      this.showNotice("ok", "可信节点纳管配置已保存。");
    } catch (error) {
      this.showNotice("bad", error.message);
    } finally {
      this.busy = false;
    }
  },
};
