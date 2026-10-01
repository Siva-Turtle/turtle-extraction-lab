# Starts backend (:8002) + frontend (:5175) together.
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"

Start-Job -Name "lab-api" -WorkingDirectory $backend -ScriptBlock { uv run uvicorn app.main:app --reload --port 8002 } | Out-Null
Start-Job -Name "lab-web" -WorkingDirectory $frontend -ScriptBlock { npm run dev } | Out-Null
Write-Output "lab-api + lab-web started. Open http://localhost:5175"
Get-Job -Name "lab-api","lab-web" | Receive-Job -Wait
