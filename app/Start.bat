@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

:: ==============================================================================
:: VN-MateAI: Script Khởi Động Toàn Diện (Full Stack Launcher)
:: ==============================================================================

cd /d "%~dp0"
title VN-MateAI — Đang khởi động...

echo =====================================================================
echo  🤖 VN-MATEAI — TRỢ LÝ AI VẬN HÀNH DOANH NGHIỆP ^& ROBOT TRỢ LÝ
echo =====================================================================
echo.

:: ----------------------------------------------------------------------
:: 1. Kiểm tra môi trường Python
:: ----------------------------------------------------------------------
echo 🐍 [1/4] Kiểm tra môi trường Python...
set "PYTHON_EXE="
if exist ".venv\Scripts\python.exe" (
    set "PYTHON_EXE=.venv\Scripts\python.exe"
    echo    -^> Sử dụng Python Virtualenv ^(.venv^)
) else (
    where python >nul 2>&1
    if !errorlevel! equ 0 (
        set "PYTHON_EXE=python"
        echo    -^> Sử dụng Python toàn cục ^(Global PATH^)
    )
)

if "%PYTHON_EXE%"=="" (
    echo.
    echo ❌ [LỖI] Không tìm thấy Python!
    echo Vui lòng cài đặt Python 3.11+ hoặc chạy file 'setup.bat' trước.
    echo =====================================================================
    pause
    exit /b 1
)

:: ----------------------------------------------------------------------
:: 2. Đảm bảo cấu trúc thư mục & cấu hình cơ bản
:: ----------------------------------------------------------------------
echo 📁 [2/4] Kiểm tra thư mục dữ liệu và cấu hình...
if not exist "logs" mkdir "logs"
if not exist "data" mkdir "data"
if not exist "certs" mkdir "certs"
if not exist "backups" mkdir "backups"
if not exist "storage\audio_cache" mkdir "storage\audio_cache"
if not exist "storage\vector_db" mkdir "storage\vector_db"

if not exist "config.json" (
    if exist "config.example.json" (
        copy "config.example.json" "config.json" >nul
        echo    -^> Đã tạo file config.json từ bản mẫu.
    )
)

:: ----------------------------------------------------------------------
:: 3. Khởi động Hạ tầng Docker (PostgreSQL, Redis, S3, OTel)
:: ----------------------------------------------------------------------
echo 🐳 [3/4] Kiểm tra hạ tầng Docker (PostgreSQL 16, Redis 7, S3, OTel)...
where docker >nul 2>&1
if !errorlevel! equ 0 (
    docker info >nul 2>&1
    if !errorlevel! equ 0 (
        if exist "deploy\docker-compose.infra.yml" (
            echo    -^> Đang kích hoạt containers hạ tầng...
            if exist "deploy\.env" (
                docker compose -f deploy\docker-compose.infra.yml --env-file deploy\.env up -d >nul 2>&1
            ) else (
                docker compose -f deploy\docker-compose.infra.yml up -d >nul 2>&1
            )
            echo    -^> ✅ Hạ tầng Docker đã sẵn sàng!
        )
    ) else (
        echo    -^> [LƯU Ý] Docker Desktop chưa bật. Hệ thống sẽ tự dùng SQLite ^& RAM cache.
    )
) else (
    echo    -^> [LƯU Ý] Chưa cài Docker. Hệ thống sẽ tự dùng SQLite ^& RAM cache.
)

:: ----------------------------------------------------------------------
:: 4. Khởi chạy Ứng dụng Chính (main.py)
:: ----------------------------------------------------------------------
echo 🚀 [4/4] Đang khởi chạy VN-MateAI Engine...
echo =====================================================================
echo  Địa chỉ truy cập sau khi máy chủ sẵn sàng:
echo    • Web Portal:  https://localhost
echo    • Màn hình HUD: https://localhost/hud
echo    • Bảng ROI:    https://localhost/roi
echo    • Kênh Robot:  ws://localhost:8000 (ESP32 Gateway)
echo =====================================================================
echo.

title VN-MateAI — Đang chạy (Đóng cửa sổ này để dừng hệ thống)

"%PYTHON_EXE%" main.py

if !errorlevel! neq 0 (
    echo.
    echo ⚠️ Ứng dụng đã dừng với mã lỗi !errorlevel!.
    echo Vui lòng kiểm tra log trong thư mục 'logs/' để biết chi tiết.
    pause
)
