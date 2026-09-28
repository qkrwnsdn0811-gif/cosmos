$ErrorActionPreference = 'Stop'
$wsl = Join-Path $env:SystemRoot 'System32\wsl.exe'
& $wsl -d Ubuntu-22.04 -- bash -lc 'exec "$HOME/cosmos-gpu-news/current/deploy/gpu-news-worker/run-worker.sh"'
exit $LASTEXITCODE

