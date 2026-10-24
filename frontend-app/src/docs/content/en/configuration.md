# Configuration

TRUSTA is configured with environment variables kept in `.env` in the project folder (written by the [setup wizard](#/setup) and ignored by git). You can also set them in the environment before starting; environment values win. Money amounts are in **cents (fen)**.

## Model

| Variable | Default | Meaning |
|---|---|---|
| `DEEPSEEK_API_KEY` | — | Model API key; empty means the rule engine is used |
| `TRUSTA_CHAT_LLM_ENDPOINT` | `https://api.deepseek.com/chat/completions` | OpenAI-compatible endpoint |
| `TRUSTA_CHAT_LLM_MODEL` | `deepseek-flash` | Model name |
| `TRUSTA_CHAT_LLM_PROXY` | — | HTTPS proxy |
| `TRUSTA_CHAT_LLM_TIMEOUT` | `30` | Per-call timeout (seconds) |
| `TRUSTA_CHAT_LLM_CONCURRENCY` | `4` | Maximum concurrent calls |
| `TRUSTA_CHAT_LLM_DAILY_CALLS` | `300` | Daily calls per user; once used up, the rule engine takes over for the day |
| `TRUSTA_LLM_CONFIG` | — | Model configuration file used by evaluations |

## Bank

| Variable | Default | Meaning |
|---|---|---|
| `TRUSTA_BANK_CONFIG` | — | External bank sandbox config (`config/bank.*.local.toml`); empty means the local sandbox bank |
| `TRUSTA_BANKING_VERSION` | `5` | Bank action set version |
| `TRUSTA_DISABLED_FEATURES` | — | Comma-separated features to switch off, e.g. `wealth,trip` |

## Access and channels

| Variable | Default | Meaning |
|---|---|---|
| `TRUSTA_ACCESS_MODE` | `local` | `local` this computer only / `lan` local network / `tunnel` |
| `TRUSTA_PUBLIC_HOST` | — | Public hostname in tunnel mode (no `https://`) |
| `TRUSTA_LAN_HOST` | auto-detected | This computer's address in local-network mode |
| `TRUSTA_PORT` | `8000` | Port used in Feishu “back to the app” links (the server itself always listens on 8000) |
| `TRUSTA_API_TOKEN` | generated | Local access token; saved in `data/api.token` by default |
| `TRUSTA_APP_URL` | derived from access mode | Address used by “back to the app” links in Feishu |
| `FEISHU_APP_ID` / `FEISHU_APP_SECRET` | — | Feishu app credentials |
| `FEISHU_MODE` | `longconn` | `longconn` persistent connection / `webhook` |
| `FEISHU_VERIFICATION_TOKEN` / `FEISHU_ENCRYPT_KEY` | — | For webhook mode |
| `FEISHU_USER_BINDINGS` | — | Operator-preset bindings, `open_id:owner,open_id:owner` |

## Safety

| Variable | Default | Meaning |
|---|---|---|
| `TRUSTA_SAFETY_PROFILE` | `competition` | `competition` strong verification / `legacy` |
| `TRUSTA_DEMO_MODE` | `false` (`true` in the wizard) | Demo mode: simulated SMS codes shown, plans can be fast-forwarded |
| `TRUSTA_HIGH_RISK_CENTS` | `500000` | A single amount at or above this is red (¥5,000) |
| `TRUSTA_DAILY_TRANSFER_CENTS` | `100000` | Daily total above this is red (¥1,000) |
| `TRUSTA_ANOMALY_LARGE_CENTS` | `100000` | Threshold for flagging large purchases |
| `TRUSTA_ANOMALY_DUPLICATE_SECONDS` | `300` | Window for flagging possible duplicate charges |
| `TRUSTA_AUTH_FAILURE_LIMIT` | `5` | Failed verifications in a row before a safety lock |
| `TRUSTA_EXECUTION_FAILURE_LIMIT` | `3` | Failed executions in a row before a safety lock |
| `TRUSTA_SECURITY_OPERATOR_TOKEN` | — | Bank staff token: lifting transfer protection, human hand-off |
| `TRUSTA_STRONG_AUTH_SECRET` | — | Strong-auth passphrase for red operations under the `legacy` profile |

## Signed messaging and audit

| Variable | Default | Meaning |
|---|---|---|
| `TRUSTA_COMMUNICATION_VERSION` | `3` | Signed messaging protocol version; at 3 the audit and scheduled-plan workers start |
| `TRUSTA_CRYPTO_KEY_DIR` | `data/trusta.keys` | Agent private key folder (a `.keys` folder named after the database) |
| `TRUSTA_AGENT_TIMEOUT_SECONDS` | `20` | Per-agent timeout |
| `TRUSTA_FINANCE_PROCESS_ENABLED` | `false` | Run the Finance agent as a separate process |
| `TRUSTA_BLOCKCHAIN_ANCHOR_ENABLED` | `false` | Anchor audit checkpoints on chain |
| `TRUSTA_DID_ENABLED` | `true` | On-chain identities for cross-institution work |
| `TRUSTA_ZKP_ENABLED` | `false` | Zero-knowledge capability admission |
| `TRUSTA_ZKP_ARTIFACT_DIR` / `TRUSTA_ZKP_MANIFEST_SHA256` | — | ZK artefact folder and manifest hash |

## Data

| Variable | Default | Meaning |
|---|---|---|
| `TRUSTA_DB` | `data/trusta.sqlite3` | Database path |

`data/` holds the database, the root signing key (`signing.key`), agent keys and logs; `.env` holds your secrets. Never commit or share either.
