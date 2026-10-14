# Pre-built Offline Docker Images / Ảnh Docker Dựng Sẵn

Thư mục này dùng để chứa các gói ảnh Docker đóng gói sẵn dạng file `.tar` dành cho việc triển khai máy chủ độc lập (offline / air-gapped environment) mà không cần kéo image từ Docker Registry hoặc build lại từ mã nguồn:

- `vnmateai-1.0.0-images.tar`: Gói ảnh dành cho kiến trúc CPU x86_64 / amd64 (Intel, AMD).
- `vnmateai-1.0.0-arm64-images.tar`: Gói ảnh dành cho kiến trúc CPU ARM64 (Apple Silicon M1-M4, AWS Graviton, Raspberry Pi 5...).

### Lưu ý:
1. Do dung lượng mỗi tệp ảnh vượt quá giới hạn 100MB của GitHub (`> 1.6 GB`), các tệp `.tar` không được lưu trữ trực tiếp trong kho mã nguồn Git.
2. Bạn có thể tải các tệp tar dựng sẵn từ mục **Releases** trên GitHub hoặc tự build từ mã nguồn bằng lệnh:
   ```bash
   docker compose build app
   # Hoặc chạy trực tiếp:
   docker compose up -d --build
   ```
3. Khi có tệp `.tar` trong thư mục này, tập lệnh `install.sh` (Linux/macOS) hoặc `install.ps1` (Windows) sẽ tự động nạp image vào Docker mà không cần tải qua Internet.
