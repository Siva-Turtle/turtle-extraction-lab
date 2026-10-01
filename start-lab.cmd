@echo off
REM Starts the whole Extraction Lab -- backend and frontend -- from a
REM plain Command Prompt, or by double-clicking this file. The real script is
REM scripts\dev.ps1, which needs PowerShell 7; cmd.exe cannot run a .ps1
REM directly, so this hands it over to pwsh.
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
"%PWSH%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\dev.ps1" %*
set "ERR=%ERRORLEVEL%"
if %ERR% neq 0 (
  echo.
  echo Lab failed to start ^(exit %ERR%^). See the message above.
  pause
)
exit /b %ERR%
