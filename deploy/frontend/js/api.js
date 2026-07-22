// Browser API helpers shared by frontend modules.

export function jsonHeaders(token = "") {
  const headers = { "Content-Type": "application/json" };
  if (token) headers["X-Admin-Token"] = token;
  return headers;
}

export async function requestJson(path, options = {}, token = "") {
  const response = await fetch(path, {
    ...options,
    headers: { ...jsonHeaders(token), ...(options.headers || {}) }
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || data.error || `HTTP ${response.status}`);
  }
  return data;
}
