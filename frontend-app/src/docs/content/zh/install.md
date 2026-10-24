# 安装

TRUSTA 有四种安装方式，效果一样，按你的习惯选。所有方式都只在一个文件夹里安装，不会改动系统环境。

| 方式 | 适合 | 需要 |
|---|---|---|
| 便携版安装包 | 只想用，不想装 Python（Windows） | 无 |
| 一行命令 | 大多数人 | Python 3.11+ |
| pip | 习惯用 Python 工具（即将推出） | Python 3.11+ |
| 源码 | 想改代码、跑测试、接自己的银行和模型 | Python 3.11+、git |

## 便携版（Windows）

1. 下载 `TRUSTA-windows-x64.zip`（[发布页](https://github.com/piggyz666/Trusta/releases)）。
2. 解压到任意文件夹，例如 `D:\TRUSTA`。
3. 双击 `TRUSTA.cmd`。首次运行会打开[配置向导](#/setup)，之后直接启动。

压缩包里自带 Python 运行环境（在 `runtime\` 里）和全部依赖，不需要另外安装任何东西。数据保存在同一文件夹的 `data\` 里，删除文件夹即完全卸载。

macOS 和 Linux 的安装包即将推出，目前请用一行命令安装。

## 一行命令

**Windows（PowerShell）**

```powershell
irm https://raw.githubusercontent.com/piggyz666/Trusta/main/install.ps1 | iex
```

**macOS / Linux**

```bash
curl -fsSL https://raw.githubusercontent.com/piggyz666/Trusta/main/install.sh | bash
```

安装脚本会依次：

1. 检查 Python 3.11+。Windows 上没有时，会询问是否用 `winget` 为当前用户安装 Python 3.12；macOS / Linux 会提示安装方法后退出。
2. 把代码下载到 `~/TRUSTA`（Windows 为 `%USERPROFILE%\TRUSTA`）。有 git 时用 `git clone`，没有时下载源码压缩包。
3. 在该目录创建独立的虚拟环境 `.venv`，安装依赖。
4. 打开配置向导。

可以用环境变量调整：

| 变量 | 作用 | 默认 |
|---|---|---|
| `TRUSTA_HOME` | 安装目录 | `~/TRUSTA` |
| `TRUSTA_BRANCH` | 下载哪个分支 | `main` |
| `TRUSTA_NO_SETUP` | 设为 `1` 时跳过配置向导 | — |

再次运行同一条命令会更新代码（git 安装时执行 `git pull`）并保留你的配置和数据。

## pip（即将推出）

发布到 PyPI 后：

```bash
pip install trusta
trusta setup && trusta start
```

## 从源码运行

```bash
git clone https://github.com/piggyz666/Trusta.git && cd Trusta
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt -r requirements-channels.txt
python -m scripts.setup
./start.sh                           # Windows: .\start.ps1
```

- `requirements.txt` 是运行必需的依赖。
- `requirements-channels.txt` 是可选的：飞书长连接和终端二维码。
- `requirements-llm.txt`、`requirements-phase6.txt`、`requirements-evaluation.txt` 用于评测和外部审计集成，日常使用不需要。

修改网页前端后需要重新构建（需要 Node.js）：

```bash
cd frontend-app
npm install
npm run build
```

构建结果在 `frontend/dist`，由后端直接提供。

## 启动和停止

| 系统 | 启动 |
|---|---|
| Windows | 双击 `TRUSTA.cmd`，或在 PowerShell 里 `.\start.ps1` |
| macOS / Linux | `./start.sh` |

启动脚本会同时运行：API 服务、审计检查点 worker、计划任务 worker（生日下单、周期转账等）；配置了飞书长连接时还会启动飞书 worker。后台 worker 的日志写在 `data/*.log`。关闭窗口或按 `Ctrl+C` 会一起停止。

启动脚本按以下顺序选择 Python：环境变量 `TRUSTA_PYTHON` → 便携版自带的 `runtime/python` → 安装脚本创建的 `.venv` → 系统里的 `python`。

## 卸载

TRUSTA 只写入它自己的文件夹。删除整个文件夹即可；`data/` 里是数据库和密钥，`.env` 里是你的配置，如有需要先备份。
