@echo off
REM ==============================================================================
REM Hermes Workspace Manager — Runner (Windows 10 / 11)
REM ==============================================================================
title Hermes Workspace Manager (Running)
color 0A

cd /d "%~dp0"

REM Tambahkan path default Docker Desktop Windows jika belum ada di PATH
if exist "%ProgramFiles%\Docker\Docker\resources\bin\docker.exe" (
    set "PATH=%ProgramFiles%\Docker\Docker\resources\bin;%PATH%"
)

if not exist ".venv\Scripts\activate.bat" (

    echo Virtual environment belum disiapkan. Menjalankan installer terlebih dahulu...
    call install.bat
)

call ".venv\Scripts\activate.bat"

echo ================================================================
echo    Hermes Workspace Manager Sedang Berjalan (Port: 8899)
echo ================================================================
echo.
echo Akses Web: http://127.0.0.1:8899
echo Membuka browser default Windows...
start http://127.0.0.1:8899

REM Jalankan via waitress WSGI server produksi untuk Windows jika ada
python -c "import waitress" >nul 2>nul
if %errorlevel% equ 0 (
    echo [INFO] Menjalankan server via Waitress (Produksi)...
    waitress-serve --listen=127.0.0.1:8899 app:app
) else (
    echo [INFO] Menjalankan server via Flask Development Server...
    python app.py
)

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Server berhenti secara tidak normal (Error Code: %errorlevel%).
    echo.
)

pause


