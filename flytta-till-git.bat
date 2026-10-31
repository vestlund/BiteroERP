@echo off
chcp 65001 >nul
setlocal
REM Hämtar Bitero ERP från GitHub till C:\kod\bitero-erp (utanför OneDrive), tar med dina Visma-filer
REM och säkerhetskopior från den gamla mappen och skapar en genväg. Databasen och bilagorna rörs inte.
REM Kör skriptet från den gamla programmappen i OneDrive.

set "KALLA=%~dp0"
set "KALLA=%KALLA:~0,-1%"
set "REPO=https://github.com/vestlund/BiteroERP.git"

echo.
echo  Bitero ERP - hamta koden fran GitHub
echo  ====================================
echo  Gammal mapp: %KALLA%
echo.

where git >nul 2>nul
if errorlevel 1 (
  echo  Git saknas. Installerar Git for Windows ...
  winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements
  echo.
  echo  Klart. Stang det har fonstret och dubbelklicka pa flytta-till-git.bat igen.
  pause
  exit /b
)

set "MAL=C:\kod\bitero-erp"
set /p "MAL=Ny plats for koden [%MAL%]: "
if exist "%MAL%\.git" (
  echo  %MAL% finns redan som git-mapp. Hamta uppdateringar dar med: git pull
  pause
  exit /b 1
)

echo.
echo  1/4 Hamtar koden fran GitHub. Forsta gangen oppnas webblasaren sa att du kan logga in ...
git clone "%REPO%" "%MAL%"
if errorlevel 1 (
  echo  Kunde inte hamta koden. Kontrollera att du ar inloggad pa GitHub och forsok igen.
  pause
  exit /b 1
)

echo  2/4 Kopierar Visma-filerna i import\ (de ligger inte pa GitHub) ...
if exist "%KALLA%\import" robocopy "%KALLA%\import" "%MAL%\import" /E /NFL /NDL /NJH /NJS /NP >nul

echo  3/4 Flyttar gamla sakerhetskopior till OneDrive\bitero-erp-backups ...
set "BACKUP=%OneDriveCommercial%"
if not defined BACKUP set "BACKUP=%OneDrive%"
if defined BACKUP if exist "%KALLA%\backups" (
  robocopy "%KALLA%\backups" "%BACKUP%\bitero-erp-backups" /E /NFL /NDL /NJH /NJS /NP >nul
)

echo  4/4 Skapar genvag "Bitero ERP" pa skrivbordet ...
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Bitero ERP.lnk'); $s.TargetPath='%MAL%\start.bat'; $s.WorkingDirectory='%MAL%'; $s.Save()"

echo.
echo  Klart!
echo  - Starta i fortsattningen fran genvagen "Bitero ERP" (eller %MAL%\start.bat).
echo  - Hamta uppdateringar med:  git pull   (i %MAL%)
echo  - Sakerhetskopiorna hamnar i %BACKUP%\bitero-erp-backups.
echo  - Den gamla mappen %KALLA% kan du radera nar du har startat fran den nya och sett att allt fungerar.
echo.
pause
