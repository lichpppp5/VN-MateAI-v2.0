# Đóng góp cho VN-MateAI

Cảm ơn bạn muốn đóng góp. Vài quy tắc ngắn:

- **Giấy phép đóng góp.** Gửi pull request nghĩa là bạn đồng ý đóng góp được cấp theo **Apache License 2.0** (Điều 5), giống phần còn lại của dự án. Bạn giữ bản quyền phần bạn viết; thêm tên bạn vào `AUTHORS` khi được merge.
- **Chỉ gửi mã bạn có quyền gửi.** Không dán mã từ nguồn có giấy phép không tương thích (GPL, độc quyền, đáp án bài tập…).
- **Giữ tiêu đề bản quyền.** Tệp mới cần tiêu đề SPDX: chạy `python scripts/license_headers.py --apply`. Đừng xoá hay sửa dòng bản quyền / `NOTICE` của người khác.
- **Kiểm thử.** `python -m pytest -q` và `for f in tests/*.mjs; do node $f; done` phải qua.
- **Không đưa bí mật vào repo:** khoá API, token, `config.json`, chứng chỉ, cơ sở dữ liệu, bản sao lưu.
- **Tên và logo.** "VN-MateAI" và tên tác giả không được dùng để đặt tên bản fork của bạn (xem `NOTICE`).
