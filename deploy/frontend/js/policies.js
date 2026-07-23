// Shared policy metadata and form defaults.

export const POLICY_TYPES = [
  { key: "measured_boot", label: "可信启动", creatable: true },
  { key: "ima_runtime", label: "IMA 运行时策略", creatable: true }
];

export const DYNAMIC_MEASUREMENT_OBJECTS = [
  { key: "kernel_section", label: "内核代码段" },
  { key: "syscall_table", label: "系统调用表" },
  { key: "idt_table", label: "中断描述符表" }
];

export const DEFAULT_IMA_POLICY = `dont_measure fsmagic=0x9fa0
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

export const DEFAULT_IMA_EXCLUDES = `^/var/lib/docker/containers/[0-9a-f]+/\\.tmp-config\\.v2\\.json.*$
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

export const emptyPolicyForm = (policyType = "measured_boot") => ({
  name: "",
  description: "",
  targetNodeIds: [],
  trustedRootType: "tpm",
  pcrs: [0, 1, 2, 3, 4, 5, 6, 7],
  secureBootRequired: true,
  tpcmMinimumBootReferences: 1,
  tpcmRequireCleanTrustReport: true,
  tpcmWriteEnabled: false,
  tpcmAuthRef: "bmeasure-uid",
  nodeImaPolicy: DEFAULT_IMA_POLICY,
  excludesText: DEFAULT_IMA_EXCLUDES,
  rebootAfterApply: false,
  policy_type: policyType
});

export const emptyDynamicForm = () => ({
  nodeEnabled: true,
  objects: Object.fromEntries(
    DYNAMIC_MEASUREMENT_OBJECTS.map((item) => [
      item.key,
      { enabled: true, intervalMilli: 60000 }
    ])
  )
});

export const VIEW_TITLES = {
  dashboard: "首页",
  control_nodes: "控制节点",
  compute_nodes: "计算节点",
  policies: "策略管理",
  environment_dynamic_policy: "环境动态度量策略",
  global_policy: "全局策略控制",
  alerts: "告警中心",
  tasks: "任务中心",
  audit: "审计日志"
};
