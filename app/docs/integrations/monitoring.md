# Giám sát hạ tầng bằng Prometheus và Grafana

VN-MateAI **đọc** dữ liệu giám sát có sẵn của doanh nghiệp (Prometheus, Grafana) để: hiện tình trạng hạ tầng trong một màn hình, tự mở **sự cố** và báo qua kênh cảnh báo khi có cảnh báo nghiêm trọng, và cho AI trả lời câu hỏi về hạ tầng bằng số liệu thật. Module **không ghi** vào Prometheus / Grafana (chỉ GET).

> **Trạng thái trung thực.** Đã kiểm thử với máy chủ giả đúng hình dạng API công khai của Prometheus và Grafana (`tests/fake_enterprise.py`). **Chưa** thử với Prometheus / Grafana thật. Các truy vấn mặc định theo tài liệu của hãng; khác biệt phiên bản sửa trong khai báo nguồn.

## 1. Kết nối

**Tích Hợp Hệ Thống → Thêm kết nối → chọn mẫu**:

| Mẫu | Địa chỉ | Khoá | Ghi chú |
|---|---|---|---|
| **Prometheus** | `http://prometheus.congty.local:9090` | không (hoặc đổi kiểu xác thực nếu đặt sau proxy) | chỉ đọc |
| **Grafana** | `https://grafana.congty.local` | Service Account token, vai trò **Viewer** | dùng cả Alertmanager nội bộ của Grafana |

Bấm **Thử kết nối**, lưu, rồi mở tab **Giám Sát Hạ Tầng**. Một nguồn có nhãn `monitor_type` (`prometheus` / `grafana`) trong khai báo; mẫu đã đặt sẵn nhãn và các truy vấn đặt tên module cần (`canh bao`, `muc tieu`, `truy van` cho Prometheus; `canh bao dang bat`, `quy tac`, `dashboard` cho Grafana). Tự khai báo nguồn khác phải giữ đúng các tên đó.

## 2. Hiển thị gì

| Mục | Nguồn dữ liệu | Ghi chú |
|---|---|---|
| Cảnh báo đang bật | Prometheus `/api/v1/alerts`; Grafana `/api/alertmanager/grafana/api/v2/alerts` | bỏ cảnh báo bị tắt tiếng; mức nghiêm trọng suy từ nhãn `severity` (critical/page/high/error → nghiêm trọng; warning/warn → cảnh báo; còn lại thông tin) |
| Target down | Prometheus `/api/v1/targets` | kèm lỗi scrape gần nhất |
| CPU · RAM · ổ đĩa theo máy | PromQL (instant) | thử `node_exporter`, nếu không có dữ liệu thì thử `windows_exporter`; ngưỡng cảnh báo/nghiêm trọng cấu hình được |
| Quy tắc cảnh báo Grafana | `/api/prometheus/grafana/api/v1/rules` | đang bắn / chờ / lỗi |
| Dashboard Grafana | `/api/search` | liên kết mở ở tab mới |
| Truy vấn PromQL | hộp nhập tay | chỉ đọc, tối đa 600 ký tự |

Không có số đo → hiện "—" kèm lý do (exporter chưa chạy / khác tên metric); **không điền 0**. Nguồn mất liên lạc → thẻ nguồn ghi "Mất liên lạc" kèm lỗi, các nguồn khác vẫn hiển thị; tất cả mất liên lạc → tình trạng **UNKNOWN**, không báo "ổn".

## 3. Sự cố tự động

Mỗi 60 s (cấu hình): cảnh báo bắn ở mức ≥ `incident_min_severity` (mặc định `critical`) → mở **một** sự cố trong sổ tác vụ (AI **không** tự xử lý, người phụ trách xử lý) và gửi cảnh báo qua các kênh đã kết nối (Telegram/Teams/Email…). Cảnh báo hết bắn → đóng sự cố bằng bằng chứng "nguồn không còn báo", gửi tin khôi phục. **Chỉ đóng khi nguồn còn liên lạc được** — mất liên lạc không bị hiểu là "đã hết lỗi". Mỗi chu kỳ mở tối đa `max_incidents_per_cycle` sự cố (chống bão cảnh báo), phần còn lại ở chu kỳ sau. Sự cố hiện ở 🔔 và tab Bảng điều khiển như mọi sự cố khác.

## 4. Cấu hình (`config.json → monitoring`)

`enabled` (true) · `interval_s` (60) · `cache_ttl_s` (20) · `incident_min_severity` (`critical` | `warning`) · `max_incidents_per_cycle` (10) · `cpu_warn/cpu_crit` (85/95) · `memory_warn/crit` (90/95) · `disk_warn/crit` (85/95).

## 5. AI và API

Công cụ AI (đều chỉ đọc, L0 — dùng được cả khi bật kill switch; admin + it_support): `get_infra_status`, `get_infra_alerts`, `query_prometheus`, `list_grafana_dashboards`. Chưa khai báo nguồn → công cụ nói thẳng "chưa có nguồn giám sát", không bịa.

API (manager + admin): `GET /api/v1/monitoring/overview`, `POST /api/v1/monitoring/query` (PromQL), `POST /api/v1/monitoring/refresh` (admin: thu thập + đồng bộ sự cố ngay).

## 7. Giám sát lại chính VN-MateAI (`GET /metrics`)

VN-MateAI xuất số đo của chính nó theo định dạng Prometheus để **Prometheus / Grafana của bạn cảnh báo khi hệ thống AI có vấn đề**.

1. Đặt token: biến môi trường `VNMATEAI_METRICS_TOKEN` (khuyên dùng) hoặc `config.json → monitoring.metrics_token` (tự được mã hoá khi lưu). **Chưa đặt token = endpoint tắt (404).**
2. Thêm vào `prometheus.yml`:

```yaml
scrape_configs:
  - job_name: vn-mateai
    scheme: https
    metrics_path: /metrics
    authorization: { type: Bearer, credentials_file: /etc/prometheus/vnmateai.token }
    tls_config: { ca_file: /etc/prometheus/ca-noi-bo.pem }   # hoặc insecure_skip_verify: true cho chứng chỉ tự ký
    static_configs: [{ targets: ["vnmateai.congty.local:443"] }]
```

Chuỗi số đo (chỉ có khi đo được — không xuất 0 giả): `vnmateai_up`, `vnmateai_process_uptime_seconds`, `vnmateai_kill_switch`, `vnmateai_tasks_24h{status}`, `vnmateai_tool_actions_24h{decision}`, `vnmateai_approvals_pending`, `vnmateai_incidents_open`, `vnmateai_llm_tokens_24h`, `vnmateai_llm_cost_24h`, `vnmateai_voice_stage_ms{outcome,stage,quantile}`, `vnmateai_host_cpu_percent|ram_percent|disk_percent`, `vnmateai_devfleet_runs{status}`, `vnmateai_infra_*`, `vnmateai_scrape_error{section}`.

Quy tắc cảnh báo gợi ý:

```yaml
- alert: VNMateAIDown
  expr: up{job="vn-mateai"} == 0
  for: 2m
  labels: { severity: critical }
- alert: VNMateAIKillSwitchOn
  expr: vnmateai_kill_switch == 1
  labels: { severity: warning }
- alert: VNMateAIApprovalsStuck
  expr: vnmateai_approvals_pending > 0
  for: 30m
  labels: { severity: warning }
- alert: VNMateAIScrapeSectionError
  expr: vnmateai_scrape_error == 1
  for: 5m
  labels: { severity: warning }
```

## 6. Chưa làm

- Chưa thử với Prometheus / Grafana thật (xem trên); chưa hỗ trợ Prometheus qua nhiều cluster liên kết (Thanos / Mimir dùng cùng API nên có thể chạy, chưa kiểm).
- Chưa vẽ biểu đồ chuỗi thời gian trong VN-MateAI (dùng liên kết sang Grafana); mới có giá trị tức thời.
- Chưa đẩy cảnh báo theo thời gian thực (Alertmanager webhook → VN-MateAI): hiện thăm dò theo chu kỳ.
- Chưa có tắt tiếng / xác nhận cảnh báo từ VN-MateAI (chỉ đọc có chủ đích).
