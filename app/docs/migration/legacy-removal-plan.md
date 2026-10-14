# Kế hoạch gỡ mã cũ / trùng lặp

> Phase 0 — 2026-10-05, commit `128c87d`. Thay phần "chưa làm" của `docs/architecture/legacy-candidates.md` (bản đó giữ làm lịch sử những gì đã xoá). Mọi mục dưới đây đã được **kiểm lại bằng grep ngày 2026-10-05**.
> Luật xoá (§174, §202): chỉ xoá khi có bản thay thế, mọi caller đã chuyển, đã kiểm dùng động (chuỗi tên, `importlib`, `registry.json`, test, frontend), test pass, chạy thật đã kiểm. Phase 0 **không xoá gì**.

## 1. Đính chính danh sách cũ

| Mục cũ | Ghi "chưa làm" | Thực tế 2026-10-05 |
|---|---|---|
| A5 `/ws/voice` | chưa làm | **đã gỡ** (không còn trong `routers/websockets.py`) |
| A6 `apps/*` | chưa làm | **đã gỡ** (thư mục không tồn tại) |
| C6 `meta_architect`, `analytics_engine` client riêng | chưa làm | **đã chuyển** sang `llm_provider.complete_text_blocking` (`meta_architect.py:132`, `analytics_engine.py:72`) |
| Hàm LLM chết (`chat`, `process_voice_command`, `generate_response`, `report_action_execution`) | ứng viên xoá | **đã gỡ** (grep = 0) |
| D7 gộp `plugin_manager` + `plugin_registry` | "đã gộp" | **chưa hết**: `plugin_registry` còn giữ tool riêng (connector, computer-use) và đường duyệt riêng |

## 2. Danh sách hiện hành

### 2.1 Trùng lặp nghiệp vụ — phải GỘP (không chỉ xoá)

| # | Bản thừa | Bản chuẩn | Caller phải chuyển | Phase | Rủi ro |
|---|---|---|---|---|---|
| L1 | Đường duyệt riêng ở `routers/skills.py:233–245` (`execute_with_hitl`) | `tool_gate.run_tool_with_policy` | endpoint `/api/v1/skills/execute` | P2 | TB — đổi thông điệp trả về |
| L2 | Đường duyệt trong `plugin_registry.execute_tool` (`:441–514`) | `tool_gate` (giữ breaker + timeout của registry làm lớp thực thi) | `tool_gate:200` | P2 | TB |
| L3 | `safety_guard.SecurityEngine.evaluate_action_risk` (luật cấu hình) | rủi ro chuẩn `zero_trust.get_risk_level` + bước DENY của control plane | `routers/security.py:166` (nút thử) | P2 | Thấp — nhưng **phải nối luật vào cổng trước khi xoá** |
| L4 | 4 hàm bọc audit (`safety_guard.log_audit`, `security_guard._write_audit`, `erp_db.log_audit_action`, `zero_trust.log_security_audit`) | một API audit có trường chuẩn (actor, agent_id, policy, risk, trace_id) | ~20 nơi gọi | P7 | Thấp |
| L5 | Hai danh mục tool (`core/plugin_manager`, `plugin_registry`) | một danh mục + một lớp thực thi | `tool_bridge`, `computer_use_plugin`, `skill_router` | P6 | TB — cần kiểm LLM có thấy tool connector không (chưa kiểm runtime) |
| L6 | Gửi Telegram trực tiếp ở `proactive_manager.py:198,356` | `alert_dispatcher.dispatch` | proactive_manager | P7 | Thấp |
| L7 | SQL trực tiếp trong `agent_orchestrator`, `onboarding_workflow`, `proactive_manager` | phương thức repository trong `erp_database` / `db_manager` | 3 module | P8 | TB |
| L8 | Client Whisper `OpenAI()` dựng mỗi lần gọi (`audio_processor.py:490`) | client từ pool / provider | audio_processor | P6 | Thấp (đã được miễn RULE-011 có lý do) |
| L9 | Skill `ninerouter_skills` tự gọi `/audio/speech` (`:255`) | `TTSStreamEngine` | skill | P6 | Thấp (baseline RULE-012 = 1) |
| L10 | Schema sự kiện riêng của HUD (`/ws/hud`) + `hudSpeechQueue` base64 (12 chỗ trong `hud.js`) + Web Speech ở HUD | sự kiện `/ws/v1/voice-stream` + STT máy chủ | `web/hud.js`, `hud_voice.py` | P6 | TB — cần kiểm trên trình duyệt |

### 2.2 Không có caller runtime — ứng viên xoá

| # | Đối tượng | Bằng chứng | Điều kiện trước khi xoá |
|---|---|---|---|
| L11 | `src/mateai/domain/*` (8 gói entity, 595 dòng) | `grep mateai.domain.<x>` ngoài `src/mateai/domain` = 0; chỉ `tests/unit/test_domain_entities.py` dùng | quyết định ở P1: dùng làm kiểu dữ liệu cho control plane / task ledger, hay xoá cùng test |
| L12 | Alias `/ws/audio-stream[/{id}]` | firmware trong repo dùng `/api/v1/xiaozhi/ws` | log truy cập xác nhận không thiết bị nào còn dùng (cần chạy thật) |

### 2.3 Tệp chưa được theo dõi trong git (không phải mã cũ — cần chủ dự án xử lý)

`skills/auto_*.py` (4), `skills/generate_pdf.py`, `scripts/close_tab.py`, `bao_cao_he_thong.md`, thay đổi chưa commit ở `identity_core.md`, `skills/registry.json`, `web/hud.html`. Không đụng tới trong refactor.

## 2.4 Trạng thái sau khi làm (2026-10-05, prompt Supervisor P2–P13)

| # | Trạng thái | Bằng chứng |
|---|---|---|
| L1, L2, L3 | **ĐÃ GỘP** — mọi đường thực thi tool dùng `policy_engine.authorize()`; luật cấu hình được cổng áp dụng | RULE-017 = 0, `test_policy_engine.py` |
| L4 | **GIỮ** — 4 hàm là lớp chuyển đổi tham số mỏng tới MỘT hàm ghi (`erp_db.write_audit_log`) và MỘT bảng; không có logic audit trùng | `test_audit_single_store.py` |
| L5 | còn — `plugin_registry` vẫn giữ tool đăng ký động; đường duyệt riêng đã bỏ (cổng chính sách quyết định) | — |
| L6 | **ĐÃ GỘP** — đôn đốc / phân công đi qua `alert_dispatcher` | `proactive_manager.py` |
| L7 | **ĐÃ GỘP** — truy vấn chuyển vào phương thức repository của `ERPDatabase` | RULE-024 = 0 |
| L8 | **GIỮ** — client Whisper đồng bộ có miễn trừ RULE-011 ghi rõ (STT, không phải LLM) | `test_core_rules.py` ALLOWED |
| L9 | **ĐÃ GỘP** — skill đi qua `TTSStreamEngine` | RULE-012 = 0 |
| L10 | còn — cần kiểm trên trình duyệt (HUD) | — |
| L11 | **ĐÃ XOÁ** — `src/mateai/domain/` + test riêng của nó (không caller, không dùng động: đã kiểm import chuỗi / registry / cấu hình / frontend) | `git rm`, `find_spec('mateai.domain') is None` |
| L12 | **GIỮ, có đo** — log không ghi đường WS nên chưa có bằng chứng; nay ghi `[DEPRECATED]` mỗi lần thiết bị dùng alias. Gỡ khi log sạch một thời gian | `routers/websockets.py` |

## 3. Thứ tự

P2 (L1–L3) → P6 (L5, L8, L9, L10) → P7 (L4, L6) → P8 (L7) → P13 (L11, L12, quét rác cuối §203).
