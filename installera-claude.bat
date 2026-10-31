@echo off
chcp 65001 >nul
REM Kopplar Bitero ERP till Claude-appen (MCP), så att Claude kan bokföra kvitton från inkorgen.
cd /d "%~dp0"
uv run python manage.py installera_claude %*
pause
