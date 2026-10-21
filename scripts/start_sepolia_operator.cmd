@echo off
cd /d "%~dp0.."
python -m scripts.sepolia_metamask_gateway operator --kind deploy --db "data/sepolia-public-acceptance/business/business.sqlite3" --reuse-rpc-session --open-page
pause
