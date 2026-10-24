# One command: API server + audit checkpoint worker + scheduled-plan worker (birthday orders, recurring transfers).
# Workers run hidden and are stopped when the server exits. Logs go to data/*.log.
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = 'utf-8'
# Python: the bundled runtime (portable package) > the installer's virtualenv > python on PATH
$py = if ($env:TRUSTA_PYTHON) { $env:TRUSTA_PYTHON }
      elseif (Test-Path (Join-Path $PSScriptRoot 'runtime/python/python.exe')) { Join-Path $PSScriptRoot 'runtime/python/python.exe' }
      elseif (Test-Path (Join-Path $PSScriptRoot '.venv/Scripts/python.exe')) { Join-Path $PSScriptRoot '.venv/Scripts/python.exe' }
      else { 'python' }
New-Item -ItemType Directory -Force -Path (Join-Path $PSScriptRoot 'data') | Out-Null
$communicationProfile = & $py -c "from backend.config import Config; print(Config.local().communication_version)"
if ($LASTEXITCODE -ne 0) { throw 'TRUSTA configuration failed.' }
$workers = @()
function Start-Worker($name, $module) {
    return Start-Process -FilePath $py -ArgumentList @('-m', $module, '--watch') -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $PSScriptRoot "data/$name.log") -RedirectStandardError (Join-Path $PSScriptRoot "data/$name-error.log")
}
try {
    if ($communicationProfile.Trim() -eq '3') {
        $workers += Start-Worker 'audit-worker' 'scripts.audit_worker'
        $workers += Start-Worker 'scheduled-worker' 'scripts.scheduled_worker'
    }
    # Feishu long connection (no public URL needed) when FEISHU_APP_ID / FEISHU_APP_SECRET are configured.
    $feishuLongConn = & $py -c "from backend.config import Config; Config.local(); from backend.channels.feishu import FeishuConfig; c=FeishuConfig.from_env(); print('1' if c.enabled and c.mode=='longconn' else '0')"
    if ($feishuLongConn.Trim() -eq '1') {
        $workers += Start-Process -FilePath $py -ArgumentList @('-m', 'scripts.feishu_worker') -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $PSScriptRoot 'data/feishu-worker.log') -RedirectStandardError (Join-Path $PSScriptRoot 'data/feishu-worker-error.log')
        Write-Host '飞书长连接已启动（日志：data/feishu-worker.log）。'
    }
    Write-Host '另开一个 PowerShell 窗口运行：python -m scripts.chat_cli   即可在终端里和助手对话。'
    & $py -m backend.server
} finally {
    foreach ($worker in $workers) {
        if ($null -ne $worker -and -not $worker.HasExited) { Stop-Process -Id $worker.Id }
    }
}
