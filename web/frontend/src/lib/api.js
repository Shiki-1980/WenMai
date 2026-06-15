const BASE = "/api";

async function request(url, options = {}) {
  const res = await fetch(`${BASE}${url}`, {
    headers: { "Content-Type": "application/json", ...options.headers },
    ...options,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || res.statusText);
  }
  return res.json();
}

// Config
export const getConfig = () => request("/config");
export const updateConfig = (novel) =>
  request("/config", { method: "PUT", body: JSON.stringify({ novel }) });
export const updateLLMConfig = (config) =>
  request("/config/llm", { method: "PUT", body: JSON.stringify(config) });

// Novels
export const listNovels = () => request("/novels");
export const switchNovel = (name) =>
  request("/novels/switch", { method: "POST", body: JSON.stringify({ name }) });
export const renameNovel = (name, to) =>
  request("/novels/rename", { method: "POST", body: JSON.stringify({ name, to }) });

// Status
export const getStatus = (novel = "") =>
  request(`/status${novel ? `?novel=${encodeURIComponent(novel)}` : ""}`);

// Content
export const listChapters = (novel = "") =>
  request(`/chapters${novel ? `?novel=${encodeURIComponent(novel)}` : ""}`);
export const getChapter = (num, novel = "") =>
  request(`/chapters/${num}${novel ? `?novel=${encodeURIComponent(novel)}` : ""}`);
export const listEntities = (type = "", novel = "") => {
  const params = new URLSearchParams();
  if (type) params.set("type", type);
  if (novel) params.set("novel", novel);
  return request(`/entities?${params}`);
};
export const getEntity = (etype, name, novel = "") =>
  request(`/entities/${etype}/${encodeURIComponent(name)}${novel ? `?novel=${encodeURIComponent(novel)}` : ""}`);
export const listArcs = (novel = "") =>
  request(`/arcs${novel ? `?novel=${encodeURIComponent(novel)}` : ""}`);
export const getArc = (name, novel = "") =>
  request(`/arcs/${encodeURIComponent(name)}${novel ? `?novel=${encodeURIComponent(novel)}` : ""}`);
export const getWorld = (novel = "") =>
  request(`/world${novel ? `?novel=${encodeURIComponent(novel)}` : ""}`);
export const getMainPlot = (novel = "") =>
  request(`/main-plot${novel ? `?novel=${encodeURIComponent(novel)}` : ""}`);
export const getPlotPool = (novel = "") =>
  request(`/plot-pool${novel ? `?novel=${encodeURIComponent(novel)}` : ""}`);
export const getAuditSummary = (novel = "") =>
  request(`/audit${novel ? `?novel=${encodeURIComponent(novel)}` : ""}`);

// Health
export const getHealth = () => request("/health");

// SSE endpoint URLs (used directly by EventSource)
export const sseUrl = (endpoint) => `${BASE}${endpoint}`;
export const respondInput = (taskId, value) =>
  request(`/respond/${taskId}`, { method: "POST", body: JSON.stringify({ value }) });

// Post with SSE — returns fetch Response for reading the stream
export function postSSE(endpoint, body) {
  return fetch(`${BASE}${endpoint}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}
