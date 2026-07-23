import { commonFormatters } from "./formatters/common.js";
import { policyFormatters } from "./formatters/policies.js";
import { trustFormatters } from "./formatters/trust.js";
import { statusFormatters } from "./formatters/status.js";
import { openStackFormatters } from "./formatters/openstack.js";
import { auditFormatters } from "./formatters/audit.js?v=20260723-audit-task-ux";
import { taskFormatters } from "./formatters/tasks.js?v=20260723-audit-task-ux";

export const displayMethods = {
  ...commonFormatters,
  ...trustFormatters,
  ...statusFormatters,
  ...openStackFormatters,
  ...auditFormatters,
  ...taskFormatters,
  ...policyFormatters
};
