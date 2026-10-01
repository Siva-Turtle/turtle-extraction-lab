@echo off
REM First-time database setup for the Extraction Lab -- creates the
REM turtle_agent_lab database, writes backend\.env, migrates and seeds.
REM Run by double-clicking this file, or from a plain Command Prompt. The
REM real script is scripts\db-init.ps1, which needs PowerShell 7; cmd.exe
REM cannot run a .ps1 directly (double-clicking one opens Notepad by
REM Windows design), so this hands it over to pwsh.
setlocal
set "PWSH=pwsh"
where pwsh >nul 2>nul
if errorlevel 1 (
  if exist "%ProgramFiles%\PowerShell\7\pwsh.exe" (
    set "PWSH=%ProgramFiles%\PowerShell\7\pwsh.exe"
  ) else (
    echo PowerShell 7 was not found. Install it from https://aka.ms/powershell and run this again.
    exit /b 1
  )
)
"%PWSH%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\db-init.ps1" %*
set "ERR=%ERRORLEVEL%"
if %ERR% neq 0 (
  echo.
  echo Database setup failed ^(exit %ERR%^). See the message above.
  pause
)
exit /b %ERR%
