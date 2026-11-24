// Thin JSON fetch wrapper. Every failure is surfaced to the page, never
// swallowed: the local API and the page share one origin (Vite proxies /api).

async function request(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  let body = null;
  try {
    body = await res.json();
  } catch (err) {
    throw new Error(`接口返回了非 JSON 内容（${res.status}）`);
  }
  if (!res.ok) {
    throw new Error(body?.error || `请求失败（${res.status}）`);
  }
  return body;
}

export const api = {
  get(path) {
    return request(path);
  },
  post(path, payload) {
    return request(path, { method: "POST", body: JSON.stringify(payload ?? {}) });
  },
};

// Poll a cheap endpoint until the local server answers. Used only for the
// status pill; the page still works if this never resolves.
export async function waitForApi(timeoutMs = 8000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    try {
      await api.get("/api/boards");
      return true;
    } catch (err) {
      await new Promise((r) => setTimeout(r, 600));
    }
  }
  return false;
}
