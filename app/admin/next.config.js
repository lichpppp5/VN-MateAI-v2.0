// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
/**
 * admin/next.config.js
 *
 * Xuất bản TĨNH (output: 'export') + basePath '/admin'.
 *
 * Vì sao xuất tĩnh mà không chạy `next start` ở cổng riêng:
 *
 *  1. Người dùng chỉ cần mở MỘT địa chỉ (http://127.0.0.1:8000/admin) thay vì
 *     phải nhớ chạy thêm server ở cổng 3001 rồi mở cổng đó. Nếu không có
 *     server admin chạy, trang portal vẫn bình thường — thay vì báo
 *     "Failed to fetch" như trước.
 *
 *  2. Cùng origin với backend nên KHÔNG còn CORS. Trước đây Admin ở cổng
 *     3001 gọi API ở 8001 khác origin, phải thêm cổng vào danh sách
 *     VNMATEAI_CORS_ORIGINS mới chạy được — thêm một bước cấu hình dễ sót.
 *
 *  3. Không phát sinh thêm tiến trình node khi chạy production.
 *
 * Đổi lại: không có API route phía server của Next.js. Backend FastAPI đã
 * giữ toàn bộ API, nên mất gì không.
 */

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,

  // Xuất tĩnh: kết quả nằm trong admin/out/, FastAPI phục vụ ở /admin.
  output: 'export',

  // Mọi asset và route của app gắn tiền tố /admin để không đụng phần portal.
  basePath: '/admin',

  // Không dùng trình tối ưu ảnh của Next — không có server xử lý ảnh.
  images: { unoptimized: true },

  // Bản export tĩnh không phục vụ trailing slash: dùng đường dẫn có .html.
  trailingSlash: false,
};

module.exports = nextConfig;
