# Hiệu năng voice — trước / sau từng bước tối ưu

Mỗi bước đo bằng cùng một lệnh với baseline (`scripts/bench_voice.py --ws wss://localhost --ws-rounds 20 --tts-rounds 10 --concurrency 1,5,10`), cùng máy, cùng nhà cung cấp LLM/TTS. Số phía máy chủ từ `VoiceTurnTrace`. Đơn vị ms, dạng p50 / p95, n = 20 mỗi loại lượt qua WebSocket.

**Lưu ý về nhiễu:** độ trễ của nhà cung cấp LLM thay đổi đáng kể giữa hai lần chạy cách nhau vài chục phút. Đối chứng là **lệnh vận hành**, đường mà P2 không đổi (vẫn 11.333 ký tự prompt, 5 tool). Nó vẫn nhanh hơn ~30% ở lần chạy sau. Vì vậy chỉ kết luận ở những chỉ số đổi vượt mức đó, hoặc đổi theo cơ chế đo được trực tiếp (kích thước prompt, số tool, câu xác nhận).

## Supervisor P2–P5 (2026-10-05): `bench-2026-10-03-final.json` → `bench-2026-10-05-supervisor.json`

Cùng lệnh đo, cùng máy (thêm `--memory-turns 100`). Thay đổi giữa hai lần: Policy Engine + sổ tác vụ + kiểm chứng trên đường tool, ngân sách thử model dự phòng (12 s stream / 16 s complete), đo CPU chạy ngoài event loop, quy tắc ranh giới tin cậy (+456 ký tự, chỉ prompt vòng agent; prompt thoại không đổi). ms, p50 / p95, n = 20.

| Loại lượt | Chỉ số (máy chủ) | Trước | Sau | Đọc thế nào |
|---|---|---|---|---|
| Lệnh nhanh | TTFT | 27 / 53 | 27 / 54 | không đổi |
| Lệnh nhanh | tiếng câu trả lời | 51,5 / 1 694 | 53 / 1 888 | trong nhiễu (p95 = câu có giờ phút phải tổng hợp TTS) |
| Câu cần LLM | chữ đầu LLM | 2 510 / 8 156 | 3 463 / 7 364 | nhà cung cấp dao động — đường mã thoại không đổi |
| Câu cần LLM | tiếng câu trả lời | 4 399 / 10 410 | 4 858 / 13 700 | như trên |
| Lệnh vận hành | tiếng câu trả lời | 12 752 / 40 703 | 12 020 / 12 681 | **p95 giảm vì ngân sách thử model**, KHÔNG vì trả lời nhanh hơn: trong lúc đo, 9Router trả 503 / timeout liên tục — 19/20 lượt chạm ngân sách 12 s rồi trả câu "quá tải" (log `Dừng thử model: hết 12s`); chỉ 1/20 lượt chạy được vòng agent (`agent_ms` n = 1, trước n = 18). Kết luận đúng: thời gian **chờ một lỗi** giảm từ tới 40,7 s xuống ≤ 12,7 s; tỷ lệ trả lời được phụ thuộc nhà cung cấp |
| Lệnh vận hành | câu xác nhận | 1 / 1 | 1 / 1 | không đổi |
| Lệnh nhanh "kiểm tra cpu" (in-process) | thời gian lệnh | 50,8 / 51,4 | 51,3 / 52,0 | lệnh vẫn đo CPU 50 ms; khác biệt là 50 ms đó nay chạy ở luồng phụ — **bench này không đo** việc các phiên khác hết bị đứng (chỉ khẳng định bằng mã + test RULE-026) |
| TTS câu đầu (engine) | — | 1 032 / 1 777 | 1 216 / 2 783 | nhà cung cấp TTS dao động (n = 10) |
| Đồng thời 10 phiên | tiếng câu trả lời | 4 670 / 5 317 | 4 395 / 6 664 | trong nhiễu |
| 100 lượt liên tiếp | RSS / task nền | 132,1 → 132,0 MB / 1 → 1 | 143,0 → 143,9 MB / 1 → 1 | +0,9 MB sau 100 lượt — **cần theo dõi**, chưa đủ để kết luận rò bộ nhớ; không rò task |

Lỗi WebSocket: 0 ở cả ba nhóm. p99: không báo (n = 20 không đủ).

## Tổng kết: Phase 1 (baseline) → sau P2–P6

Cùng lệnh đo (`bench_voice.py --ws wss://localhost --ws-rounds 20 --tts-rounds 10 --concurrency 1,5,10 --memory-turns 100`), cùng máy, cùng nhà cung cấp. `bench-2026-10-03-phase1.json` → `bench-2026-10-03-final.json`. ms, p50 / p95.

| Loại lượt | Chỉ số | Baseline | Sau | Mục tiêu (prompt) | Đạt? |
|---|---|---|---|---|---|
| Mọi lượt | sự kiện đầu (client) | 2,5 / 3,4 | ≈ như cũ | < 300 | ✅ (cả trước) |
| Lệnh vận hành | câu xác nhận từ cache | 1,0 | 1,0 | < 300–500 | ✅ (cả trước) |
| Lệnh nhanh | tiếng câu trả lời | 53 / 1.462 | 52 / 1.694 | < 800–1.500 (động) | ✅ p50; p95 là câu có giờ phút phải tổng hợp TTS |
| Câu trò chuyện | prompt (ký tự) | 11.298 | **3.624** | — | −68% |
| | tool đưa cho model | 5 | 1 | — | |
| | LLM-1st | 2.503 / 8.134 | 2.509 / 8.156 | < 500–800 "nếu provider cho phép" | ❌ — độ trễ cố định của 9Router/model |
| | **TTFA-answer** | 4.531 / 10.451 | **4.399 / 10.410** | < 800–1.500 "nếu provider/TTS cho phép" | ❌ |
| Lệnh vận hành | vòng agent | 8.248 / 12.081 | **3.915 / 7.267** | — | −53% p50 |
| | **TTFA-answer** | 15.018 / 48.972 | **12.752 / 40.703** | — | −15% p50 |
| Đồng thời 5 | TTFA-answer | 4.813 / 8.383 | 3.798 / 7.752 | — | lỗi 0 |
| Đồng thời 10 | TTFA-answer | 5.353 / 6.579 | 4.670 / 5.317 | — | lỗi 0 |
| 100 lượt liên tiếp | RSS / task | 133,7 → 132,3 MB; 1 → 1 | 132,1 → 132,0 MB; 1 → 1 | không rò | ✅ |

**Đọc kết quả cho đúng:**
- Phần do máy chủ quyết định đã nhỏ: sự kiện đầu ~3 ms, câu xác nhận ~1 ms, tách câu < 1 ms, lệnh nhanh có cache ~50 ms; lệnh vận hành bớt một lần gọi LLM (đo trực tiếp: 18/18 lượt có tool dùng lại lời gọi đã stream).
- Phần còn lại của TTFA gần như toàn bộ là **nhà cung cấp**: LLM-1st p50 2,5 s (câu trò chuyện) / 4,5 s (lệnh vận hành, phân bố hai đỉnh ~4 s và ~10 s tuỳ model), TTS 9Router 1,3–2,2 s mỗi câu. Mục tiêu < 1,5 s cho tiếng động đầu không đạt được với nhà cung cấp hiện tại; cần model / TTS nhanh hơn (vd model cục bộ qua `routing_mode=direct`, TTS stream) — ngoài phạm vi refactor.
- **2/20 lượt vận hành treo 40,7 s** rồi trả câu "hệ thống quá tải": mọi model đều không mở được stream; chuỗi thử model dự phòng (5 s / model) quá dài. Ghi ở production-readiness.

## P2 — Voice Brain gọn + sửa định tuyến nhầm (2026-10-03)

Số gốc: `bench-2026-10-03-phase1.json` → `bench-2026-10-03-p2.json`.

### Câu cần LLM ("Giải thích ngắn gọn RAID 1 là gì trong hai câu.")

| Chỉ số | Trước | Sau |
|---|---|---|
| Kiểu lượt | vận hành (định tuyến nhầm) | trò chuyện |
| Prompt (ký tự, trung vị) | 11.298 | **3.633** (−68%) |
| Tool đưa cho model | 5 | 1 (`create_new_skill` — tài khoản bench là admin) |
| Câu xác nhận "để em kiểm tra" | có (thừa) | không |
| LLM-1st | 2.503 / 8.134 | 2.569 / 4.343 |
| TTFT | 2.948 / 8.238 | 2.634 / 4.343 |
| **TTFA-answer** | **4.531 / 10.451** | **3.938 / 6.081** |
| TTL | 5.551 / 10.454 | 4.807 / 6.633 |

### Đồng thời (trong tiến trình, cùng câu)

| Phiên | TTFA-answer trước | sau | LLM-1st trước | sau | Lỗi |
|---|---|---|---|---|---|
| 1 | 5.879 | 5.365 | 3.866 | 3.284 | 0 |
| 5 | 4.813 / 8.383 | 4.401 / 5.872 | 2.986 / 6.662 | 2.482 / 3.766 | 0 |
| 10 | 5.353 / 6.579 | 3.570 / 5.185 | 2.756 / 4.274 | 2.122 / 3.466 | 0 |

### Đối chứng (P2 không đổi các đường này)

| | Trước | Sau |
|---|---|---|
| Lệnh nhanh TTFA-answer | 53 / 1.462 | 53 / 1.600 |
| Lệnh vận hành TTFA-answer | 15.018 / 48.972 | 10.433 / 16.048 |
| Lệnh vận hành LLM-1st | 4.403 / 39.769 | 3.863 / 8.795 |
| TTS engine đoạn đầu (n=10) | 1.136 / 1.826 | 1.069 / 1.727 |

### Kết luận

- **Chắc chắn (do cơ chế):** câu hỏi kiến thức không còn bị coi là lệnh vận hành. Prompt giảm từ 11,3k xuống 3,6k ký tự, số tool từ 5 xuống 1, và không còn câu xác nhận thừa.
- **TTFA-answer p95 của câu trò chuyện 10,5 s → 6,1 s, p50 4,5 s → 3,9 s.** Một phần cải thiện p50 nằm trong nhiễu của nhà cung cấp, vì đường đối chứng cũng nhanh hơn ở lần chạy này. LLM-1st p50 gần như không đổi (2,5 s): ở p50, thời gian chờ chủ yếu là độ trễ cố định của nhà cung cấp, không phải độ dài prompt.
- **Lệnh vận hành vẫn chậm nhất** (TTFA-answer p50 10–15 s) → P3.
- **Còn lại:** `create_new_skill` vẫn được đưa cho câu hỏi kiến thức của admin, khoảng 1k ký tự schema. Gỡ nó cần quyết định, vì chủ dự án yêu cầu trợ lý chủ động tạo skill khi yêu cầu chưa có công cụ. Ghi lại để xem cùng P3.

## P3 — Lệnh vận hành không hỏi model hai lần (2026-10-03)

Số gốc: `bench-2026-10-03-p2.json` → `bench-2026-10-03-p4.json` (lần đo sau P3 + P4; P4 không đổi đường vận hành ngoài tách câu). Thêm: đo trong tiến trình từng bước của một lượt (`get_system_info`, LLM + TTS thật, 2026-10-03 21:50).

**Cơ chế (đo trực tiếp, không phụ thuộc nhiễu):**

| | Trước | Sau |
|---|---|---|
| Lần gọi LLM trong vòng agent | 2 (chọn lại tool với 82 tool, rồi trả lời) | 1 (trả lời từ kết quả tool) |
| Tool trong mỗi lần gọi vòng agent | 82 (~73.700 ký tự schema) | 7 (tool của lần stream + công cụ quản lý kỹ năng) |
| Câu trả lời của agent đưa sang TTS | cả đoạn là MỘT câu | tách câu như đường stream |
| Chờ TTS sau khi có câu trả lời (1 lượt đo trong tiến trình) | 5,3 s | 1,9 – 2,4 s |

**Qua WebSocket (n = 20, lệnh "Báo cáo thông tin hệ thống máy chủ…"):**

| Chỉ số | P2 | Sau P3 + P4 |
|---|---|---|
| Vòng agent | 5.711 / 8.427 | **3.358 / 5.506** (−41% p50) |
| TTFA-answer | 10.433 / 16.048 | 8.939 / 20.078 |
| LLM-1st (lần stream, P3 không đổi) | 3.863 / 8.795 | 4.462 / 13.431 |

p95 TTFA-answer tệ hơn vì đuôi của nhà cung cấp ở lần stream đầu (LLM-1st p95 8,8 → 13,4 s) — phần P3 không đụng tới. Một lần đo trung gian (P3 chưa tách câu, file đã thay bằng lần đo sau) cho TTFA-answer p50 11,0 s: khi đó câu trả lời agent vẫn đọc cả đoạn.

## P4 — TTS (2026-10-03)

**Nhà cung cấp (`bench_voice.py --tts-providers 6`, không cache, giọng Hoài My):**

| Câu | 9Router (cả câu) p50 / p95 | Edge — byte đầu p50 / p95 | Edge — hết câu p50 |
|---|---|---|---|
| 7 từ | 1.258 / 1.869 | 2.662 / 3.203 | 4.634 |
| 12 từ | 1.554 / 1.844 | 3.473 / 4.536 | 5.077 |
| 28 từ | 2.183 / 2.439 | 4.604 / 5.917 | 6.886 |

→ Giữ thứ tự 9Router trước, Edge dự phòng (chú thích cũ "Edge byte đầu 150–250 ms" sai, đã sửa). Thời gian TTS tăng theo độ dài câu.

**Thay đổi:**
- Câu đầu của câu trả lời thoại ngắn (`SentenceBuffer(first_max_words=12)`): chỉ cắt ở dấu phẩy, vẫn đủ 8 từ; khi stream thì phát ngay ở dấu phẩy đầu đủ dài thay vì chờ hết câu. **Đo A/B trong tiến trình (n = 8 mỗi bên, xen kẽ, LLM + TTS thật):** TTFA-answer p50 4.238 (bật) / 5.180 (tắt) ms, nhưng phần lớn chênh lệch là LLM-1st (2.761 / 3.332); thời gian từ câu đầu sẵn sàng tới tiếng 1.500 / 1.690 ms. Câu đầu của model trung vị 24 từ ở cả hai bên — quy tắc ít khi kích hoạt (cần dấu phẩy sau ≥ 8 từ). **Không chứng minh được cải thiện đáng kể**; giữ vì không đổi hành vi khi không kích hoạt và có test.
- Hàng đợi audio ra có giới hạn (4 đoạn): client nhận chậm thì TTS / LLM chờ thay vì audio dồn trong RAM. Không có số đo hiệu năng (đây là giới hạn bộ nhớ, không phải tốc độ).
- Làm nóng cache TTS thêm 5 câu cố định của lệnh nhanh + câu hỏi lại / tạm biệt của HUD.

**Đối chứng lần đo P4 (đường không đổi):** câu trò chuyện TTFA-answer 3.938 / 6.081 (P2) → 4.147 / 7.556; lệnh nhanh 53 → 56 ms; đồng thời 10 phiên 3.570 → 4.916 ms p50 với LLM-1st cũng chậm hơn (2.122 → 2.359; p95 3.466 → 8.531) — nhà cung cấp chậm hơn ở lần đo này.
