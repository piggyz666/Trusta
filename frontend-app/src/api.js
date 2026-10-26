// Thin client for the local TRUSTA server. Credentials:
//  - master token: from the link the server prints (#token=…), kept for this browser tab only;
//  - device token: a phone that paired (#pair=CODE) keeps its own revocable token.
const MASTER = "trusta-token";
const DEVICE = "trusta-device";

function safeGet(store, key) {
  try { return store.getItem(key) || ""; } catch { return ""; }
}
function safeSet(store, key, value) {
  try { value ? store.setItem(key, value) : store.removeItem(key); } catch { /* private mode */ }
}

export function bootstrapAuth() {
  const params = new URLSearchParams(location.hash.slice(1));
  const token = params.get("token");
  const pair = params.get("pair");
  if (token) {
    safeSet(sessionStorage, MASTER, token);
    history.replaceState(null, "", location.pathname);
  }
  if (pair) history.replaceState(null, "", location.pathname);
  return { pairCode: pair && /^\d{6}$/.test(pair) ? pair : null };
}

export const auth = {
  token: () => safeGet(sessionStorage, MASTER) || safeGet(localStorage, DEVICE),
  isDevice: () => !safeGet(sessionStorage, MASTER) && !!safeGet(localStorage, DEVICE),
  setDevice: (token) => safeSet(localStorage, DEVICE, token),
  clear: () => { safeSet(sessionStorage, MASTER, ""); safeSet(localStorage, DEVICE, ""); },
};

export class ApiError extends Error {
  constructor(status, code, message) {
    super(message || code);
    this.status = status;
    this.code = code;
  }
}

async function request(path, body, { method } = {}) {
  const headers = { "Content-Type": "application/json" };
  const token = auth.token();
  if (token) headers.Authorization = "Bearer " + token;
  const response = await fetch(path, {
    method: method || (body === undefined ? "GET" : "POST"),
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data = null;
  try { data = await response.json(); } catch { data = null; }
  if (!response.ok) throw new ApiError(response.status, data?.error || "HTTP_" + response.status, data?.message);
  return data;
}

export const api = {
  get: (path) => request(path),
  post: (path, body = {}) => request(path, body),

  sessions: () => request("/api/chat/sessions"),
  createSession: () => request("/api/chat/sessions", { channel: "app" }),
  session: (id) => request(`/api/chat/sessions/${id}`),
  rename: (id, title) => request(`/api/chat/sessions/${id}/rename`, { title }),
  remove: (id) => request(`/api/chat/sessions/${id}/delete`, {}),
  decide: (id, card, approve, verification) =>
    request(`/api/chat/sessions/${id}/actions/${card.action_id}/decision`,
      { action_hash: card.action_hash, approve, ...(verification ? { verification } : {}) }),
  sms: (id, actionId) => request(`/api/chat/sessions/${id}/actions/${actionId}/sms`, {}),
  decideRule: (id, card, approve) =>
    request(`/api/chat/sessions/${id}/automations/${card.bundle_id}/decision`, { bundle_hash: card.bundle_hash, approve }),
  interrupt: (id, revision) => request(`/api/chat/sessions/${id}/interrupt`, { revision }),
  assessment: (id, answers) => request(`/api/chat/sessions/${id}/risk-assessment`, { answers }),
  protect: (id, hours = 24) => request(`/api/chat/sessions/${id}/protection`, { hours }),
  trace: (id, messageId) => request(`/api/chat/sessions/${id}/trace/${messageId}`),
  demoAdvance: (id, eventId) => request(`/api/chat/sessions/${id}/demo/advance`, { event_id: eventId }),

  capabilities: () => request("/api/chat/capabilities"),
  toggle: (feature, enabled) => request("/api/chat/capabilities/toggle", { feature, enabled }),
  probe: (targets) => request("/api/chat/capabilities/probe", targets ? { targets } : {}),
  overview: () => request("/api/chat/overview"),
  bills: () => request("/api/chat/bills"),
  markRecurring: (key, state) => request("/api/chat/bills/mark", { key, state }),
  removeUserSub: (id) => request("/api/chat/bills/remove", { id }),
  wealth: () => request("/api/chat/wealth"),
  market: (refresh) => request("/api/chat/market" + (refresh ? "?refresh=1" : "")),
  activity: (kind) => request("/api/chat/activity" + (kind ? `?kind=${kind}` : "")),
  settings: () => request("/api/chat/settings"),
  updateLlm: (body) => request("/api/chat/settings/llm", body),
  updateAccess: (body) => request("/api/chat/settings/access", body),
  access: () => request("/api/access"),
  startPairing: (purpose) => request("/api/pairing/start", { purpose }),
  claimPairing: (code, name) => request("/api/pairing/claim", { code, name }),
  revokeDevice: (id) => request(`/api/pairing/devices/${id}/revoke`, {}),
  unbindFeishu: () => request("/api/channels/feishu/unbind", {}),
  security: () => request("/api/security/status"),
  enrollTotp: () => request("/api/security/totp/enroll", {}),
  confirmTotp: (code) => request("/api/security/totp/confirm", { code }),
  events: () => request("/api/events"),
  protectAccount: (hours = 24) => request("/api/chat/protection", { hours }),
  cancelRule: (id) => request(`/api/chat/automations/${id}/cancel`, {}),
  llmUsage: () => request("/api/chat/llm-usage"),

  // Sends a message as Server-Sent Events so the UI can show the pipeline live (guard → intent → tools → cards).
  async send(id, body, onEvent) {
    const headers = { "Content-Type": "application/json", Accept: "text/event-stream" };
    const token = auth.token();
    if (token) headers.Authorization = "Bearer " + token;
    const response = await fetch(`/api/chat/sessions/${id}/messages`, { method: "POST", headers, body: JSON.stringify(body) });
    if (!response.ok || !(response.headers.get("Content-Type") || "").includes("event-stream")) {
      let data = null;
      try { data = await response.json(); } catch { data = null; }
      if (!response.ok) throw new ApiError(response.status, data?.error || "HTTP_" + response.status, data?.message);
      return data;
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let final = null;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let index;
      while ((index = buffer.indexOf("\n\n")) >= 0) {
        const block = buffer.slice(0, index);
        buffer = buffer.slice(index + 2);
        const name = (block.match(/^event: (.*)$/m) || [])[1];
        const raw = (block.match(/^data: (.*)$/m) || [])[1];
        if (!name) continue;
        let data = null;
        try { data = raw ? JSON.parse(raw) : null; } catch { data = raw; }
        onEvent?.(name, data);
        if (name === "turn.completed") final = data;
        if (name === "turn.failed") throw new ApiError(400, data?.code || "TURN_FAILED");
      }
    }
    return final;
  },
};

export const ERRORS = {
  UNAUTHENTICATED: "这台设备还没有连接到 TRUSTA。请用电脑上显示的链接打开，或在手机上输入配对码。",
  CHAT_STALE: "对话已在别处更新，已为你刷新。",
  CHAT_BUSY: "上一句还在处理中，请稍等。",
  CHAT_CLOSED: "这段对话已结束，请开始新对话。",
  CHAT_RATE_LIMITED: "发送太频繁了，请稍后再试。",
  PAIRING_INVALID: "配对码无效或已过期，请在电脑上重新生成。",
  PAIRING_RATE_LIMITED: "尝试次数过多，请 10 分钟后再试。",
  SETTINGS_LOCAL_ONLY: "这项设置只能在运行 TRUSTA 的电脑上修改。",
  FEATURE_LOCKED: "这是安全底线功能，不能关闭。",
  ACTION_NOT_PENDING: "这项操作已经处理过或已失效。",
  AUDIT_CHECKPOINT_PENDING: "回执已生成，还在等待写入签名检查点。请确认后台审计进程在运行，稍后再打开。",
  ACCOUNT_PROTECTION_ACTIVE: "账户处于转账保护期，对外转账已暂停。",
};

export function explain(error) {
  if (!(error instanceof ApiError)) return "网络连接中断，请确认 TRUSTA 仍在运行。";
  return ERRORS[error.code] || error.message || error.code;
}
