#!/usr/bin/env bash
# ==============================================================================
# VN-MateAI: Script Khởi Tạo Môi Trường Cục Bộ (macOS / Linux)
# ==============================================================================

set -e

echo "=========================================================="
echo "🚀 KHỞI TẠO HỆ THỐNG VN-MATEAI (ENTERPRISE AIOPS & ROBOTICS)"
echo "=========================================================="

# 1. Tạo các thư mục cần thiết
echo "📁 [1/4] Đang tạo các thư mục dữ liệu và bộ nhớ cục bộ..."
mkdir -p storage/audio_cache storage/vector_db storage/backups
mkdir -p data
mkdir -p certs
mkdir -p models
mkdir -p logs

# 2. Khởi tạo file config.json nếu chưa tồn tại
echo "⚙️  [2/4] Kiểm tra file cấu hình config.json..."
if [ ! -f "config.json" ]; then
    if [ -f "config.example.json" ]; then
        cp config.example.json config.json
        echo "   -> Đã sao chép config.example.json thành config.json."
    else
        echo "   -> CẢNH BÁO: Không tìm thấy config.example.json!"
    fi
else
    echo "   -> File config.json đã tồn tại. Bỏ qua sao chép."
fi

# 3. Tự động sinh chứng chỉ SSL tự ký nếu chưa có
echo "🔒 [3/4] Kiểm tra chứng chỉ bảo mật HTTPS/WSS..."
if [ ! -f "certs/server.key" ] || [ ! -f "certs/server.crt" ]; then
    if command -v openssl &> /dev/null; then
        echo "   -> Đang sinh chứng chỉ SSL tự ký trong thư mục certs/..."
        openssl req -x509 -newkey rsa:2048 -nodes \
            -keyout certs/server.key \
            -out certs/server.crt \
            -days 365 \
            -subj "/C=VN/ST=Hanoi/L=Hanoi/O=VN-MateAI/OU=IT/CN=localhost" 2>/dev/null
        echo "   -> Đã tạo certs/server.key và certs/server.crt."
    else
        echo "   -> openssl chưa được cài đặt. Vui lòng tạo certs/server.key và certs/server.crt thủ công."
    fi
else
    echo "   -> Chứng chỉ SSL trong certs/ đã tồn tại. Bỏ qua."
fi

# 4. Kiểm tra môi trường Python
echo "🐍 [4/4] Kiểm tra môi trường Python..."
if command -v python3 &> /dev/null; then
    PY_VER=$(python3 --version)
    echo "   -> Tìm thấy: $PY_VER"
else
    echo "   -> CẢNH BÁO: Chưa phát hiện Python 3 trong PATH!"
fi

echo "=========================================================="
echo "✅ KHỞI TẠO HỆ THỐNG THÀNH CÔNG!"
echo ""
echo "👉 BƯỚC TIẾP THEO:"
echo "   1. Mở file 'config.json' và điền API Key (9router, Groq, Telegram...)"
echo "   2. Cài đặt thư viện: pip install -r requirements.txt"
echo "   3. Khởi chạy hệ thống: python3 main.py"
echo "   4. Truy cập giao diện:"
echo "      - Web Portal:  https://localhost"
echo "      - Standby HUD: https://localhost/hud"
echo "      - ROI Board:   https://localhost/roi"
echo "=========================================================="
