# Việc chủ dự án cần làm (điền sau)

Dành cho: chủ dự án / quản trị viên. Những việc dưới đây cần thông tin hoặc thiết bị thật mà máy chủ không tự làm được. Đánh dấu `[x]` khi xong.

## Bắt buộc trước khi dùng thật

- [x] **Đổi mật khẩu admin** (chủ dự án xác nhận 2026-10-06). Còn `manager` / `viewer` nếu vẫn dùng mật khẩu mặc định — đổi trong trang quản trị → Người dùng.
- [x] **LLM model** (2026-10-02, đo thật từng model qua 9Router — 3 lượt cho model chạy được):
  - Chính: `ag/gemini-3.8-flash-low` (trung vị 2,7 s, 3/3).
  - Dự phòng theo độ trễ: `ag/claude-sonnet-4-6`, `ag/claude-opus-4-6-thinking`, `ag/gemini-3.7-flash-low`, `ag/gemini-3.7-flash-medium`, `ag/gemini-3.8-flash-medium`, `ag/gemini-3.7-flash-high`, `ag/gemini-3.6-flash-low`, `ag/gemini-3.8-flash`, `ag/gemini-3.6-flash-high`.
  - Chuyên gia: `ag/claude-sonnet-4-6`, `ag/claude-opus-4-6-thinking`, `ag/gemini-3.8-flash-medium`.
  - Đã bỏ (thêm lại trong trang Cấu hình LLM nếu 9Router sửa): **đã ngừng** `ag/gemini-3-flash-agent`, `ag/gemini-3.5-flash-low`, `ag/gemini-3.5-flash-extra-low`; **trả rỗng** `ag/gpt-oss-120b-medium`, `ag/gemini-3.1-pro-low`, `ag/gemini-pro-agent`, `ag/gemini-3-flash`; **lỗi 400/503** `oc/ling-3.0-flash-fin-free`, `ag/gemini-3.5-flash-high`, `openrouter/typesafe/jev-1.13`; **timeout 15 s** `ag/gemini-3.6-flash-medium`, `ag/gemini-3.8-flash-high`; giá trị mẫu `YOUR_MODEL_NAME_HERE`.
  - Kết quả: lượt thoại qua LLM chữ đầu 8,5 s (lượt đầu) / 2,8 s (lượt sau) — trước đó 88,9 s.
  - Còn lại: khối cũ `router.primary.provider_model` = `oc/ling-3.0-flash-fin-free` (lỗi 400) — KHÔNG được dùng khi đã có khối `llm`; có thể xoá khỏi config.json cho gọn.
- [x] **9Router** đã hoạt động lại (chủ dự án sửa 2026-10-02).
- [x] **MFA bắt buộc cho admin** (2026-10-14): `security.require_mfa_roles = ["admin"]`. Lần đăng nhập tới: quét mã QR bằng ứng dụng xác thực và **cất mã khôi phục** (mất điện thoại mà không có mã → sửa `config.json` bỏ "admin" khỏi danh sách, khởi động lại). Robot / máy trạm / Telegram không bị ảnh hưởng (dùng token thiết bị). Bản cấu hình trước khi đổi: `backups/config.json.before-mfa-*`.

## Telegram

- [x] Token thật đã thêm. Lưu ý: token từng được lưu thành `••••••••<token>` (dán sau ký hiệu che trên giao diện) → đã sửa dữ liệu và sửa máy chủ để tự bỏ ký hiệu che khi lưu.
- [x] `admin_chat_ids` (1 id) và `incident_group_id` đã điền; `enabled` = true; bot `@VNMateai_bot` polling chạy; tin thử gửi tới nhóm `-1003922961701` thành công.
- [x] Id nhóm `-1003922961701` đã thêm vào `admin_chat_ids` (2026-10-02): mọi thành viên nhóm ra lệnh / bấm duyệt được (quyền admin của kênh Telegram) — chỉ giữ người được phép trong nhóm.
- [ ] Kiểm tra: nhắn bot một câu trong nhóm (vd. "mấy giờ rồi") và thử bấm nút duyệt của một yêu cầu HITL.

## Robot ESP32 / Xiaozhi (mỗi robot)

Máy chủ đã hỗ trợ token riêng cho từng robot. Mã firmware đã được sửa để đưa id robot vào đường dẫn kết nối — **chưa build/nạp thử được trên chip thật**.

- [ ] Chọn id riêng cho robot (vd. `robot_phong_hop`; chữ, số, `_ - .`, ≤ 64 ký tự).
- [ ] Cấp token (tài khoản admin): `POST /api/v1/security/devices` với `{"device_id": "<id>"}` — token chỉ hiện một lần.
- [ ] `esp32_firmware/src/secrets.h`: đặt `DEFAULT_DEVICE_ID` và `DEFAULT_DEVICE_TOKEN`; build và nạp.
- [ ] Kiểm: robot kết nối được; `GET /api/v1/security/devices` có `last_seen_at`.
- [x] Bắt buộc token riêng từng robot (2026-10-06): `security.require_per_device_token = true` — robot `vnmate_robot_01` dùng token riêng, vẫn kết nối bình thường.

Robot nạp firmware cũ (token rỗng) hiện bị từ chối (HTTP 403) — nạp lại theo các bước trên.

## Hạ tầng

- [ ] Thay chứng chỉ tự ký `certs/server.crt` / `certs/server.key` bằng chứng chỉ do CA cấp (giữ tên file).
- [ ] Tường lửa: chỉ cho VLAN thiết bị vào cổng 8000; người dùng vào cổng 443.
- [ ] Đặt `VNMATEAI_JWT_SECRET` nếu sẽ chạy lại máy chủ ở máy khác (để phiên đăng nhập không mất).
- [ ] Worker daemon (nếu dùng): đặt `VNMATE_ENROLLMENT_TOKEN` (giá trị `enrollment_token` trong gói tải agent) và `MASTER_API_URL`.
- [x] Lịch sao lưu (2026-10-06): 02:00 hằng ngày, chụp PostgreSQL, đẩy S3, chép sang ổ vật lý thứ hai `E:\VNMateAI-backups` (giữ 30 bản), tự chạy bù khi lỡ giờ; sentinel cảnh báo khi quá 26 giờ không có bản mới. **Còn:** cho tác vụ chạy cả khi chưa đăng nhập Windows — mở PowerShell bằng quyền Administrator và chạy: `Set-ScheduledTask -TaskName VNMateAI-Backup -Principal (New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U)`. Bản sao thật sự ngoài máy (OneDrive / ổ ngoài): thêm thư mục vào `backup.offsite_dirs` — bản sao lưu chứa bí mật, chỉ chọn nơi bạn kiểm soát.

## Connector (khi dùng)

- [ ] M365 / eInvoice / Paperless / OCI chưa được chạy thật (không có tài khoản thử). Bật từng cái trong môi trường thử; webhook cần `VNMATE_WEBHOOK_<NGUỒN>_SECRET`.
- [ ] **Webhook giờ bị TỪ CHỐI nếu chưa có chữ ký** (2026-10-02). Khi tích hợp hệ thống gửi webhook, đặt `VNMATE_WEBHOOK_<NGUỒN>_SECRET` (`PAPERLESS`, `EINVOICE`, `CUSTOM`, `OCI`; AWS SNS dùng chứng chỉ ký). Chỉ khi cài đặt thử mới tạm bật `"security": {"allow_unsigned_webhooks": true}`.
- [ ] Xoá trong nhóm Telegram tin cảnh báo thử "smoke" (2026-10-02 19:44) — do kiểm tra webhook trước khi chặn.

## Thay đổi cần biết (2026-10-12)

- **Tác vụ cần màn hình trên máy chủ** (mở trình duyệt / YouTube / ứng dụng, phát nhạc, chụp màn hình, clipboard) bị **từ chối rõ ràng** khi máy chủ chạy ở phiên nền (Windows Session 0, tác vụ đặt lịch không đăng nhập). Trước đây chúng báo "thành công" mà không cửa sổ nào hiện (sự cố 2026-10-08: AI tạo 39 kỹ năng gần giống nhau). Muốn mở cửa sổ trên máy chủ: chạy bằng `Start.bat` trong phiên Windows đang đăng nhập. Trạng thái hiện ở bước khởi động `desktop_session`.
- **Tạo kỹ năng mới** (`create_new_skill`): tối đa 3 lần / 10 phút cho mỗi người / thiết bị.
- **Cổng Agent** (tab mới): nối MCP server có sẵn của doanh nghiệp — xem `docs/integrations/agent-gateway.md`.
- **Sao lưu** (sự cố 09–12/10: 85 giờ không có bản nào vì tác vụ 02:00 chạy khi Docker/PostgreSQL chưa lên): tác vụ nay chạy `create --push --ensure-infra` (tự bật Docker Desktop + hạ tầng, chờ CSDL), thử lại 3 lần cách 30 phút; hỏng thì **cảnh báo ngay** (category `backup_failed`); không còn để lại thư mục dở; máy chủ đang chạy mà bản mới nhất cũ hơn 26 giờ thì **tự chạy bù** (`backup.auto_catch_up`, mặc định bật). Vẫn nên chạy lệnh S4U ở mục Hạ tầng để tác vụ chạy cả khi chưa đăng nhập — khi đó Docker Desktop chưa chắc tự bật được (cần phiên đăng nhập), nên bật *Start Docker Desktop when you sign in*.
- **Gọn tab:** *Tích hợp hệ thống* gộp Kết nối + Cấu hình + Công cụ thành một mục *Kết nối*; gỡ mục *Elastic Grid* (chưa từng giao việc — trạm tự báo nhịp tim nay hiện ở tab Dev Fleet, chỉ theo dõi); Danh bạ Agent hiện cả Dev Fleet và máy trạm.

## Thay đổi cần biết (2026-10-03)

- **Chỉ admin duyệt được tác vụ rủi ro cao** (portal, HUD, panel HITL, Telegram, câu "đồng ý"). Manager không còn duyệt được. Danh sách chờ duyệt và nhật ký audit cũng chỉ admin xem.
- **Mọi yêu cầu duyệt giờ đi một hàng đợi** và tác vụ cần duyệt từ hội thoại/portal/`fs`/máy trạm cũng **gửi thẻ duyệt có nút bấm tới Telegram** (trước chỉ một số loại). Nếu thấy nhiều tin hơn trước, đó là lý do.
- `/api/v1/fs/*` chỉ admin; công cụ đọc tệp từ chối tệp chứa bí mật (`config.json`, `.env`, khoá, chứng chỉ, CSDL) ở mọi kênh.
- Endpoint cũ `/api/v1/audit-logs` đã gỡ — dùng `/api/v1/security/audit-logs` (giao diện đã chuyển). Ai có script riêng gọi đường cũ cần đổi.

## Kiểm tra / quyết định còn mở

- [ ] **CI GitHub Actions**: đã chạy trên repo `lichpppp/VNMateAI-v1.0` nhưng job **không được khởi động — tài khoản GitHub bị khoá do vấn đề thanh toán** (2026-10-06). Việc của chủ dự án: GitHub → Settings → Billing của tài khoản `lichpppp`. Mọi bước của workflow đã chạy đạt trên máy này (lint, pip-audit, build admin, pytest, Node, kiểm bí mật).
- [ ] **Skill trùng giữa máy chủ và gói agent máy trạm**: `skills/{custom_skills, pc_control_skills, sysadmin_skills}.py` giống hệt `client_agent/skills/`; `file_system`, `monitoring_skills`, `excel_records_skill`, `visual_skills` đã lệch nhau. Quyết định: máy chủ có cần tự điều khiển chính nó (chuột/bàn phím/cửa sổ) không? Nếu không → bỏ bản ở `skills/`, chỉ giữ ở agent; nếu có → giữ một nguồn và đóng gói agent từ nguồn đó.
- [x] **PostgreSQL — cutover** (2026-10-06): ứng dụng chạy trên PostgreSQL 16 Docker. Bản lùi: `backups/pre-pg-cutover/` + tệp SQLite gốc. Việc của chủ dự án: giữ Docker chạy cùng máy chủ; muốn quay về SQLite thì xoá `DATABASE_URL` trong config.json (dữ liệu sau cutover cần chép ngược).
- [ ] **Tách tiến trình / nhiều tiến trình**: chưa cần — đo thật 100 phiên trên một tiến trình không nghẽn. Kế hoạch + kiểm kê trạng thái trong RAM: `docs/architecture/multi-process-plan.md`.
- [ ] **Container**: chưa làm — ứng dụng dùng COM/pywin32, micro, điều khiển màn hình Windows nên không chạy được trong container Linux; nếu cần, chỉ tách phần API thuần.
- [ ] `skills/registry.json` và hai skill do AI tạo (`skills/auto_play_music.py`, `skills/thong_ke_lo_xsmb.py`) cùng `get_lotto.py`, `generate_pdf.py` ở gốc repo là tệp sinh trong lúc chạy / của bạn — **chưa commit, không đụng tới**. Xem lại và tự quyết có đưa vào git không.

## Thoại realtime (2026-10-03, sau P2–P6)

- [ ] **Nghe thử trên trình duyệt thật** portal + HUD sau khi đổi sang bộ phát chung `web/voice-audio-queue.js` (máy làm việc không có công cụ tự động trình duyệt — đã kiểm bằng test Node + file tĩnh + máy chủ, chưa nghe tai): một câu dài nhiều câu (phát đúng thứ tự, liền mạch), ngắt lời giữa chừng (không còn tiếng của lượt cũ), HUD vòng hội thoại hỏi lại / 30 s tạm biệt. Nhấn Ctrl+F5 một lần để nạp `hud.js?v=52.0`, `app.js?v=43.0`.
- [ ] **Quyết định: `create_new_skill` cho câu hỏi kiến thức của admin.** Hiện mọi câu trò chuyện của admin không khớp skill rõ đều được đưa thêm công cụ này (~1.100 ký tự schema mỗi lượt) để trợ lý chủ động tạo skill như anh yêu cầu. Bỏ đi thì câu hỏi kiến thức nhẹ hơn nhưng trợ lý chỉ tạo skill khi câu bị phân loại là lệnh vận hành.
- [ ] **Audit ghi hai dòng cho mỗi lần chạy tool** (một từ cổng tool, một kèm ghi chú RBAC, cùng thời điểm). Không sửa vì thuộc phần bảo mật / audit bất biến — quyết định có gộp không.
- [ ] **HUD dùng chung schema sự kiện với portal (D4)** — chưa làm: cần viết lại phần nhận sự kiện của `hud.js` và kiểm trên trình duyệt.
- [ ] **Robot: gộp ba nhánh kết thúc câu nói (L5)** — cần thử trên robot thật (mỗi nhánh gửi thông điệp khác nhau theo firmware). Đồng thời đo `stt_ms` của robot (đã có trong trace, chưa có số vì không có thiết bị lúc đo).
- [x] **Đồng thời 50 / 100 phiên** (2026-10-06): `scripts/load_test.py` — 100 / 100 phiên kết nối, 0 rớt, `/readyz` p99 105 ms. Chưa gồm LLM / STT (xem multi-process-plan.md).

## Tạo kỹ năng mới (2026-10-04)

- [x] **Quyết định 2026-10-14: giữ `auto_execute = true`** (chủ dự án: kỹ năng không cần duyệt), và **không đặt hạn mức chi phí LLM** (`llmops.*_limit` để trống). Ghi chú cũ — `auto_execute = true` trong `config.json`: mã do AI sinh được cài NGAY sau kiểm toán AST, không người duyệt. Muốn duyệt tay: đặt `false` → mã sinh ra nằm ở `skills/pending/`, xem rồi chuyển vào `skills/` và nói "nạp lại kỹ năng".
- [ ] Kỹ năng AI sinh có thể dùng nguồn dữ liệu đã cũ / bị chặn (thử "giá vàng SJC": hai nguồn model chọn đều từ chối). Chưa có tự kiểm tra sau khi tạo (chạy thử có thể gây tác dụng phụ — vd mở trình duyệt). Kỹ năng hỏng thì xoá tệp `skills/auto_*.py` tương ứng rồi "nạp lại kỹ năng" để lần sau trợ lý tạo lại.
- [ ] `install_skill_from_url` chỉ lưu `SKILL.md` vào thư mục con, không nạp mã — mô tả của công cụ ("tự nạp vào runtime") nói quá; quyết định giữ / sửa mô tả.

## Robot: gọi "hey Ly Ly" (2026-10-04)

- [x] Robot `vnmate_robot_01` có token riêng (`certs/robot_vnmate_robot_01.key`, `esp32_firmware/src/secrets.h` — cả hai không vào git). Firmware mới đã nạp qua USB.
- [x] Gọi tên: lúc nghỉ robot lọc tiếng nói trên chip và chỉ gửi đoạn có tiếng nói (≤ 4 s) cho máy chủ trong LAN; máy chủ nhận dạng OFFLINE (faster-whisper tiny, không gửi ra internet), không ghi log nội dung câu không gọi tên. Đo thật: "hey Ly Ly" nhận ra sau 293 ms.
- [ ] **Quyền riêng tư:** micro robot giờ luôn bật lúc nghỉ. Muốn tắt: đặt `WAKE_LISTEN_ENABLED 0` trong `esp32_firmware/src/main.cpp`, build và nạp lại (khi đó chỉ chạm để gọi).
- [ ] Ồn nền đo được trên robot dao động RMS 1.500–5.000, đỉnh tới 30.000 (gần ngưỡng tràn 16-bit). Nếu robot hay bị đánh thức nhầm hoặc không nghe thấy khi gọi xa, báo lại để chỉnh ngưỡng (log `[Wake] ... ồn nền`).
- [ ] Câu hỏi đầu tiên trên robot: STT (Google) 715 ms, tiếng câu trả lời đầu sau 7,9 s, hết lượt 19,4 s (LLM-1st 5,6 s).

## Quyền robot (2026-10-04)

- [x] Web Portal → **Bảo mật → Thiết bị & Robot**: đặt quyền từng thiết bị (Quản trị / Hỗ trợ IT / Vận hành / Chỉ xem). `vnmate_robot_01` đã đặt **Quản trị**.
- Quyền chỉ áp dụng khi thiết bị kết nối bằng **token riêng** (máy chủ gán danh tính `device:<id>`); token dùng chung không mang quyền này.
- Theo lựa chọn A: tác vụ rủi ro cao (Level ≥ 3: dừng tiến trình, PowerShell, xoá…) robot vẫn **hỏi duyệt lần đầu**; anh bấm Đồng ý một lần thì tác vụ đó được **nhớ** — lần sau robot làm luôn (audit ghi `APPROVAL_REMEMBERED`). Nhớ theo TÊN tác vụ, không theo tham số (duyệt "dừng tiến trình" một lần = sau đó dừng được mọi tiến trình). Thu hồi từng tác vụ hoặc tất cả ở cùng thẻ.
- Không làm (bị chặn, theo lựa chọn A): robot chạy tác vụ rủi ro cao mà không duyệt lần nào.

## Prompt cuối — hạ tầng & thiết bị (2026-10-06)

- [x] **Docker** (2026-10-06): đã chạy. `deploy/docker-compose.infra.yml` lên đủ PostgreSQL 16, Redis 7, S3 (SeaweedFS, BẮT BUỘC khoá), OTel collector — cổng chỉ mở trên 127.0.0.1; mật khẩu / khoá sinh ngẫu nhiên trong `deploy/.env` + `deploy/s3.json` (không commit). Ghi chú cũ: WSL 2 thiếu kernel ("The WSL 2 kernel file is not found"), nên Docker Desktop không chạy. Chạy `wsl --update` bằng quyền quản trị (có thể cần khởi động lại máy), rồi `docker compose -f deploy/docker-compose.infra.yml up -d`. Trong lúc chưa có Docker: `python scripts/dev_infra.py fetch && python scripts/dev_infra.py start` (Redis + S3 chạy tạm, không bền qua khởi động lại máy).
- [x] **Redis cho máy chủ** (2026-10-06): `REDIS_URL` trong config.json (mã hoá); khoá đăng nhập / rate limit / bộ đếm khẩn cấp ghi vào Redis — đã kiểm trên máy chủ thật. khi chạy nhiều tiến trình: đặt `VNMATEAI_REDIS_URL` (hoặc `REDIS_URL` trong config.json). Chưa đặt = RAM một tiến trình (vẫn đúng với một máy).
- [x] **Object storage** (2026-10-06): S3 Docker (bucket `vnmateai`, cần khoá); sao lưu tự động **02:00 hằng ngày** (Task Scheduler `VNMateAI-Backup`, chạy thử đạt; chỉ chạy khi tài khoản Windows đang đăng nhập) đẩy lên S3; kéo về kiểm chứng ĐẠT. Ghi chú cũ: khối `object_storage` trong config.json (`backend: "s3"`, endpoint, bucket **PRIVATE**, khoá). Bản sao lưu chứa bí mật, nên bucket không được công khai. Sau đó `python scripts/backup.py create --push`.
- [x] **PostgreSQL (giai đoạn di trú)** (2026-10-06): bản sao dữ liệu thật đã ở PostgreSQL Docker (schema `vnmate`, 23 bảng / 1 231 dòng, khớp checksum). Cutover đã làm cùng ngày (mục PostgreSQL — cutover ở trên). Ghi chú cũ: chạy thử `python scripts/migrate_sqlite_to_pg.py migrate --pg <dsn>` trên bản sao lưu, xem báo cáo (phải `ok: true`). Đã chạy thử với bản sao dữ liệu thật: 23 bảng, 1.195 dòng, khớp checksum. Chuyển ứng dụng sang chạy trên PostgreSQL (cutover) là giai đoạn sau, xem `docs/architecture/current-system-map.md` §10.
- [x] **Opus cho micro robot — đã đánh giá, CHƯA cần** (đo thật trung bình khoảng 82 kbps, Wi-Fi −41 dBm; xem `codec-evaluation.md`). Ghi chú cũ: thêm bộ mã hoá Opus 16 kbps vào firmware ESP32-S3, giúp giảm băng thông tải lên 16 lần (`docs/realtime/codec-evaluation.md`). Cần build và nạp trên chip thật.
- [ ] **Gán phòng ban + cấp bảo mật** cho tài khoản `manager` / `viewer` (Quản lý người dùng). Tài khoản chưa gán phòng ban không thấy dữ liệu ERP theo phòng ban; dữ liệu tài chính cần cấp 3.
- [x] **Firmware 54.0** (2026-10-06): đã build + nạp qua USB (COM7) cho `vnmate_robot_01`, hash xác nhận; robot kết nối lại báo `version=54.0`, tính năng `volume_ctrl / reboot / status_report` chạy thật (báo trạng thái: Wi-Fi −41 dBm, heap 137 KB).
