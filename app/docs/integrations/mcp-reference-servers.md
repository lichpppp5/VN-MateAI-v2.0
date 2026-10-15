# MCP server tham chiếu chính thức

VN-MateAI đóng kèm ba MCP server của dự án Model Context Protocol (`modelcontextprotocol/servers`, bản 2026.10.10). Mã đã được
rà soát từng dòng trước khi đưa vào `vendor/mcp-servers/`. Các server này nối vào AI qua **cổng MCP có sẵn**
(`agent_gateway/mcp_gateway.py`): mọi lời gọi đi qua cổng chính sách và vào audit, kết quả mang nhãn **dữ liệu không tin cậy**.

## Công cụ AI nhận được

| Server | Công cụ (tên trong VN-MateAI) | Rủi ro | Ghi chú |
|---|---|---|---|
| `time` | `mcp_time_get_current_time`, `mcp_time_convert_time` | 1 | Múi giờ mặc định `Asia/Ho_Chi_Minh` |
| `fetch` | `mcp_fetch_fetch` | 2 (tự chạy, có audit) | Đọc trang web công khai thành Markdown, tối đa 8000 ký tự mỗi lần. **Chặn** địa chỉ nội bộ, loopback, link-local và metadata cloud (`169.254.169.254`…) ở mọi bước chuyển hướng; tôn trọng `robots.txt` |
| `git` | `mcp_git_git_status`, `…_git_diff_unstaged`, `…_git_diff_staged`, `…_git_diff`, `…_git_log`, `…_git_show`, `…_git_branch` | 1 | Chỉ trong **một kho** (`--repository`). `git_commit`, `git_add`, `git_reset`, `git_create_branch`, `git_checkout` bị **tắt**; nếu admin bật lại thì rủi ro 4 (cần duyệt) |

`fetch` để rủi ro 2, cao hơn gợi ý "chỉ đọc" của server, vì URL do AI soạn có thể mang dữ liệu ra ngoài. Công cụ vẫn tự chạy và
vào audit. Muốn bắt duyệt từng lần thì đặt rủi ro 3 ở Cổng Agent → MCP server → `fetch`.

## Không đưa vào (và lý do)

| Server | Lý do |
|---|---|
| `filesystem` | Trùng công cụ tệp hiện có, và sẽ **lách** cơ chế bảo vệ tệp dự án (`identity_core.md`, mã nguồn, cấu hình) |
| `memory` | Trùng bộ nhớ / GraphRAG; lưu đồ thị ra tệp JSON không mã hoá, có lệnh xoá |
| `sequentialthinking` | Giá trị thấp với model hiện nay; cần thêm Node.js trong image |
| `everything` | Server demo / kiểm thử giao thức |

`awesome-mcp-servers` chỉ là danh sách liên kết, không có mã. Muốn dùng một server trong danh sách: rà soát mã của nó, rồi khai
báo ở **Cổng Agent → MCP server** (HTTP hoặc stdio), đặt rủi ro từng công cụ, bấm **Đồng bộ công cụ**.

## Cài và đăng ký

```
python scripts/setup_mcp_servers.py --install     # lần đầu: tạo .mcp-venv, cài, đăng ký
python scripts/setup_mcp_servers.py               # đăng ký lại (sau khi đổi máy / khôi phục cấu hình)
python scripts/setup_mcp_servers.py --dry-run     # xem sẽ ghi gì
```

- **Môi trường Python riêng** (`.mcp-venv`, trong Docker là `/opt/mcp-venv`): thư viện `mcp` kéo starlette / uvicorn bản khác, không được lẫn vào môi trường của ứng dụng. Phiên bản ghim ở `vendor/mcp-servers/constraints.txt`.
- **Bật `agent_gateway.allow_stdio`**: máy chủ chạy các chương trình cục bộ đã rà soát ở trên.
- **Chạy lặp lại an toàn**: giữ nguyên quy tắc admin đã đặt trên Portal (công cụ đã tắt vẫn tắt).
- **Docker**: image cài sẵn; entrypoint đăng ký `time` và `fetch` mỗi lần khởi động. Image chỉ cài `time` và `fetch`: container không có mã nguồn Git lẫn chương trình `git`. Tắt bằng `VNMATE_MCP_REFERENCE=0` trong `.env`.
- **Tiến trình MCP chỉ nhận biến môi trường tối thiểu** (PATH, thư mục tạm, proxy, chứng chỉ…), không nhận mật khẩu, khoá LLM hay chuỗi kết nối CSDL của máy chủ. Biến riêng của từng server khai báo trong `env` của server (được mã hoá khi lưu).

## Cập nhật bản mới

1. Lấy mã mới của `src/time`, `src/fetch`, `src/git` từ `modelcontextprotocol/servers`.
2. Rà soát thay đổi (diff với `vendor/mcp-servers/`).
3. Chép đè, cài lại vào `.mcp-venv` rồi ghi lại tệp ghim: `pip freeze | grep -viE "^(mcp-server-|pywin32)" > vendor/mcp-servers/constraints.txt`.
4. Chạy `pytest tests/test_mcp_reference_servers.py` (chạy server thật).
5. `python scripts/setup_mcp_servers.py`.
