export const commonFormatters = {
  timestampFromSeconds(value) {
    if (!value) return "";
    const seconds = Number(value);
    if (!Number.isFinite(seconds) || seconds <= 0) return "";
    return new Date(seconds * 1000).toISOString();
  },
  formatTime(value) {
    if (!value) return "-";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "-";
    return date.toLocaleString("zh-CN", { hour12: false });
  },
  prettyJson(value) {
    return JSON.stringify(value || {}, null, 2);
  },
  listText(items) {
    return (items || []).length ? items.join("\n") : "-";
  }
};
