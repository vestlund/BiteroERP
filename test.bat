@echo off
REM Kör de automatiska testerna. Din riktiga databas rörs inte (testerna använder en tillfällig i minnet).
cd /d "%~dp0"
uv run python manage.py test tester
pause
