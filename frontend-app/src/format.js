export const yuan = (minor, { sign = false } = {}) => {
  if (minor === null || minor === undefined) return "—";
  const value = Math.abs(minor) / 100;
  const text = value.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const prefix = sign ? (minor < 0 ? "−" : "+") : minor < 0 ? "−" : "";
  return `${prefix}¥${text}`;
};

export const shortYuan = (minor) => {
  const value = (minor || 0) / 100;
  if (value >= 10000) return `¥${(value / 10000).toFixed(value >= 100000 ? 0 : 1)}万`;
  return `¥${Math.round(value).toLocaleString("zh-CN")}`;
};

const pad = (n) => String(n).padStart(2, "0");

export const dateTime = (seconds) => {
  if (!seconds) return "—";
  const d = new Date(seconds * 1000);
  return `${d.getMonth() + 1}月${d.getDate()}日 ${pad(d.getHours())}:${pad(d.getMinutes())}`;
};

export const dateOnly = (seconds) => {
  if (!seconds) return "—";
  const d = new Date(seconds * 1000);
  return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`;
};

export const clock = (seconds) => {
  const d = new Date(seconds * 1000);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
};

export function dayBucket(seconds) {
  const now = new Date();
  const d = new Date(seconds * 1000);
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() / 1000;
  if (seconds >= start) return "今天";
  if (seconds >= start - 86400) return "昨天";
  if (seconds >= start - 7 * 86400) return "最近 7 天";
  return `${d.getFullYear()}年${d.getMonth() + 1}月`;
}

// chats expire after 24 h, so the sidebar only needs: today, yesterday, everything older
export function chatBucket(seconds) {
  const now = new Date();
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() / 1000;
  if (seconds >= start) return "今天";
  if (seconds >= start - 86400) return "昨天";
  return "更早";
}

export const TIER = {
  GREEN: { label: "直接执行", tone: "green", past: "绿色 · 只读" },
  YELLOW: { label: "需你确认", tone: "amber", past: "黄色 · 本人确认" },
  RED: { label: "需验证码", tone: "red", past: "红色 · 验证码" },
};

export const uid = () => Math.random().toString(36).slice(2, 10) + Date.now().toString(36);

// bank adapter → a name a customer understands
export const bankName = (bank) => {
  if (!bank?.provider) return "未配置";
  if (bank.provider === "virtual" || bank.environment === "local") return "本地演示银行";
  if (bank.environment === "sandbox") return `${bank.provider}（沙箱）`;
  return bank.provider;
};
