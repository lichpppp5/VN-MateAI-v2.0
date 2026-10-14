# Phương án nâng cấp `/admin/topology` — giám sát thời gian thực

Ngày 2026-10-04. Mục tiêu: sơ đồ phản ánh **đúng trạng thái thật**, thấy **từng bước của mỗi lượt xử lý** (thoại, tool, phê duyệt) khi đang diễn ra, và chỉ ra **thành phần hỏng + lý do** để xử lý sự cố.

## 1. Hiện trạng (rà 2026-10-04)

| # | Vấn đề | Bằng chứng |
|---|---|---|
| H1 | Trạng thái **viết cứng** — luôn "online / Active / OPERATIONAL" | `routers/system.py` `get_system_topology`; `api_admin.get_admin_topology` |
| H2 | **Số giả**: số máy trạm `max(thật, 17)`, "uptime 99.98%", "latency < 12ms", `activeAgents max(…, 4)` | `routers/system.py` |
| H3 | Connector M365 / eInvoice / Paperless / AWS báo "active" dù **chưa từng chạy thật** (owner-todo) | cả hai API |
| H4 | Thiếu thành phần thật: robot, HUD/portal đang kết nối, phiên thoại, máy trạm LAN, TTS / STT, CSDL, Telegram, hàng đợi phê duyệt, model LLM đang hỏng | — |
| H5 | Sự kiện thời gian thực chỉ có "tool đã chạy" (plugin_registry) + heartbeat máy trạm; **không có bước của lượt thoại**, lỗi, phê duyệt | `broadcast_topology_event` |
| H6 | **Mô phỏng lẫn với thật**: nút chạy luồng giả phát lên MỌI người xem dưới nhãn "[Real-time]"; mọi tài khoản (kể cả viewer) bơm được sự kiện giả qua WS `trigger` và `POST /topology/trigger` | `SystemCanvas.tsx`, `websockets.py`, `routers/system.py` |
| H7 | WS chào với `"active_nodes": 11` viết cứng | `websockets.py` |
| H8 | Hai API topology trùng nhau; `/api/v1/admin/topology` không còn ai gọi | grep `admin/`, `web/` |
| H9 | API quản trị (`/api/v1/admin/*`, kể cả xoá bộ đệm RAM) chỉ kiểm **đăng nhập**, không kiểm **vai trò** | `api_admin.py`, middleware |

## 2. Kiến trúc mục tiêu

```
 nguồn trạng thái thật                    TopologyService (application/operations/topology.py)
 health_monitor (CPU/RAM/9Router/DB/TG) ┐   snapshot(): nút + cạnh + trạng thái ok/degraded/down/off
 llm_provider.model_health()            │   + số đo + lý do + "từ lúc nào"
 voice_turn.recent_traces / trace_stats  ├─▶  events: vòng 300 sự kiện gần nhất
 xiaozhi_gateway (robot), realtime_hub   │   publish(event) ─▶ /ws/topology (đẩy ngay)
 client_orchestrator (máy trạm LAN)      │   snapshot đẩy định kỳ 2 s (chỉ khi có người xem)
 hitl_manager, plugin_manager, connectors┘
                                              ▼
 nguồn sự kiện bước xử lý:                Giao diện (admin/ Next.js + React Flow)
 VoiceTurnTrace (stt, router, llm, tts,     - nút: màu theo trạng thái, số đo thật, lý do
   xong / lỗi / huỷ) · tool_gate (tool bắt   - cạnh: sáng khi có sự kiện thật đi qua
   đầu / xong / lỗi / chờ duyệt) · HITL     - "Luồng trực tiếp": từng lượt, từng bước + ms
   (yêu cầu / duyệt / từ chối) · robot       - "Sự cố": thành phần không OK + lý do + từ lúc
   (gọi tên, nghe tiếp, tạm biệt) · đổi     - mô phỏng: CHỈ trên máy người bấm, gắn nhãn
   trạng thái thành phần                       "MÔ PHỎNG", không phát cho người khác
```

## 3. Các bước

| Bước | Nội dung | Giải quyết |
|---|---|---|
| T1 | `TopologyService.snapshot()` dựng từ trạng thái thật; một API `/api/v1/system/topology` (bố cục người dùng lưu chỉ giữ VỊ TRÍ, trạng thái luôn lấy thật); gỡ `/api/v1/admin/topology` | H1–H4, H8 |
| T2 | `publish()` sự kiện bước: VoiceTurnTrace, tool_gate, HITL, robot, đổi trạng thái thành phần; `GET /api/v1/system/topology/events` (vòng sự kiện để trang mới mở thấy ngay) | H5 |
| T3 | `/ws/topology`: đẩy sự kiện ngay + snapshot 2 s; bỏ lệnh `trigger` từ client; `POST /topology/trigger` chỉ admin, sự kiện gắn `simulated` | H6, H7 |
| T4 | Giao diện: nút theo trạng thái thật, panel "Luồng trực tiếp" + "Sự cố", mô phỏng cục bộ có nhãn | H1–H6 |
| T5 | API quản trị kiểm vai trò admin | H9 |
| T6 | Test + chạy thật: một lượt thoại / tool / lỗi hiện đúng bước, đúng trạng thái | — |

Nguyên tắc: không số giả — thiếu dữ liệu thì ghi "chưa có số đo"; một nguồn sự thật cho trạng thái; sự kiện là dữ liệu đo (không chứa nội dung lời nói đầy đủ — câu hỏi cắt ngắn, không token / mật khẩu).

## 4. Kết quả (2026-10-05)

T1–T6 xong.
- Giao diện mới `admin/components/topology/LiveTopology.tsx` thay `SystemCanvas` và 7 loại ô viết cứng (đã gỡ).
- Test: `tests/test_phase88_workflow_topology.py` (8), `test_websockets_require_login.py`, `test_public_endpoints_locked.py`. Toàn bộ pytest: 545 passed.

**Chạy thật** (máy chủ đã khởi động lại, lệnh REST "Kiểm tra CPU và RAM máy chủ", theo dõi qua `/ws/topology`):

| Thời điểm | Bước | Cạnh | ms |
|---|---|---|---|
| 09:04:55.845 | turn start / router | portal → voice | 0 |
| 09:04:59.065 | tool start `get_system_info` | tools → core | — |
| 09:04:59.588 | tool end | core → tools | 523 |
| 09:05:03.924 | LLM trả câu đầu | llm → voice | 8.080 |
| 09:05:05.459 | tiếng trả lời đầu | tts → portal | 9.615 |
| 09:05:06.603 | kết thúc | voice → portal | 10.759 |
| 09:05:07.668 | đổi trạng thái | Lõi hội thoại, TTS: unknown → ok | — |

- Trong lượt này có 45 snapshot được đẩy, đúng nhịp 2 s/lần.
- Trạng thái ban đầu đều là trạng thái thật: 6 ok, 3 unknown (chưa có lượt thoại hoặc số đo), 8 off (không có robot, HUD hay máy trạm nào kết nối; connector chưa cấu hình).

Sửa sau lần chạy thật đầu tiên:
- Tool chạy trên máy chủ từng hiện `tools → tools`, nay hiện `tools → core`.
- Bước "kết thúc" từng không gắn cạnh nào, nay gắn `voice → kênh`.

## 5. Bổ sung module còn thiếu (2026-10-05)

Rà toàn bộ dịch vụ được khởi động lúc máy chủ chạy (`server._on_startup`). Trước đợt này, 11 dịch vụ đang chạy thật nhưng không có ô nào trên sơ đồ. Nay mỗi dịch vụ có một ô, đọc trạng thái thật:

| Ô | Nguồn trạng thái | Sự kiện mới |
|---|---|---|
| Autonomous Sentinel | `_running`, task vòng quét, sự cố đang mở | phát hiện / khôi phục sự cố → Telegram |
| Lịch đôn đốc (08:00 · 16:00) | luồng `proactive-manager-loop`, lịch sử chạy | mỗi lần rà soát |
| Tác vụ nền | số tác vụ theo trạng thái; ≥ 1 lỗi trong 10 gần nhất → suy giảm | bắt đầu / xong / lỗi, có ms |
| Email Gateway | bật trong cấu hình + luồng đọc hộp thư | tạo ticket (P1 → đỏ) |
| Webhook Gateway | `WEBHOOK_STATS` (đã nhận / trùng / gần nhất) | nhận / trùng |
| UDP Beacon :8888 | luồng `vnmate-udp-beacon` | — |
| Active Directory / HR | health cache; chưa đồng bộ lần nào → Tắt | — |
| Tri thức RAG + Graph | số đoạn trong ChromaDB, số thực thể / quan hệ | — |
| Bộ nhớ sự cố | số bản ghi (khi collection đã mở) | — |
| Đa tác tử CFO/HR/CTO | tác tử đã đăng ký, số lượt trao đổi | — |
| Bộ đệm phiên (RAM) | `ephemeral_cache.get_stats()` | — |

Thêm sự kiện cho hai kênh có sẵn:
- **Telegram:** từng lượt hỏi đáp (bắt đầu / kết thúc, ms, model).
- **Cảnh báo gửi ra:** sự kiện theo kết quả thật — đã gửi (Telegram trả 200), bị bỏ qua (nêu lý do) hoặc lỗi.

Nguyên tắc:
- Sơ đồ **không import** module chưa được máy chủ nạp. Ví dụ RAG sẽ khởi động ChromaDB, nên ô hiện "chưa nạp" thay vì tự nạp module.
- Đường nối vẽ từ trái sang phải; mũi tên theo đúng chiều thật (xuôi, ngược hoặc hai chiều).
- Cạnh `robot:*` nối tới mọi robot đang kết nối.

Test: `tests/test_phase88_workflow_topology.py`, 16 test. Toàn bộ pytest đều qua.
