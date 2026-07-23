// Browser API helpers shared by frontend modules.

export function jsonHeaders(token = "") {
  const headers = { "Content-Type": "application/json" };
  if (token) headers["X-Admin-Token"] = token;
  return headers;
}

export async function requestJson(path, options = {}, token = "") {
  const response = await fetch(path, {
    ...options,
    credentials: "same-origin",
    headers: { ...jsonHeaders(token), ...(options.headers || {}) }
  });
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json")
    ? await response.json()
    : { detail: await response.text() };
  if (!response.ok) {
    const fallback = response.status >= 500 ? "服务器内部错误，请查看 API 日志。" : `HTTP ${response.status}`;
    const error = new Error(data.detail || data.error || fallback);
    error.status = response.status;
    error.data = data;
    throw error;
  }
  return data;
}
