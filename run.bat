@echo off
REM ==============================================================================
REM Hermes Workspace Manager — Runner (Windows 10 / 11)
REM ==============================================================================
title Hermes Workspace Manager (Running)
color 0A

cd /d "%~dp0"

if not exist ".venv" (
    echo Virtual environment belum disiapkan. Menjalankan installer...
    call install.bat
)

call .venv\Scripts\activate.bat

echo ================================================================
echo    Hermes Workspace Manager Sedang Berjalan (Port: 8899)
echo ================================================================
echo.
echo Membuka browser: http://127.0.0.1:8899 ...
start http://127.0.0.1:8899

REM Jalankan via waitress WSGI server produksi untuk Windows jika ada
where waitress-serve >nul 2>nul
if %errorlevel% equ 0 (
    echo Menjalankan server via Waitress...
    waitress-serve --listen=127.0.0.1:8899 app:app
) else (
    echo Menjalankan server via Flask...
    python app.py
)

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Server berhenti dengan error code %errorlevel%.
)

pause

