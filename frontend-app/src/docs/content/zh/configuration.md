# 配置项

TRUSTA 的配置都是环境变量，保存在项目目录下的 `.env`（由[配置向导](#/setup)写入，已被 git 忽略）。也可以在启动前直接设置环境变量，环境变量优先。金额类配置的单位是**分**。

## 大模型

| 变量 | 默认 | 说明 |
|---|---|---|
| `DEEPSEEK_API_KEY` | — | 模型 API Key；为空时使用规则引擎 |
| `TRUSTA_CHAT_LLM_ENDPOINT` | `https://api.deepseek.com/chat/completions` | 兼容 OpenAI 接口的地址 |
| `TRUSTA_CHAT_LLM_MODEL` | `deepseek-flash` | 模型名 |
| `TRUSTA_CHAT_LLM_PROXY` | — | HTTPS 代理 |
| `TRUSTA_CHAT_LLM_TIMEOUT` | `30` | 单次调用超时（秒） |
| `TRUSTA_CHAT_LLM_CONCURRENCY` | `4` | 并发调用上限 |
| `TRUSTA_CHAT_LLM_DAILY_CALLS` | `300` | 每个用户每日调用上限；用完后当天改用规则引擎 |
| `TRUSTA_LLM_CONFIG` | — | 评测用的模型配置文件路径 |

## 银行

| 变量 | 默认 | 说明 |
|---|---|---|
| `TRUSTA_BANK_CONFIG` | — | 外部银行沙箱配置文件（`config/bank.*.local.toml`）；为空时使用本地沙箱银行 |
| `TRUSTA_BANKING_VERSION` | `5` | 银行动作集版本 |
| `TRUSTA_DISABLED_FEATURES` | — | 逗号分隔，关闭指定功能，例如 `wealth,trip` |

## 访问与渠道

| 变量 | 默认 | 说明 |
|---|---|---|
| `TRUSTA_ACCESS_MODE` | `local` | `local` 仅本机 / `lan` 局域网 / `tunnel` 隧道 |
| `TRUSTA_PUBLIC_HOST` | — | 隧道模式的公网域名（不带 `https://`） |
| `TRUSTA_LAN_HOST` | 自动检测 | 局域网模式下手动指定本机地址 |
| `TRUSTA_PORT` | `8000` | 飞书“回到 APP”链接里使用的端口（服务本身固定监听 8000） |
| `TRUSTA_API_TOKEN` | 自动生成 | 本机访问令牌；默认保存在 `data/api.token` |
| `TRUSTA_APP_URL` | 按访问方式推算 | 飞书里“回到 APP”链接使用的地址 |
| `FEISHU_APP_ID` / `FEISHU_APP_SECRET` | — | 飞书应用凭据 |
| `FEISHU_MODE` | `longconn` | `longconn` 长连接 / `webhook` |
| `FEISHU_VERIFICATION_TOKEN` / `FEISHU_ENCRYPT_KEY` | — | webhook 模式使用 |
| `FEISHU_USER_BINDINGS` | — | 运营方预置绑定，格式 `open_id:owner,open_id:owner` |

## 安全

| 变量 | 默认 | 说明 |
|---|---|---|
| `TRUSTA_SAFETY_PROFILE` | `competition` | `competition` 强验证 / `legacy` |
| `TRUSTA_DEMO_MODE` | `false`（向导默认 `true`） | 演示模式：模拟短信验证码回显、计划可快进 |
| `TRUSTA_HIGH_RISK_CENTS` | `500000` | 单笔 ≥ 此金额为红色（¥5,000） |
| `TRUSTA_DAILY_TRANSFER_CENTS` | `100000` | 当日累计超过此金额为红色（¥1,000） |
| `TRUSTA_ANOMALY_LARGE_CENTS` | `100000` | 大额消费异常提醒阈值 |
| `TRUSTA_ANOMALY_DUPLICATE_SECONDS` | `300` | 疑似重复扣费的时间窗口 |
| `TRUSTA_AUTH_FAILURE_LIMIT` | `5` | 连续验证失败多少次后安全锁定 |
| `TRUSTA_EXECUTION_FAILURE_LIMIT` | `3` | 连续执行失败多少次后安全锁定 |
| `TRUSTA_SECURITY_OPERATOR_TOKEN` | — | 银行人员令牌：解除转账保护、人工接管 |
| `TRUSTA_STRONG_AUTH_SECRET` | — | `legacy` 安全档位下红色操作使用的强验证口令 |

## 可信通信与审计

| 变量 | 默认 | 说明 |
|---|---|---|
| `TRUSTA_COMMUNICATION_VERSION` | `3` | 签名通信协议版本；为 3 时启动审计和计划任务 worker |
| `TRUSTA_CRYPTO_KEY_DIR` | `data/trusta.keys` | Agent 私钥存放目录（与数据库同名的 `.keys` 目录） |
| `TRUSTA_AGENT_TIMEOUT_SECONDS` | `20` | 单个 Agent 处理超时 |
| `TRUSTA_FINANCE_PROCESS_ENABLED` | `false` | 财务 Agent 以独立进程运行 |
| `TRUSTA_BLOCKCHAIN_ANCHOR_ENABLED` | `false` | 审计检查点上链锚定 |
| `TRUSTA_DID_ENABLED` | `true` | 跨机构协作的链上身份 |
| `TRUSTA_ZKP_ENABLED` | `false` | 零知识能力准入 |
| `TRUSTA_ZKP_ARTIFACT_DIR` / `TRUSTA_ZKP_MANIFEST_SHA256` | — | 零知识证明产物目录和清单哈希 |

## 数据

| 变量 | 默认 | 说明 |
|---|---|---|
| `TRUSTA_DB` | `data/trusta.sqlite3` | 数据库路径 |

`data/` 里保存数据库、根签名密钥（`signing.key`）、Agent 密钥和日志，`.env` 保存你的密钥配置。两者都不要提交或分享。
