// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
/**
 * esp32_firmware/src/face.cpp — xem face.h.
 *
 * Bố cục 128x64:
 *   y  2..32  hai mắt (tâm x=40 và x=88)
 *   y 35..48  miệng
 *   y 51..63  chữ trạng thái (bitmap tiếng Việt, oled_labels.h) + dấu chấm chạy
 */
#include "face.h"
#include "oled_labels.h"

namespace Face {
namespace {

Adafruit_SSD1306* d = nullptr;
Mode curMode = MODE_IDLE;
Emotion curEmo = EMO_NEUTRAL;
uint32_t emoUntil = 0;              // 0 = giữ mãi
volatile float speechLevel = 0.0f;
volatile float listenLevel = 0.0f;
float mouthSmooth = 0.0f;
uint32_t lastFrame = 0;
uint32_t lastActivity = 0;
uint32_t nextBlink = 0;
uint32_t blinkStart = 0;
uint32_t nextLook = 0;
int8_t lookX = 0, lookY = 0, lookTX = 0, lookTY = 0;
uint32_t frameNo = 0;
bool dirty = true;

const int EYE_L = 40, EYE_R = 88, EYE_Y = 17;   // tâm mắt
const int EYE_W = 30, EYE_H = 26, EYE_RAD = 7;
const uint32_t FRAME_MS = 66;                    // ~15 khung/giây
const uint32_t SLEEPY_AFTER_MS = 90000;          // không ai nói chuyện 90 s -> buồn ngủ

// Độ mở mắt 0..1 theo chu kỳ chớp (đóng nhanh, mở chậm hơn).
float blinkOpen(uint32_t now) {
    if (now >= nextBlink && blinkStart == 0) blinkStart = now;
    if (blinkStart == 0) return 1.0f;
    uint32_t t = now - blinkStart;
    if (t < 70) return 1.0f - t / 70.0f;
    if (t < 170) return (t - 70) / 100.0f;
    blinkStart = 0;
    nextBlink = now + 2500 + (uint32_t)random(0, 3500);
    if (random(0, 5) == 0) nextBlink = now + 250;   // thỉnh thoảng chớp đôi
    return 1.0f;
}

void updateLook(uint32_t now) {
    if (curMode == MODE_THINKING) {                 // suy nghĩ: liếc lên trái / lên phải
        lookTX = ((now / 900) % 2) ? 5 : -5;
        lookTY = -3;
    } else if (curMode == MODE_LISTENING || curMode == MODE_SPEAKING) {
        lookTX = 0; lookTY = 0;                     // nhìn thẳng người nói
    } else if (now >= nextLook) {                   // rảnh: thỉnh thoảng liếc quanh
        lookTX = (int8_t)random(-6, 7);
        lookTY = (int8_t)random(-3, 4);
        nextLook = now + 1500 + (uint32_t)random(0, 3000);
    }
    lookX += (lookTX > lookX) - (lookTX < lookX);
    lookY += (lookTY > lookY) - (lookTY < lookY);
}

void drawHeart(int cx, int cy, int s) {
    d->fillCircle(cx - s / 2, cy - s / 4, s / 2, SSD1306_WHITE);
    d->fillCircle(cx + s / 2, cy - s / 4, s / 2, SSD1306_WHITE);
    d->fillTriangle(cx - s, cy - s / 6, cx + s, cy - s / 6, cx, cy + s, SSD1306_WHITE);
}

void drawStar(int cx, int cy, int r, bool big) {
    d->drawLine(cx - r, cy, cx + r, cy, SSD1306_WHITE);
    d->drawLine(cx, cy - r, cx, cy + r, SSD1306_WHITE);
    if (big) {
        int q = r * 2 / 3;
        d->drawLine(cx - q, cy - q, cx + q, cy + q, SSD1306_WHITE);
        d->drawLine(cx - q, cy + q, cx + q, cy - q, SSD1306_WHITE);
        d->fillCircle(cx, cy, 2, SSD1306_WHITE);
    }
}

// Một mắt. side: -1 trái, +1 phải. open: 0..1.
void drawEye(int cx, int side, float open, uint32_t now) {
    int x = cx + lookX, y = EYE_Y + lookY;
    switch (curEmo) {
        case EMO_LOVE:
            drawHeart(x, y, 11 + ((now / 300) % 2));          // tim "đập"
            return;
        case EMO_EXCITED: {
            int r = 11 + ((now / 150) % 3);
            drawStar(x, y, r, true);
            return;
        }
        case EMO_SLEEPY:
            d->fillRoundRect(x - EYE_W / 2, y + 2, EYE_W, 3, 1, SSD1306_WHITE);
            return;
        case EMO_WOW: {
            int r = 13;
            d->fillCircle(x, y, r, SSD1306_WHITE);
            d->fillCircle(x, y, r - 3, SSD1306_BLACK);
            d->fillCircle(x + 2, y - 1, 5, SSD1306_WHITE);         // con ngươi
            d->fillCircle(x + 4, y - 3, 1, SSD1306_BLACK);         // ánh sáng
            return;
        }
        default: break;
    }

    int h = max(2, (int)(EYE_H * open));
    if (curMode == MODE_LISTENING) h = max(2, (int)((EYE_H + 2) * open));   // nghe: mắt mở to hơn
    d->fillRoundRect(x - EYE_W / 2, y - h / 2, EYE_W, h, min(EYE_RAD, h / 2), SSD1306_WHITE);
    if (h < 6) return;

    int top = y - h / 2, bot = y + h / 2, l = x - EYE_W / 2, r = x + EYE_W / 2;
    switch (curEmo) {
        case EMO_HAPPY:      // mắt cười "^ ^": khoét nửa dưới thành vòng cung
            d->fillCircle(x, bot + 8, EYE_W / 2 + 2, SSD1306_BLACK);
            break;
        case EMO_SAD:
        case EMO_CRY:        // mí trên xệ xuống phía NGOÀI
            if (side < 0) d->fillTriangle(l - 1, top - 1, r + 1, top - 1, l - 1, top + h / 2 + 2, SSD1306_BLACK);
            else          d->fillTriangle(l - 1, top - 1, r + 1, top - 1, r + 1, top + h / 2 + 2, SSD1306_BLACK);
            break;
        case EMO_ANGRY:      // mí trên xệ xuống phía TRONG
            if (side < 0) d->fillTriangle(l - 1, top - 1, r + 1, top - 1, r + 1, top + h / 2 + 2, SSD1306_BLACK);
            else          d->fillTriangle(l - 1, top - 1, r + 1, top - 1, l - 1, top + h / 2 + 2, SSD1306_BLACK);
            break;
        default:
            break;
    }
    if (curEmo == EMO_CRY) {                               // giọt nước mắt rơi
        int ty = bot + 2 + (int)((now / 60) % 14);
        int tx = (side < 0) ? l + 5 : r - 5;
        if (ty < 50) {
            d->fillCircle(tx, ty, 2, SSD1306_WHITE);
            d->fillTriangle(tx - 2, ty, tx + 2, ty, tx, ty - 4, SSD1306_WHITE);
        }
    }
}

void drawMouth(uint32_t now) {
    const int mx = 64, my = 42;
    if (curMode == MODE_SPEAKING) {                        // mấp máy theo âm lượng thật
        float target = speechLevel;
        mouthSmooth += (target - mouthSmooth) * 0.6f;
        int h = 2 + (int)(mouthSmooth * 12.0f);
        int w = (curEmo == EMO_HAPPY || curEmo == EMO_EXCITED) ? 22 : 18;
        d->fillRoundRect(mx - w / 2, my - h / 2, w, h, min(4, h / 2), SSD1306_WHITE);
        if (h > 6) d->fillRoundRect(mx - w / 2 + 3, my - h / 2 + 2, w - 6, h - 4, 2, SSD1306_BLACK);
        return;
    }
    switch (curEmo) {
        case EMO_HAPPY:
            d->fillCircle(mx, my - 4, 9, SSD1306_WHITE);
            d->fillRect(mx - 10, my - 14, 21, 10, SSD1306_BLACK);
            d->fillCircle(mx, my - 6, 6, SSD1306_BLACK);
            break;
        case EMO_EXCITED:                                  // cười to "D"
            d->fillCircle(mx, my - 3, 9, SSD1306_WHITE);
            d->fillRect(mx - 10, my - 13, 21, 10, SSD1306_BLACK);
            break;
        case EMO_LOVE:
            d->fillCircle(mx, my - 4, 7, SSD1306_WHITE);
            d->fillRect(mx - 8, my - 12, 17, 8, SSD1306_BLACK);
            d->fillCircle(mx, my - 5, 4, SSD1306_BLACK);
            break;
        case EMO_SAD:
        case EMO_CRY:                                      // miệng mếu
            d->fillCircle(mx, my + 6, 8, SSD1306_WHITE);
            d->fillCircle(mx, my + 8, 6, SSD1306_BLACK);
            d->fillRect(mx - 9, my + 5, 19, 10, SSD1306_BLACK);
            break;
        case EMO_WOW:
            d->fillCircle(mx, my, 5, SSD1306_WHITE);
            d->fillCircle(mx, my, 3, SSD1306_BLACK);
            break;
        case EMO_ANGRY:
            for (int i = 0; i < 4; ++i)
                d->drawLine(mx - 10 + i * 5, my + (i % 2 ? 2 : -1), mx - 5 + i * 5, my + (i % 2 ? -1 : 2), SSD1306_WHITE);
            break;
        case EMO_SLEEPY: {                                 // "z Z" bay lên
            d->drawFastHLine(mx - 4, my, 8, SSD1306_WHITE);
            int k = (now / 400) % 3;
            d->setTextSize(1); d->setTextColor(SSD1306_WHITE);
            d->setCursor(100, 30 - k * 6); d->print('z');
            if (k > 0) { d->setCursor(108, 22 - k * 4); d->print('Z'); }
            break;
        }
        default:
            if (curMode == MODE_LISTENING) {               // đang nghe: miệng "o" nhỏ, nhịp theo mic
                int r = 2 + (int)(listenLevel * 3.0f);
                d->drawCircle(mx, my, r, SSD1306_WHITE);
            } else {
                d->fillRoundRect(mx - 6, my - 1, 12, 3, 1, SSD1306_WHITE);
            }
            break;
    }
}

void drawLabel(uint32_t now) {
    const OledLabel* lab = nullptr;
    switch (curMode) {
        case MODE_LISTENING: lab = &LABEL_LISTENING; break;
        case MODE_THINKING:  lab = &LABEL_THINKING;  break;
        case MODE_SPEAKING:  lab = &LABEL_SPEAKING;  break;
        case MODE_ALERT:     lab = &LABEL_EDGE;      break;
        default: return;
    }
    int dots = (curMode == MODE_ALERT) ? 0 : (int)((now / 350) % 4);
    int totalW = lab->w + 10;
    int x = (128 - totalW) / 2;
    int y = 64 - lab->h;
    d->drawBitmap(x, y, lab->bits, lab->w, lab->h, SSD1306_WHITE);
    for (int i = 0; i < dots; ++i) d->fillRect(x + lab->w + 2 + i * 3, 62, 2, 2, SSD1306_WHITE);
    if (curMode == MODE_LISTENING) {                       // vạch âm lượng micro hai bên
        int bars = (int)(listenLevel * 5.0f);
        for (int i = 0; i < bars; ++i) {
            d->fillRect(x - 6 - i * 3, 63 - (i + 1) * 2, 2, (i + 1) * 2, SSD1306_WHITE);
            d->fillRect(x + totalW + 4 + i * 3, 63 - (i + 1) * 2, 2, (i + 1) * 2, SSD1306_WHITE);
        }
    }
}

void render(uint32_t now) {
    if (emoUntil && now >= emoUntil) { curEmo = EMO_NEUTRAL; emoUntil = 0; }
    if (curMode == MODE_IDLE && curEmo == EMO_NEUTRAL && now - lastActivity > SLEEPY_AFTER_MS) curEmo = EMO_SLEEPY;

    updateLook(now);
    float open = (curEmo == EMO_SLEEPY || curEmo == EMO_LOVE || curEmo == EMO_EXCITED || curEmo == EMO_WOW)
                     ? 1.0f : blinkOpen(now);
    d->clearDisplay();
    if (curMode == MODE_ALERT) {                           // mép bàn: mắt hoảng + dấu "!"
        Emotion saved = curEmo; curEmo = EMO_WOW;
        drawEye(EYE_L, -1, 1.0f, now); drawEye(EYE_R, 1, 1.0f, now);
        curEmo = saved;
        if ((now / 250) % 2) d->fillRect(62, 34, 4, 9, SSD1306_WHITE), d->fillRect(62, 45, 4, 3, SSD1306_WHITE);
    } else {
        drawEye(EYE_L, -1, open, now);
        drawEye(EYE_R, 1, open, now);
        drawMouth(now);
    }
    drawLabel(now);
    d->display();
}

}  // namespace

void begin(Adafruit_SSD1306* display) {
    d = display;
    lastActivity = millis();
    nextBlink = millis() + 1500;
    dirty = true;
}

void setMode(Mode m) {
    if (m != curMode) { curMode = m; dirty = true; }
    if (m != MODE_IDLE) noteActivity();
    if (curEmo == EMO_SLEEPY && m != MODE_IDLE) curEmo = EMO_NEUTRAL;
}

Mode mode() { return curMode; }

void setEmotion(Emotion e, uint32_t holdMs) {
    curEmo = e;
    emoUntil = holdMs ? millis() + holdMs : 0;
    if (e != EMO_SLEEPY) noteActivity();
    dirty = true;
}

Emotion parseEmotion(const String& raw) {
    String n = raw; n.toLowerCase();
    if (n == "happy" || n == "vui" || n == "smile") return EMO_HAPPY;
    if (n == "sad" || n == "buon") return EMO_SAD;
    if (n == "cry" || n == "crying" || n == "khoc") return EMO_CRY;
    if (n == "wow" || n == "surprised" || n == "shocked" || n == "alert") return EMO_WOW;
    if (n == "excited" || n == "phan_khich" || n == "celebrate") return EMO_EXCITED;
    if (n == "angry" || n == "gian") return EMO_ANGRY;
    if (n == "love" || n == "yeu") return EMO_LOVE;
    if (n == "sleeping" || n == "sleepy") return EMO_SLEEPY;
    return EMO_NEUTRAL;                                     // focused, thinking, neutral, …
}

void setSpeechLevel(float v) { speechLevel = constrain(v, 0.0f, 1.0f); }
void setListenLevel(float v) { listenLevel = constrain(v, 0.0f, 1.0f); }
void noteActivity() { lastActivity = millis(); }
void relaxEmotion(uint32_t afterMs) {
    if (curEmo != EMO_NEUTRAL && curEmo != EMO_SLEEPY && emoUntil == 0) emoUntil = millis() + afterMs;
}
void forceRedraw() { dirty = true; }

void tick() {
    if (!d) return;
    uint32_t now = millis();
    if (!dirty && now - lastFrame < FRAME_MS) return;
    lastFrame = now; dirty = false; frameNo++;
    render(now);
}

}  // namespace Face
