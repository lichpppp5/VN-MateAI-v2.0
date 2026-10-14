# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
import os

desktop = r'C:\Users\DUONG THANH LICH\Desktop'
html_path = os.path.join(desktop, 'Palo_Alto_Networks_Overview.html')

content = """<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<title>Báo Cáo Chi Tiết: Giải Pháp & Công Cụ Mạng Palo Alto Networks</title>
<style>
  body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; line-height: 1.6; color: #333; margin: 40px; }
  h1 { color: #FA582D; border-bottom: 2px solid #FA582D; padding-bottom: 10px; }
  h2 { color: #005A9C; margin-top: 30px; border-bottom: 1px solid #ddd; padding-bottom: 5px; }
  h3 { color: #2C3E50; }
  table { width: 100%; border-collapse: collapse; margin: 20px 0; }
  th, td { border: 1px solid #ddd; padding: 10px 12px; text-align: left; }
  th { background-color: #f4f6f9; color: #333; }
  tr:nth-child(even) { background-color: #fafafa; }
  .box { background: #f8f9fa; border-left: 4px solid #005A9C; padding: 15px; margin: 15px 0; }
</style>
</head>
<body>
  <h1>TỔNG QUAN GIẢI PHÁP & CÔNG CỤ MẠNG PALO ALTO NETWORKS</h1>
  <p><em>Tài liệu tổng hợp kỹ thuật dành cho Doanh nghiệp</em></p>

  <div class="box">
    <strong>Palo Alto Networks (PANW)</strong> là tập đoàn an ninh mạng hàng đầu thế giới, tiên phong trong công nghệ Next-Generation Firewall (NGFW) và kiến trúc bảo mật Zero Trust đa nền tảng.
  </div>

  <h2>1. Các Dòng Sản Phẩm & Giải Pháp Cốt Lõi</h2>
  <table>
    <tr><th>Trụ cột</th><th>Sản phẩm tiêu biểu</th><th>Chức năng chính</th></tr>
    <tr><td><strong>Strata</strong> (Network Security)</td><td>PA-Series, VM-Series, CN-Series, Panorama</td><td>Next-Gen Firewall vật lý/ảo hóa, quản lý tập trung toàn mạng, App-ID, User-ID, Threat Prevention.</td></tr>
    <tr><td><strong>Prisma</strong> (Cloud Security & SASE)</td><td>Prisma Access, Prisma SD-WAN, Prisma Cloud</td><td>Bảo mật truy cập từ xa (SASE), tối ưu định tuyến mạng chi nhánh (SD-WAN), bảo vệ hạ tầng đám mây đa nền tảng.</td></tr>
    <tr><td><strong>Cortex</strong> (SecOps & AI Automation)</td><td>Cortex XDR, Cortex XSOAR, Xpanse</td><td>Giám sát phát hiện và phản ứng mở rộng (XDR), tự động hóa phản ứng sự cố (SOAR), quản lý bề mặt tấn công.</td></tr>
  </table>

  <h2>2. Công Cụ & Tính Năng Nổi Bật Trên Strata NGFW</h2>
  <ul>
    <li><strong>App-ID:</strong> Nhận diện và kiểm soát hơn 4,000+ ứng dụng bất kể cổng (port), giao thức hay mã hóa SSL/TLS.</li>
    <li><strong>User-ID:</strong> Tích hợp sâu Active Directory / LDAP để gán chính sách bảo mật trực tiếp theo danh tính người dùng thay vì chỉ IP.</li>
    <li><strong>Content-ID:</strong> Quét dữ liệu một luồng (Single-Pass Architecture), ngăn chặn mã độc, virus, khai thác lỗ hổng và rò rỉ dữ liệu (DLP).</li>
    <li><strong>WildFire:</strong> Sandbox phân tích mã độc zero-day bằng AI trên Cloud, cập nhật mẫu chữ ký toàn cầu trong vài giây.</li>
    <li><strong>Panorama:</strong> Công cụ quản trị tập trung hàng ngàn firewall với chính sách đồng nhất từ một giao diện duy nhất.</li>
  </ul>

  <h2>3. So Sánh Với Các Đối Thủ Cạnh Tranh (Fortinet, Cisco, Check Point)</h2>
  <table>
    <tr><th>Tiêu chí</th><th>Palo Alto Networks</th><th>Fortinet (FortiGate)</th><th>Check Point</th><th>Cisco (Firepower)</th></tr>
    <tr><td><strong>Hiệu năng L7 / App-ID</strong></td><td>Rất cao (Single-Pass)</td><td>Tốt (ASIC SoC)</td><td>Khá</td><td>Trung bình</td></tr>
    <tr><td><strong>Bảo mật Zero-Day / AI</strong></td><td>Xuất sắc (WildFire)</td><td>Rất tốt (FortiSandbox)</td><td>Xuất sắc (ThreatCloud)</td><td>Tốt (Talos)</td></tr>
    <tr><td><strong>Hệ sinh thái Cloud / SASE</strong></td><td>Toàn diện số 1</td><td>Đang phát triển mạnh</td><td>Khá</td><td>Đang tái cấu trúc</td></tr>
    <tr><td><strong>Chi phí đầu tư (TCO)</strong></td><td>Phân khúc cao cấp</td><td>Tối ưu ngân sách</td><td>Cao</td><td>Trung bình - Cao</td></tr>
  </table>

  <h2>4. Khuyến Nghị Triển Khai Cho Doanh Nghiệp</h2>
  <div class="box">
    <ul>
      <li><strong>Hội sở / Trụ sở chính:</strong> Triển khai cụm PA-Series chạy High Availability (Active/Passive hoặc Active/Active) kết hợp đầy đủ license Threat Prevention, DNS Security, WildFire.</li>
      <li><strong>Chi nhánh & Nhân sự từ xa:</strong> Sử dụng Prisma SD-WAN kết hợp Prisma Access (SASE) để loại bỏ VPN truyền thống, tăng cường kiểm soát Zero Trust.</li>
      <li><strong>Môi trường Ảo hóa / Private Cloud:</strong> Sử dụng VM-Series trên VMware ESXi / Nutanix / OpenStack để phân đoạn vi mô (Micro-segmentation).</li>
    </ul>
  </div>
</body>
</html>
"""

with open(html_path, 'w', encoding='utf-8') as f:
    f.write(content)

print(f"DONE:{html_path}")
