#Requires -Version 7.0
<#
.SYNOPSIS
  Start the Turtle Extraction Lab backend (port 8002) and frontend (port 5175) together.

.DESCRIPTION
  Loads backend/.env if present, verifies PostgreSQL is reachable on
  localhost:5432, refuses to start if port 8002 or 5175 is already held
  (naming the owning process), then starts:
    backend : uv run uvicorn app.main:app --reload --port 8002  (cwd backend/)
    frontend: npm run dev                                      (cwd frontend/)
  Child output is prefixed [api] / [web]. Once port 5175 answers,
  http://localhost:5175 is opened in the default browser. Ctrl+C stops both.
  Ports are offset from the CRM (8000/5173, test stack 8001/5174) so all three
  run side by side.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$RepoRoot    = Split-Path -Parent $PSScriptRoot
$BackendDir  = Join-Path $RepoRoot 'backend'
$FrontendDir = Join-Path $RepoRoot 'frontend'
$DotEnvPath  = Join-Path $BackendDir '.env'
$ApiPort     = 8002
$WebPort     = 5175

function Import-DotEnvFile {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return }
    Write-Host "Loading env from $Path"
    foreach ($line in (Get-Content -LiteralPath $Path)) {
        $trimmed = $line.Trim()
        if (($trimmed -eq '') -or $trimmed.StartsWith('#')) { continue }
        if ($trimmed.StartsWith('export ')) { $trimmed = $trimmed.Substring(7).Trim() }
        $eq = $trimmed.IndexOf('=')
        if ($eq -le 0) { continue }
        $name = $trimmed.Substring(0, $eq).Trim()
        $value = $trimmed.Substring($eq + 1).Trim()
        if (($value.Length -ge 2) -and
            (($value.StartsWith('"') -and $value.EndsWith('"')) -or
             ($value.StartsWith("'") -and $value.EndsWith("'")))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        [System.Environment]::SetEnvironmentVariable($name, $value, 'Process')
    }
}

function Test-TcpPortOpen {
    param(
        [Parameter(Mandatory = $true)][string]$ComputerName,
        [Parameter(Mandatory = $true)][int]$Port,
        [int]$TimeoutMs = 800
    )
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $client.BeginConnect($ComputerName, $Port, $null, $null)
        return $iar.AsyncWaitHandle.WaitOne($TimeoutMs)
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Get-PortOwnerDescription {
    param([Parameter(Mandatory = $true)][int]$Port)
    try {
        $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($null -eq $conn) {
            $conn = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue |
                Select-Object -First 1
        }
        if ($null -ne $conn -and $conn.OwningProcess) {
            $proc = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
            if ($null -ne $proc) {
                return "'$($proc.ProcessName)' (PID $($proc.Id))"
            }
            return "PID $($conn.OwningProcess)"
        }
    } catch {
        # Get-NetTCPConnection may be unavailable; fall through.
    }
    return 'an unknown process'
}

function Assert-PortFree {
    param([Parameter(Mandatory = $true)][int]$Port)
    if (Test-TcpPortOpen -ComputerName '127.0.0.1' -Port $Port -TimeoutMs 500) {
        $owner = Get-PortOwnerDescription -Port $Port
        Write-Error "Port $Port is already in use by $owner. Stop that process first; not starting a duplicate."
    }
}

function Stop-ProcessTree {
    param([Parameter(Mandatory = $true)][int]$ProcessId)
    try {
        & taskkill /T /F /PID $ProcessId 2>$null | Out-Null
    } catch {
        # Already gone; nothing to do.
    }
}

function Stop-StaleBackend {
    # Preflight: clear uvicorn --reload orphans from earlier lab runs so the
    # new backend binds a free port 8002 and serves fresh code. Only port
    # 8002 and this repo's backend are ever touched here; never the CRM's
    # 8000/8001 or any frontend port.
    try {
        $spawnProcs = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object {
                $_.Name -like 'python*' -and
                $_.CommandLine -and
                ($_.CommandLine -like '*spawn_main(parent_pid=*')
            })
    } catch {
        $spawnProcs = @()
    }
    foreach ($proc in $spawnProcs) {
        $cmd = $proc.CommandLine
        $exe = $proc.ExecutablePath
        $relates = ($cmd -like '*uvicorn*') -and
            (($cmd -like '*turtle-extraction-lab*') -or ($exe -like '*turtle-extraction-lab*'))
        if (-not $relates) { continue }
        $m = [regex]::Match($cmd, 'parent_pid=(\d+)')
        if (-not $m.Success) { continue }
        $parentId = [int]$m.Groups[1].Value
        if ($null -ne (Get-Process -Id $parentId -ErrorAction SilentlyContinue)) { continue }
        Stop-ProcessTree -ProcessId $proc.ProcessId
        Write-Host "Stopped stale lab backend worker PID $($proc.ProcessId): parent PID $parentId is gone."
    }
}

# --- Preflight ---------------------------------------------------------------
if (-not (Test-Path -LiteralPath $BackendDir -PathType Container)) {
    Write-Error "Backend directory not found: $BackendDir"
}
if (-not (Test-Path -LiteralPath $FrontendDir -PathType Container)) {
    Write-Error "Frontend directory not found: $FrontendDir"
}

Import-DotEnvFile -Path $DotEnvPath

# Idempotency: clear stale lab orphans first, then refuse duplicates.
Stop-StaleBackend
Assert-PortFree -Port $ApiPort
Assert-PortFree -Port $WebPort

# Fail fast when Postgres is down so uvicorn errors are not a mystery.
if (-not (Test-TcpPortOpen -ComputerName '127.0.0.1' -Port 5432 -TimeoutMs 1000)) {
    Write-Error 'PostgreSQL is not reachable on localhost:5432. Start the PostgreSQL service (e.g. services.msc -> postgresql-x64-18 -> Start) and retry.'
}

try { $uvPath = (Get-Command uv -ErrorAction Stop).Source } catch {
    Write-Error "'uv' was not found on PATH. Install uv (Python 3.13 toolchain) and retry."
}
# On Windows `npm` usually resolves to npm.ps1, which Process.Start cannot
# launch ("not a valid application for this OS platform"); npm.cmd is the one
# that actually runs. Prefer it, and fall back to whatever npm resolves to.
$npmPath = (Get-Command npm.cmd -ErrorAction SilentlyContinue).Source
if (-not $npmPath) {
    $npmPath = (Get-Command npm.exe -ErrorAction SilentlyContinue).Source
}
if (-not $npmPath) {
    $npmResolved = (Get-Command npm -ErrorAction SilentlyContinue).Source
    if ($npmResolved -and $npmResolved.EndsWith('.ps1')) {
        $npmSibling = [System.IO.Path]::ChangeExtension($npmResolved, 'cmd')
        if (Test-Path -LiteralPath $npmSibling) { $npmResolved = $npmSibling }
    }
    $npmPath = $npmResolved
}
if (-not $npmPath) {
    Write-Error "'npm' was not found on PATH. Install Node.js 20+ and retry."
}
if ($npmPath.EndsWith('.ps1')) {
    Write-Error "npm resolved to '$npmPath', which cannot be started directly. Install Node.js 20+ so that npm.cmd is on PATH, and retry."
}

# --- Start children ----------------------------------------------------------
$apiProc = New-Object System.Diagnostics.Process
$apiProc.StartInfo.FileName = $uvPath
$apiProc.StartInfo.Arguments = "run uvicorn app.main:app --reload --port $ApiPort"
$apiProc.StartInfo.WorkingDirectory = $BackendDir
$apiProc.StartInfo.UseShellExecute = $false
$apiProc.StartInfo.RedirectStandardOutput = $true
$apiProc.StartInfo.RedirectStandardError = $true
$apiProc.StartInfo.CreateNoWindow = $true

$webProc = New-Object System.Diagnostics.Process
$webProc.StartInfo.FileName = $npmPath
$webProc.StartInfo.Arguments = 'run dev'
$webProc.StartInfo.WorkingDirectory = $FrontendDir
$webProc.StartInfo.UseShellExecute = $false
$webProc.StartInfo.RedirectStandardOutput = $true
$webProc.StartInfo.RedirectStandardError = $true
$webProc.StartInfo.CreateNoWindow = $true

$apiOutSub = $apiErrSub = $webOutSub = $webErrSub = $null
try {
    $apiOutSub = Register-ObjectEvent -InputObject $apiProc -EventName OutputDataReceived -Action {
        param($sender, $e)
        if ($e.Data) { Write-Host "[api] $($e.Data)" }
    }
    $apiErrSub = Register-ObjectEvent -InputObject $apiProc -EventName ErrorDataReceived -Action {
        param($sender, $e)
        if ($e.Data) { Write-Host "[api] $($e.Data)" }
    }
    $webOutSub = Register-ObjectEvent -InputObject $webProc -EventName OutputDataReceived -Action {
        param($sender, $e)
        if ($e.Data) { Write-Host "[web] $($e.Data)" }
    }
    $webErrSub = Register-ObjectEvent -InputObject $webProc -EventName ErrorDataReceived -Action {
        param($sender, $e)
        if ($e.Data) { Write-Host "[web] $($e.Data)" }
    }

    Write-Host "Starting backend : uv run uvicorn app.main:app --reload --port $ApiPort  (cwd backend/)"
    [void]$apiProc.Start()
    $apiProc.BeginOutputReadLine()
    $apiProc.BeginErrorReadLine()

    Write-Host 'Starting frontend: npm run dev  (cwd frontend/)'
    [void]$webProc.Start()
    $webProc.BeginOutputReadLine()
    $webProc.BeginErrorReadLine()

    # Wait for Vite (5175) then open the browser once.
    $deadline = [DateTime]::UtcNow.AddSeconds(90)
    $browserOpened = $false
    while ([DateTime]::UtcNow -lt $deadline) {
        if ($apiProc.HasExited) {
            Write-Error "Backend exited early (code $($apiProc.ExitCode)). See [api] output above."
        }
        if ($webProc.HasExited) {
            Write-Error "Frontend exited early (code $($webProc.ExitCode)). See [web] output above."
        }
        if (Test-TcpPortOpen -ComputerName '127.0.0.1' -Port $WebPort -TimeoutMs 500) {
            Write-Host "Frontend is up at http://localhost:$WebPort - opening browser."
            Start-Process "http://localhost:$WebPort/"
            $browserOpened = $true
            break
        }
        Start-Sleep -Milliseconds 500
    }
    if (-not $browserOpened) {
        Write-Error "Timed out waiting for the frontend on http://localhost:$WebPort. See [web] output above."
    }

    Write-Host 'Backend and frontend running. Press Ctrl+C to stop.'
    while ($true) {
        if ($apiProc.HasExited) {
            Write-Error "Backend exited (code $($apiProc.ExitCode)). See [api] output above."
        }
        if ($webProc.HasExited) {
            Write-Error "Frontend exited (code $($webProc.ExitCode)). See [web] output above."
        }
        Start-Sleep -Milliseconds 500
    }
} finally {
    Write-Host 'Stopping backend and frontend...'
    foreach ($sub in @($apiOutSub, $apiErrSub, $webOutSub, $webErrSub)) {
        if ($sub) { Unregister-Event -SubscriptionId $sub.Id -ErrorAction SilentlyContinue }
    }
    foreach ($proc in @($apiProc, $webProc)) {
        try {
            # taskkill /T takes the whole tree so a uvicorn --reload worker
            # cannot outlive its reloader parent.
            if ($proc -and -not $proc.HasExited) { Stop-ProcessTree -ProcessId $proc.Id }
        } catch {
            # Already exiting; nothing to do.
        } finally {
            if ($proc) { $proc.Dispose() }
        }
    }
}
