# BẢN ĐỒ CẤU TRÚC REPOSITORY VN-MATEAI (REPOSITORY MAP)

**Dự án**: VN-MateAI — Autonomous Enterprise AI Voice & Robotics Assistant  
**Thời điểm kiểm toán**: Tháng 10/2026 (Sau khi hoàn tất 13 Phase Realtime Voice Revamp)  
**Trạng thái**: Production Ready

---

## 1. TỔNG QUAN PHÂN BỔ THƯ MỤC THỰC TẾ

```text
VN-MATEAI/
├── core/                       # Cốt lõi hệ thống Backend, Voice, LLM, Database, Security
│   ├── agents/                 # Specialized Agent roles (Ops, Voice, Reasoning)
│   ├── audio/                  # Pipeline Realtime Voice & TTS Stream mới (Phases 1-12)
│   │   ├── acoustic_ack_catalog.py # Phase 6: Catalog 12 nhóm âm đệm tiếng Việt (0ms cache)
│   │   ├── binary_transport.py    # Phase 11: Binary transport protocol (Zero Base64 overhead)
│   │   ├── sentence_buffer.py     # Phase 3: Bộ đệm ngắt câu tiếng Việt thông minh
│   │   ├── sentence_streamer.py   # Streamer tách câu thô từ LLM token generator
│   │   ├── streaming_tts_pipeline.py # Facade tương thích ngược cho TTS streaming
│   │   ├── tts_queue_pipeline.py  # Phase 4: Worker hàng đợi TTS gối đầu In-Order
│   │   └── tts_stream_engine.py   # HTTP client tổng hợp giọng nói qua 9Router / EdgeTTS
│   ├── connectors/             # Kết nối ERP/CRM bên ngoài (Postgres, SAP, Hubspot, Zalo, ...)
│   ├── knowledge/              # GraphRAG & Cognitive Knowledge Engine
│   ├── plugins/                # Plugin hệ thống (Computer Use, Automation)
│   ├── schemas/                # Data Transfer Objects & Pydantic Validation Models
│   ├── security/               # Zero-Trust & Vault security credentials
│   ├── skills/                 # Skills core nội bộ (Robotics, Onboarding, FileSystem, Business)
│   ├── worknodes/              # Distributed Worknode coordination
│   ├── agent_voice_loop.py     # Phase 7: Voice Agent Loop không LLM Round 2 (Tiết kiệm ~2.5s)
│   ├── analytics_engine.py     # Phân tích kinh doanh, KPI & ROI calculation
│   ├── api_admin.py            # REST Router quản trị Admin (/api/v1/admin/*)
│   ├── api_erp.py              # REST Router ERP (/api/v1/erp/*)
│   ├── api_voice_stream.py     # [CANDIDATE: REPLACED/SUPERSEDED] Code streaming thử nghiệm cũ
│   ├── audio_cache.py          # Quản lý bộ nhớ đệm âm thanh RAM/Disk MD5
│   ├── audio_processor.py      # Audio Engine (Whisper STT, SileroVAD, Audio Normalizer)
│   ├── auth_manager.py         # JWT Token Authentication & User Session Management
│   ├── autonomous_sentinel.py  # Giám sát tự động 24/7 (SLA, crash recovery, alert dispatch)
│   ├── background_workers.py   # Scheduler, Celery/Asyncio background cron jobs
│   ├── cognitive_memory.py     # ChromaDB Vector Store & Cognitive Episodic Memory
│   ├── config_loader.py        # Cấu hình tập trung pydantic-settings (config.json, env)
│   ├── connection_pool.py      # Phase 10: Persistent HTTP/2 Connection Pools (300s keep-alive)
│   ├── database.py             # ERP Database Engine (SQLite WAL, KPI, Business schemas)
│   ├── db_manager.py           # Core System DB Manager (Auth users, task records, hw stats)
│   ├── department_engine.py    # Phân luồng nghiệp vụ theo phòng ban
│   ├── domain_sync.py          # Đồng bộ dữ liệu ERP sang cơ sở dữ liệu hr_kpi.db
│   ├── download_queue.py       # Quản lý hàng đợi tải file an toàn cho AI tool calls
│   ├── dynamic_skill_router.py # Phase 8: Dynamic Skill Loading (Pre-indexed 10 domains)
│   ├── email_gateway.py        # Gửi/nhận email báo cáo tự động
│   ├── ephemeral_cache.py      # Bộ đệm tự hủy trên RAM tuân thủ Nghị định 13/GDPR
│   ├── fast_command_router.py  # Phase 5: Fast Command Router tất định (< 2ms, TTFA < 100ms)
│   ├── health_monitor.py       # Giám sát sức khỏe phần cứng CPU/RAM/GPU/Disk
│   ├── history_pruner.py       # Phase 9: Cửa sổ trượt thoại & nén lịch sử (< 1200 chars)
│   ├── llm_engine.py           # Bộ Não Tri-Brain (Supervisor, Router, Tool Executor, Prompts)
│   ├── llm_provider.py         # Phase 2: Streaming LLM Token Layer & Event Bus
│   ├── memory_manager.py       # Lịch sử hội thoại phiên người dùng + Voice History
│   ├── meta_architect.py       # Tự chuẩn đoán lỗi mã nguồn và đề xuất tự vá
│   ├── orchestrator.py         # Điều phối đa kênh (Telegram, Zalo, Voice, Web)
│   ├── plugin_manager.py       # Quản lý đăng ký, nạp động, hot-reload các skills/*.py
│   ├── plugin_registry.py      # Sổ đăng ký công cụ và chính sách rủi ro (Risk Level)
│   ├── rag_engine.py           # Retrieval-Augmented Generation với ChromaDB
│   ├── realtime_voice_ws.py    # Phase 1, 11, 12: Realtime Voice WebSocket chuẩn hóa (/ws/voice)
│   ├── safety_guard.py         # Zero-Trust Data Masking & AST Inspection
│   ├── security_guard.py       # Enterprise RBAC Permission Guard & Audit Log
│   ├── security_tls.py         # Tự cấp chứng chỉ SSL mTLS cho kết nối nội bộ
│   ├── server.py               # FastAPI Server chính (REST API + WebSocket Gateway)
│   ├── state_manager.py        # Quản lý trạng thái chờ duyệt (HITL Pending Actions)
│   ├── task_manager.py         # Quản lý hàng đợi tác vụ hệ thống
│   ├── telegram_gateway.py     # Gateway bot Telegram tương tác hai chiều
│   ├── voice_controller.py     # Điều phối Offline Voice, WakeWord & Loa phần cứng
│   ├── voice_session.py        # Quản lý ngữ cảnh phiên hội thoại cho HUD
│   ├── voice_widget.py         # Floating UI Siri-style (Tkinter transparent window)
│   ├── wake_word_engine.py     # Nhận diện từ khóa "Hey Lyly" qua OpenWakeWord
│   ├── webhook_gateway.py      # Nhận webhook từ Facebook, Zalo OA, Webhooks
│   ├── xiaozhi_gateway.py      # Gateway giao tiếp phần cứng ESP32 XiaoZhi Box
│   └── zero_trust.py           # Đánh giá rủi ro động đa nhân tố (Zero-Trust Model)
│
├── web/                        # Web Portal Frontend (Vanilla JS + HTML5 + CSS)
│   ├── app.js                  # Logic ứng dụng, Realtime Voice client, Binary player
│   ├── hud.js                  # Desktop Floating HUD widget controller
│   ├── index.html              # Trang giao diện chính Web Portal (Command Center)
│   ├── styles.css              # Giao diện Dark Mode, Glassmorphism, animations
│   └── audio-processor.js      # Web Audio API worklet xử lý PCM micro
│
├── admin/                      # Next.js Enterprise Admin Dashboard (Standalone App)
│   ├── app/                    # Next.js 14 App Router (Pages, Layouts)
│   ├── components/             # React UI components (Tailwind + Radix UI)
│   ├── hooks/                  # Custom React hooks
│   ├── lib/                    # API client và tiện ích hỗ trợ
│   ├── package.json            # Node.js dependencies
│   └── tailwind.config.ts      # Cấu hình Tailwind CSS
│
├── skills/                     # 26 Kỹ năng tác tử (Plugins do PluginManager nạp động)
│   ├── ai_delegation.py        # Ủy quyền tác vụ cho máy trạm client
│   ├── business_tools.py       # Báo cáo doanh thu, chi phí, dòng tiền ERP
│   ├── computer_use.py         # Điều khiển chuột, bàn phím, ứng dụng desktop
│   ├── database_tools.py       # Truy vấn SQL và báo cáo dữ liệu
│   ├── file_system.py          # Thao tác tệp tin và thư mục an toàn
│   ├── integration_tools.py    # Tích hợp hệ thống bên thứ ba
│   ├── monitoring_skills.py    # Giám sát tiến trình và tài nguyên máy chủ
│   ├── onboarding_workflow.py  # Quy trình hướng dẫn nhân viên mới
│   ├── proactive_manager.py    # Tác tử chủ động nhắc việc và cảnh báo
│   ├── robotics_tools.py       # Điều khiển cử động robot vật lý
│   └── visual_skills.py        # Chụp màn hình và nhận diện thị giác OCR
│
├── client_agent/               # Mã nguồn máy trạm Agent (Chạy tại máy con trong mạng LAN)
│   ├── agent.py                # Main loop kết nối wss://server/ws/client
│   ├── overlay_ui.py           # Giao diện đè màn hình client
│   └── skills/                 # Kỹ năng cục bộ của máy trạm
│
├── client_template/            # Gói template dùng để đóng gói khi client tải về từ Web Portal
│   ├── agent.py                # Template tự nạp config.json động
│   └── skills/                 # Bản sao kỹ năng cho bộ cài máy trạm
│
├── esp32_firmware/             # Firmware C++/Arduino/PlatformIO cho thiết bị phần cứng ESP32-S3
│   ├── src/                    # Mã nguồn C++ (Audio I2S, WebSocket client, OLED display)
│   └── platformio.ini          # Cấu hình PlatformIO
│
├── workers/                    # Background daemons & OS drivers
│   ├── browser_session_vault.py# Kho lưu cookie trình duyệt mã hóa
│   ├── native_os_driver.py     # Driver điều khiển macOS/Windows native apps
│   ├── remote_worker_daemon.py # Worker xử lý phân tán
│   └── self_healing_engine.py  # Động cơ tự phục hồi mạng & dịch vụ
│
├── tests/                      # Bộ kiểm thử tự động (Phases 1-12 & Regression Tests)
├── storage/                    # Dữ liệu động: vector DB, audio cache, cache_index.json (Git ignored)
├── certs/                      # Chứng chỉ TLS/mTLS cho môi trường cục bộ (Git ignored)
├── scripts/                    # Kịch bản tiện ích (prewarm_vocabulary.py)
├── docs/                       # Tài liệu kiến trúc và kế hoạch nâng cấp
├── main.py                     # Entry point chính khởi động toàn bộ máy chủ VN-MateAI
├── config.json                 # Cấu hình đang chạy của máy chủ
├── config.example.json         # File mẫu cấu hình cho triển khai
└── requirements.txt            # Danh mục thư viện Python dependencies
```

---

## 2. CANONICAL PIPELINE DATAFLOW SAU NÂNG CẤP

Luồng xử lý chuẩn và duy nhất của hệ thống:

```text
[Người Dùng] (Giọng nói qua Microphone / Web Audio)
     │
     ▼
[WebSocket Gateway] (Endpoint: /ws/voice hoặc /ws/v1/voice-stream trong realtime_voice_ws.py)
     │
     ├─► [FastCommandRouter] (Kiểm tra lệnh tất định: Giờ, CPU, RAM, Chào hỏi...)
     │         │
     │         ├─► [Khớp: TTFA < 100ms] ──► [Pre-warmed RAM Cache 0ms] ──► [Phát Binary Frame]
     │         │
     │         └─► [Không khớp: Chuyển tiếp vào Agent Loop]
     ▼
[AgentVoiceLoop & DynamicSkillRouter] (Lọc 3-5 tools trong < 2ms từ 10 domains)
     │
     ▼
[LLMStreamProvider] (Tri-Brain / DeepSeek / Groq HTTP/2 Keep-Alive Pool)
     │ (Tokens Stream liên tục)
     ▼
[SentenceBuffer] (Tách câu chuẩn tiếng Việt, bảo vệ IP, số thập phân, chữ viết tắt)
     │ (Câu hoàn chỉnh)
     ▼
[StreamingTTSWorkerPipeline] (2-3 Workers tổng hợp giọng nói gối đầu song song)
     │ (Thứ tự Sequence 1 -> Sequence 2 -> Sequence 3 được bảo đảm tuyệt đối)
     ▼
[Binary Audio Transport] (Gửi gói nhị phân 16KB không mã hóa Base64)
     │
     ▼
[Web Audio API Player] (Phát liên tục không ngắt quãng trên Trình duyệt / Loa)
```

**Cơ chế ngắt lời (Barge-In)**:
Khi người dùng bấm Dừng hoặc nói lệnh mới:
`Client Event 'barge_in'` ──► `realtime_voice_ws.cancel_active_turn()` (< 0.01ms) ──► `StreamingTTSWorkerPipeline.cancel()` ──► Hủy toàn bộ worker, dọn sạch hàng đợi (Queue Drain 0.007ms), triệt tiêu hoàn toàn rò rỉ âm thanh.
