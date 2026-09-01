@echo off
REM ==============================================================================
REM Reset / Buat Akun Admin Baru (Windows)
REM ==============================================================================
title Reset Akun Admin
color 0E

cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
    echo [ERROR] Virtual environment (.venv) belum disiapkan. Jalankan install.bat terlebih dahulu.
    pause
    exit /b 1
)

call ".venv\Scripts\activate.bat"

echo ================================================================
echo          Pembuatan / Reset Akun Administrator Panel
echo ================================================================
echo.

set /p ADMIN_USER="Masukkan Username Admin [default: admin]: "
if "%ADMIN_USER%"=="" set ADMIN_USER=admin

set /p ADMIN_PASS="Masukkan Password Baru: "
if "%ADMIN_PASS%"=="" (
    echo [ERROR] Password tidak boleh kosong!
    pause
    exit /b 1
)

python -c "import manager; from werkzeug.security import generate_password_hash; h=generate_password_hash('%ADMIN_PASS%'); conn=manager.db(); conn.execute('INSERT OR REPLACE INTO users (username, password_hash, role, is_active, created_at) VALUES (?, ?, \'admin\', 1, ?)', ('%ADMIN_USER%', h, manager.now())); conn.commit(); print('\n✓ Akun admin [%ADMIN_USER%] berhasil diset!')"

echo.
echo Silakan login di browser dengan akun tersebut.
echo.
pause
