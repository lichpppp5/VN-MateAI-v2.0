# Bản quyền, ghi công và cách chứng minh tác giả

Dự án: **VN-MateAI** · Tác giả gốc và chủ sở hữu bản quyền: **Dương Thanh Lịch** · Giấy phép: **Apache-2.0**.

> Đây là hướng dẫn kỹ thuật, không phải tư vấn pháp lý. Nếu dự án có giá trị thương mại, hãy hỏi luật sư sở hữu trí tuệ.

## 1. Mọi người được phép gì, bắt buộc gì

Apache-2.0 cho phép **tải về, cài, dùng, sửa, phân phối lại, kể cả thương mại**. Đổi lại, ai phân phối lại (nguyên bản hoặc bản sửa) **phải**:

1. giữ nguyên tệp `LICENSE` và **`NOTICE`** (chứa tên tác giả) — hoặc đưa nội dung `NOTICE` vào tài liệu / màn hình "Giới thiệu" đi kèm;
2. giữ mọi dòng bản quyền, bằng sáng chế, nhãn hiệu, ghi công trong mã nguồn;
3. ghi rõ **tệp nào đã bị họ sửa**;
4. **không** dùng tên "VN-MateAI", logo hay tên tác giả để đặt tên / quảng bá sản phẩm của họ, và không giả làm bản gốc hay làm như chính tác giả phát hành (Điều 6 của giấy phép).

Người dùng cuối chỉ cài và chạy thì không cần làm gì thêm.

## 2. Giới hạn cần biết

Không kỹ thuật nào ngăn được người khác tải mã về và xoá tên bạn trong bản của họ. Điều bảo vệ bạn là:

- quyền pháp lý: xoá hay giả mạo ghi công là **vi phạm giấy phép**, bạn có căn cứ yêu cầu gỡ;
- bằng chứng rằng bạn là tác giả gốc (mục 3) và quyền với **nhãn hiệu** (mục 4).

Các phiên bản đã công bố **trước** khi đổi sang Apache-2.0 (lịch sử git cũ) từng ghi giấy phép độc quyền; từ commit đổi giấy phép trở đi mã được cấp theo Apache-2.0 và **không thể thu hồi** đối với bản đã phát hành.

## 3. Chứng minh bạn là người tạo ra

**a. Ký commit** — GitHub hiện nhãn "Verified", chứng minh commit do đúng bạn tạo. Cách đơn giản nhất là ký bằng khoá SSH (một lần):

```bash
git config --global gpg.format ssh
git config --global user.signingkey ~/.ssh/id_ed25519.pub     # khoá công khai của bạn
git config --global commit.gpgsign true
git config --global tag.gpgsign true
```

Rồi vào GitHub → *Settings → SSH and GPG keys → New SSH key* → chọn loại **Signing Key** và dán cùng khoá công khai đó. (Dùng GPG nếu bạn đã có khoá GPG.)

**b. Phát hành có chữ ký.** Mỗi bản phát hành: tạo tag ký (`git tag -s v1.0.0 -m "VN-MateAI 1.0.0"`), đính kèm file cài và tệp `SHA256SUMS`; ai cũng kiểm được file không bị sửa.

**c. Lịch sử git công khai** với mốc thời gian là bằng chứng về người viết trước. Đừng xoá hay viết lại lịch sử (`push --force`) của nhánh chính.

**d. Đăng ký bản quyền** tại Cục Bản quyền tác giả (Bộ Văn hoá, Thể thao và Du lịch, Việt Nam) cho tác phẩm "VN-MateAI" (chương trình máy tính). Bản quyền phát sinh tự động, đăng ký cho bạn **giấy chứng nhận** dùng làm bằng chứng khi tranh chấp.

**e. Tiêu đề bản quyền trong từng tệp mã** (đã có): `SPDX-License-Identifier: Apache-2.0` + `Copyright (c) 2026 Dương Thanh Lịch`. Kiểm bằng `python scripts/license_headers.py --check`; thêm cho tệp mới bằng `--apply`. Có test tự động giữ quy tắc này.

## 4. Bảo vệ tên và logo

Giấy phép không cấp quyền dùng tên "VN-MateAI". Để chặn người khác dùng tên này, hãy **đăng ký nhãn hiệu** (Cục Sở hữu trí tuệ, nhóm 9 phần mềm / nhóm 42 dịch vụ phần mềm). Đây là lớp bảo vệ mạnh hơn bản quyền mã nguồn khi ai đó bán lại sản phẩm dưới tên giống.

## 5. Khi phát hiện vi phạm

1. Chụp màn hình / lưu trang (kèm ngày giờ), ghi lại URL của repo hoặc sản phẩm vi phạm.
2. Liên hệ người vi phạm yêu cầu khôi phục ghi công trong thời hạn hợp lý.
3. Gửi yêu cầu gỡ theo **DMCA** tới GitHub (https://github.com/contact/dmca) — GitHub xử lý vi phạm giấy phép mã nguồn.

## 6. Đóng góp từ bên ngoài

Mọi đóng góp (pull request) được cấp theo Apache-2.0 (Điều 5 của giấy phép), người đóng góp vẫn giữ bản quyền phần của họ — xem `CONTRIBUTING.md`. Bạn vẫn là tác giả gốc và chủ sở hữu phần mình viết.

## 7. Thành phần bên thứ ba

Xem `THIRD_PARTY_NOTICES.md` (sinh bằng `python scripts/gen_third_party_notices.py`). Khi đóng gói cài đặt cho khách (PyInstaller…), phải kèm các giấy phép của thư viện được đóng gói; thư viện LGPL cần cho phép người nhận thay thế thư viện đó.
