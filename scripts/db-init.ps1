#Requires -Version 7.0
<#
.SYNOPSIS
  First-time database setup for Turtle Extraction Lab (PostgreSQL).

.DESCRIPTION
  Creates the turtle_agent_lab database if missing (via psql, found on PATH or at
  C:\Program Files\PostgreSQL\18\bin), writes backend/.env from
  backend/.env.example with DATABASE_URL filled in (the password is never
  echoed), then runs `uv run alembic upgrade head` and the seed
  (`uv run python -m app.seed`) from backend/. Prints each step and stops
  on the first failure. After this, double-click start-lab.cmd (or run
  ./scripts/dev.ps1) to start the app. Use the same postgres password as
  turtle-crm (same user 'postgres' on localhost:5432, different database
  name); it is prompted securely via SecureString and never echoed.

.EXAMPLE
  ./scripts/db-init.ps1
  (prompts securely for the postgres password)

.EXAMPLE
  ./scripts/db-init.ps1 -PostgresPassword (Read-Host -AsSecureString -Prompt 'postgres password') -DbHost localhost -Port 5432
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [SecureString]$PostgresPassword,

    [Parameter(Mandatory = $false)]
    [string]$DbHost = 'localhost',

    [Parameter(Mandatory = $false)]
    [int]$Port = 5432
)

$ErrorActionPreference = 'Stop'

$RepoRoot      = Split-Path -Parent $PSScriptRoot
$BackendDir    = Join-Path $RepoRoot 'backend'
$EnvExample    = Join-Path $BackendDir '.env.example'
$EnvFile       = Join-Path $BackendDir '.env'
$DbName        = 'turtle_agent_lab'
$DbUser        = 'postgres'
$FallbackPsql  = 'C:\Program Files\PostgreSQL\18\bin\psql.exe'

function Invoke-Step {
    param(
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][scriptblock]$Action
    )
    Write-Host "==> $Label"
    & $Action
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Step failed: $Label (exit code $LASTEXITCODE)."
    }
    Write-Host "    done: $Label"
}

function Get-PsqlPath {
    $cmd = Get-Command psql -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    if (Test-Path -LiteralPath $FallbackPsql) { return $FallbackPsql }
    Write-Error "Could not find psql on PATH or at $FallbackPsql. Install PostgreSQL (which provides psql) and retry."
}

function ConvertFrom-SecureStringPlain {
    param([Parameter(Mandatory = $true)][SecureString]$Secure)
    $bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secure)
    try {
        return [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    } finally {
        [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}

# --- Preflight ---------------------------------------------------------------
if (-not (Test-Path -LiteralPath $EnvExample -PathType Leaf)) {
    Write-Error "Missing template: $EnvExample"
}
try { $uvPath = (Get-Command uv -ErrorAction Stop).Source } catch {
    Write-Error "'uv' was not found on PATH. Install uv (Python 3.13 toolchain) and retry."
}

if (-not $PostgresPassword) {
    $PostgresPassword = Read-Host -AsSecureString -Prompt "PostgreSQL password for user '$DbUser' on ${DbHost}:${Port}"
}
if (-not $PostgresPassword -or $PostgresPassword.Length -eq 0) {
    Write-Error 'No password was entered; aborting without writing any files.'
}
$plainPassword = ConvertFrom-SecureStringPlain -Secure $PostgresPassword
try {
    if ([string]::IsNullOrEmpty($plainPassword)) {
        Write-Error 'No password was entered; aborting without writing any files.'
    }

    $psql = Get-PsqlPath
    Write-Host "Using psql: $psql"

    # Provide the password to psql without ever printing it.
    $env:PGPASSWORD = $plainPassword

    # --- Step 1: create the database if missing ------------------------------
    Write-Host "==> Step 1/4: ensuring database '$DbName' exists on ${DbHost}:${Port}"
    $exists = & $psql -h $DbHost -p $Port -U $DbUser -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$DbName';" 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Error "psql could not connect as user '$DbUser' on ${DbHost}:${Port}. Check the service is running and the password is correct. psql said: $exists"
    }
    if (($exists | Out-String).Trim() -eq '1') {
        Write-Host "    database '$DbName' already exists; leaving it alone."
    } else {
        Invoke-Step -Label "Step 1/4: creating database '$DbName'" -Action {
            & $psql -h $DbHost -p $Port -U $DbUser -d postgres -c "CREATE DATABASE `"$DbName`";"
        }
    }

    # --- Step 2: write backend/.env (password never echoed) -------------------
    Write-Host '==> Step 2/4: writing backend/.env from .env.example'
    $encoded = [System.Uri]::EscapeDataString($plainPassword)
    $databaseUrl = "DATABASE_URL=postgresql+psycopg://${DbUser}:${encoded}@${DbHost}:${Port}/${DbName}"
    $lines = Get-Content -LiteralPath $EnvExample
    $replaced = $false
    $out = foreach ($line in $lines) {
        if ($line -match '^\s*DATABASE_URL\s*=') {
            $replaced = $true
            $databaseUrl
        } else {
            $line
        }
    }
    if (-not $replaced) { $out += $databaseUrl }
    Set-Content -LiteralPath $EnvFile -Value $out -Encoding UTF8
    Write-Host "    wrote $EnvFile (DATABASE_URL host/port/database set; password not shown)."
    Write-Host '    NOTE: set OPENROUTER_API_KEY in backend/.env before running test agents.'
} finally {
    $plainPassword = $null
    Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
}

# --- Step 3: migrations -------------------------------------------------------
Write-Host '==> Step 3/4: running Alembic migrations (uv run alembic upgrade head)'
Push-Location -LiteralPath $BackendDir
try {
    & $uvPath run alembic upgrade head
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Step failed: Step 3/4: running Alembic migrations (exit code $LASTEXITCODE)."
    }
    Write-Host '    done: Step 3/4: running Alembic migrations'
} finally {
    Pop-Location
}

# --- Step 4: seed -------------------------------------------------------------
Write-Host '==> Step 4/4: seeding the database (uv run python -m app.seed)'
Push-Location -LiteralPath $BackendDir
try {
    & $uvPath run python -m app.seed
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Step failed: Step 4/4: seeding the database (exit code $LASTEXITCODE)."
    }
    Write-Host '    done: Step 4/4: seeding the database'
} finally {
    Pop-Location
}

Write-Host 'Database setup complete. Double-click start-lab.cmd to launch the lab.'
