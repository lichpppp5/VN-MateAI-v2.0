@echo off
chcp 65001 >nul
:: ==============================================================================
:: VN-MateAI: Script Khởi Tạo Môi Trường Cục Bộ (Windows CMD / Batch)
:: ==============================================================================

echo ==========================================================
echo 🚀 KHỞI TẠO HỆ THỐNG VN-MATEAI (ENTERPRISE AIOPS ^& ROBOTICS)
echo ==========================================================

:: 1. Tạo các thư mục cần thiết
echo 📁 [1/4] Đang tạo các thư mục dữ liệu và bộ nhớ cục bộ...
if not exist "storage\audio_cache" mkdir "storage\audio_cache"
if not exist "storage\vector_db" mkdir "storage\vector_db"
if not exist "storage\backups" mkdir "storage\backups"
if not exist "data" mkdir "data"
if not exist "certs" mkdir "certs"
if not exist "models" mkdir "models"
if not exist "logs" mkdir "logs"

:: 2. Khởi tạo file config.json nếu chưa tồn tại
echo ⚙️  [2/4] Kiểm tra file cấu hình config.json...
if not exist "config.json" (
    if exist "config.example.json" (
        copy "config.example.json" "config.json" >nul
        echo    -^> Đã sao chép config.example.json thành config.json.
    ) else (
        echo    -^> CẢNH BÁO: Không tìm thấy config.example.json!
    )
) else (
    echo    -^> File config.json đã tồn tại. Bỏ qua sao chép.
)

:: 3. Kiểm tra chứng chỉ bảo mật HTTPS
echo 🔒 [3/4] Kiểm tra chứng chỉ bảo mật HTTPS/WSS...
if not exist "certs\server.key" (
    echo    -^> Thư mục certs chưa có SSL certs. Nếu máy đã cài openssl, chạy lệnh sau:
    echo        openssl req -x509 -newkey rsa:2048 -nodes -keyout certs/server.key -out certs/server.crt -days 365 -subj "/CN=localhost"
) else (
    echo    -^> Chứng chỉ SSL trong certs\ đã tồn tại. Bỏ qua.
)

:: 4. Kiểm tra Python
echo 🐍 [4/4] Kiểm tra Python...
python --version >nul 2>&1
if %errorlevel% equ 0 (
    for /f "tokens=*" %%i in ('python --version') do echo    -^> Tìm thấy: %%i
) else (
    echo    -^> CẢNH BÁO: Chưa phát hiện Python trong PATH. Vui lòng cài đặt Python 3.10+
)

echo ==========================================================
echo ✅ KHỞI TẠO HỆ THỐNG THÀNH CÔNG!
echo.
echo 👉 BƯỚC TIẾP THEO:
echo    1. Mở file 'config.json' và điền API Key (9router, Groq, Telegram...)
echo    2. Cài đặt thư viện: pip install -r requirements.txt
echo    3. Khởi chạy hệ thống: python main.py
echo    4. Mở trình duyệt truy cập:
echo       - Web Portal:  https://localhost
echo       - Standby HUD: https://localhost/hud
echo       - ROI Board:   https://localhost/roi
echo ==========================================================
pause
