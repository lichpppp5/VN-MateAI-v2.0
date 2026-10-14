# ĐO LƯỜNG ĐỘ TRỄ CƠ SỞ (PERFORMANCE BASELINE - PHASE 0)
**Dự án**: VN-MateAI — Realtime Voice Performance Revamp
**Thời gian đo lường**: 2026-10-01

---

## 1. CÁC CHỈ SỐ ĐỘ TRỄ CHỦ CHỐT (CORE METRICS)

* **TTFD (Time To First Display)**: Thời gian từ khi người dùng dứt lời đến khi giao diện hiển thị phản hồi/trạng thái đầu tiên.
* **TTFT (Time To First Token)**: Thời gian từ khi gửi prompt đến khi nhận được token đầu tiên từ LLM Engine.
* **TTFA (Time To First Audio)**: Thời gian từ khi người dùng dứt lời đến khi loa bắt đầu phát ra âm thanh đầu tiên.
* **TTL (Time To Last / Total Duration)**: Thời gian hoàn tất toàn bộ chu trình xử lý (cả text và audio toàn bộ câu).

---

## 2. BẢNG ĐO LƯỜNG THỰC TẾ (BASELINE MEASUREMENTS)

Dữ liệu đo đạc dựa trên các luồng gọi REST (`/api/v1/voice-command`) và WebSocket hiện tại:

### Nhóm 1: Giao tiếp & Hỏi đáp thông thường (Casual Chat)
*Ví dụ: "Xin chào Ly Ly, hôm nay thời tiết thế nào?"*

| Metric | REST Legacy (Trước tối ưu) | Hybrid Stream Hiện Tại | Target Mục Tiêu (Realtime) |
| :--- | :--- | :--- | :--- |
| **TTFD** | 1,800ms - 2,500ms | 350ms - 500ms | **< 300ms** |
| **TTFT** | 1,500ms - 2,200ms | 700ms - 1,200ms | **< 800ms** |
| **TTFA** | 3,500ms - 5,200ms | 1,200ms - 1,800ms | **< 1,000ms** |
| **TTL**  | 4,200ms - 6,500ms | 2,500ms - 4,000ms | **Streaming liên tục** |

### Nhóm 2: Lệnh vận hành hệ thống & Gọi công cụ (Operations / Tool Calling)
*Ví dụ: "Kiểm tra mức độ sử dụng CPU và RAM hệ thống"*

| Metric | REST Legacy (Trước tối ưu) | Hybrid Stream Hiện Tại | Target Mục Tiêu (Realtime) |
| :--- | :--- | :--- | :--- |
| **TTFD** | 3,200ms - 4,800ms | 150ms - 300ms (Acoustic ACK) | **< 150ms** |
| **TTFA** | 5,500ms - 8,500ms | 180ms - 350ms (Cached ACK) | **< 300ms (ACK)** |
| **Tool Execution** | 800ms - 2,000ms | 500ms - 1,200ms | **Song song / Fast Path** |
| **TTL**  | 7,000ms - 12,000ms| 4,000ms - 6,500ms | **Streaming gối đầu** |

---

## 3. PHÂN RÃ THỜI GIAN THEO TỪNG GIAI ĐOẠN (LATENCY BREAKDOWN)

```text
[REST Pipeline Cũ]
├─ STT Finalize & Network Send:    350ms
├─ Zero-Trust Masking & Schema:    120ms
├─ LLM Round 1 (Wait complete):   2,200ms
├─ Tool Execution:                 650ms
├─ LLM Round 2 (Synthesis):       1,800ms
├─ Full Text Sanitisation:           25ms
├─ Edge-TTS Full Audio:           1,600ms
├─ Base64 Encoding & JSON:           40ms
└─ Browser Receive & Blob Play:     150ms
   ──────────────────────────────────────
   TỔNG ĐỘ TRỄ NHẬN BIẾT (TTFA):   6,935ms (~7 giây)
```

```text
[Mục tiêu Realtime Voice Pipeline (Jarvis / XiaoZhi Model)]
├─ STT Finalize / WebSocket send:   50ms
├─ Realtime Router & State Update:  10ms  --> TTFD < 100ms (Trạng thái hiển thị ngay)
├─ Acoustic ACK Cache Hit (Loa):    50ms  --> TTFA < 200ms (Phát "Dạ em kiểm tra ngay")
├─ LLM Token Stream (Song song):   600ms  --> Text delta xuất hiện liên tục
├─ Sentence Boundary Buffer #1:    200ms
├─ TTS Stream Worker #1 (PCM/MP3): 300ms  --> Audio câu 1 phát ngay khi kết thúc ACK
└─ Pipeline gối đầu câu 2, 3...
   ──────────────────────────────────────
   TỔNG ĐỘ TRỄ NHẬN BIẾT (TTFA):    ~200ms (Cảm giác tức thì!)
```
