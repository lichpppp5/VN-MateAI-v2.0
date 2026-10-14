# Dev Fleet — điều phối cụm Dev qua Ubuntu Master

VN-MateAI là **Project Authority + AI Supervisor**; Ubuntu Master là **cổng hạ tầng**; OpenClaw là **runtime thực thi**; Mac mini là **worker**. Module này là **ngoại vi**: tắt thì voice, chat, agent, RAG, ERP… vẫn chạy nguyên.

```
Người ─▶ VN-MateAI ──(Master Control API v1, HTTPS + Bearer)──▶ Ubuntu Master ──▶ Mac mini + OpenClaw
         │ dự án · tác vụ · lập lịch · chính sách · duyệt · kiểm chứng · audit      │ Ansible · 9Router · OpenClaw
```

> **Trạng thái trung thực.** Mã phía VN-MateAI đã làm xong và được kiểm thử với **Master giả theo hợp đồng v1** (`tests/fake_master.py`). **Chưa** thử với Ubuntu Master / OpenClaw / Mac mini thật — khi viết, repo và môi trường này không có quyền truy cập vào chúng. Hợp đồng v1 dưới đây là do VN-MateAI **định nghĩa**; phía Master phải cung cấp API đúng hợp đồng (hoặc một adapter mỏng bọc API sẵn có). Xem mục 8.

## 1. Phát hiện khi audit (Phase 0, chỉ đọc)

| Hạng mục | Hiện trạng thật trong repo |
|---|---|
| Cấu trúc | `core/` cũ đã chuyển sang `src/mateai/{application,infrastructure,interfaces}`; prompt mô tả `core/…` là bản cũ |
| OpenClaw / Master / Ansible | Repo **không có** mã tích hợp. Duy nhất: chữ "Mac Mini OpenClaw" trong `application/devices/elastic_grid_manager.py` |
| Sổ worker sẵn có | `elastic_grid_manager` — worker **tự đẩy heartbeat** (`POST /api/v1/worknodes/heartbeat`), RAM, TTL 15 s, trạng thái READY/OFFLINE. **Khác** mô hình Dev Fleet (Master báo hộ), nên giữ nguyên, không trộn. *2026-10-12:* đường này **chưa từng giao việc** (hàm giao việc không được gọi ở đâu) → đã gỡ phần giao việc giả và mục con "Elastic Grid"; danh sách trạm tự báo nhịp tim hiện ở tab Dev Fleet, **chỉ để theo dõi** |
| Sổ tác vụ | `application/tasks/ledger.py` (`op_tasks`): **một** máy trạng thái NEW…COMPLETED, bằng chứng, cổng COMPLETED chỉ khi kiểm chứng đạt — **tái dùng** |
| Chính sách | `policy_engine.authorize` + `zero_trust.execute_with_hitl`: kill switch, L0–L5, duyệt, audit — **tái dùng** |
| Kết nối | `BaseConnector` (timeout, retry, che bí mật) — **tái dùng**; thiếu cầu dao nên thêm cầu dao riêng cho Master |
| Cấu hình / bí mật | `AppSettings` + `secret_box` (`api_token` tự mã hoá trong `config.json`) |
| DB | `db_manager` (SQLite ↔ PostgreSQL dùng chung DDL) |

## 2. Tái dùng / mở rộng / thêm mới

**Tái dùng:** Task Ledger (tác vụ `kind="dev_task"`, bằng chứng, máy trạng thái), `execute_with_hitl` (mọi thao tác ghi), `log_security_audit`, `config_governance.save_config` (đổi chế độ có lịch sử), `BaseConnector`, RBAC (`security_guard`), `require_roles`.

**Mở rộng:** `config/loader.py` (khối `dev_fleet`), `db_manager.py` (+4 bảng `dev_projects`, `dev_runs`, `dev_leases`, `dev_events`), `lifecycle.py` (bước `dev_fleet` + tắt máy), `routes.py`, `security_guard.py` (it_support được XEM).

**Thêm mới:** `application/devfleet/{models,provider,scheduler,verification,service}.py`, `infrastructure/connectors/dev_fleet_master.py`, `interfaces/http/routers/dev_fleet.py`, `skills/dev_fleet_tools.py`.

Không tạo: máy trạng thái thứ hai, sổ node thứ hai, lớp DB thứ hai, LLM engine, pool kết nối thứ hai, tầng xác thực thứ hai.

## 3. Chế độ và công tắc (prompt §36–§37, §65)

`dev_fleet.enabled` + `dev_fleet.mode`:

| mode | Được | Không được |
|---|---|---|
| `disabled` (mặc định) | không làm gì, không gọi mạng | tất cả |
| `read_only` | xem Master / worker / agent / tác vụ / báo cáo | giao, huỷ, chạy lại |
| `controlled` | thêm: giao / huỷ / chạy lại — **qua cổng chính sách** | tự trị |
| `autonomous` | hiện **hành xử như `controlled`** (tự trị có giới hạn là Phase E, chưa làm) | — |

Công tắc: đổi `mode` (FLEET OFF) · `disabled_workers` (tắt từng máy) · **kill switch toàn cục** của hệ thống chặn mọi thao tác ghi của AI (rủi ro > 1) ngoài LLM · huỷ từng tác vụ.

Cấu hình (`config.json → dev_fleet`): `endpoint`, `api_token` (hoặc biến `VNMATEAI_DEV_FLEET_TOKEN`), `tls_verify`, `ca_bundle`, `timeout_s`, `stale_after_s` (60), `offline_after_s` (180), `cache_ttl_s` (5), `stuck_after_s` (900), `lease_ttl_s` (1800), `disabled_workers`.

## 4. Luồng tác vụ

```
đặc tả (mục tiêu + tiêu chí + kiểm chứng) ─▶ chọn máy (scheduler thuần, có lý do) ─▶ thuê workspace/nhánh
 ─▶ cổng chính sách: rủi ro low = tự chạy · medium+ = chờ người duyệt · kill switch/L5 = chặn
 ─▶ kiểm lại máy còn đủ điều kiện ─▶ giao qua Master (Idempotency-Key) ─▶ theo dõi (sync) ─▶ kiểm chứng bằng bằng chứng
 ─▶ COMPLETED chỉ khi đạt · thiếu bằng chứng = COMPLETED_UNVERIFIED (người xác nhận) · trượt = chạy lại / FAILED
```

- Ánh xạ trạng thái: một máy trạng thái duy nhất là của Task Ledger. Lượt chạy (`dev_runs.status`): PENDING_APPROVAL → DISPATCHING → QUEUED → RUNNING → FINISHED/FAILED/CANCELLED, hoặc UNKNOWN/INTERRUPTED khi mất liên lạc. `display_status` chỉ để hiển thị (WAITING_APPROVAL, COMPLETED_UNVERIFIED, INTERRUPTED_UNKNOWN).
- **Không báo thành công giả:** Master báo "FINISHED" chỉ là *thực thi xong*. Hoàn thành cần `require_build` / `require_tests` / `require_commit` đạt; commit được đối chiếu với `HEAD` do Master báo độc lập. Đặc tả không đòi bằng chứng độc lập nào → luôn `COMPLETED_UNVERIFIED`.
- **Mất liên lạc ≠ kết luận:** Master không tới được → worker hạ về `UNKNOWN` (không báo ONLINE), lượt chạy `INTERRUPTED`; không tự đánh dấu xong/hỏng. Giao việc mà mất liên lạc giữa chừng → `UNKNOWN`, không giao lại mù quáng.
- **Giao lại an toàn:** `retry`/chuyển máy chỉ khi lượt cũ được Master xác nhận dừng (hoặc Master không biết lượt đó); không chắc → `refused`.
- **Workspace:** thuê `workspace:{máy}:{repo}` và `branch:{repo}:{nhánh}` có TTL; hai tác vụ không sửa cùng nhánh.
- **Tiến độ dự án:** có trọng số theo ưu tiên, chỉ tính tác vụ COMPLETED đã kiểm chứng; chưa có tác vụ → `null` (không bịa 0%).

## 5. Quyền

| | Xem (`GET /api/v1/dev-fleet/*`) | Ghi (giao/huỷ/chạy lại/chế độ/tắt máy) | Công cụ AI |
|---|---|---|---|
| admin | có | có (vẫn qua duyệt theo rủi ro) | tất cả |
| manager | có | không | — |
| it_support | — | không | 4 công cụ **xem** |
| operator, viewer | không | không | không |

Công cụ AI: `get_dev_fleet_status`, `list_dev_fleet_workers`, `get_dev_fleet_task`, `get_dev_fleet_briefing` (đều L0 — chạy được khi bật kill switch); `create_dev_fleet_task`, `cancel_dev_fleet_task` (ghi, chỉ admin, qua cổng).

## 6. Hợp đồng Master Control API v1

Cơ sở: `{endpoint}/api/v1/fleet`. HTTPS (http chỉ cho host mạng nội bộ). `Authorization: Bearer <api_token>`. Phiên bản lấy từ `health.api_version` (bộ nối chỉ nhận `1.x`; khác → `incompatible`, không đoán).

| Phương thức + đường dẫn | Ghi chú |
|---|---|
| `GET /health` | `{api_version, server_time, master:{master_id,name,hostname,ip,os,version,ansible_status,router_status,openclaw_status,fleet_status,health: healthy\|degraded\|offline}}` |
| `GET /workers` | `{workers:[{worker_id, hostname, platform, architecture, os_version, state, cpu, memory, disk, capabilities[], openclaw_status, openclaw_version, agent_status, health_status, current_project, current_task, current_agent, last_seen, labels}]}` — số đo thiếu thì **bỏ trường**, đừng điền 0 |
| `GET /workers/{id}` · `/metrics` · `/git?repository=` | git trả `{head, branch, dirty}`; repo không có → 404 |
| `GET /agents` | `{agents:[{agent_id, worker_id, role, status, runtime, model, provider, current_task}]}` |
| `POST /tasks/dispatch` | header `Idempotency-Key`; body `{worker_id, task:{task_id, run_id, attempt, title, objective, acceptance_criteria[], verification_steps[], constraints[], repository, branch, required_capabilities[], idempotency_key, …}}` → `{task_id, status: QUEUED\|RUNNING, agent_id?}`. **Master dùng `run_id` làm khoá tác vụ** và phải idempotent theo `Idempotency-Key` |
| `GET /tasks/{id}` | `{status: QUEUED\|RUNNING\|FINISHED\|FAILED\|CANCELLED, exit_code, summary, changed_files[], git_commit, build_status, test_status, progress?, last_progress_at?, agent_id?}`; không biết → 404 |
| `GET /tasks/{id}/logs?tail=` · `POST /tasks/{id}/cancel\|pause\|resume` | cancel phải idempotent |

Ranh giới: Master lo Ansible, 9Router, OpenClaw (pairing, capability approval, **exec approval** của OpenClaw **không bị VN-MateAI vượt qua** — VN-MateAI chỉ giao việc qua Master). Ansible không do LLM chạy ad-hoc; hành động Ansible phải được Master chuẩn hoá thành tác vụ có tên.

## 7. Kiểm thử đã có

`tests/test_dev_fleet_models.py` (27, thuần) · `test_dev_fleet_service.py` (32, Master giả: tắt/chỉ đọc, tươi/cũ, Master chết, bản không tương thích, chờ duyệt, kill switch, giao trùng, kiểm chứng PASS/FAIL/chưa rõ, commit đối chiếu Git, mất liên lạc, thuê nhánh, huỷ, chạy lại, chuyển máy, treo, tiến độ, ranh giới import) · `test_dev_fleet_api_tools.py` (12: phân quyền HTTP, dịch lỗi, công cụ AI, RBAC, rủi ro L0). Cả bộ chạy trên SQLite và PostgreSQL.

## 8. Chưa làm / rủi ro còn lại — **KHÔNG sẵn sàng production**

1. **Chưa thử với Master / OpenClaw / Mac mini thật.** Cần: (a) Master hiện thực đúng hợp đồng v1 (hoặc adapter bọc API sẵn có); (b) kiểm tra phiên bản OpenClaw và giao thức Gateway thực tế ở phía Master (Phase 0 phía hạ tầng: OS, Ansible inventory, vị trí 9Router/OpenClaw, mô hình pairing) — **chưa thực hiện được từ đây**.
2. Mới poll (mặc định 20 s) — chưa có cầu nối **sự kiện đẩy** từ Master (§39) và chưa có backpressure.
3. Chưa có: hàng đợi ưu tiên/SLA, đồ thị phụ thuộc, đường găng, tự thử lại / tự chữa lành, `pause`/`resume` ở service (provider đã có), Redis cho khoá/hiện diện, ước tính chi phí (hiển thị UNKNOWN), đa cluster.
4. Chưa có giao diện (trang Dev Fleet trong Command Center) và đường lệnh nhanh bằng giọng nói; hiện dùng API + công cụ AI.
5. Quyền theo dự án (project scope) chưa có — hiện theo vai trò. Đồng hồ Master lệch được bù bằng `server_time` trong `health`.
6. `autonomous` chưa khác `controlled`.
