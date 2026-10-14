# Phát hành Agent máy trạm: build, ký số, triển khai, cập nhật, thu hồi

Dành cho quản trị viên. Hướng dẫn cài cho người dùng máy trạm nằm trong `client_agent/README.md`.

## 1. Ba dạng gói

| Gói (`package`) | Tệp | Cần trên máy trạm | Build ở đâu |
|---|---|---|---|
| `windows-exe` | `dist/agent/windows/VNMateAgent.exe` | không gì | máy Windows |
| `macos-bin` | `dist/agent/macos/VNMateAgent` | không gì | **máy Mac** (PyInstaller không build chéo) |
| `source` | mã `client_agent/` | Python 3.10+ | không cần build |

Portal → **Tải Agent** cho chọn nền tảng. Nếu chưa có bản build cho nền tảng đó, máy chủ phát gói `source` kèm script cài và báo rõ trên giao diện.

**Kỹ năng máy chủ dùng chung** (`client_agent/shared_skills.txt`): các tệp trong `skills/` của máy chủ (mở nhạc, YouTube,
Notepad…) được đóng NGUYÊN BẢN vào `skills/` của gói `source` và của bản build — một tính năng một bản mã, không chép tay.
Tệp dùng chung chỉ được import thư viện chuẩn và `core.plugin_manager.export_skill` (kiểm trong
`tests/test_agent_shared_skills.py`). Đổi danh sách hoặc tệp thì tăng `AGENT_VERSION` và build lại.

**Gói cài máy chủ Docker** (`scripts/build_release.py`) chép `dist/agent/<nền tảng>/` vào image, và dừng nếu bản build
khác `AGENT_VERSION` — build Agent trước rồi mới đóng gói máy chủ.

## 2. Build

```
python scripts/build_agent.py              # lần đầu: tạo build/agent/venv-<nền tảng>, cài thư viện + PyInstaller
python scripts/build_agent.py --reuse-venv # các lần sau
```

Kết quả nằm trong `dist/agent/<nền tảng>/`, gồm file chạy và `version.txt` (bằng `AGENT_VERSION` lúc build). Thư mục `dist/agent/` và `build/agent/` không commit.

**Phát hành bản mới:**
1. Tăng `AGENT_VERSION` trong `client_agent/agent.py`.
2. Build trên từng nền tảng cần phát hành.
3. Agent đang chạy sẽ tự cập nhật khi kết nối lại, hoặc trong vòng 6 giờ.

Gói `source` cập nhật ngay khi mã `client_agent/` trên máy chủ đổi phiên bản.

## 3. Ký số (khuyên dùng)

Không ký thì Windows SmartScreen và macOS Gatekeeper sẽ cảnh báo ở lần chạy đầu. Khoá ký **không** để trong code; script chỉ đọc biến môi trường.

- **Windows:**
  - đặt `SIGNTOOL` (đường dẫn `signtool.exe` trong Windows SDK) và `WIN_SIGN_CERT_SHA1` (thumbprint chứng chỉ code-signing trong kho chứng chỉ của máy build);
  - tuỳ chọn `WIN_SIGN_TIMESTAMP_URL`.
- **macOS:**
  1. Đặt `MAC_SIGN_IDENTITY="Developer ID Application: <Công ty> (<TEAMID>)"`.
  2. Sau khi build, notarize:
     ```
     ditto -c -k --keepParent dist/agent/macos/VNMateAgent VNMateAgent.zip
     xcrun notarytool submit VNMateAgent.zip --keychain-profile <profile> --wait
     ```
     File chạy một-tệp không staple được ticket. Gatekeeper kiểm tra notarization trực tuyến ở lần chạy đầu.

## 4. Triển khai hàng loạt (Windows)

1. Mỗi máy cần **một mã đăng ký riêng**. Tạo mã bằng Portal → Tải Agent → "+ Tạo mã", hoặc gọi `POST /api/v1/agent/enroll-codes` (admin) với `{"label": "...", "ttl_days": 7}`.
2. Với mỗi máy, chuẩn bị `config.json` lấy từ một gói tải về, thay `enroll_code` bằng mã của máy đó.
3. Chạy trong **phiên người dùng** (Agent cần popup / chụp màn hình phiên đó): `VNMateAgent.exe --quiet`.
   - Agent cài vào `%LOCALAPPDATA%\VNMateAI\Agent`, ghi `HKCU\...\Run` và mục gỡ trong Settings → Apps.
   - Không cần quyền admin.
4. **Gỡ:** `%LOCALAPPDATA%\VNMateAI\Agent\VNMateAgent.exe --uninstall --quiet`.

## 5. Thu hồi và đổi khoá

- **Thu hồi một máy:** Portal → Tải Agent → Máy trạm đã đăng ký → **Thu hồi**, hoặc gọi `POST /api/v1/agent/devices/{client_id}/revoke`. Kết nối bị cắt ngay, và khoá của máy đó không dùng lại được.
- **Cài lại máy đã thu hồi:** tải gói mới. Máy nhận `client_id` mới.
- **Mã chưa dùng:** huỷ được trong danh sách mã. Mã tự hết hạn sau `ttl_days`.
- **Agent bản cũ dùng secret chung:** bị từ chối, trừ khi chạy trên chính máy chủ. Nếu cần thời gian chuyển đổi, bật tạm `"security": {"allow_shared_worker_secret": true}` trong `config.json` của máy chủ, rồi cài lại các máy bằng gói mới.

## 6. Tắt tự cập nhật

- Toàn hệ thống: `"agent_updates": {"enabled": false}` trong `config.json` của máy chủ. Máy chủ ngừng báo `update_available`; Agent vẫn tự kiểm tra định kỳ, nên muốn chặn hẳn thì không đặt bản build mới vào `dist/agent/`.
- Từng máy: đặt biến môi trường `VNMATE_AGENT_NO_UPDATE=1`.

## 7. Đã kiểm thử

Ngày 2026-10-05, trên máy chủ thật:
- Tải gói Windows qua API, chạy `VNMateAgent.exe --quiet` trong thư mục thử (không ghi registry): Agent tự cài trong 3 giây, đổi mã lấy khoá, kết nối, gửi nhịp tim, nạp 24 kỹ năng.
- Build bản 2.2.0 rồi khởi động lại máy chủ: máy chủ báo có bản mới, Agent 2.1.9 tải và kiểm SHA-256, 4 giây sau bản 2.2.0 đã kết nối lại.
- `--uninstall`: thư mục cài bị xoá, không còn tiến trình.

**Chưa kiểm thử thật** (không có máy Mac trong môi trường phát triển):
- bản build macOS và LaunchAgent;
- `install_agent_macos.sh` (mới kiểm cú pháp bằng `bash -n`).

Phần đăng ký, cập nhật và giao thức dùng chung mã với Windows và có test tự động.
