// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
/**
 * esp32_firmware/secrets.example.h
 * =================================
 * Template cho thông tin bí mật của firmware ESP32.
 *
 * Zero-Trust: thông tin đăng nhập WiFi KHÔNG được commit vào git.
 * (Trước đây SSID/PASS thật nằm cứng trong src/config.h và đã bị commit —
 *  bất kỳ ai đọc repo cũng nối được vào mạng của bạn.)
 *
 * ── Cách dùng ───────────────────────────────────────────────────────────────
 *   1. Copy file này thành secrets.h:
 *        cp esp32_firmware/secrets.example.h esp32_firmware/src/secrets.h
 *   2. Điền SSID/PASS thật của bạn.
 *   3. Build. File src/secrets.h đã được .git-ignore nên không lọt lên remote.
 *
 * ── Lưu ý ──────────────────────────────────────────────────────────────────
 * - Nên cấu hình WiFi qua NVS/SoftAP thay vì nhúng vào firmware, nhưng nếu dùng
 *   cách này thì TUYỆT ĐỐI không commit secrets.h.
 * - File này chỉ chứa #define, không có logic.
 */

#pragma once

// ─── WiFi Credentials (KHÔNG commit file chứa giá trị thật) ──────────────────
#define DEFAULT_WIFI_SSID "TEN_WIFI_CUA_BAN"
#define DEFAULT_WIFI_PASS "MAT_KHAU_WIFI_CUA_BAN"

// ─── Danh tính + token của RIÊNG robot này ──────────────────────────────────
// Máy chủ từ chối kết nối nếu thiếu/sai token (HTTP 403). Mỗi robot một id và
// một token riêng — lộ token của robot này không giả được robot khác.
//
// Cấp token (tài khoản admin), token chỉ hiện MỘT lần:
//   POST https://<máy chủ>/api/v1/security/devices   {"device_id": "robot_phong_hop"}
// Robot kết nối ws://<máy chủ>:8000/api/v1/xiaozhi/ws/<DEFAULT_DEVICE_ID>?token=...
//
// (Tương thích cũ: token chung trong certs/device_secret.key vẫn được nhận cho
//  tới khi admin bật security.require_per_device_token trong config.json.)
#define DEFAULT_DEVICE_ID    "robot_phong_hop"
#define DEFAULT_DEVICE_TOKEN ""

