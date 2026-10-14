# DANH SÁCH ĐỐI TƯỢNG RÀ SOÁT & DỌN DẸP (CLEANUP CANDIDATES)

> **Đã được thay thế (Phase 0, 2026-10-01):** xem `docs/architecture/legacy-candidates.md`. Nhiều mục dưới đây tham chiếu file không còn tồn tại (`core/api_voice_stream.py`, `scratch/`, `vnmateai.db.bak-*`).

**Dự án**: VN-MateAI  
**Mục tiêu**: Loại bỏ code thừa, trùng lặp, tệp rác lịch sử nhưng bảo tồn 100% chức năng hoạt động.  
**Nguyên tắc**: Xác định rõ Canonical Implementation trước khi sửa đổi, kiểm chứng tất cả liên kết tĩnh và động.

---

## 1. PHÂN LOẠI & MỨC ĐỘ TIN CẬY (CONFIDENCE LEVEL)

| Nhóm | Số lượng | Mô tả hành động |
| :--- | :---: | :--- |
| **KEEP** | 92 files | Code chuẩn, đang hoạt động trực tiếp, là Single Source of Truth |
| **MERGE** | 2 files | Trùng lặp nội dung 100% giữa `skills/` và `core/skills/`, cần quy về 1 nguồn |
| **REPLACE** | 1 file | Module prototype cũ (`api_voice_stream.py`) đã có module mới thay thế (`realtime_voice_ws.py`) |
| **DELETE** | 17 files | File backup tạm (`.bak`, `.before-*`), scratch scripts từ tháng 9/2026 không có runtime reference |
| **ARCHIVE** | 3 files | File thử nghiệm cũ trong `scratch/` có giá trị đối chiếu nhưng không nằm trong runtime |
| **REVIEW** | 2 files | Cần đánh giá kỹ trước khi chạm vào (ví dụ WebSocket compatibility fallback) |

---

## 2. CHI TIẾT CÁC ĐỐI TƯỢNG (CANDIDATE AUDIT)

### Candidate 1: `scratch/` HTML & JS Backups
- **Files**:
  - `scratch/index.html.bak` (405 KB)
  - `scratch/index.before-merge.html` (409 KB)
  - `scratch/index.before-merge-cc.html` (409 KB)
  - `scratch/index.before-gom-logs.html` (408 KB)
  - `scratch/index.before-dynform.html` (409 KB)
  - `scratch/app.before-dynform.js` (565 KB)
- **Category**: Temporary Backups / Outdated Snapshot
- **Current usage**: Không có bất kỳ import, dynamic loading, server route hay runtime usage nào.
- **References**: Zero. (Đã kiểm tra qua ripgrep toàn bộ repository).
- **Replacement**: `web/index.html` và `web/app.js` đang hoạt động trực tiếp.
- **Reason**: Các tệp sao lưu thủ công từ ngày 29/09/2026 khi gộp giao diện Command Center. Chiếm hơn 2.6 MB dung lượng rác.
- **Confidence**: **HIGH**
- **Action**: **DELETE**

---

### Candidate 2: `scratch/` One-off Migration Scripts
- **Files**:
  - `scratch/merge_cc_into_dash.py`
  - `scratch/merge_tabs.py`
  - `scratch/move_approvals.py`
  - `scratch/move_logs.py`
  - `scratch/gom_logs_recent.py`
  - `scratch/quét_rò_rỉ.py`
  - `scratch/security_queue.py`
  - `scratch/nul_check.cjs`
- **Category**: One-off Migration Utility Scripts
- **Current usage**: Các script python/cjs dùng 1 lần trong giai đoạn tái cấu trúc tab giao diện cuối tháng 9.
- **References**: Zero runtime imports.
- **Replacement**: Không cần thay thế, các tab đã được hợp nhất hoàn tất trong `web/index.html`.
- **Reason**: Đã hoàn thành nhiệm vụ lịch sử, không tham gia vào vòng đời vận hành hệ thống.
- **Confidence**: **HIGH**
- **Action**: **DELETE**

---

### Candidate 3: Root Database Backup `vnmateai.db.bak-20260929-101027`
- **File**: `vnmateai.db.bak-20260929-101027` (524 KB)
- **Category**: Database Snapshot
- **Current usage**: Không có tiến trình nào kết nối hoặc đọc ghi.
- **References**: Chỉ được nhắc đến trong `.gitignore` rule (`*.db.bak-*`).
- **Replacement**: `vnmateai.db` (Database chính đang mở và đồng bộ WAL).
- **Reason**: Bản backup trước khi dọn dữ liệu mẫu ở Phase 73.
- **Confidence**: **HIGH**
- **Action**: **DELETE** (hoặc chuyển ra thư mục backup ngoài source tree nếu cần lưu trữ).

---

### Candidate 4: `core/api_voice_stream.py` (Superceded Voice Prototype)
- **File**: `core/api_voice_stream.py` (21.8 KB)
- **Category**: Superseded Implementation
- **Current usage**:
  - Không được mount trong `core/server.py` (cả `/ws/voice` và `/ws/v1/voice-stream` đều trỏ sang `core/realtime_voice_ws.py`).
  - Được import bởi `tests/test_phase92_voice_stream_pipeline.py` (`_select_relevant_tools`).
  - Dòng 362 của `core/realtime_voice_ws.py` import `_select_relevant_tools` nhưng KHÔNG gọi sử dụng (dư thừa import).
- **Replacement**: `core/realtime_voice_ws.py` (Realtime standardized event protocol) kết hợp `core/dynamic_skill_router.py` (Phase 8 Dynamic Skill Loading).
- **Reason**: Đây là bản prototype ban đầu trước khi chia tách thành 13 Phase chuẩn hóa. Hiện tại logic đã được hoàn thiện trong `realtime_voice_ws.py`, `dynamic_skill_router.py`, `sentence_buffer.py` và `tts_queue_pipeline.py`.
- **Confidence**: **HIGH**
- **Action**: **REPLACE / CLEANUP CALLERS**:
  1. Loại bỏ import thừa tại dòng 362 `core/realtime_voice_ws.py`.
  2. Cập nhật `tests/test_phase92_voice_stream_pipeline.py` chuyển sang kiểm tra `dynamic_skill_router.get_tools_for_query` thay vì hàm cũ.
  3. Di chuyển `core/api_voice_stream.py` sang `_archive/` hoặc giữ dạng fallback có đánh dấu rõ ràng.

---

### Candidate 5: Duplicate File `skills/integration_tools.py` vs `core/skills/integration_tools.py`
- **Files**:
  - `skills/integration_tools.py` (19,929 bytes)
  - `core/skills/integration_tools.py` (19,929 bytes)
- **Category**: Duplicate Implementation (100% Identical)
- **Current usage**:
  - `core/plugin_manager.py` quét `settings.SKILLS_DIR` (`_PROJECT_ROOT / "skills"`).
  - Tất cả các kỹ năng khác trong `skills/` (như `robotics_tools.py`, `file_system.py`, `ai_delegation.py`) đều đặt phần thân logic ở `core/skills/` và tạo file facade gọn nhẹ tại `skills/` để re-export.
  - Riêng `integration_tools.py` bị copy nguyên vẹn sang cả 2 nơi.
- **Replacement**: Giữ code gốc tại `core/skills/integration_tools.py` và chuyển `skills/integration_tools.py` thành facade re-export đồng nhất như các skill khác.
- **Confidence**: **HIGH**
- **Action**: **MERGE / STANDARDIZE FACADE**

---

### Candidate 6: Unused Imports in Active Core Files
- **Files**:
  - `core/realtime_voice_ws.py`: dòng 362 `from core.api_voice_stream import _select_relevant_tools` (không bao giờ được gọi).
  - `core/llm_engine.py`: các import tạm thời đã được thay thế bằng Persistent Pool.
- **Category**: Unused / Dead Imports
- **Confidence**: **HIGH**
- **Action**: **REMOVE OBSOLETE IMPORTS**

---

## 3. DANH SÁCH BẢO VỆ TUYỆT ĐỐI (CANONICAL KEEP LIST)

Các thành phần sau **TUYỆT ĐỐI KHÔNG ĐƯỢC XÓA HOẶC LÀM SUY GIẢM**:

1. **Security & Zero-Trust**:
   - `core/zero_trust.py`: Đánh giá rủi ro động đa nhân tố.
   - `core/safety_guard.py`: Masking dữ liệu nhạy cảm & kiểm tra tĩnh mã nguồn Python AST.
   - `core/security_guard.py`: Phân quyền RBAC doanh nghiệp & ghi nhật ký kiểm toán bất biến.
   - `core/auth_manager.py`: Xác thực JWT token, mã hóa bcrypt mật khẩu.

2. **Realtime Voice & Audio Pipeline**:
   - `core/realtime_voice_ws.py`: Gateway WebSocket chính cho toàn bộ tính năng thoại.
   - `core/fast_command_router.py`: Router lệnh phản xạ tất định (< 2ms).
   - `core/dynamic_skill_router.py`: Router nạp công cụ động theo 10 miền nghiệp vụ.
   - `core/agent_voice_loop.py`: Vòng lặp đàm thoại tối ưu không qua LLM Round 2.
   - `core/connection_pool.py`: HTTP/2 Persistent Pools (300s keep-alive socket).
   - `core/audio/sentence_buffer.py`: Bộ đệm tách câu thông minh tiếng Việt.
   - `core/audio/tts_queue_pipeline.py`: Worker hàng đợi TTS gối đầu tuần tự.
   - `core/audio/tts_stream_engine.py`: Động cơ tổng hợp giọng nói HTTP streaming.
   - `core/audio/acoustic_ack_catalog.py`: Kho 12 câu đệm phản hồi tức thì 0ms.
   - `core/audio/binary_transport.py`: Giao thức khung nhị phân Zero Base64 overhead.
   - `core/audio_processor.py`: Nhận dạng giọng nói Whisper STT & SileroVAD.

3. **Client Agent & Template**:
   - `client_agent/`: Mã nguồn máy trạm chạy trong mạng LAN.
   - `client_template/`: Gói mẫu được Web Portal đóng gói động khi người dùng tải phần mềm agent.
