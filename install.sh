#!/usr/bin/env bash
# TRUSTA one-line installer for macOS / Linux:
#   curl -fsSL https://raw.githubusercontent.com/piggyz666/Trusta/main/install.sh | bash
# Downloads TRUSTA into ~/TRUSTA (override with TRUSTA_HOME), creates a private virtualenv,
# installs the dependencies and runs the setup wizard. Nothing is installed system-wide.
set -euo pipefail

REPO="${TRUSTA_REPO:-https://github.com/piggyz666/Trusta}"
BRANCH="${TRUSTA_BRANCH:-main}"
DIR="${TRUSTA_HOME:-$HOME/TRUSTA}"

say()  { printf '\033[1m==>\033[0m %s\n' "$*"; }
fail() { printf '\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

# 1. Python 3.11+
PY=""
for c in python3.13 python3.12 python3.11 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  case "$(uname -s)" in
    Darwin) fail "需要 Python 3.11 或更高版本。安装：brew install python@3.12（或从 python.org 下载），然后重新运行本命令。" ;;
    *)      fail "需要 Python 3.11 或更高版本。Debian/Ubuntu：sudo apt install python3.12 python3.12-venv；其他发行版用自带包管理器安装后重新运行。" ;;
  esac
fi
say "使用 $($PY --version)"

# 2. Code: git if available, otherwise the source tarball
if [ -d "$DIR/.git" ] && command -v git >/dev/null 2>&1; then
  say "更新 $DIR"
  git -C "$DIR" pull --ff-only
elif [ -e "$DIR/backend" ]; then
  say "$DIR 已存在，保留现有文件"
elif command -v git >/dev/null 2>&1; then
  say "下载 TRUSTA 到 $DIR"
  git clone --depth 1 --branch "$BRANCH" "$REPO.git" "$DIR"
else
  say "下载 TRUSTA 到 $DIR（未检测到 git，改用压缩包）"
  mkdir -p "$DIR"
  curl -fsSL "${REPO/github.com/codeload.github.com}/tar.gz/refs/heads/$BRANCH" | tar -xz -C "$DIR" --strip-components 1
fi
cd "$DIR"

# 3. Private virtualenv + dependencies
if [ ! -x .venv/bin/python ]; then
  say "创建虚拟环境 .venv"
  "$PY" -m venv .venv || fail "创建虚拟环境失败。Debian/Ubuntu 需要先安装 python3-venv。"
fi
say "安装依赖"
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements.txt -r requirements-channels.txt
chmod +x start.sh

# 4. Setup wizard (read answers from the terminal even when this script arrives through a pipe)
if [ "${TRUSTA_NO_SETUP:-}" != "1" ] && [ ! -f .env ]; then
  say "配置向导：大模型 → 银行 → 访问方式 → 安全"
  if [ -r /dev/tty ]; then .venv/bin/python -m scripts.setup </dev/tty; else .venv/bin/python -m scripts.setup; fi
fi

printf '\n\033[1m安装完成。\033[0m 启动：\n\n  cd %s && ./start.sh\n\n然后用窗口里打印的 http://127.0.0.1:8000/#token=… 链接打开。\n' "$DIR"
