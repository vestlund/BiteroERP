@echo off
REM Kopierar de krypterade säkerhetskopiorna till en USB-disk. Kör en gång i månaden.
cd /d "%~dp0"
set /p ENHET="Vilken enhetsbokstav har USB-disken (t.ex. E)? "
uv run python manage.py extern_kopia %ENHET%:\
pause
