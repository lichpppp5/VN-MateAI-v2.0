# VN-MateAI — Bản Đồ Phụ Thuộc Mã Nguồn & Vòng Lặp Import (Dependency Graph & Coupling Analysis)

> **Đã cập nhật (Phase 0, 2026-10-01):** thực đo không có vòng import top-level; tính cả import trong hàm có 1 khối liên thông 37 module + 1 vòng 2 module (`sentence_buffer` ↔ `sentence_streamer`); 10 module lõi import ngược `core.server` (không tính `main.py`). Xem `current-vs-target.md` §6.

## I. TỔNG QUAN PHÂN TÍCH PHỤ THUỘC TẦNG `core/`

Phân tích tĩnh trên toàn bộ 87 tệp Python trong thư mục `core/` cho kết quả:

### 1. Top 10 Module Được Import Nhiều Nhất Trong Hệ Thống
| Tên Module | Số file phụ thuộc trực tiếp | Vai trò hiện tại | Định hướng tái cấu trúc |
| :--- | :---: | :--- | :--- |
| `core/config_loader.py` | **20 files** | Đọc cấu hình môi trường và file config | Chuyển thành `src/mateai/config/` tập trung |
| `core/plugin_manager.py`| **19 files** | Đăng ký và nạp tools | Chuyển vào `application/skills/` |
| `core/database.py`      | **14 files** | Khởi tạo bảng và execute raw SQL | Trừu tượng hóa qua Repository Ports |
| `core/connectors/`      | **11 files** | Kết nối AWS, OCI, ERP, Telegram | Đưa vào `infrastructure/connectors/` |
| `core/zero_trust.py`    | **10 files** | Kiểm tra quyền truy cập và rủi ro | Chuyển vào `infrastructure/security/` |
| `core/telegram_gateway.py` | **10 files** | Nhận và xử lý lệnh qua Telegram | Tách thành Interface Adapter riêng |
| `core/audio_cache.py`   | **9 files**  | Quản lý bộ đệm file MP3 | Chuyển vào `infrastructure/storage/` |
| `core/server.py`        | **8 files**  | FastAPI server và WebSocket | **Vi phạm phụ thuộc ngược (Inverted Coupling)** |
| `core/audio/`           | **8 files**  | Xử lý TTS và stream âm thanh | Chuyển vào `infrastructure/tts/` & `stt/` |
| `core/safety_guard.py`  | **7 files**  | Guard rails kiểm tra an toàn prompt | Tích hợp vào `domain/identity/` & `security/` |

---

## II. PHÂN TÍCH 24 VÒNG LẶP PHỤ THUỘC (CYCLIC IMPORT LOOPS)

Hệ thống hiện tại có **24 vòng lặp import** giữa các module trong `core/`. Đây là nguyên nhân dẫn đến nguy cơ lỗi `ImportError: cannot import name ...` khi khởi động hoặc reload module.

### Các vòng lặp nghiêm trọng điển hình:
1. **Vòng lặp giữa Web Server và Voice Controller**:
   `core/server.py` ➔ `core/voice_controller.py` ➔ `core/server.py`
   - *Nguyên nhân*: `server.py` gọi hàm voice control, trong khi `voice_controller.py` lại import `server` để phát broadcast status.
   - *Giải pháp*: `voice_controller` chỉ phát event (Domain Event), `server` (Interfaces) đăng ký lắng nghe event.

2. **Vòng lặp giữa Web Server và Orchestrator**:
   `core/server.py` ➔ `core/orchestrator.py` ➔ `core/server.py`
   - *Nguyên nhân*: `server.py` gọi bộ điều phối, nhưng `orchestrator.py` lại import trực tiếp FastAPI app instance hoặc WebSocket manager từ `server.py`.
   - *Giải pháp*: Dependency Inversion — `orchestrator` nằm ở Application layer, không biết `server.py` là gì.

3. **Vòng lặp giữa Plugin Manager và Dynamic Skill Router**:
   `core/plugin_manager.py` ➔ `core/dynamic_skill_router.py` ➔ `core/plugin_manager.py`
   - *Nguyên nhân*: `dynamic_skill_router` cần danh sách tool từ `plugin_manager`, còn `plugin_manager` lại gọi `dynamic_skill_router` để phân loại domain.
   - *Giải pháp*: Tách riêng `ToolRegistry` (lưu trữ danh sách) và `SkillResolver` (thuật toán phân loại theo intent).

---

## III. BẢN ĐỒ GIAO TIẾP VỚI CÁC THƯ VIỆN BÊN NGOÀI (EXTERNAL DEPENDENCIES)

| Thư viện bên ngoài | Số module sử dụng | Đánh giá kiến trúc |
| :--- | :---: | :--- |
| `httpx` | 14 files | Cần dùng chung Connection Pool (`ConnectionPoolManager`) để tái sử dụng socket HTTP/2 Keep-Alive. |
| `fastapi` | 8 files | Hiện bị rải rác ngoài `server.py`. Cần gom toàn bộ vào `interfaces/http/`. |
| `sqlite3` | 6 files | Gọi trực tiếp SQLite cursor. Cần chuyển vào `infrastructure/database/sqlite_adapter.py`. |
| `edge_tts` | 3 files | Cần đóng gói vào `infrastructure/tts/edge_tts_adapter.py`. |
| `websockets` | 1 file | Cần chuẩn hóa qua WebSocket transport abstraction. |
| `redis` | 1 file | Đang dùng fallback in-memory, cần chuẩn hóa `RedisCacheAdapter`. |

---

## IV. ĐỊNH HƯỚNG CẮT ĐỨT PHỤ THUỘC (DECOUPLING STRATEGY)

Để loại bỏ hoàn toàn 24 vòng lặp trên:
1. **Tách Domain Events**: Mọi thông báo trạng thái từ Agent hoặc Voice loop được gửi qua một `EventBus` hoặc `Callback Hook`, không import ngược module điều khiển.
2. **Khởi tạo Dependency Injection Container**: Toàn bộ database adapter, LLM provider, và cache được khởi tạo ở điểm vào hệ thống (Composition Root) và truyền vào Application Use Cases.
