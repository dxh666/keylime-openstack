import { commonFormatters } from "./formatters/common.js";
import { policyFormatters } from "./formatters/policies.js?v=20260723-trusted-boot";
import { trustFormatters } from "./formatters/trust.js?v=20260723-trusted-boot";
import { statusFormatters } from "./formatters/status.js";
import { openStackFormatters } from "./formatters/openstack.js";
import { auditFormatters } from "./formatters/audit.js?v=20260723-list-pages";
import { taskFormatters } from "./formatters/tasks.js?v=20260723-trusted-boot";

export const displayMethods = {
  ...commonFormatters,
  ...trustFormatters,
  ...statusFormatters,
  ...openStackFormatters,
  ...auditFormatters,
  ...taskFormatters,
  ...policyFormatters
};
