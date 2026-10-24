// Docs table of contents. Each slug has content/zh/<slug>.md and content/en/<slug>.md.
export const NAV = [
  { zh: "开始", en: "Get started", pages: [
    ["overview", "概览", "Overview"],
    ["quickstart", "快速开始", "Quick start"],
    ["install", "安装", "Installation"],
    ["setup", "首次配置", "First-run setup"],
  ] },
  { zh: "使用", en: "Using TRUSTA", pages: [
    ["chat-cards", "对话与结果卡片", "Conversations and cards"],
    ["authorization", "授权等级", "Authorisation tiers"],
    ["feishu", "飞书接入", "Feishu"],
    ["mobile", "手机访问", "Phone access"],
    ["integrations", "集成", "Integrations"],
  ] },
  { zh: "可信机制", en: "Trust", pages: [
    ["agent-communication", "多 Agent 签名通信", "Signed agent messaging"],
    ["evidence", "证据包离线验证", "Verifying evidence offline"],
    ["cross-institution", "跨机构协作", "Cross-institution collaboration"],
    ["benchmarks", "安全基准", "Security benchmarks"],
  ] },
  { zh: "参考", en: "Reference", pages: [
    ["configuration", "配置项", "Configuration"],
    ["cli", "命令行", "Command line"],
    ["api", "HTTP API", "HTTP API"],
    ["faq", "常见问题", "FAQ"],
    ["changelog", "更新日志", "Changelog"],
  ] },
];
export const PAGES = NAV.flatMap((g) => g.pages.map(([slug, zh, en]) => ({ slug, zh, en, group: g })));
