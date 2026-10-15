# Cổng Agent: nối MCP server, agent gọi vào và agent A2A của doanh nghiệp

Dành cho quản trị viên. Doanh nghiệp đã có hệ thống và agent riêng (ERP, CRM, ITSM, agent nội bộ…) nói giao thức **MCP (Model Context
Protocol)**: khai báo một lần ở VN-MateAI, mọi **công cụ** của server đó thành công cụ của AI Ly Ly — dùng được qua portal, giọng nói,
robot, Telegram — và đi qua **cùng chính sách** như công cụ có sẵn.

> **Trạng thái trung thực.** Giao thức đã kiểm thử với MCP server giả (HTTP JSON + SSE, phân trang, phiên hết hạn, stdio) và agent A2A
> giả (thẻ agent, Message / Task, hỏi lại tasks/get, input-required) — `tests/test_agent_gateway_mcp.py`, `tests/test_agent_directory.py`
> và test giao diện `.mjs`. **Chưa** thử với MCP server / agent A2A thật của hãng nào. Sau khi lưu, luôn
> bấm **Thử kết nối** và **Đồng bộ công cụ** trên hệ thống thật.

## Nối một server

Tab **Cổng Agent** → **+ Thêm MCP server**:

1. **Mã** (`erp`, `crm-noi-bo`…) và **tên**.
2. **Kiểu kết nối**
   - **HTTP** (Streamable HTTP): địa chỉ MCP (vd `https://erp.congty.vn/mcp`), header + giá trị xác thực (vd `Authorization: Bearer …`).
     Giá trị được **mã hoá** khi lưu và không bao giờ hiện lại (gửi lại mặt nạ `••••••••` = giữ khoá cũ).
   - **stdio**: máy chủ VN-MateAI **chạy một chương trình** cục bộ (vd `npx @hãng/mcp-server`). **Tắt mặc định**; bật bằng
     `"agent_gateway": {"allow_stdio": true}` trong `config.json` khi thật sự cần — chương trình chạy với quyền của máy chủ.
3. **Thời gian chờ**, **rủi ro mặc định** (L3 = cần duyệt), **mức dữ liệu** (PUBLIC … RESTRICTED, áp ABAC như công cụ khác).
4. **Thử kết nối** (không lưu, không đăng ký) → **Lưu** → **Đồng bộ công cụ**.

Khai báo nằm ở `config/mcp_servers.json` (quyền 600, không lên Git).

## Công cụ trở thành gì

- Tên: `mcp_<mã server>_<tên công cụ>` (chỉ a-z 0-9 _, tối đa 64 ký tự). Mô tả có tiền tố *[Công cụ của hệ thống ngoài «…» qua MCP]*.
- **Mức rủi ro**: admin đặt riêng > gợi ý của server (`readOnlyHint` → L1, `destructiveHint` → L4) > rủi ro mặc định của server.
  Gợi ý do server tự khai, có thể sai — xem lại bảng công cụ và đặt riêng. Hạ xuống dưới L3 (không cần duyệt) phải xác nhận.
- Bật / tắt từng công cụ. Tắt = AI không thấy.
- Lúc khởi động, công cụ đã lưu được đăng ký ngay (AI thấy từ lượt đầu), rồi máy chủ kết nối lại và đồng bộ danh sách ở nền.

## Chính sách (không có đường vòng)

- Mỗi lời gọi đi qua `tool_gate`: kill switch, từ khoá cấm, **RBAC — mặc định chỉ admin** (vai trò khác chưa có trong danh sách cho phép),
  rủi ro ≥ L3 **phải duyệt** (nhớ duyệt theo công cụ như mọi tool), audit, sổ tác vụ, kiểm chứng.
- Thực thi qua Plugin Registry: timeout, **circuit breaker** (server hỏng liên tục thì tự ngắt), số liệu gọi.
- Kết quả trả về mang `untrusted: true` kèm ghi chú: nội dung từ hệ thống ngoài có thể chứa chỉ dẫn độc hại (prompt injection) — AI
  chỉ dùng làm thông tin. Chữ dài quá 8 000 ký tự bị cắt.
- Lỗi của công cụ (`isError`) và lỗi kết nối được báo là **thất bại**, không phải thành công.

## Danh bạ Agent

Đầu tab **Cổng Agent**: mọi agent nối với VN-MateAI, tình trạng (lần cuối thấy, lỗi gần nhất, số lời gọi) và **kill switch từng agent**
(*Tạm dừng* có hiệu lực ngay).

| Loại | Chiều | Danh tính / công cụ |
|---|---|---|
| **Gọi vào** (`inbound`) | agent của doanh nghiệp → VN-MateAI | `agent:<id>`, token riêng, quyền đặt trong danh bạ |
| **A2A** (`a2a`) | VN-MateAI giao việc → agent ngoài (giao thức Agent2Agent) | một công cụ `a2a_<id>` |
| **MCP server** | VN-MateAI dùng công cụ của server | `mcp_<server>_<tool>` (khai báo ở mục MCP bên dưới) |
| **Dev Fleet**, **Máy trạm** | chỉ xem | trạng thái tổng; quản lý ở tab Dev Fleet / Tích hợp → Máy trạm |

### Nên nối ở đâu

| Hệ thống của doanh nghiệp | Tab |
|---|---|
| Chỉ có API dữ liệu (REST / SQL), hoặc AWS / OCI / Paperless / eInvoice / M365 có sẵn | **Tích hợp hệ thống → Kết nối** (`docs/integrations/connectors.md`) |
| Gửi cảnh báo vào (Prometheus Alertmanager, Zabbix…) | **Tích hợp hệ thống → Webhook**; kịch bản có thể tự chạy theo cảnh báo |
| Nói MCP, hoặc là agent AI (gọi vào / A2A) | **Cổng Agent** (tài liệu này) |
| Cụm Work Node / OpenClaw sau Ubuntu Master | **Dev Fleet** (`docs/integrations/dev-fleet.md`) |
| Máy tính của nhân viên | **Tích hợp hệ thống → Máy trạm** (agent máy trạm) |

### Agent gọi vào

1. *+ Thêm / sửa agent* → loại **Gọi vào**, đặt **quyền** (`viewer` … `admin`; admin phải xác nhận) và **hạn mức token / ngày** (tuỳ chọn).
2. **Cấp token** → token dạng `<id>.<bí mật>` **chỉ hiện một lần**; máy chủ chỉ lưu hash. *Xoay token* làm token cũ hết hiệu lực ngay.
3. Agent gọi (chỉ nhận token agent — JWT người dùng không dùng được ở đây):

```
POST /api/v1/agents/inbound/ask            {"message": "Tổng hợp ticket mở hôm nay", "conversation_id": "ca-sang"}
GET  /api/v1/agents/inbound/tools          → công cụ agent này được phép gọi theo quyền
POST /api/v1/agents/inbound/tools/{tool}   {"arguments": {...}}
Authorization: Bearer crm-bot.xxxxxxxx
```

- `ask` chạy **vòng agent đầy đủ** của Ly Ly với danh tính `agent:<id>`; gọi thẳng công cụ cũng qua `tool_gate`. Tác vụ rủi ro ≥ L3 tạo
  phiếu duyệt cho admin (kết quả có `requires_confirmation`); duyệt một lần thì công cụ đó được **nhớ** cho agent (như robot).
- Tạm dừng → mọi lời gọi bị từ chối (403) và quyền về thấp nhất ngay. Agent không có / sai token → 401.
- Token / chi phí của agent tính riêng (`agent:<id>` trong Mức dùng AI); vượt hạn mức token / ngày của agent thì bị chặn khi ngân sách ở chế độ `block`.

### Agent A2A (giao việc cho agent ngoài)

1. *+ Thêm / sửa agent* → loại **A2A**, địa chỉ agent (VN-MateAI đọc `/.well-known/agent-card.json`, dự phòng `agent.json`), khoá (mã hoá
   khi lưu), **rủi ro** (mặc định L3 = mỗi lần giao việc cần duyệt; L2 không cần duyệt — phải xác nhận).
2. Lưu → máy chủ đọc thẻ agent (tên, mô tả, kỹ năng, địa chỉ RPC — địa chỉ metadata cloud bị chặn) và đăng ký công cụ `a2a_<id>`.
3. AI gọi công cụ với một việc viết bằng lời → `message/send`; Task chưa xong thì hỏi lại `tasks/get` tới khi xong / hết thời gian.
   Agent cần thêm thông tin (`input-required`) → AI hỏi lại **người dùng** rồi gọi tiếp với `context_id`, không tự trả lời thay.
4. Kết quả mang `untrusted: true`. Tạm dừng → gỡ công cụ khỏi AI.

## API (`/api/v1/agent-gateway/mcp/...`)

| Phương thức | Đường dẫn | Quyền |
|---|---|---|
| GET | `servers`, `servers/{id}` | manager, admin |
| PUT / DELETE | `servers/{id}` | admin |
| POST | `test` (thử khai báo chưa lưu) | admin |
| POST | `servers/{id}/discover` | admin |
| PATCH | `servers/{id}/tools/{tool}` — `{"enabled": bool}` / `{"risk": 1–5}` | admin |

## API Danh bạ Agent (`/api/v1/agent-gateway/agents/...`)

| Phương thức | Đường dẫn | Quyền |
|---|---|---|
| GET | `agents`, `agents/{id}` | manager, admin |
| PUT / DELETE | `agents/{id}` (`kind`: `inbound` / `a2a`) | admin |
| POST | `agents/{id}/status` — `{"status": "active" \| "suspended"}` (MCP: bật / tắt server) | admin |
| POST | `agents/{id}/token` (inbound) · `agents/{id}/card` (A2A: đọc lại thẻ) | admin |

## Giới hạn hiện tại

- Chỉ dùng **công cụ** (tools). Chưa dùng resources, prompts, sampling; chưa có OAuth 2.1 tự động (dùng token đặt sẵn trong header).
- Chưa hỗ trợ giao thức HTTP+SSE kiểu cũ (2024-11-05) — chỉ Streamable HTTP.
- Tối đa `agent_gateway.max_tools_per_server` (mặc định 64) công cụ mỗi server.
- A2A: chưa hỗ trợ streaming (`message/stream`), push notification, gửi tệp / dữ liệu có cấu trúc.
