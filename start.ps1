$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$communicationProfile = python -c "from backend.config import Config; print(Config.local().communication_version)"
if ($LASTEXITCODE -ne 0) { throw 'TRUSTA configuration failed.' }
$auditWorkerProcess = $null
try {
    if ($communicationProfile.Trim() -eq '3') {
        $auditWorkerProcess = Start-Process -FilePath 'python' -ArgumentList @('-m', 'scripts.audit_worker', '--watch') -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $PSScriptRoot 'data/audit-worker.log') -RedirectStandardError (Join-Path $PSScriptRoot 'data/audit-worker-error.log')
    }
    python -m backend.server
} finally {
    if ($null -ne $auditWorkerProcess -and -not $auditWorkerProcess.HasExited) {
        Stop-Process -Id $auditWorkerProcess.Id
    }
}
