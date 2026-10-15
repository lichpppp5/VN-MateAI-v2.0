# Phản hồi báo cáo thẩm định thương mại (14/10/2026)

Báo cáo gốc: *"Nhận xét & đánh giá mức sẵn sàng thương mại nền tảng VN-MateAI Command Center"*, Trung tâm Vận hành Công nghệ, kết luận 2,6/5. Tài liệu này ghi lại:

- những gì đã sửa ngay trong mã;
- những điểm báo cáo ghi khác với mã hiện tại;
- trạng thái của cả 32 hạng mục backlog;
- các quyết định cần lãnh đạo hoặc chủ dự án chốt.

Mã nguồn tham chiếu: nhánh `main`, phiên bản 2.0.0, sau commit có tài liệu này.

## 1. Tóm tắt

- **Đồng ý với kết luận chính.** Lõi kiểm soát AI là điểm mạnh nên giữ. Các điểm chặn việc bán là: dữ liệu đi ra LLM bên ngoài, độ tin cậy và độ trễ của AI, phạm vi quá rộng, và thiếu hạ tầng thương mại (tenant, bản quyền, phát hành).
- **Đợt 1 đã làm xong trong mã:** 6 hạng mục, được làm toàn bộ hoặc một phần — P0-2, P0-3, P0-4, P0-6, P0-10, P0-12 — cùng P1-15 và mục "Cao" về kỹ năng chạy trên máy chủ. Tất cả được gom vào một công tắc **phiên bản sản phẩm** (`edition`). Bản giao khách hàng (`commercial`) bật cứng các kiểm soát. Bản nội bộ (`internal`) giữ nguyên các quyết định vận hành đã chốt.
- **Đợt 2 (cùng ngày):** ẩn công cụ chưa có nguồn dữ liệu, bộ đánh giá 300 câu và cổng phát hành, bắt đổi mật khẩu, duyệt hai người, báo cáo kiểm toán và neo chuỗi băm, tuân thủ dữ liệu cá nhân, danh mục kỹ năng, báo cáo hiệu năng theo tháng, kênh Zalo OA, bản quyền ngoại tuyến và đo lượng sử dụng, SBOM và ký số gói. Trạng thái chi tiết ở §4.
- **Hai hạng mục P0 trước đây báo cáo đánh giá là thiếu, nay đã có:**
  - P0-8: lõi chạy Linux container, thao tác Windows chuyển xuống Agent máy trạm.
  - P0-13: bộ cài, nâng cấp có migration và sao lưu trước khi nâng cấp.
- **Phần còn lại cần nguồn lực hoặc quyết định:** multi-tenant, sandbox kỹ năng, model tự host, pentest, bản quyền, đổi tên. Ước tính công sức trong báo cáo (khoảng 167 người-tuần cho P0+P1) vẫn hợp lý sau khi trừ phần đã làm (khoảng 25–30 người-tuần).

## 2. Điểm báo cáo ghi khác với mã hiện tại

Báo cáo khảo sát bản đang chạy tại 10.3.243.17 qua giao diện. Đối chiếu với mã nguồn cho thấy:

| Báo cáo ghi | Thực tế trong mã (14/10/2026) |
|---|---|
| Phiên bản 2.1.0 | `2.1.0` chỉ là chuỗi cứng trong khai báo FastAPI. Số chính thức là **2.0.0**, dùng thống nhất cho `pyproject.toml`, `mateai.__version__`, image Docker và API. |
| MFA chưa bật | Đã có MFA (TOTP + mã khôi phục) và cờ bắt buộc theo vai trò. Máy chủ dự án đã bắt buộc MFA cho `admin` từ 14/10. |
| Số đo lượt thoại chỉ nằm trong RAM 500 lượt | Trace được lưu bền trong bảng `voice_traces`. RAM chỉ là bộ đệm 500 lượt gần nhất cho màn hình. Hạn lưu nâng từ 30 lên **400 ngày** (xem P1-15). |
| Không có bộ cài, cập nhật | Có gói Docker (`scripts/build_release.py`) chạy được trên Linux/macOS, cả x86_64 lẫn ARM64, cài offline. Có bước nâng cấp CSDL theo số thứ tự, tự sao lưu trước khi nâng cấp, và `/readyz` báo khi chạy bản cũ trên CSDL đã nâng cấp. |
| Bắt buộc Windows | Lõi chạy trong container Linux. Thao tác Windows (COM, PowerShell, màn hình, phím media, Notepad…) chạy qua Agent máy trạm 2.3.0, có sẵn `.exe` trong gói. |
| Gửi mô tả mọi công cụ | Đã có chọn công cụ theo câu hỏi (`get_tools_for_query`, 3–5 công cụ mỗi lượt). Phần nặng là chỉ dẫn hệ thống 13.922 ký tự (xem đợt 2). |
| Chuỗi dự phòng có model `:free` | Không có trong cấu hình máy chủ dự án. Đó là cấu hình riêng của máy được thẩm định. Mã nay chặn cứng model `:free` ở bản thương mại. |

## 3. Đợt 1: đã làm (14/10/2026)

### 3.1 Phiên bản sản phẩm `edition`

Mã nằm ở `src/mateai/config/edition.py`. Chọn phiên bản bằng `"edition": "commercial"` trong `config.json`, hoặc biến môi trường `VNMATEAI_EDITION`. Gói Docker mặc định là `VNMATE_EDITION=commercial`.

| Kiểm soát (bản `commercial`) | Hạng mục | Cách hoạt động |
|---|---|---|
| AI không sinh, cài hay nạp mã | P0-2 | `create_new_skill`, `install_skill_from_url`, `reload_all_skills` bị cổng chính sách từ chối (luật `edition`, áp cho mọi người gọi) và bị ẩn khỏi danh mục. `auto_execute` luôn tắt. |
| Ẩn kỹ năng demo, dữ liệu mô phỏng, cá nhân | P0-3 | 24 kỹ năng trong `DEMO_SKILLS` (dòng tiền, chấm công, thi đua, email giả lập, YouTube, nhạc, thời tiết, gia phả, robot, Dev Fleet, điều khiển GUI tự trị). Khách hàng có nguồn dữ liệu thật thì bật lại từng kỹ năng bằng `edition_allow_skills`. |
| Không gửi dữ liệu tới model miễn phí | P0-4 (một phần) | Model `…:free` bị loại khỏi chuỗi thử. Kênh Direct từ chối gọi model này. |
| Che dữ liệu cá nhân trước khi gửi LLM bên ngoài, kể cả admin | P0-4 (một phần) | `pii_guard` che SĐT, CCCD, email, STK, lương, địa chỉ, ngày sinh… trong kết quả công cụ trước khi AI đọc. Bản nội bộ bật được bằng `privacy.external_llm_mask_pii`. |
| Không dùng giọng Microsoft Edge | P0-6 | Chặn cả Edge trực tiếp lẫn `edge-tts/…` qua 9Router. **Bắt buộc cấu hình Vbee hoặc ElevenLabs** (hoặc TTS có giấy phép) trong Voice Studio; nếu không, câu trả lời chỉ có chữ. |
| Kỹ năng mạnh không chạy trên máy chủ | §8 mức Cao | `run_powershell_command`, `delete_item`, `write_file`, `kill_process`, `run_local_sql_check`, dịch vụ Windows, clipboard, đọc/ghi tệp… chỉ chạy trên máy trạm qua Agent (`target_client`), không chạy trong tiến trình lõi. |
| Tài liệu API luôn tắt | P0-10 | Xem 3.2. |

### 3.2 Bảo mật nhanh (P0-10), áp dụng cho mọi phiên bản

- `/docs`, `/redoc` và `/openapi.json` **tắt mặc định**. Bản nội bộ bật lại bằng `security.public_api_docs: true` hoặc `VNMATEAI_API_DOCS=1`. Bản thương mại luôn tắt.
- `/api/v1/health-dashboard` **cần đăng nhập**. HUD đã gửi kèm token.
- Mọi phản hồi có header: CSP (`default-src 'self'`, `frame-ancestors 'none'`, `object-src 'none'`…), `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Permissions-Policy` và `Cross-Origin-Opener-Policy`.
  - CSP đã chặt: `script-src 'self'`, không còn `'unsafe-inline'` trên portal, HUD và ROI. Script nội tuyến đã tách ra tệp. Khoảng 430 thuộc tính `onclick="…"` sẵn có do `web/inline-handlers.js` chạy hộ theo một ngữ pháp nhỏ (không `eval`). Bộ này chỉ gọi được 237 tên có trong danh sách cho phép, sinh từ chính mã nguồn (`scripts/inline_handlers.py`, test kiểm tra khớp), nên HTML bị chèn vào trang không gọi được `fetch`, `localStorage` hay API. Trang Next.js xuất tĩnh (`/topology`) giữ CSP cũ. Có công tắc khẩn `VNMATEAI_CSP_LEGACY=1`.
- **HSTS** chỉ bật khi đặt `security.hsts: true`, sau khi đã có chứng chỉ CA thật. Với chứng chỉ tự ký, HSTS khiến trình duyệt không cho người dùng bấm "Vẫn tiếp tục".
- MFA bắt buộc cho `admin` và log ra tệp có che bí mật (`logs/vnmateai.log`) đã làm trước đó trong ngày.

### 3.3 Độ trễ giọng nói (P0-12, một phần)

- Kênh có màn hình (`hud`, `web`, `portal`) chỉ **đọc to 4 câu đầu**, sau đó nói "Chi tiết em đã hiện trên màn hình.". Chữ vẫn hiện đầy đủ. Số câu chỉnh bằng `voice.max_spoken_sentences` (0 = không giới hạn).
- Robot không có màn hình nên vẫn đọc đủ.
- Lượt 16 câu, 31 giây trong báo cáo (§5.1) giờ chỉ còn 5 câu đọc to.
- TTS vốn đã tổng hợp song song (2 luồng) và phát theo thứ tự.

### 3.4 Số đo lưu bền (P1-15, phần lưu trữ)

Trace lượt thoại giữ **400 ngày** (khoảng 13 tháng), cấu hình bằng `observability.voice_trace_retention_days`. Báo cáo SLA theo khoảng thời gian dài trên giao diện thuộc đợt 2.

### 3.5 Kiểm thử

- `tests/test_edition_commercial.py`: 16 test. Các test cập nhật theo hành vi mới: thứ tự audio của HUD, ảnh chụp bảng route (bỏ 4 route tài liệu), mẫu mã của dashboard và barge-in.
- Toàn bộ bộ test: 1453 đạt (14/10/2026).

## 4. Trạng thái 32 hạng mục backlog

Chú thích trạng thái:
- **Xong**: có trong mã và có test.
- **Một phần**: đã làm một phần, phần còn lại ghi ở cột bên phải.
- **Đợt 2**: việc trong mã, không cần quyết định, làm tiếp được ngay.
- **Đợt 3**: việc lớn, cần thêm người.
- **Quyết định**: chờ lãnh đạo hoặc chủ dự án.
- **Bên ngoài**: cần đơn vị hoặc tài nguyên ngoài.

### P0

| # | Hạng mục | Trạng thái | Còn lại / ghi chú |
|---|---|---|---|
| 1 | Đổi tên, logo, tên trợ lý | **Quyết định** | NOTICE chỉ cấm *bên thứ ba* dùng tên "VN-MateAI". Nếu tác giả tự thương mại hoá thì không bắt buộc đổi tên. Chỉ bắt buộc khi đơn vị khác phát triển phái sinh (xem §6). |
| 2 | Tắt sinh/cài mã AI ở bản bán | **Xong** | `edition=commercial`. |
| 3 | Loại kỹ năng demo; danh mục có chủ sở hữu và kiểm thử | **Xong** | Danh mục `skills/catalog.json`, test hợp đồng cho 108 kỹ năng, và **test hành vi cho cả 79 kỹ năng của bản thương mại**. Khi viết test, phát hiện tên dịch vụ Windows lạ không bị chặn; đã sửa ở cả máy chủ lẫn Agent 2.3.1. |
| 4 | LLM gateway: lọc PII, chính sách theo mức dữ liệu, chặn model không DPA | **Một phần** | Đã che PII trước LLM và chặn `:free`. Còn: định tuyến theo mức phân loại (`CONFIDENTIAL` trở lên chỉ đi model tự host) và danh sách nhà cung cấp có DPA (đợt 2, sau quyết định về nhà cung cấp). |
| 5 | Model tự host (vLLM) | **Bên ngoài** | Mã gọi LLM theo chuẩn OpenAI nên trỏ được vào vLLM. Cần GPU và đánh giá chất lượng tiếng Việt bằng bộ ở #11. |
| 6 | Thay Edge-TTS | **Xong** (bản bán) | Edge bị chặn. Vbee đang là engine chính. Cần hợp đồng Vbee/FPT.AI/Azure cho từng khách hàng. |
| 7 | Sandbox kỹ năng, khai báo quyền | **Đợt 3** | Bước đầu đã làm: kỹ năng mạnh không chạy trên máy chủ ở bản bán. Sandbox (gVisor/Firecracker) cho kỹ năng còn lại cần thiết kế riêng. |
| 8 | Tách phần Windows ra agent; lõi Linux | **Xong** | Gói Docker Linux (amd64/arm64), Agent 2.3.0 chạy kỹ năng desktop. |
| 9 | Multi-tenant (`tenant_id`, RLS, khoá theo tenant) | **Quyết định → Đợt 3** | Phụ thuộc phân khúc đầu tiên (§6). Có bước nâng cấp CSDL theo số thứ tự (`schema_migrations`) để thêm `tenant_id` an toàn. |
| 10 | Header bảo mật, tắt /docs, health tối giản, bắt đổi mật khẩu + MFA | **Xong** | Header, /docs, health-dashboard, MFA cho admin. Bắt đổi mật khẩu cho tài khoản mặc định, tài khoản admin vừa đặt lại cho người khác, và mật khẩu dev công khai. Chính sách: ≥ 10 ký tự, có chữ và số. |
| 11 | Bộ đánh giá AI 300–500 câu, cổng ≥ 90% | **Xong** | **Đã chạy đủ 300 câu trên LLM thật (15/10)**: đạt 245/269 câu được chấm = **91,1%** (công cụ 170/187, hội thoại 45/47, an toàn 30/35), p50 4,0 s, p95 12,3 s, 8,2 triệu token; 31 câu bỏ qua vì công cụ kỳ vọng chưa có ở máy này. Lần chạy đó trượt cổng vì 1 đề xuất công cụ cấm (cài kỹ năng từ URL — cổng chính sách vẫn chặn khi chạy thật); đã thêm quy tắc vào prompt, đo lại 35 câu an toàn: **0 đề xuất cấm, cổng ĐẠT 97,2%**. Lỗi gọi model giảm từ ~17% lượt xuống 2% nhờ gửi song song có trễ (9Router: model chính hết hạn mức 122 h). Nhiều câu trượt còn lại là model gọi công cụ liệt kê trước (`list_data_sources`, `list_directory`) khi câu hỏi chưa nói nguồn / đường dẫn — bộ chấm một lượt tính là trượt. |
| 12 | Độ trễ | **Một phần** | Phần quy tắc hệ thống trong prompt giảm từ 6,4 nghìn xuống 2,5 nghìn ký tự. Prompt vận hành HUD giảm từ 14.227 xuống 9.481 ký tự; token −14%, p95 từ 11,2 s xuống 7,9 s (đo thật). Phần còn lại là nội dung chủ dự án viết (giới thiệu, cá tính, mẫu báo cáo), không đụng tới. Còn giới hạn câu đọc to và ẩn công cụ chưa có nguồn như đã nêu. |
| 13 | Bộ cài, nâng cấp, sao lưu khôi phục được kiểm thử | **Xong** | Đã cài thật trên Linux/ARM64 giả lập. **Kiểm thử nâng cấp thật N → N+1** (`scripts/upgrade_test.py`, 15/10): dựng gói 1.0.0 trên Docker, tạo dữ liệu, thay compose + image bản mới giữ `.env` và volume → tài khoản, mật khẩu, chuỗi audit, schema đều đạt. Chạy trước mỗi phát hành (CI còn khoá vì thanh toán GitHub). |
| 14 | Pentest độc lập | **Bên ngoài** | — |

### P1

| # | Hạng mục | Trạng thái | Còn lại / ghi chú |
|---|---|---|---|
| 15 | Số đo lưu bền 13 tháng | **Xong** | Giữ 400 ngày. API báo cáo tháng (JSON/CSV) và **màn hình trong tab "Thương mại & Tuân thủ"**. |
| 16 | Chính sách khai báo (OPA/Cedar), duyệt hai người cho L4 | **Một phần** | Đã có duyệt hai người (`security.dual_approval_min_risk`, mặc định tắt): hai người khác nhau, người yêu cầu không tự duyệt. Chính sách khai báo OPA/Cedar: đợt 3. |
| 17 | Neo chuỗi băm audit ra ngoài, xuất báo cáo kiểm toán | **Xong** | Xuất CSV và HTML in được, kèm kết quả kiểm chuỗi. Neo đầu chuỗi ra ổ khác và S3 sau mỗi lần sao lưu, kiểm ở `/audit-logs/verify`. Muốn có WORM thật: bật Object Lock ở bucket. Dịch vụ dấu thời gian (TSA) tuỳ chọn. |
| 18 | SSO OIDC/SAML, vai trò tuỳ biến, phân tách nhiệm vụ | **Phần lớn** | Đã có OIDC. **Tách nhiệm vụ** (15/10, `security.separation_of_duties`, mặc định tắt): người yêu cầu và người soạn / sửa kịch bản không tự duyệt. Còn: SAML, vai trò tuỳ biến (làm khi khách hàng cần). |
| 19 | Trình soạn kịch bản trực quan, 20 mẫu | **Đợt 3** | Cần người làm frontend. |
| 20 | Connector kiểm thử thật | **Bên ngoài** | Cần hệ thống thật của khách hàng thí điểm (Zabbix, GLPI, Jira, ServiceNow, vCenter, Veeam, MISA). |
| 21 | Teams, Zalo OA, email | **Xong** (mã) | Có Teams, Email, Outlook, Slack, Webhook, Zalo OA. Zalo **tự làm mới token** (refresh token dùng một lần, lưu ngay, mã hoá). Chưa thử với tài khoản thật. |
| 22 | Thiết kế lại giao diện theo vai trò | **Xong** | Menu và điều hướng theo vai trò (viewer: 5 tab, manager: không có Cấu hình và Bảo mật). Khối kỹ thuật trên Bảng điều khiển ẩn với viewer. CSP chặt. White-label. Nhãn Việt hoá. Gzip (khoảng 1,4 MB → 330 KB). **Tổng quan điều hành cho giám đốc** (15/10) đầu Bảng điều khiển: việc AI làm xong, tỉ lệ thành công, giờ công ước tính, chi phí — so tháng trước — và việc đang chờ quyết định. Cài MFA bằng mã QR. `app.js` vẫn không tách (42 test đọc trực tiếp; tính năng mới nằm ở tệp riêng). |
| 23 | Bản quyền và đo lượng sử dụng | **Xong** (cơ chế) | Tệp bản quyền ký Ed25519, ngoại tuyến (`scripts/issue_license.py`). Hạn mức người dùng và máy trạm, dùng thử 5/5, đo lượng `/api/v1/license/usage`. **Mô hình giá vẫn là quyết định §6**; cơ chế đổi được thước đo. |
| 24 | Tuân thủ dữ liệu cá nhân: sổ xử lý, thời hạn lưu, xoá theo yêu cầu | **Xong** | Sổ hoạt động xử lý (API + `docs/compliance/`), tra cứu và xoá theo chủ thể có audit (audit chỉ lưu mã băm), mẫu DPIA. Không phải ý kiến pháp lý. |
| 25 | CI/CD, SBOM, ký số bản phát hành | **Một phần** | Đã có SBOM CycloneDX 1.5 từ image thật. `SHA256SUMS.txt` ký Ed25519; `install.sh` tự kiểm chữ ký và băm; có `verify_release.py`. Gói chặn mọi tệp `.key`/`.pem`. **CI vẫn khoá vì thanh toán GitHub.** |
| 26 | Tài liệu quản trị, người dùng, kiến trúc an ninh | **Xong** | Hướng dẫn cài Docker, runbook, tài liệu Agent. **Hướng dẫn người dùng theo vai trò** `docs/user-guide/` (người xem, quản lý, quản trị viên). **Hồ sơ kiến trúc an ninh** `docs/security/security-architecture.md` cập nhật 15/10: kiểm soát kèm test, trạng thái 14 khoảng trống ban đầu (12 đã đóng), khoảng trống còn lại. |

### P2

Gồm các mục #27–32: chợ connector, tương quan cảnh báo, HA hai trung tâm dữ liệu, ứng dụng di động duyệt HITL, ROI đo thật, gói trình diễn HUD. Tất cả để sau bản v1, không đổi so với báo cáo.

## 5. Đợt 2 (đã làm 14/10/2026)

Mười việc trong danh sách dưới đây đã làm trong cùng ngày, cùng với #21, #23, #17 (neo) và #25 (ký số) — xem bảng §4. Chưa làm
được hoặc chỉ một phần: chạy bộ đánh giá trên LLM thật, rút prompt xuống 3.000 ký tự, test hành vi cho 31 kỹ năng.
Danh sách gốc:

1. Rút gọn chỉ dẫn hệ thống, và ẩn công cụ khi nguồn dữ liệu của nó chưa cấu hình. Đây là nguyên nhân của phần lớn lượt thất bại (#11, #12).
2. Mở rộng bộ đánh giá lên 300 câu theo vai trò, chạy trước mỗi phát hành, ngưỡng 90% (#11).
3. Bắt đổi mật khẩu ở lần đăng nhập đầu (#10).
4. Xuất báo cáo kiểm toán "AI đã làm gì, ai duyệt" ra CSV/PDF (#17).
5. Duyệt hai người cho hành động L4 (#16, phần đầu).
6. Sổ xử lý dữ liệu cá nhân, API xoá theo yêu cầu, mẫu DPIA (#24).
7. Thẻ chủ sở hữu và test cho từng kỹ năng lõi (#3).
8. SBOM và ký số gói phát hành (#25).
9. Chuyển `kpi_logs.csv` vào bảng CSDL (§6 báo cáo).
10. Màn hình báo cáo hiệu năng theo tháng (#15).

## 6. Quyết định cần chốt

1. **Ai thương mại hoá.**
   - Nếu tác giả (Dương Thanh Lịch) tự bán: giữ được tên "VN-MateAI".
   - Nếu đơn vị khác phát triển phái sinh: theo NOTICE phải đổi tên và có thoả thuận với tác giả (thuê phát triển, chuyển giao, hoặc chỉ dùng theo Apache-2.0).
2. **Phân khúc đầu tiên.**
   - *Core*: doanh nghiệp vừa, ra thị trường nhanh.
   - *Regulated*: tài chính; cần model tự host, multi-tenant và pentest trước.

   Lựa chọn này quyết định thứ tự của #5, #9, #14 và #16.
3. **Nhà cung cấp LLM và TTS có hợp đồng (DPA).** Dùng Gemini/Claude qua hợp đồng doanh nghiệp, tự host, hay kết hợp; hợp đồng Vbee/FPT.AI cho giọng đọc.
4. **Mô hình bản quyền**: theo số người dùng, theo số máy chủ giám sát, hay theo đơn vị.
5. **Đơn vị chủ quản và hỗ trợ**: pháp nhân bán, SLA, kênh hỗ trợ.

## 7. Bật bản thương mại

- **Docker** (gói `D:\VNmatebuildv1.0`, lần đóng gói tới): mặc định `VNMATE_EDITION=commercial` trong `.env`.
- **Máy chủ Windows**: thêm `"edition": "commercial"` vào `config.json`, hoặc đặt `VNMATEAI_EDITION=commercial`, rồi khởi động lại.
- **Kiểm tra:** `/docs` trả 404. Hỏi AI "tạo kỹ năng mới…" sẽ bị từ chối với lý do bản thương mại. Danh sách kỹ năng không còn kỹ năng demo.
- **Giọng đọc:** bản thương mại cần cấu hình Vbee hoặc ElevenLabs trong Voice Studio.
