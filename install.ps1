# TRUSTA one-line installer for Windows (PowerShell):
#   irm https://raw.githubusercontent.com/piggyz666/Trusta/main/install.ps1 | iex
# Downloads TRUSTA into %USERPROFILE%\TRUSTA (override with $env:TRUSTA_HOME), creates a private
# virtualenv, installs the dependencies and runs the setup wizard. Nothing is installed system-wide
# except Python itself, and only if it is missing and you agree.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = 'utf-8'

$Repo   = if ($env:TRUSTA_REPO)   { $env:TRUSTA_REPO }   else { 'https://github.com/piggyz666/Trusta' }
$Branch = if ($env:TRUSTA_BRANCH) { $env:TRUSTA_BRANCH } else { 'main' }
$Dir    = if ($env:TRUSTA_HOME)   { $env:TRUSTA_HOME }   else { Join-Path $HOME 'TRUSTA' }

function Say($m)  { Write-Host "==> $m" -ForegroundColor Cyan }
function Fail($m) { Write-Host "x $m" -ForegroundColor Red; throw $m }

function Find-Python {
    $candidates = @(@('py', '-3.13'), @('py', '-3.12'), @('py', '-3.11'), @('python'), @('python3'))
    foreach ($c in $candidates) {
        $exe = $c[0]; $args_ = @($c | Select-Object -Skip 1)
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        try {
            & $exe @args_ -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>$null
            if ($LASTEXITCODE -eq 0) { return , $c }
        } catch { }
    }
    return $null
}

# 1. Python 3.11+
$py = Find-Python
if (-not $py) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        $ans = Read-Host '没有找到 Python 3.11+。现在用 winget 安装 Python 3.12（仅当前用户）？(y/n)'
        if ($ans -eq 'y') {
            winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
            $env:Path = [Environment]::GetEnvironmentVariable('Path', 'User') + ';' + [Environment]::GetEnvironmentVariable('Path', 'Machine')
            $py = Find-Python
        }
    }
    if (-not $py) { Fail '需要 Python 3.11 或更高版本：从 https://www.python.org/downloads/ 安装（勾选 Add python.exe to PATH），然后重新运行本命令。' }
}
$pyExe = $py[0]; $pyArgs = @($py | Select-Object -Skip 1)
Say ("使用 " + (& $pyExe @pyArgs --version))

# 2. Code: git if available, otherwise the source zip
if ((Test-Path (Join-Path $Dir '.git')) -and (Get-Command git -ErrorAction SilentlyContinue)) {
    Say "更新 $Dir"
    git -C $Dir pull --ff-only
} elseif (Test-Path (Join-Path $Dir 'backend')) {
    Say "$Dir 已存在，保留现有文件"
} elseif (Get-Command git -ErrorAction SilentlyContinue) {
    Say "下载 TRUSTA 到 $Dir"
    git clone --depth 1 --branch $Branch "$Repo.git" $Dir
} else {
    Say "下载 TRUSTA 到 $Dir（未检测到 git，改用压缩包）"
    $zip = Join-Path $env:TEMP 'trusta-src.zip'
    $src = ($Repo -replace 'github.com', 'codeload.github.com') + "/zip/refs/heads/$Branch"
    Invoke-WebRequest -UseBasicParsing -Uri $src -OutFile $zip
    $tmp = Join-Path $env:TEMP ('trusta-src-' + [guid]::NewGuid())
    Expand-Archive -Path $zip -DestinationPath $tmp
    $inner = Get-ChildItem -Path $tmp -Directory | Select-Object -First 1
    New-Item -ItemType Directory -Force -Path $Dir | Out-Null
    Copy-Item -Path (Join-Path $inner.FullName '*') -Destination $Dir -Recurse -Force
    Remove-Item -Recurse -Force $tmp, $zip
}
Set-Location -LiteralPath $Dir

# 3. Private virtualenv + dependencies
$venvPy = Join-Path $Dir '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPy)) {
    Say '创建虚拟环境 .venv'
    & $pyExe @pyArgs -m venv .venv
    if ($LASTEXITCODE -ne 0) { Fail '创建虚拟环境失败。' }
}
Say '安装依赖'
& $venvPy -m pip install --quiet --upgrade pip
& $venvPy -m pip install --quiet -r requirements.txt -r requirements-channels.txt
if ($LASTEXITCODE -ne 0) { Fail '安装依赖失败，请检查网络后重新运行。' }

# 4. Setup wizard
if ($env:TRUSTA_NO_SETUP -ne '1' -and -not (Test-Path (Join-Path $Dir '.env'))) {
    Say '配置向导：大模型 → 银行 → 访问方式 → 安全'
    & $venvPy -m scripts.setup
}

Write-Host ''
Write-Host '安装完成。启动：' -ForegroundColor Green
Write-Host ''
Write-Host "  双击 $Dir\TRUSTA.cmd"
Write-Host "  或：cd $Dir; .\start.ps1"
Write-Host ''
Write-Host '然后用窗口里打印的 http://127.0.0.1:8000/#token=… 链接打开。'
