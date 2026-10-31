@echo off
REM Startar konsultsystemet (Windows). Stoppa med Ctrl+C.
cd /d "%~dp0"
where uv >nul 2>nul
if errorlevel 1 (
  echo Installerar uv ...
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  set "PATH=%USERPROFILE%\.local\bin;%PATH%"
)
uv run python manage.py starta %*
pause
