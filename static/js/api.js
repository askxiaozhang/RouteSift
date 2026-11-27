async function api(path, method = "GET", body) {
  const options = { method };
  if (method !== "GET") options.headers = { "X-Auditor-Request": "1" };
  if (body instanceof File) {
    options.headers["Content-Type"] = "application/octet-stream";
    options.body = body;
  } else if (body) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  if (!response.ok) {
    const result = await response.json().catch(() => ({}));
    const message = typeof result.detail === "string" ? result.detail : `请求失败（HTTP ${response.status}）`;
    throw new Error(message);
  }
  return response.status === 204 ? null : response.json();
}

export { api };
