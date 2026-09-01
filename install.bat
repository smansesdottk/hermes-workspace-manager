@echo off
REM ==============================================================================
REM Hermes Workspace Manager — 1-Click Installer (Windows 10 / 11)
REM ==============================================================================
title Hermes Workspace Manager - Setup
color 0B

cd /d "%~dp0"

echo ================================================================
echo    Hermes Workspace Manager - 1-Click Setup ^& Installer (Windows)
echo ================================================================
echo.

REM 1. Cek Python
echo [1/4] Memeriksa Python...
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Python tidak ditemukan di sistem Windows Anda.
    echo Harap unduh dan instal Python dari https://www.python.org/downloads/
    echo PENTING: Centang opsi "Add python.exe to PATH" saat instalasi!
    echo.
    pause
    exit /b 1
)
python --version

REM 2. Cek Docker Desktop
echo.
echo [2/4] Memeriksa Docker Desktop...
where docker >nul 2>nul
if %errorlevel% neq 0 (
    if exist "%ProgramFiles%\Docker\Docker\resources\bin\docker.exe" (
        echo [INFO] Docker terdeteksi di Program Files: "%ProgramFiles%\Docker\Docker\resources\bin\docker.exe"
        set "PATH=%ProgramFiles%\Docker\Docker\resources\bin;%PATH%"
    ) else (
        echo [PERINGATAN] Perintah 'docker' belum ditemukan di PATH Windows.
        echo Panel web tetap bisa dijalankan, namun untuk membuat ^& menjalankan instance,
        echo pastikan aplikasi Docker Desktop sudah terinstal dan dalam keadaan Running/Buka.
    )
) else (
    docker --version
)


REM 3. Setup Virtual Environment
echo.
echo [3/4] Menyiapkan Virtual Environment (.venv)...
if not exist ".venv\Scripts\activate.bat" (
    echo Membuat folder virtual environment .venv...
    python -m venv .venv
)

if not exist ".venv\Scripts\activate.bat" (
    echo.
    echo [ERROR] Gagal membuat virtual environment Python.
    echo Pastikan Anda memiliki izin tulis di folder ini.
    echo.
    pause
    exit /b 1
)

call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip --quiet
echo Memasang dependensi dari requirements.txt...
pip install -r requirements.txt

REM 4. Inisialisasi Database
echo.
echo [4/4] Menginisialisasi Database dan Security Keys...
python -c "import manager; manager.init_db(); print('Database panel.db siap.')"
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Terjadi kegagalan saat menginisialisasi database.
    echo.
    pause
    exit /b 1
)

echo.
echo ================================================================
echo       INSTALASI SELESAI DAN SIAP DIGUNAKAN!
echo ================================================================
echo.
echo Panel sudah siap. Anda dapat menjalankan panel dengan klik ganda:
echo   - run.bat
echo.
pause

