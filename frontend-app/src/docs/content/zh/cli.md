# 命令行

所有命令都在项目根目录运行。用安装脚本安装时，请先激活虚拟环境（`source .venv/bin/activate`，Windows 为 `.venv\Scripts\activate`），或者把 `python` 换成 `.venv/bin/python`。

## 日常

| 命令 | 作用 |
|---|---|
| `./start.sh` / `.\start.ps1` / 双击 `TRUSTA.cmd` | 启动服务和后台任务 |
| `python -m scripts.setup` | 配置向导 |
| `python -m scripts.setup --status` | 显示功能表 |
| `python -m scripts.setup --probe` | 探测模型、银行、飞书的连通性后显示功能表 |
| `python -m scripts.pair` | 生成手机配对码 |
| `python -m scripts.pair --feishu` | 生成飞书绑定码 |
| `python -m scripts.pair --devices` | 列出已配对设备 |
| `python -m scripts.pair --revoke ID` | 吊销设备 |

## 在终端里对话

```bash
python -m scripts.chat_cli             # 连接正在运行的服务
python -m scripts.chat_cli --sandbox   # 进程内启动 + 临时数据库，不影响 data/
```

对话里可以用这些命令：

| 命令 | 作用 |
|---|---|
| `/y N` / `/n N` | 确认 / 拒绝第 N 张卡片 |
| `/sms N` | 为红色卡片 N 发送模拟短信验证码 |
| `/otp N CODE` | 用短信验证码确认红色卡片 N |
| `/totp N CODE` | 用认证器动态码确认红色卡片 N |
| `/stop` | 中断当前计划 |
| `/trace` | 查看上一轮的决策链 |
| `/events` | 列出计划任务 |
| `/advance ID` | 立即执行某个计划（仅演示模式） |
| `/quit` | 退出 |

## 验证与审计

| 命令 | 作用 |
|---|---|
| `python -m scripts.verify_handoff_evidence BUNDLE --trusted-registry REGISTRY` | 离线验证签名证据包 |
| `python -m scripts.verify_audit_record PROOF --trusted-public-key KEY` | 验证单条审计记录的 Merkle 包含证明 |
| `python -m scripts.manage_crypto_identity --agent NAME --rotate` | 轮换 Agent 密钥 |
| `python -m scripts.manage_crypto_identity --agent NAME --status SUSPENDED` | 暂停 Agent 身份 |
| `python -m scripts.manage_crypto_identity --revoke-key KEY_ID` | 吊销密钥 |

## 测试与评测

| 命令 | 作用 |
|---|---|
| `python -m pytest -q` | 全部自动化测试（约 1,400 项） |
| `python -m benchmarks.scenario_audit` | 六大场景 49 项闭环检查 |
| `python -m benchmarks.security_benchmark --banking-version 5` | 18 类攻击实测 |

## 后台 worker

启动脚本会自动运行它们，一般不需要手动启动：

| 命令 | 作用 |
|---|---|
| `python -m scripts.audit_worker --watch` | 生成签名 Merkle 检查点 |
| `python -m scripts.scheduled_worker --watch` | 执行到期的计划（生日下单、周期转账等） |
| `python -m scripts.feishu_worker` | 飞书长连接 |

## 打包

```bash
python -m scripts.build_portable --python-embed python-3.12.10-embed-amd64.zip --out dist
```

生成 Windows 便携版 `dist/TRUSTA-windows-x64.zip`。仓库里的 GitHub Actions 工作流 `Packages` 会自动完成下载嵌入式 Python、打包、在 Windows 上启动自检，并在推送 `v*` 标签时附到发布页。
