// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
/**
 * esp32_firmware/src/face.h
 * =========================
 * Gương mặt robot trên OLED SSD1306 128x64: mắt bo góc tự chớp / liếc, biểu
 * cảm (vui, buồn, khóc, wow, phấn khích, giận, yêu, buồn ngủ), miệng mấp máy
 * theo ÂM LƯỢNG THẬT của giọng đang phát, chữ trạng thái tiếng Việt có dấu
 * ("Đang lắng nghe…", "Đang suy nghĩ…", "Đang nói…").
 *
 * Vẽ lại tối đa ~15 khung/giây trong Face::tick(); gọi tick() thường xuyên
 * (vòng loop và trong lúc phát âm thanh).
 */
#pragma once
#include <Arduino.h>
#include <Adafruit_SSD1306.h>

namespace Face {

enum Mode : uint8_t { MODE_IDLE, MODE_LISTENING, MODE_THINKING, MODE_SPEAKING, MODE_ALERT };

enum Emotion : uint8_t {
    EMO_NEUTRAL, EMO_HAPPY, EMO_SAD, EMO_CRY, EMO_WOW, EMO_EXCITED,
    EMO_ANGRY, EMO_LOVE, EMO_SLEEPY,
};

void begin(Adafruit_SSD1306* display);
void setMode(Mode mode);
Mode mode();
void setEmotion(Emotion emotion, uint32_t holdMs = 0);   // holdMs > 0: về NEUTRAL sau ngần ấy ms
Emotion parseEmotion(const String& name);                // "happy", "sad", "cry", "wow", …
void setSpeechLevel(float level01);                      // 0..1 — âm lượng giọng đang phát
void setListenLevel(float level01);                      // 0..1 — âm lượng micro khi đang nghe
void noteActivity();                                      // có tương tác: không buồn ngủ
void relaxEmotion(uint32_t afterMs);                      // giữ biểu cảm hiện tại thêm afterMs rồi về bình thường
void tick();                                              // gọi thường xuyên; tự giới hạn khung hình
void forceRedraw();

}  // namespace Face
