# Nghiên cứu tích hợp từ bộ mẫu `D:\mautichhop` (2026-10-15)

Phạm vi: 9 dự án mẫu. Mục tiêu: tìm tính năng, thuật toán, skill giúp VN-MateAI **chính xác hơn, ổn định hơn**, và trả lời
câu hỏi: có khung agent vận hành theo vai (Dev, BA, DB, System, nghiệp vụ đặc thù) đấu nối linh hoạt được không.

Phương pháp: đọc mã và tài liệu của từng dự án (không chạy mã tải về), đối chiếu với mã VN-MateAI hiện tại để chỉ đề xuất
thứ **còn thiếu** — không đề xuất lại thứ đã có.

## 1. Tóm tắt

| Kết luận | Chi tiết |
|---|---|
| **Nên làm (ưu tiên 1)** | Khung Vai trò Agent (§4) · Hợp nhất thứ hạng RRF cho RAG (§3.1) · Vòng thẩm định "người làm ≠ người kiểm" (§3.2) · Sandbox kỹ năng qua giao diện adapter (§3.4) |
| **Nên làm (ưu tiên 2)** | Kiểm chứng hậu điều kiện kiểu behavior-validator (§3.3) · MMR khử trùng lặp đoạn RAG (§3.1) · Closeout + chắt lọc kinh nghiệm (§4.6) |
| **Để sau** | Nén ngữ cảnh thích ứng · Checkpoint tác vụ dài · Diễn đạt lại câu hỏi |
| **Không nên** | Nhúng nguyên agentUniverse làm khung · Dùng OpenClaw làm lõi · Chép mã `openclaw-multi-agent-manager` (không có giấy phép) |

Nguyên tắc chung: **tái hiện thuật toán / mẫu thiết kế theo phong cách VN-MateAI** (tiếng Việt, đi qua `tool_gate`, có test,
SQL ở tầng infrastructure), không nhúng khung ngoài. Mọi vai trò / sandbox mới chỉ được **thu hẹp** quyền, không bao giờ nới.

## 2. Bộ mẫu — là gì, giấy phép, giá trị

| Dự án | Là gì | Giấy phép | Giá trị cho VN-MateAI |
|---|---|---|---|
| `agentUniverse-master` (Ant Group) | Khung đa tác nhân Python: mẫu PEER / GRR / IS, RAG (RRF, MMR, rerank, chia đoạn), nén ngữ cảnh, OTel | Apache-2.0 | **Cao** — thuật toán; **không** nhúng khung (xung đột phụ thuộc, §5) |
| `openclaw-dev-main` (Microsoft) | Mẫu triển khai OpenClaw trên Azure; **sandbox MCP**: adapter, pool, egress mặc định chặn | MIT | **Cao** — kiến trúc sandbox (§3.4) |
| `opencrew-main` | "Hệ điều hành" đội agent: thang tự chủ, phân loại việc Q/A/P/S, closeout, chắt lọc tri thức, giao thức A2A có giới hạn vòng | MIT | **Cao** — quy trình vận hành đa tác nhân (§4) |
| `openclaw-multi-agent-team-main` | Đội phần mềm 11 vai (PM, Architect, Backend, QA, Security, DevOps…), vòng đời task, quy tắc định tuyến | MIT | **Cao** — khung vai Dev / BA / DB / System (§4) |
| `agent-skills-main` | Skill dùng chung cho coding agent: `behavior-validator`, `autoreview`, `handoff` | MIT | **TB** — mẫu kiểm chứng chống gian lận (§3.3), mẫu gói giao việc |
| `openclaw-agents-main` | 9 agent nghiên cứu học thuật, cặp đối kháng Ideator↔Critic | MIT | **Thấp** — chỉ lấy mẫu cặp đối kháng + leo thang sau 3 vòng |
| `openclaw-skill-claude-code-main` | Chạy Claude Code thành job tách rời (start / status / logs / kill) | MIT | **Thấp** — Dev Fleet của VN-MateAI đã làm, có kiểm chứng bằng bằng chứng |
| `openclaw-multi-agent-manager-main` | Trình tạo đội agent cho Feishu, thư viện 100+ vai | **Không có LICENSE** | **Chỉ ý tưởng** (topology cộng tác) — không được chép mã |
| `openclaw-tutorial-main` | Giáo trình 12 ngày OpenClaw (cron, heartbeat, webhook) | MIT | **Thấp** — tài liệu học; VN-MateAI đã có sentinel / proactive / webhook |

## 3. Tính năng và thuật toán nên tích hợp

### 3.1 RAG: hợp nhất thứ hạng RRF + MMR (agentUniverse) — chính xác hơn

**Hiện trạng VN-MateAI** (`application/knowledge/rag_engine.py`): Chroma dùng mô hình nhúng **tiếng Anh** (MiniLM) — đo thật: câu
hợp lệ cosine 0,071, câu bịa 0,373. Cổng chống ảo giác đã đúng (bằng chứng từ khoá `lexical_evidence_gate`), nhưng **thứ hạng
vẫn theo cosine**, còn kênh BM25 nằm riêng trong `graph_rag.py`. Hai kênh không được hợp nhất.

**Đề xuất:** truy hồi hai kênh (vector top-N, BM25 / từ khoá top-N) rồi hợp nhất bằng **Reciprocal Rank Fusion**:
`score(d) = Σ_kênh 1 / (k + hạng_kênh(d))`, `k = 60`. Không cần so điểm giữa các kênh (cosine và BM25 không cùng thang) —
đúng chỗ yếu hiện nay. Sau đó **MMR** (`λ·sim(d,q) − (1−λ)·max sim(d, đã chọn)`) để bỏ các đoạn gần trùng (chunk chồng lấn
150 ký tự hiện nay hay trả hai đoạn gần giống nhau). Với tiếng Việt, độ giống giữa đoạn dùng Jaccard trên `content_tokens`
(đã có) thay cho embedding tiếng Anh.

| Giá trị | Công sức | Rủi ro | Cách làm |
|---|---|---|---|
| Cao (thứ hạng đúng hơn cho tiếng Việt, ít đoạn trùng) | ~1,5 ngày | Thấp — cổng bằng chứng giữ nguyên | Tái hiện ~80 dòng, đo trước/sau bằng `eval_llm` nhóm chính sách + bộ câu RAG riêng |

Ghi chú: nguyên nhân gốc là mô hình nhúng tiếng Anh; đổi sang mô hình nhúng đa ngữ là việc riêng (không có trong bộ mẫu),
RRF vẫn có lợi kể cả sau khi đổi.

### 3.2 Vòng thẩm định "người làm ≠ người kiểm" (PEER / GRR của agentUniverse, Orchestrator≠Worker của OpenCrew)

**Ý chính** (cả ba nguồn cùng kết luận): một AI vừa làm vừa tự chấm sẽ dễ dãi với chính nó. Tách vai **sinh** và **thẩm
định**; thẩm định trả `{đạt: bool, điểm, góp ý}`; dưới ngưỡng thì viết lại, tối đa N vòng; bế tắc thì đẩy lên người.

**Hiện trạng:** VN-MateAI có kiểm chứng *hành động* (ledger, Dev Fleet, playbook verify) nhưng **chưa thẩm định nội dung trả
lời** — báo cáo, số liệu, kết quả `delegate_to_specialist` đi thẳng tới người dùng.

**Đề xuất:** bước thẩm định cho câu trả lời **rủi ro cao** (có số liệu tài chính / nhân sự, báo cáo tổng hợp, kết quả chuyên
gia), kênh chữ (Portal, Telegram, email) — không áp cho thoại vì độ trễ. Tiêu chí thẩm định cụ thể, kiểm được:
1. Mọi con số / tên / mốc thời gian trong câu trả lời phải xuất hiện trong kết quả công cụ của lượt đó (đối chiếu bằng mã
   trước, LLM sau).
2. Không tuyên bố "đã làm" khi không có công cụ báo thành công (Quy tắc 1 — nay được kiểm độc lập).
3. Trả lời đúng câu hỏi (mẫu `is_useful` của agentUniverse).

Model thẩm định nên **khác** model sinh (có sẵn chuỗi model 9Router). Tối đa 2 vòng; không đạt thì trả lời kèm cảnh báo
"chưa kiểm chứng được: …" thay vì im lặng.

| Giá trị | Công sức | Rủi ro | Cách làm |
|---|---|---|---|
| Cao (giảm ảo giác số liệu — rủi ro lớn nhất với giám đốc) | 2–3 ngày | +1 lời gọi LLM, +3–6 s ở câu rủi ro cao | Tái hiện; thêm nhóm câu "bẫy số liệu" vào bộ đánh giá |

### 3.3 Kiểm chứng hậu điều kiện, chống gian lận (agent-skills `behavior-validator`)

**Ý chính:** kiểm chứng theo **hợp đồng hành vi**, không tin lời tự báo; "phép thử chống gian lận": đổi dữ liệu, làm mới, thử
lại, kiểm tính lưu bền, xác nhận nút bấm *làm việc thật* chứ không chỉ hiện chữ thành công.

**Hiện trạng:** Dev Fleet và playbook đã kiểm bằng bằng chứng; **lượt agent thường thì chưa** — `restart_service` trả
`success` là ghi COMPLETED.

**Đề xuất:** bảng **hậu điều kiện theo công cụ** — sau công cụ ghi (khởi động lại dịch vụ, tạo ticket, ghi tệp…) tự gọi một
công cụ **đọc độc lập** để xác nhận (vd. `manage_windows_service restart` → `get_service_status` = Running). Không xác nhận
được → `COMPLETED_UNVERIFIED` như Dev Fleet, AI nói rõ "đã gửi lệnh, chưa xác nhận được".

| Giá trị | Công sức | Rủi ro | Cách làm |
|---|---|---|---|
| TB–Cao (ổn định, trung thực về kết quả) | ~2 ngày cho 10–15 công cụ ghi phổ biến | Thêm một lời gọi đọc / hành động | Tái hiện, gắn vào `tool_gate` sau thực thi |

### 3.4 Sandbox kỹ năng qua giao diện adapter (openclaw-dev `sandbox_mcp`) — ổn định + an toàn

**Ý chính:** mọi chi tiết nền tảng sandbox nằm sau **một giao diện** (`SandboxAdapter`: boot, specialize, exec, suspend,
resume, destroy); **pool** tái dùng sandbox ấm theo phiên; **egress mặc định chặn** + danh sách cho phép theo tác vụ;
đặc tả theo vai (`SandboxSpec`: env, giới hạn CPU / RAM, nhãn).

**Hiện trạng:** kỹ năng do AI tạo được kiểm AST rồi **nạp thẳng vào tiến trình máy chủ** (`importlib`), và chủ dự án đã quyết
`auto_execute = true` (14/10). Hạng mục thẩm định #7 (sandbox) còn mở.

**Đề xuất:** cùng giao diện, driver đầu tiên là **Docker cục bộ** (không cần Azure): `--network none` (hoặc proxy egress theo
danh sách cho phép), `--read-only`, `--memory`, `--cpus`, `--pids-limit`, người dùng không phải root, hết giờ thì huỷ;
tuỳ chọn `runtime=runsc` (gVisor) trên Linux. Kỹ năng AI tự tạo và mã AI sinh chạy trong sandbox, chỉ nhận đầu vào JSON và trả
JSON. Kỹ năng cần máy thật (Windows, phần cứng) vẫn đi Agent máy trạm như hiện nay.

| Giá trị | Công sức | Rủi ro | Cách làm |
|---|---|---|---|
| Cao (giữ được quyết định tự cài kỹ năng mà không đặt cược cả máy chủ; đóng #7) | 4–6 ngày | Khởi động container ~1 s/lần → dùng pool ấm | Tái hiện giao diện; driver Docker mới |

### 3.5 Để sau

| Mục | Nguồn | Vì sao để sau |
|---|---|---|
| Nén ngữ cảnh thích ứng (cắt / chọn lọc / tóm tắt theo ưu tiên) | agentUniverse `context/compressor` | VN-MateAI đã cắt lịch sử theo lượt và ký tự; lợi ích chỉ rõ ở phiên chữ rất dài |
| Checkpoint tác vụ dài | OpenCrew `CHECKPOINT_TEMPLATE` | Ledger đã lưu bước; cần khi có tác vụ đa tác nhân kéo dài nhiều ngày (§4) |
| Diễn đạt lại câu hỏi / trích từ khoá | agentUniverse `query_paraphraser` | Thêm độ trễ; làm sau RRF và đo lại |
| Heartbeat gộp kiểm tra, giờ yên lặng | openclaw-tutorial | Đã có sentinel, proactive manager |
| Job tách rời cho coding agent | openclaw-skill-claude-code | Dev Fleet đã làm, tốt hơn (kiểm chứng bằng Git / test) |

## 4. Khung Vai trò Agent (Dev · BA · DB · System · nghiệp vụ đặc thù)

**Trả lời câu hỏi: có, và nên làm** — nhưng dựng **bên trong** VN-MateAI trên các khối đã có (vòng agent, `tool_gate`, ledger,
HITL, Dev Fleet, danh bạ A2A), không nhập khung ngoài. Bộ mẫu cho đủ "bản thiết kế":

| Mẫu | Lấy từ |
|---|---|
| Định nghĩa vai: sứ mệnh, phạm vi, việc cấm, định dạng báo cáo, mức chặn `info / warning / blocking` | `openclaw-multi-agent-team/roles/*/AGENTS.md` |
| Quy tắc định tuyến: agent chính tự làm khi **chỉ đọc + không lưu bền + rủi ro thấp**; còn lại vào quy trình đội | `multi-agent-team/docs/concepts/routing-decision.md` |
| Vòng đời: intake → clarify → plan → execute → review → final → archived (+ blocked, cancelled) | `multi-agent-team/task-lifecycle.md` |
| Gói giao việc: Mục tiêu · Ngữ cảnh · Sản phẩm · **Định nghĩa "xong"** · Ranh giới · Định dạng trả về; cấm sinh tiếp sub-agent | OpenCrew `SUBAGENT_PACKET_TEMPLATE` |
| Giới hạn vòng, dừng khi 2 vòng không tiến triển, điều phối ≠ thực thi | OpenCrew `A2A_PROTOCOL` |
| Độ sâu tối đa 2, số con tối đa mỗi agent, tổng chạy đồng thời | `multi-agent-team/subagent-architecture.md` |
| Topology cộng tác: vai → vai khác + điều kiện kích hoạt | ý tưởng từ `multi-agent-manager` (không chép mã) |
| Closeout bắt buộc → chắt lọc nguyên tắc / mẫu / "vết sẹo" | OpenCrew `CLOSEOUT_TEMPLATE`, `KNOWLEDGE_PIPELINE` |

### 4.1 Định nghĩa vai (lưu CSDL, sửa trên Portal, có phiên bản + audit như kịch bản)

```yaml
id: dba
ten: "Quản trị CSDL"
su_menh: "Chẩn đoán và tối ưu cơ sở dữ liệu; không bao giờ tự sửa dữ liệu thật"
cong_cu: [run_local_sql_check, list_data_sources, fetch_data_source, query_company_policy]   # danh sách cho phép → thu hẹp tool
du_lieu: {phong_ban: [IT], cap_bao_mat_toi_da: 3}          # ABAC — giao với quyền người yêu cầu, không vượt
tu_chu_toi_da: 2                                           # bước rủi ro > 2 → HITL như hiện nay
hop_dong_dau_ra: [ket_luan, bang_chung, thay_doi_de_xuat, rui_ro, buoc_tiep]
cong_tac: [{vai: system, khi: "nghi do tài nguyên máy chủ"}, {vai: dev, khi: "cần đổi truy vấn trong mã"}]
nguoi_tham_dinh: qa
backend: local                                             # local | a2a:<id> | devfleet
model_uu_tien: [ag/gemini-3.8-flash-medium]
```

Bất biến (thực thi bằng mã, không bằng prompt): vai **chỉ thu hẹp** công cụ / dữ liệu / tự chủ so với người yêu cầu; mọi lời
gọi công cụ vẫn qua `tool_gate` với danh tính người yêu cầu; L5 vẫn DENY.

### 4.2 Bộ vai đề xuất ban đầu

| Vai | Làm gì | Công cụ chính | Không được |
|---|---|---|---|
| **BA** — Phân tích nghiệp vụ | Làm rõ yêu cầu, viết user story + tiêu chí chấp nhận, gói yêu cầu cho Dev | RAG chính sách, dữ liệu tổ chức (đọc), ticket | Ghi bất cứ gì ngoài tài liệu |
| **Dev** | Thiết kế, sửa mã, viết test | **Dev Fleet** (đã có, kiểm chứng build / test / commit) | Deploy production, push thẳng nhánh chính |
| **DB** — Quản trị CSDL | Truy vấn đọc, EXPLAIN, đề xuất index / migration | SQL chỉ đọc, nguồn dữ liệu | Câu lệnh ghi → L4, cần duyệt |
| **System** — Quản trị hệ thống / SRE | Giám sát, chẩn đoán, khởi động lại dịch vụ, chạy kịch bản | Giám sát hạ tầng, máy trạm, playbook | Thay đổi cấu hình mạng / bảo mật không duyệt |
| **QA / Thẩm định** | Kiểm "định nghĩa xong", chạy hậu điều kiện (§3.3) | Công cụ đọc | Sửa kết quả của vai khác |
| **Security** | Rà kế hoạch có rủi ro trước khi xin duyệt | `security/explain`, chính sách | Tự duyệt |
| **Đặc thù** — Kế toán, Nhân sự, Pháp chế, Kinh doanh… | Chuyển CFO / HR / CTO hiện tại (đang cứng trong `agent_orchestrator.py`, chọn bằng từ khoá) thành vai cấu hình | Theo nghiệp vụ | Theo ABAC hiện có |

### 4.3 Điều phối

1. **Ly Ly (agent chính)** nhận yêu cầu. Chỉ đọc + không lưu bền + rủi ro thấp → tự làm như hôm nay (thoại giữ nguyên đường
   nhanh).
2. Còn lại: mở **tác vụ cha** trong ledger, lập kế hoạch, chọn vai bằng LLM phân loại trên **danh mục vai** (thay
   `can_handle` theo từ khoá), sinh **gói giao việc** có "định nghĩa xong".
3. Mỗi vai chạy vòng agent hiện có với prompt vai + **bộ công cụ thu hẹp** (đã đo trên LLM thật: thu hẹp giảm ~70% token, chất lượng
   bằng nhau — việc §10.2 của bản đồ hệ thống đang chờ, khung vai giải quyết luôn). Tác vụ con ghi vào ledger.
4. Vai thẩm định chấm theo định nghĩa xong; tối đa 3 vòng; 2 vòng không tiến triển → hộp thư HITL của quản trị viên.
5. Ly Ly tổng hợp và trả lời người dùng; kênh thoại nói "em đã giao cho nhóm, xong sẽ báo".

### 4.4 Đấu nối linh hoạt (`backend` của vai)

| Backend | Dùng khi | Đã có sẵn |
|---|---|---|
| `local` | Vai chạy bằng LLM qua 9Router | Vòng agent, `tool_gate` |
| `devfleet` | Vai Dev cần sửa mã thật trên worker OpenClaw / Ubuntu Master | Dev Fleet + kiểm chứng bằng chứng |
| `a2a:<id>` | Doanh nghiệp đã có agent riêng (vd. agent DBA của nhà cung cấp) | Danh bạ agent A2A (rủi ro mặc định L3, qua cổng) |
| `mcp` | Công cụ chuyên ngành từ MCP server | MCP gateway |

### 4.5 Rủi ro và cách chặn

| Rủi ro | Chặn bằng |
|---|---|
| Độ trễ tăng 2–5 lần (nhiều lời gọi LLM) | Chỉ kênh chữ / bất đồng bộ; thoại giữ một agent |
| Vòng lặp vô tận, sinh agent dây chuyền | Độ sâu ≤ 2, số con ≤ 4, số vòng ≤ 3 — **trong mã**. OpenCrew ghi rõ kỷ luật bằng prompt chỉ ổn định trên Claude Opus |
| Vai được nhiều quyền hơn người yêu cầu | Giao (∩) quyền vai với quyền người dùng; test hợp đồng |
| 9Router hết hạn mức (đang xảy ra) | `model_uu_tien` theo vai + chuỗi dự phòng có hedging (đã làm 15/10) |
| Prompt injection lan giữa các vai | Kết quả vai khác là **dữ liệu** (Quy tắc 7), không phải chỉ dẫn |

### 4.6 Closeout và chắt lọc kinh nghiệm (OpenCrew KO)

Mỗi tác vụ đa tác nhân kết thúc bằng **closeout** 10–15 dòng (đã đổi gì, quyết định, bước tiếp, "vết sẹo", điểm tín hiệu
0–3). Định kỳ, một tác vụ nền đọc closeout điểm ≥ 2 và đề xuất **tối đa 2** nguyên tắc / mẫu / vết sẹo — đưa vào bộ nhớ với
mức tin cậy "chờ duyệt" (cơ chế memory trust đã có), quản trị viên duyệt thì AI mới dùng. Đây là cách hệ thống *chính xác hơn
theo thời gian* mà không tự sửa luật của mình.

### 4.7 Lộ trình

| Giai đoạn | Nội dung | Ước lượng |
|---|---|---|
| 1 | Lược đồ vai + kho + Portal CRUD (admin, audit, phiên bản); chuyển CFO / HR / CTO sang cấu hình; thu hẹp công cụ theo vai; điều phối + gói giao việc + tác vụ con trong ledger; đo bằng `eval_llm` | 4–5 ngày |
| 2 | Vòng thẩm định (§3.2), quy tắc dừng, closeout | 3–4 ngày |
| 3 | Backend `devfleet` / `a2a`; chắt lọc kinh nghiệm; bộ câu đánh giá theo từng vai | 3–4 ngày |

## 5. Không nên làm

| Đề xuất | Lý do |
|---|---|
| Nhúng nguyên agentUniverse | Xung đột phụ thuộc trực tiếp: `chromadb 0.4.24` (VN-MateAI cần ≥ 1.5), `openai 1.55.3` (VN-MateAI ghim 1.30.1), `langchain 0.1.20`, Flask, `grpcio` ghim; trùng toàn bộ tầng LLM / bộ nhớ / máy chủ; thiên về tiếng Trung (jieba, DashScope). Tái hiện thuật toán thì được lợi mà không mang nợ |
| Dùng OpenClaw làm lõi | Trùng lõi; OpenClaw đã được đấu nối đúng chỗ (worker qua Dev Fleet) |
| Chép mã `openclaw-multi-agent-manager` | Không có giấy phép — mặc định giữ mọi quyền |
| Mẫu "agent tự cài bằng cách curl tài liệu từ xa rồi làm theo" (openclaw-agents) | Chính là prompt injection theo thiết kế; trái Quy tắc 7 |
| Skill `autoreview`, `crabbox`, `beam`… | Công cụ cho *đội phát triển* dùng coding agent, không phải tính năng sản phẩm |

## 6. Giấy phép

VN-MateAI là Apache-2.0. Mã MIT và Apache-2.0 dùng được nếu giữ thông báo bản quyền (thêm vào `THIRD_PARTY_NOTICES.md`). Khuyến
nghị vẫn là **tái hiện** để khớp kiến trúc và test; nếu chép nguyên đoạn (vd. giao diện `SandboxAdapter`), ghi nguồn trong tệp.
`openclaw-multi-agent-manager`: chỉ tham khảo ý tưởng.
