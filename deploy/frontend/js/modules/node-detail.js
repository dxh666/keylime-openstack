export const nodeDetailMethods = {
  openControllerDetail(node) {
    this.detailNode = {
      title: node.hostname || "控制节点",
      subtitle: "控制节点详情",
      sections: [
        {
          title: "基本信息",
          items: [
            { label: "节点名称", value: node.hostname || "-" },
            { label: "IP 地址", value: node.management_ip || "-" },
            { label: "操作系统", value: this.nodeOsText(node) },
            { label: "内核版本", value: node.facts?.kernel || "-" },
            { label: "CPU", value: this.nodeCpuText(node) },
            { label: "CPU 核心数", value: node.facts?.cpu_count || "-" }
          ]
        },
        {
          title: "控制面运行状况",
          items: this.controlRuntimeItems.map((item) => ({
            label: item.name,
            value: item.status,
            state: item.state
          }))
        }
      ]
    };
  },
  openComputeDetail(row) {
    const node = row.rawNode || {};
    const keylimeNode = row.keylimeNode || {};
    const remediation = keylimeNode.remediation || {};
    const sections = [
      {
        title: "基本信息",
        items: [
          { label: "节点名称", value: row.host || node.hostname || "-" },
          { label: "OpenStack 计算名称", value: row.openstackComputeName || node.openstack_compute_name || node.hypervisor_name || row.host || "-" },
          { label: "管理节点 IP", value: row.managementIp || "-" },
          { label: "自身 IP", value: row.ownIp || "-" },
          { label: "操作系统", value: this.nodeOsText(node) },
          { label: "CPU", value: this.nodeCpuText(node) }
        ]
      },
      {
        title: "OpenStack 计算服务",
        items: [
          { label: "服务名称", value: node.openstack_state?.service_binary || "nova-compute" },
          { label: "服务状态", value: row.openstackText, state: row.openstackClass },
          { label: "status", value: node.openstack_state?.service_status || "-" },
          { label: "state", value: node.openstack_state?.service_state || "-" },
          { label: "更新时间", value: this.formatTime(node.openstack_state?.updated_at) }
        ]
      },
      {
        title: "可信代理",
        items: [
          { label: "纳管状态", value: row.managed, state: row.managedClass },
          { label: "代理名称", value: row.trustAgentName || this.trustAgentName(node, keylimeNode) },
          { label: "可信根", value: row.trustedRoot || this.trustedRoot(node, keylimeNode) },
          { label: "Agent UUID", value: this.agentUuidText(node, keylimeNode) },
          { label: "Agent IP", value: node.keylime_agent_ip || keylimeNode.agent_ip || node.management_ip || "-" },
          { label: "端口", value: this.agentPortText(node) },
          { label: "证明状态", value: keylimeNode.attestation_status || "-" },
          { label: "运行状态", value: keylimeNode.operational_state ?? "-" },
          { label: "最近事件", value: keylimeNode.last_event_id || "-" }
        ]
      },
      {
        title: "可信状态",
        items: [
          { label: "可信状态", value: row.trustText, state: row.trustClass },
          { label: "可信启动", value: this.stateText(keylimeNode.evidence?.boot), state: this.evidenceClass(keylimeNode.evidence?.boot) },
          { label: "IMA 运行时", value: this.stateText(keylimeNode.evidence?.runtime), state: this.evidenceClass(keylimeNode.evidence?.runtime) },
          { label: "最近证明时间", value: row.attestationTime },
          { label: "原因", value: row.reason || "-" }
        ]
      }
    ];
    if (this.isOpenTcsmNode(row, node, keylimeNode)) {
      const report = keylimeNode.trust_report || {};
      const history = keylimeNode.trust_report_history || [];
      sections.push({
        title: "TPCM 采集健康",
        items: [
          { label: "采集状态", value: this.collectionStatusText(keylimeNode.status), state: this.collectionStatusClass(keylimeNode.status) },
          { label: "证据有效性", value: this.evidenceFreshText(keylimeNode.evidence_fresh), state: this.evidenceFreshClass(keylimeNode.evidence_fresh) },
          { label: "最近采集", value: this.formatTime(report.collected_at) },
          { label: "有效期", value: this.formatTime(report.valid_until) },
          { label: "异常信息", value: this.collectionErrorText(keylimeNode, report) }
        ]
      });
      const accessCheck = this.opentcsmAccessChecks[row.host || node.hostname];
      if (accessCheck) {
        sections.push({
          title: "TPCM 接入检查",
          items: this.opentcsmAccessCheckItems(accessCheck)
        });
      }
      sections.push({
        title: "TPCM 能力状态",
        items: [
          { label: "可信根", value: report.trust_root || row.trustedRoot || "-" },
          { label: "TPCM ID", value: report.tpcm_id || "-" },
          { label: "启动度量", value: this.enabledText(report.boot_measure_on) },
          { label: "动态度量", value: this.enabledText(report.dynamic_measure_on) },
          { label: "启动基线数量", value: this.countText(report.boot_measure_ref_number) },
          { label: "动态基线数量", value: this.countText(report.dynamic_measure_ref_number) },
          { label: "动态度量次数", value: this.countText(report.dmeasure_times) },
          { label: "控制策略指纹", value: this.shortHash(report.global_control_policy_sha256) }
        ]
      });
      sections.push({
        title: "TPCM 可信报告",
        items: [
          { label: "报告状态", value: this.trustStatusText(report.trust_status), state: report.trusted === true ? "ok" : report.trusted === false ? "bad" : "warn" },
          { label: "可信报告评分", value: this.reportEvalText(report.trust_report_eval) },
          { label: "失败计数", value: report.failure_count ?? "-" },
          { label: "失败项", value: this.failureText(report.trust_report_failures) }
        ]
      });
      sections.push({
        title: "TPCM 可信报告历史",
        items: history.length
          ? history.map((item, index) => ({
              label: `${index + 1}. ${this.formatTime(item.collected_at)}`,
              value: this.tpcmHistoryText(item)
            }))
          : [{ label: "历史记录", value: "暂无" }]
      });
    }
    if (remediation.summary) {
      sections.push({
        title: "修复建议",
        items: [
          { label: "类别", value: remediation.category || "-" },
          { label: "建议", value: remediation.summary },
          { label: "命令", value: this.listText(remediation.next_commands || []) }
        ]
      });
    }
    this.detailNode = {
      title: row.host || node.hostname || "计算节点",
      subtitle: "计算节点详情",
      sections
    };
  },
  closeNodeDetail() {
    this.detailNode = null;
  },
};
