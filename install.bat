@echo off
setlocal EnableDelayedExpansion
title HF Range Tracker — Installer

echo.
echo  ================================================
echo   HF Range Tracker — Windows Installer
echo   PD1LVH / Amsterdam
echo  ================================================
echo.

:: ── Check Python ──────────────────────────────────────────────────────────
echo [1/5] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo  ERROR: Python not found.
    echo  Download from https://www.python.org/downloads/
    echo  Make sure to check "Add Python to PATH" during install.
    echo.
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('python --version 2^>^&1') do echo  Found: %%v

:: ── Install Python packages ────────────────────────────────────────────────
echo.
echo [2/5] Installing Python packages...
python -m pip install --upgrade pip --quiet
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo  ERROR: pip install failed. Check your internet connection.
    pause
    exit /b 1
)
echo  Done.

:: ── Check rigctld ─────────────────────────────────────────────────────────
echo.
echo [3/5] Checking rigctld (hamlib)...
rigctld.exe --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo  rigctld NOT found.
    echo.
    echo  You need to install hamlib manually:
    echo.
    echo    1. Go to: https://github.com/Hamlib/Hamlib/releases/latest
    echo    2. Download the Windows ZIP  e.g. hamlib-w64-4.x.zip
    echo    3. Extract to C:\hamlib\
    echo    4. Add C:\hamlib\bin to your PATH:
    echo       - Open Start ^> "Edit the system environment variables"
    echo       - Environment Variables ^> System variables ^> Path ^> Edit ^> New
    echo       - Type: C:\hamlib\bin
    echo       - OK all the way out
    echo    5. Re-open this window and run install.bat again
    echo.
    echo  Press any key to open the hamlib releases page in your browser...
    pause >nul
    start https://github.com/Hamlib/Hamlib/releases/latest
    exit /b 1
)
for /f "tokens=*" %%v in ('rigctld.exe --version 2^>^&1') do echo  Found: %%v

:: ── Check IC-7300 USB driver ───────────────────────────────────────────────
echo.
echo [4/5] IC-7300 USB driver check...
echo  Looking for Silicon Labs CP210x device...
wmic path win32_pnpentity where "Name like '%%CP210%%'" get Name 2>nul | findstr /i "CP210" >nul
if errorlevel 1 (
    echo.
    echo  CP210x driver not detected (or IC-7300 not connected).
    echo.
    echo  If this is the first time connecting the IC-7300:
    echo    1. Connect IC-7300 via USB while powered on
    echo    2. Windows should auto-install the driver
    echo    3. If not: https://www.silabs.com/developers/usb-to-uart-bridge-vcp-drivers
    echo    4. Open Device Manager to find which COM port was assigned
    echo.
) else (
    echo  CP210x driver found.
)

:: ── Create output directory ────────────────────────────────────────────────
echo.
echo [5/5] Creating output directory...
if not exist output mkdir output
echo  Done.

:: ── Done ──────────────────────────────────────────────────────────────────
echo.
echo  ================================================
echo   Installation complete!
echo  ================================================
echo.
echo  Next steps:
echo.
echo   1. Connect IC-7300 via USB and note the COM port
echo      (Device Manager ^> Ports ^> "Silicon Labs CP210x")
echo.
echo   2. On the IC-7300: Menu ^> SET ^> Connectors
echo      ^> USB SEND/MOD ^> set to "Data" (DATA)
echo.
echo   3. Start rigctld (replace COM3 with your port):
echo      rigctld.exe -m 3073 -r COM3 -s 19200 -P RIG
echo.
echo   4. Start the web app:
echo      python src\web_app.py
echo.
echo   5. Open in browser:
echo      http://localhost:8000
echo.
echo  See docs\setup.md for full details.
echo.
pause
