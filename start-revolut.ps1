param([switch]$Transfers)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if ($Transfers) {
    python scripts/revolut_transfer_prepare.py
    if ($LASTEXITCODE -ne 0) { throw '银行 READ,PAY 授权或沙箱账户预检未完成，未启动付款界面。' }
    python scripts/revolut_server.py --config config/bank.revolut-transfer.local.toml
} else {
    python scripts/revolut_server.py
}
