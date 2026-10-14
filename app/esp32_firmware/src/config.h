// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
#pragma once

/**
 * esp32_firmware/src/config.h
 * ===========================
 * Phase 52: Full Autonomous Robotics (Body-Mind Sync).
 * Pinout chuẩn hóa cho ESP32-S3 AI Desktop Companion Robot.
 */

// ─── Audio Pinout (I2S Mic INMP441 & Speaker MAX98357A) ──────────────────────
#define I2S_MIC_WS 4
#define I2S_MIC_SCK 5
#define I2S_MIC_SD 6
#define I2S_SPK_DIN 7
#define I2S_SPK_BCLK 15
#define I2S_SPK_LRC 16

// ─── UI & Sensors (Capacitive Touch, SSD1306 OLED, VL6180X/TOF050C) ──────────
#define TOUCH_PIN 17
#define OLED_SDA 41
#define OLED_SCL 42
#define TOF_SDA 1
#define TOF_SCL 2

// ─── Movement Control (L298N H-Bridge Dual DC Motors) ────────────────────────
#define MOTOR_L_IN1 11
#define MOTOR_L_IN2 12
#define MOTOR_R_IN3 13
#define MOTOR_R_IN4 14

// ─── Servos & Status LEDs ───────────────────────────────────────────────────
#define SERVO_ARM 47      // Servo vẫy cánh tay (0 - 180 độ)
#define SERVO_NECK 3      // Servo quay/gật cổ (0 - 180 độ)
#define LED1_PIN 38       // LED chỉ báo trạng thái 1
#define LED2_PIN 18       // LED chỉ báo trạng thái 2
#define RGB_PIN 48        // WS2812 Smart RGB LED

// ─── Safety Interlocks & Limits (Phase 52) ──────────────────────────────────
#define CLIFF_THRESHOLD_MM 120        // Ngưỡng phát hiện mép bàn/vực (> 120mm hoặc mất phản xạ)
#define OBSTACLE_THRESHOLD_MM 35      // Ngưỡng phát hiện vật cản quá gần (< 35mm)
#define MAX_MOTION_DURATION_MS 3000   // Giới hạn thời gian di chuyển an toàn tối đa của 1 lệnh
#define WATCHDOG_TIMEOUT_MS 10000     // Giới hạn tuyệt đối của Watchdog tự ngắt motor

// ─── Network & WebSocket Server Config ──────────────────────────────────────
// Zero-Trust: KHÔNG hardcode thông tin đăng nhập WiFi trong mã nguồn.
// Mật khẩu thật nằm trong file KHÔNG commit — xem esp32_firmware/secrets.example.h
// và copy sang secrets.h (đã .git-ignore) rồi điền SSID/PASS của bạn.
//
//   cp esp32_firmware/secrets.example.h esp32_firmware/src/secrets.h
//
// Nếu secrets.h không tồn tại, firmware dùng SSID/PASS rỗng và sẽ không nối được
// WiFi — đây là hành vi an toàn có chủ đích, không phải lỗi.
#if defined(__has_include)
#  if __has_include("secrets.h")
#    include "secrets.h"
#  endif
#endif

#ifndef DEFAULT_WIFI_SSID
#define DEFAULT_WIFI_SSID ""
#endif
#ifndef DEFAULT_WIFI_PASS
#define DEFAULT_WIFI_PASS ""
#endif

// Device token — server chặn kết nối /api/v1/xiaozhi/ws nếu thiếu (HTTP 403).
// Nên dùng token RIÊNG của robot: POST /api/v1/security/devices {"device_id": ...}.
#ifndef DEFAULT_DEVICE_TOKEN
#define DEFAULT_DEVICE_TOKEN ""
#endif

#define DEFAULT_SERVER_HOST "192.168.100.128"  // Địa chỉ IP của máy chủ VN-MateAI Master
#define DEFAULT_SERVER_PORT 8000                // Cổng HTTP / WS (Zero TLS overhead, không sập heap)
#define DEFAULT_WS_PATH "/api/v1/xiaozhi/ws"
// Id riêng của robot — máy chủ ràng buộc token riêng với id này (đặt trong secrets.h,
// mỗi robot một id khác nhau). Chỉ gồm chữ, số, '_', '-', '.', tối đa 64 ký tự.
#ifndef DEFAULT_DEVICE_ID
#define DEFAULT_DEVICE_ID "vnmate_robot_01"
#endif

// ─── Audio Sampling Rates ───────────────────────────────────────────────────
#define AUDIO_SAMPLE_RATE_MIC 16000
#define AUDIO_SAMPLE_RATE_SPK 24000
#define I2S_BUFFER_SIZE 1024
