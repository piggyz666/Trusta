# 快速开始

五分钟内在你自己的电脑上跑起 TRUSTA，并完成第一笔（虚拟的）转账。

> TRUSTA 默认连接本地虚拟银行沙箱，演示数据全部虚构，不会动到任何真实账户。

## 1. 安装

选一种方式即可，详见[安装](#/install)。

**Windows**：下载便携版压缩包，解压后双击 `TRUSTA.cmd`；或者在 PowerShell 里运行：

```powershell
irm https://raw.githubusercontent.com/piggyz666/Trusta/main/install.ps1 | iex
```

**macOS / Linux**：

```bash
curl -fsSL https://raw.githubusercontent.com/piggyz666/Trusta/main/install.sh | bash
```

## 2. 配置

安装完成后会自动打开配置向导，一共四步。第一次可以全部直接回车：

1. **大模型**：选 DeepSeek 并填 API Key；或者先选“不接入”，用规则引擎。
2. **银行**：本地沙箱银行（默认）。
3. **访问方式**：仅本机（默认，最安全）。
4. **安全与演示**：`competition` 安全档位 + 演示模式（短信验证码会直接显示在页面上）。

以后随时可以重新运行 `python -m scripts.setup` 修改。详见[首次配置](#/setup)。

## 3. 启动

```bash
./start.sh          # macOS / Linux
```

```powershell
.\start.ps1         # Windows，或者双击 TRUSTA.cmd
```

窗口里会打印一条 `http://127.0.0.1:8000/#token=…` 链接，用浏览器打开。链接里的 token 是本机访问凭证，不要发给别人。

## 4. 试几句话

- “我这个月花了多少钱”：只读查询，直接返回报告卡。
- “给张三转500元”：生成一张黄色确认卡，点“确认”才会执行。
- “我的卡丢了”：红色操作，需要验证码；演示模式下验证码显示在“模拟手机”里。

## 5. 看看它是怎么做到的

在网页“设置 → 外观”里打开**开发者模式**，每条回复下面会出现“查看决策过程”，可以看到 Agent 之间的每条签名消息，并当场重新验签。

## 接下来

- 用手机访问：[手机访问](#/mobile)
- 接入飞书：[飞书接入](#/feishu)
- 在终端里对话：`python -m scripts.chat_cli`
