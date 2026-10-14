<div align="center">

# 🤖 VN-MateAI
### Nền Tảng Trợ Lý AI Vận Hành Doanh Nghiệp Toàn Diện & Robot Thông Minh

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB.svg?logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.135.4-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Docker Compose](https://img.shields.io/badge/Docker%20Compose-v2-2496ED.svg?logo=docker&logoColor=white)](#-cài-đặt-nhanh-quick-start)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1.svg?logo=postgresql&logoColor=white)](#-dữ-liệu--hạ-tầng)
[![Redis](https://img.shields.io/badge/Redis-7-DC382D.svg?logo=redis&logoColor=white)](#-dữ-liệu--hạ-tầng)
[![Hardware](https://img.shields.io/badge/Hardware-ESP32--S3%20Xiaozhi-E7352C.svg?logo=espressif&logoColor=white)](#-robot-để-bàn-esp32-s3)

**VN-MateAI** là giải pháp trợ lý trí tuệ nhân tạo (AI Assistant) toàn diện phục vụ quản trị, vận hành CNTT và điều hành doanh nghiệp tự động hoá. Hệ thống kết nối đa kênh linh hoạt: từ **Web Portal**, **Màn hình tác chiến HUD**, **Telegram Bot**, **Agent máy trạm Windows** cho đến **Robot để bàn thông minh ESP32-S3**.

[Tính Năng](#-tính-năng-nổi-bật) • [Kiến Trúc](#-kiến-trúc-hệ-thống) • [Cài Đặt Nhanh](#-cài-đặt-nhanh-quick-start) • [Cấu Hình](#-cấu-hình) • [Bảo Mật](#-an-toàn--quản-trị-ai-zero-trust) • [Giấy Phép](#-giấy-phép--bản-quyền)

</div>

---

## 🌟 Giới Thiệu

Trong môi trường doanh nghiệp hiện đại, việc theo dõi hạ tầng máy chủ, kiểm tra số liệu ERP, quản lý phân quyền và điều khiển máy trạm thường phân mảnh và tiêu tốn nhiều thời gian. **VN-MateAI** được xây dựng nhằm giải quyết bài toán đó thông qua một nền tảng AI hợp nhất:

- 🗣️ **Giao tiếp tự nhiên bằng Tiếng Việt**: Xử lý giọng nói thời gian thực với độ trễ thấp, hỗ trợ ngắt lời tự nhiên (full-duplex thoại).
- ⚙️ **Thực thi hành động thực tế**: Không chỉ dừng lại ở trò chuyện, VN-MateAI kết nối trực tiếp với hơn **100+ kỹ năng (skills)** để tra cứu dữ liệu, kiểm tra server, gửi báo cáo, điều khiển máy tính từ xa.
- 🛡️ **Quản trị rủi ro nghiêm ngặt (Human-in-the-loop)**: Mọi thao tác tác động hệ thống đều đi qua cổng kiểm duyệt phân quyền (RBAC/ABAC), xếp hạng rủi ro (L0–L4) và yêu cầu phê duyệt từ cấp quản lý trước khi chạy.
- 📦 **Triển khai 1-Click độc lập**: Đóng gói hoàn chỉnh trên Docker Compose, không phụ thuộc dịch vụ đám mây bắt buộc, dữ liệu doanh nghiệp nằm trọn vẹn tại máy chủ nội bộ.

---

## ✨ Tính Năng Nổi Bật

### 🎙️ 1. Đường Ống Thoại Tiếng Việt Thời Gian Thực (Full-Duplex Voice)
- **STT Offline / Online**: Nhận dạng giọng nói tiếng Việt bằng **faster-whisper** chạy cục bộ kết hợp bộ lọc âm thanh **Silero VAD**, có phương án dự phòng đám mây (Groq).
- **TTS mượt mà & Truyền phát phân đoạn**: Tích hợp Edge-TTS chất lượng cao, Vbee, ElevenLabs. Tách câu thông minh để đọc ngay khi mô hình LLM vừa sinh dữ liệu, độ trễ phản hồi cực thấp.
- **Barge-in (Ngắt lời tức thì)**: Khi người dùng nói lệnh mới, hệ thống tự động hủy lượt đọc cũ để lắng nghe ngay lập tức.
- **Wake Word đa thiết bị**: Hỗ trợ gọi trợ lý bằng từ đánh thức thông minh (*"Ly Ly"*, *"MateAI"*).

### 🤖 2. Robot Để Bàn ESP32-S3 (Xiaozhi)
- Giao tiếp hai chiều với robot phần cứng ESP32-S3 qua giao thức WebSocket IoT độc lập (cổng `8000`).
- Hiển thị cảm xúc, trạng thái lắng nghe, phản hồi âm thanh trực tiếp tại bàn làm việc của nhân viên hoặc phòng họp điều hành.

### 💻 3. Agent Tự Động Hóa Máy Trạm Windows (`client_agent`)
- Ứng dụng chạy nền gọn nhẹ (`VNMateAgent.exe`), không đòi hỏi cài đặt môi trường Python phức tạp trên từng máy client.
- **Khả năng tự động hóa mạnh mẽ**:
  - Điều khiển tác vụ: mở ứng dụng, điều chỉnh âm lượng, điều khiển media, clipboard, chạy lệnh PowerShell có kiểm soát.
  - Tự động hóa bảng tính Excel qua Windows COM interface.
  - Giám sát sức khỏe máy trạm: chụp màn hình hỗ trợ kỹ thuật, kiểm tra tiến trình, kết nối mạng và thiết bị ngoại vi.
  - Giao diện popup thông báo nhắc việc và HUD mini nổi trên màn hình.

### 🛡️ 4. An Toàn & Quản Trị AI (Zero-Trust Governance)
- **Policy Engine 5 lớp**: Kill-switch toàn cục → Bộ lọc công cụ cấm → Phân quyền vai trò (RBAC) → Phân quyền ngữ cảnh (ABAC theo phòng ban & cấp bảo mật) → Đánh giá mức rủi ro.
- **Phân loại rủi ro L0–L4**:
  - *L0–L2 (Đọc dữ liệu, kiểm tra)*: Tự động phê duyệt và thực thi.
  - *L3 (Ghi, sửa đổi, xóa, cấu hình)*: Tạm giữ vào hàng đợi phê duyệt, gửi thông báo đến người có thẩm quyền qua Web Portal hoặc Telegram.
  - *L4 (Lệnh nhạy cảm cao)*: Yêu cầu ủy quyền định danh có giới hạn thời gian.
- **Chế độ khẩn cấp (Emergency Lockdown)**: Khi phát hiện AI thao tác dồn dập hoặc bất thường, hệ thống tự động khóa chế độ ghi, chuyển toàn bộ sang chỉ đọc.
- **Nhật ký Audit chống giả mạo (Tamper-proof)**: Bảng `audit_logs` hoạt động theo mô hình *Append-Only*, mỗi bản ghi chứa chuỗi băm mật mã (`prev_hash`, `row_hash`) và trigger cơ sở dữ liệu ngăn chặn hoàn toàn việc sửa/xóa.
- **Bảo mật dữ liệu cá nhân (Privacy Engine)**: Tự động che giấu CCCD, Số điện thoại, Lương, Mật khẩu theo cấp độ quyền hạn người dùng trước khi hiển thị hoặc gửi tới LLM.

### 📊 5. Giao Diện Điều Hành Trực Quan
- **Web Portal (`https://<host>/`)**: Trung tâm điều khiển toàn diện cho người dùng và quản trị viên: hội thoại văn bản & giọng nói, hàng đợi phê duyệt, quản lý danh bạ nhân sự, thiết bị IoT, kho tri thức (RAG) và cấu hình hệ thống.
- **Màn hình tác chiến HUD (`https://<host>/hud`)**: Thiết kế dạng Dark Mode tối ưu cho màn hình lớn treo tường phòng điều hành: hiển thị telemetry máy chủ, trạng thái hệ sinh thái, lệnh thoại trực ban và nút duyệt nhanh.
- **Bảng phân tích giá trị ROI (`/roi`)**: Đo lường hiệu quả vận hành và số giờ công tiết kiệm được tính toán từ dữ liệu thực tế.

---

## 🏛️ Kiến Trúc Hệ Thống

```mermaid
flowchart TB
    subgraph Clients["🌐 KÊNH TƯƠNG TÁC NGƯỜI DÙNG"]
        Portal["🖥️ Web Portal (HTTPS :443 /)"]
        HUD["📊 Màn hình HUD Tác Chiến (/hud)"]
        TG["✈️ Telegram Bot (Cảnh báo & Duyệt lệnh)"]
        Robot["🤖 Robot Để Bàn ESP32-S3 (IoT :8000)"]
        Agent["💻 Agent Máy Trạm Windows (/ws/client)"]
    end

    subgraph Core["🧠 VN-MATEAI RUNTIME (FastAPI + Python 3.11)"]
        Auth["🔐 Xác thực & Định danh (JWT · Device Token)"]
        VoiceEngine["🎙️ Pipeline Thoại (faster-whisper + VAD + Edge-TTS)"]
        AgentLoop["🔄 Vòng Lặp Agent (LLM ReAct Loop)"]
        PolicyEngine["🚦 Policy Engine (Kill Switch · RBAC · ABAC · Risk Gate)"]
        SkillRegistry["⚙️ Kho Kỹ Năng (DevOps, AD/LDAP, ERP, Windows, Office)"]
        ApprovalQueue["⏳ Hàng Đợi Duyệt Lệnh (Human-in-the-loop)"]
        PrivacyMask["🎭 Bộ Lọc Dữ Liệu Nhạy Cảm (Privacy Masking)"]
        AuditLedger["🧾 Sổ Nhật Ký Bất Biến (Hash-chained Audit Log)"]
    end

    subgraph LLMGateways["🌐 CỔNG KẾT NỐI MÔ HÌNH NGÔN NGỮ"]
        LLM["OpenAI / DeepSeek / Gemini / OpenRouter / Ollama / vLLM"]
    end

    subgraph Infra["🗄️ HẠ TẦNG LƯU TRỮ & DỊCH VỤ"]
        PG[("🗄️ PostgreSQL 16\n(Dữ liệu nghiệp vụ & Audit)")]
        Redis[("⚡ Redis 7\n(Rate limit, Session & Cache)")]
        S3[("📦 S3 / SeaweedFS\n(Tài liệu RAG & Sao lưu)")]
        OTel["📡 OpenTelemetry\n(Tracing & Metrics)"]
    end

    Clients --> Auth
    Auth --> VoiceEngine
    Auth --> AgentLoop
    VoiceEngine --> AgentLoop
    AgentLoop <--> LLM
    AgentLoop --> PolicyEngine
    PolicyEngine -->|Rủi ro L3/L4| ApprovalQueue
    ApprovalQueue -->|Quản trị viên duyệt| SkillRegistry
    PolicyEngine -->|An toàn L0-L2| SkillRegistry
    SkillRegistry --> PrivacyMask
    SkillRegistry --> AuditLedger
    AuditLedger --> PG
    PolicyEngine -.Đếm tần suất & Khoá.- Redis
    Core -.Tài liệu RAG.- S3
    Core -.Truy vết phân tán.- OTel
```

---

## 🚀 Cài Đặt Nhanh (Quick Start)

### Yêu Cầu Phần Cứng & Môi Trường
- **Hệ điều hành**: Linux (Ubuntu 22.04/24.04, Debian 12), macOS (Apple Silicon / Intel), hoặc Windows 10/11 / Server.
- **Docker**: Docker Engine + Docker Compose v2 (hoặc Docker Desktop).
- **Tài nguyên tối thiểu**: 4 Cores CPU, 8 GB RAM, 20 GB dung lượng ổ cứng khả dụng.
- **Cổng mạng**: Cần mở cổng `443` (Web Portal HTTPS) và `8000` (Giao thức IoT cho Robot).
- **Mô hình AI**: Có sẵn URL, API Key của nhà cung cấp LLM tương thích chuẩn OpenAI (OpenAI, DeepSeek, Google Gemini, OpenRouter, hoặc Proxy nội bộ).

---

### Phương Pháp 1: Cài Đặt Tự Động Bằng 1 Câu Lệnh (Khuyên Dùng)

#### Trên Linux / macOS:
```bash
# Clone dự án về máy
git clone https://github.com/lichpppp/VNMateAI.git
cd VNMateAI

# Chạy trình cài đặt tự động
sudo ./install.sh        # Trên Linux
# hoặc:
bash install.sh          # Trên macOS (Cấp tối thiểu 8GB RAM trong Docker Desktop)
```

#### Trên Windows (PowerShell với quyền Administrator):
```powershell
# Clone dự án về máy
git clone https://github.com/lichpppp/VNMateAI.git
cd VNMateAI

# Chạy script cài đặt tự động
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

> **Trình cài đặt sẽ tự động**:
> 1. Tự sinh tệp cấu hình `.env` với các mật khẩu ngẫu nhiên an toàn tuyệt đối (cho `admin`, `PostgreSQL`, `Redis`).
> 2. Dò tìm địa chỉ IP mạng nội bộ (LAN) của máy chủ để thuận tiện kết nối từ máy trạm và robot.
> 3. Hỗ trợ nhập kết nối Mô hình AI (hoặc bấm Enter để cấu hình trực quan trên Web Portal sau).
> 4. Khởi chạy toàn bộ hệ thống bằng Docker Compose và kiểm tra trạng thái sức khỏe dịch vụ cho tới khi sẵn sàng.

---

### Phương Pháp 2: Triển Khai Thủ Công Bằng Docker Compose

1. **Khởi tạo tệp môi trường từ mẫu**:
   ```bash
   cp env.example .env
   ```
2. **Điền các thông tin trong `.env`**:
   - Thiết lập `VNMATE_ADMIN_PASSWORD`, `VNMATE_PG_PASSWORD`, `VNMATE_REDIS_PASSWORD`.
   - Cung cấp `VNMATE_LLM_BASE_URL`, `VNMATE_LLM_API_KEY`, `VNMATE_LLM_MODEL`.
3. **Khởi động dịch vụ**:
   ```bash
   docker compose up -d --build
   ```
4. **Xem nhật ký hoạt động**:
   ```bash
   docker compose logs -f app
   ```

---

## 🔑 Truy Cập & Sử Dụng

Sau khi quá trình khởi động hoàn tất:

| Giao diện | Địa chỉ truy cập | Ghi chú |
|---|---|---|
| **Web Portal** | `https://<IP_MÁY_CHỦ>/` | Trung tâm quản trị, điều hành, chat & gọi thoại |
| **Màn hình HUD** | `https://<IP_MÁY_CHỦ>/hud` | Màn hình tác chiến trực ban giám sát |
| **API Healthcheck** | `https://<IP_MÁY_CHỦ>/livez` | Kiểm tra trạng thái hoạt động của hệ thống |

- **Tài khoản mặc định**: `admin`
- **Mật khẩu**: Xem thông báo in ra ở cuối script cài đặt hoặc kiểm tra biến `VNMATE_ADMIN_PASSWORD` trong tệp `.env`.
- ⚠️ *Lưu ý*: Lần đầu truy cập, trình duyệt sẽ cảnh báo chứng chỉ HTTPS tự ký (Self-signed certificate). Vui lòng chọn **"Advanced" (Nâng cao) -> "Proceed to..." (Vẫn tiếp tục)**. Để dùng chứng chỉ chính thức (Let's Encrypt / CA doanh nghiệp), bạn chỉ cần copy `server.crt` và `server.key` vào thư mục `certs/`.

---

## ⚙️ Cấu Hình Hệ Thống

Toàn bộ cấu hình chuyên sâu có thể điều chỉnh trực tiếp trên giao diện **Web Portal -> Quản Lý Trợ Lý AI**:

```
Portal -> Cài Đặt Hệ Thống
 ├── 🧠 Bộ Não (LLM): Cấu hình URL, API Key, Model chính, Model dự phòng
 ├── 🎙️ Giọng Đọc (TTS): Chọn Edge-TTS (tiếng Việt tự nhiên), Vbee, ElevenLabs
 ├── ✈️ Telegram Bot: Điền Bot Token & Chat ID để nhận cảnh báo và duyệt lệnh từ xa
 ├── 🛡️ Bảo Mật: Cấp phát Token định danh riêng cho từng Robot ESP32
 ├── 👥 Phân Quyền & Privacy: Cấu hình quy tắc che dữ liệu (CCCD, SĐT, tài chính...)
 └── 💾 Sao Lưu: Thiết lập cơ chế sao lưu định kỳ
```

> **Tính năng tự động mã hóa**: Toàn bộ API Key và mật khẩu lưu trên máy chủ được tự động mã hóa AES đối xứng thông qua khóa bí mật `certs/config_secret.key`. Ngay cả khi tệp cấu hình bị đọc trộm, các khóa API của doanh nghiệp vẫn an toàn.

---

## 💻 Cài Đặt Agent Máy Trạm Windows

Để trợ lý AI có thể hỗ trợ điều khiển ứng dụng, chụp màn hình trợ giúp, tự động hóa Office Excel trên từng máy tính làm việc:
1. Mở Web Portal bằng địa chỉ IP LAN của máy chủ (ví dụ: `https://192.168.1.10/`).
2. Bấm vào nút **"Tải Agent"** trên thanh điều hướng.
3. Giải nén gói zip vừa tải trên máy tính Windows người dùng và bấm đúp vào `VNMateAgent.exe`.
4. Agent sẽ tự động kết nối về máy chủ, xác thực chứng chỉ và hiển thị biểu tượng tại khay hệ thống (System Tray).

---

## 🛠️ Vận Hành & Bảo Trì

### Các Lệnh Thường Dùng

```bash
# Xem trạng thái các container
docker compose ps

# Xem nhật ký trực tiếp của ứng dụng
docker compose logs -f app

# Dừng hệ thống
docker compose down

# Khởi động lại ứng dụng
docker compose restart app

# Sao lưu nhanh toàn bộ CSDL và dữ liệu cấu hình
./backup.sh
```

### Sao Lưu & Phục Hồi Dữ Liệu
Script `backup.sh` sẽ tự động sao lưu:
- Cơ sở dữ liệu PostgreSQL (`backups/db-<timestamp>.sql.gz`)
- Dữ liệu tri thức, cấu hình mã hóa và chứng chỉ (`backups/appdata-<timestamp>.tgz`)

**Phục hồi từ bản sao lưu:**
```bash
docker compose down
docker volume rm vnmateai_pgdata vnmateai_appdata
docker compose up -d postgres && sleep 10

# Khôi phục CSDL
gunzip -c backups/db-<timestamp>.sql.gz | docker compose exec -T postgres psql -U vnmate -d vnmate

# Khôi phục dữ liệu cấu hình
docker run --rm --user 0 --entrypoint tar -v vnmateai_appdata:/data -v "$PWD/backups:/b" vnmateai/app:1.0.0 \
  xzf /b/appdata-<timestamp>.tgz -C /data

# Khởi chạy lại toàn bộ dịch vụ
docker compose up -d
```

---

## 🔒 Quy Chuẩn An Toàn & Bảo Mật

1. **Không Hardcode Khóa Bí Mật**: Mã nguồn dự án tuân thủ nghiêm ngặt nguyên tắc Zero-Secret trong Git. Tệp `.env` cục bộ và các chứng chỉ `certs/` được bảo vệ tự động bởi `.gitignore`.
2. **PostgreSQL & Redis Không Mở Cổng Ra Ngoài**: Trong Docker Compose, cổng 5432 và 6379 chỉ giao tiếp trong mạng nội bộ Docker bridge network, ngăn chặn triệt để nguy cơ dò quét từ bên ngoài.
3. **Audit Log Toàn Vẹn Tuyệt Đối**: CSDL sử dụng Database Trigger chặn đứng mọi lệnh `UPDATE` hoặc `DELETE` trên bảng lịch sử tác vụ `audit_logs`. Hệ thống cung cấp API đối soát chuỗi băm:
   `GET /api/v1/security/audit-logs/verify`

---

## 🤝 Đóng Góp Phát Triển (Contributing)

Chúng tôi hoan nghênh mọi đóng góp từ cộng đồng! Nếu bạn muốn đóng góp tính năng mới hoặc báo cáo lỗi:
1. Fork dự án trên GitHub.
2. Tạo nhánh tính năng mới (`git checkout -b feature/tinh-nang-moi`).
3. Commit các thay đổi (`git commit -m 'Thêm kỹ năng tích hợp mới'`).
4. Push lên nhánh của bạn (`git push origin feature/tinh-nang-moi`).
5. Mở một **Pull Request** trên GitHub.

Vui lòng xem chi tiết tại [CONTRIBUTING.md](app/CONTRIBUTING.md).

---

## 📄 Giấy Phép & Bản Quyền

Dự án được phân phối dưới giấy phép mã nguồn mở **Apache License 2.0**. Xem chi tiết tại [LICENSE](app/LICENSE) và [NOTICE](app/NOTICE).

- **Tác giả & Chủ sở hữu bản quyền**: **Dương Thanh Lịch** ([thanhlich.duong@gmail.com](mailto:thanhlich.duong@gmail.com))
- **Kho lưu trữ chính thức**: [https://github.com/lichpppp/VNMateAI](https://github.com/lichpppp/VNMateAI)

---

<div align="center">
⭐ Nếu bạn thấy dự án hữu ích, hãy tặng một <b>Star</b> trên GitHub để ủng hộ đội ngũ phát triển! ⭐
</div>
