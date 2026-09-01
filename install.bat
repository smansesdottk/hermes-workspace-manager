@echo off
REM ==============================================================================
REM Hermes Workspace Manager — 1-Click Installer (Windows 10 / 11)
REM ==============================================================================
title Hermes Workspace Manager - Setup
color 0B

echo ================================================================
echo    Hermes Workspace Manager - 1-Click Setup ^& Installer (Windows)
echo ================================================================
echo.

REM 1. Cek Python
echo [1/4] Memeriksa Python...
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Python tidak ditemukan. Harap unduh dan instal Python dari python.org
    echo Pastikan opsi "Add python.exe to PATH" dicentang saat instalasi.
    pause
    exit /b 1
)
python --version

REM 2. Cek Docker Desktop
echo.
echo [2/4] Memeriksa Docker Desktop...
where docker >nul 2>nul
if %errorlevel% neq 0 (
    echo [PERINGATAN] Docker CLI tidak terdeteksi di PATH.
    echo Pastikan Docker Desktop sudah terpasang dan berjalan di Windows Anda.
) else (
    docker --version
)

REM 3. Setup Virtual Environment
echo.
echo [3/4] Menyiapkan Virtual Environment (.venv)...
if not exist ".venv" (
    echo Membuat folder .venv...
    python -m venv .venv
)

call .venv\Scripts\activate.bat
python -m pip install --upgrade pip --quiet
echo Memasang dependensi dari requirements.txt...
pip install -r requirements.txt --quiet

REM 4. Inisialisasi Database
echo.
[4/4] Menginisialisasi Database & Security Keys...
python -c "import manager; manager.init_db(); print('Database panel.db siap.')"
if %errorlevel% neq 0 (
    echo [ERROR] Gagal menginisialisasi database.
    pause
    exit /b 1
)

echo.
echo ================================================================
echo       INSTALASI SELESAI & SIAP DIGUNAKAN!
echo ================================================================

echo.
echo Anda dapat menjalankan aplikasi dengan klik ganda file "run.bat"
echo.
pause
