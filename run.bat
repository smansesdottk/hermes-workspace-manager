@echo off
REM ==============================================================================
REM Hermes Workspace Manager — Runner (Windows 10 / 11)
REM ==============================================================================
title Hermes Workspace Manager (Running)
color 0A

cd /d "%~dp0"

REM Tambahkan path default Docker Desktop Windows jika belum ada di PATH
if exist "%ProgramFiles%\Docker\Docker\resources\bin\docker.exe" (
    set "PATH=%ProgramFiles%\Docker\Docker\resources\bin;%ProgramFiles%\Docker\Docker\resources;%PATH%"
)
if exist "%ProgramFiles%\Docker\Docker\docker.exe" (
    set "PATH=%ProgramFiles%\Docker\Docker;%PATH%"
)

if not exist ".venv\Scripts\activate.bat" (

    echo [INFO] Virtual environment belum disiapkan. Menjalankan installer terlebih dahulu...
    call install.bat
)

if not exist ".venv\Scripts\activate.bat" (
    echo [ERROR] File .venv\Scripts\activate.bat tidak ditemukan.
    echo Harap jalankan install.bat terlebih dahulu.
    pause
    exit /b 1
)

call ".venv\Scripts\activate.bat"

echo ================================================================
echo    Hermes Workspace Manager Sedang Berjalan (Port: 8899)
echo ================================================================
echo.
echo Akses Web: http://127.0.0.1:8899
echo.

REM Jalankan server langsung via Python di dalam venv (anti-crash waitress executable)
echo [INFO] Menjalankan server aplikasi di http://127.0.0.1:8899 ...
echo [INFO] Tekan Ctrl+C untuk menghentikan server.
echo.

REM Buka browser di background
start http://127.0.0.1:8899

python -c "import waitress, app; waitress.serve(app.app, host='127.0.0.1', port=8899)" 2> "%TEMP%\hermes_server_err.log"

if %errorlevel% neq 0 (
    echo [INFO] Mencoba fallback ke Flask Server...
    python app.py
)

if %errorlevel% neq 0 (
    echo.
    echo ================================================================
    echo [ERROR] Server berhenti dengan error code: %errorlevel%
    if exist "%TEMP%\hermes_server_err.log" (
        echo Detail Error:
        type "%TEMP%\hermes_server_err.log"
    )
    echo ================================================================
    echo.
)

echo Server telah berhenti.
pause
