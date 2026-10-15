# Hướng dẫn cho quản lý (manager)

Bạn thấy mọi mục trừ **Cấu Hình Hệ Thống** và **Trung Tâm Bảo Mật**. Bạn theo dõi được mọi hoạt động của AI nhưng không duyệt
việc rủi ro và không đổi cấu hình — hai việc đó thuộc quản trị viên.

## Tổng quan điều hành

Khối đầu tiên của **Bảng Điều Khiển**, đọc trong một phút:

| Số | Ý nghĩa |
|---|---|
| Việc AI đã làm xong | Số việc hoàn tất trong tháng, mũi tên so với tháng trước |
| Tỉ lệ thành công | Việc xong / việc đã kết thúc (xong + thất bại + bị chặn) |
| Giờ công tiết kiệm (ước tính) | Số việc xong × giả định giờ mỗi việc (ghi ngay dưới con số). Là **ước tính**, không phải đo |
| Chi phí AI | Chi phí gọi mô hình AI trong tháng; mũi tên đỏ khi tăng |
| Đang chờ quyết định | Yêu cầu chờ duyệt (số rủi ro cao), sự cố đang mở, việc cần người xử lý |

Bên dưới là **AI Supervisor — 24 giờ qua**: số liệu chi tiết hơn (quyết định của cổng chính sách, token, độ trễ giọng nói)
dành cho người cần đi sâu.

## Báo cáo

- **Hiệu Quả ROI** (mở tab mới): phiếu hỗ trợ, thống kê kiểm toán, giờ tiết kiệm theo ngày.
- **Chính Sách & Tuân Thủ**: báo cáo hiệu năng theo tháng (lượt thoại, độ trễ, tỉ lệ thành công, chi phí) — tải CSV để đưa vào
  báo cáo SLA.

## Sự cố và hạ tầng

**Giám Sát Hạ Tầng** hiện trạng thái máy chủ, dịch vụ và các nguồn giám sát đã kết nối. Biểu tượng chuông trên thanh đầu trang
đếm sự cố đang mở và cảnh báo mới; bấm để xem chi tiết.

## Kịch bản vận hành

**Kịch Bản Vận Hành** là chuỗi bước dựng sẵn (ví dụ: kiểm tra máy chủ → dọn đĩa → kiểm chứng). Bạn chạy được kịch bản; bước
có rủi ro khiến cả kịch bản **chờ quản trị viên duyệt một lần** cho toàn bộ kế hoạch. Nếu doanh nghiệp bật *tách nhiệm vụ*,
người soạn hoặc sửa kịch bản không được tự duyệt lượt chạy của nó.

## Quản lý trợ lý AI và giọng nói

**Quản Lý Trợ Lý AI** cho xem mô hình đang dùng và tình trạng từng mô hình. **Giọng Nói, Robot & HUD** cho xem robot và màn
hình HUD đang kết nối. Thay đổi cấu hình ở hai mục này cần quản trị viên.
