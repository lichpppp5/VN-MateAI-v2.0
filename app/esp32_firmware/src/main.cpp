// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
/**
 * esp32_firmware/src/main.cpp
 * ===========================
 * Phase 53: Pairing Code + XiaoZhi-Style Conversation Flow.
 * Sơ đồ chân chuẩn xác theo KST AI ROBOT ESP32-S3 YD Board N16R8.
 *
 * Luồng hội thoại chuẩn (giống XiaoZhi gốc):
 *   IDLE      → Ngủ, OLED đôi mắt nhắm, mic tắt
 *   LISTENING → Chạm tay / wake → mắt mở to, LED sáng, mic BẬT, stream audio
 *   THINKING  → Dứt tiếng nói → mic TẮT → OLED hiện dots animation "..."
 *   SPEAKING  → Server trả TTS → OLED lip-sync, loa phát, mic tắt
 *   IDLE      → TTS xong → ngủ lại
 *
 * Pairing Code (thay thế nhập IP):
 *   - Robot lưu mã 6 số vào NVS (hoặc sinh ngẫu nhiên khi lần đầu).
 *   - Kết nối WiFi → WebSocket → gửi mã trong frame "hello".
 *   - Server phản hồi hello_ack kèm mã đã xác nhận → OLED hiển thị mã to.
 *   - User nhập mã vào Web UI để ghép cặp — không cần biết IP robot/server.
 */

#include <Arduino.h>
#include <string.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <WebServer.h>
#include <DNSServer.h>
#include <Preferences.h>
#include <WebSocketsClient.h>
#include <ArduinoJson.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <driver/i2s.h>
#include <math.h>

#include "config.h"
#include "motion_core.h"
#include "face.h"

// ─── Global Instances ────────────────────────────────────────────────────────
WebSocketsClient webSocket;
Adafruit_SSD1306 display(128, 64, &Wire, -1);
Preferences prefs;

// ─── Runtime network config (loaded from NVS or defaults) ────────────────────
String cfg_ssid;
String cfg_pass;
String cfg_server_host;
uint16_t cfg_server_port = DEFAULT_SERVER_PORT;
String cfg_device_token;
String cfg_pairing_code;  // Mã 6 số duy nhất của robot — lưu NVS, hiển thị OLED

// ─── Conversation State Machine ───────────────────────────────────────────────
// Trạng thái hội thoại chuẩn XiaoZhi: IDLE → LISTENING → THINKING → SPEAKING → IDLE
enum ConvState {
    CONV_IDLE,      // Ngủ — mic tắt, OLED đôi mắt nhắm
    CONV_LISTENING, // Đang nghe — mic BẬT, LED sáng, mắt tập trung
    CONV_THINKING,  // Đã nói xong, chờ AI phản hồi — mic tắt, OLED dots "..."
    CONV_SPEAKING   // AI đang phát TTS — mic tắt, OLED lip-sync
};
volatile ConvState convState = CONV_IDLE;

// Backward-compat helpers
volatile bool isConnectedToServer = false;
volatile bool isPaired = false;        // Server đã xác nhận hello_ack với pairing_code

#define isListening  (convState == CONV_LISTENING)
#define isSpeaking   (convState == CONV_SPEAKING)

// UI display state
String currentUiState = "idle";
String currentEmotion = "sleeping";
String currentScreenText = "";

// Thinking dots animation

// Lip-sync & animation state

// Touch sensor debounce
unsigned long lastTouchTime = 0;
bool touchActive = false;

// Thread-safe Audio Buffer Struct & FreeRTOS Queue
struct PcmChunk {
    int16_t samples[256];
    size_t len;
};
QueueHandle_t micQueue = nullptr;

// ─── Gọi tên "hey Ly Ly" (máy chủ nhận dạng OFFLINE) ────────────────────────
// Lúc nghỉ: đọc mic, lọc giọng nói bằng năng lượng ngay trên robot (im lặng không
// gửi gì), có tiếng nói thì gửi một đoạn ngắn (kèm 0,4 s trước đó) cho máy chủ;
// máy chủ nghe thấy tên trợ lý thì ra lệnh robot lắng nghe.
#define WAKE_LISTEN_ENABLED 1
static const size_t   WAKE_PREROLL_SAMPLES   = 6400;   // 0,4 s @16 kHz
static const size_t   WAKE_MIN_SAMPLES       = 4800;   // 0,3 s tiếng nói tối thiểu
static const uint32_t WAKE_END_SILENCE_MS    = 450;    // im ngần này thì hết câu
static const uint32_t WAKE_COOLDOWN_MS       = 1200;
static const float    WAKE_MIN_RMS           = 300.0f; // sàn tuyệt đối (mic INMP441 >>14)
static const uint32_t LISTEN_SILENCE_TIMEOUT_MS = 8000; // đang nghe mà im ngần này -> nghỉ
int16_t* wakeBuf = nullptr;              // đoạn gửi máy chủ (PSRAM nếu có)
size_t   wakeCap = 0;                    // sức chứa (mẫu)
volatile size_t wakeLen = 0;
volatile bool   wakeReady = false;       // task mic -> vòng loop gửi đi
volatile float  wakeNoiseFloor = 200.0f; // ước lượng ồn nền (RMS)
volatile float  wakePeakRms = 0.0f;      // RMS lớn nhất từ lần báo trước (hiệu chỉnh)
// v54: âm lượng loa 0..100 (lưu NVS "vol"), đổi từ Portal — trước đây cố định.
uint8_t speakerVolume = 100;
#define FIRMWARE_VERSION "55.0"
static void sendStatusReport(const char* reason);
volatile uint32_t lastVoiceMs = 0;       // lần cuối có tiếng nói (khi đang nghe)
uint32_t listenEnteredMs = 0;
// Thời gian im lặng tối đa của lượt nghe hiện tại: máy chủ đặt dài hơn (30 s) khi
// robot vừa trả lời xong và chờ chỉ lệnh tiếp theo.
uint32_t listenTimeoutMs = LISTEN_SILENCE_TIMEOUT_MS;
TaskHandle_t micTaskHandle = nullptr;

// ─── Function Declarations ───────────────────────────────────────────────────
void loadConfigFromNVS();
void generateOrLoadPairingCode();
bool startProvisioningPortal();
void setupWiFi();
void setupWebSocket();
void setupOLED();
void setupTouchAndLEDs();
void setupI2S();
void playStartupChime();
void playPcmToSpeaker(const uint8_t* pcmData, size_t length);
void micRecordTaskLoop(void* arg);
void webSocketEvent(WStype_t type, uint8_t * payload, size_t length);
void handleIncomingJson(const char* jsonStr);
void onSafetyAlert(const char* alertMsg);
void drawOledPairingCode(const String& code);
// Màn hình mã ghép đôi hiện đè lên gương mặt ngần ấy ms rồi gương mặt vẽ tiếp.
uint32_t faceOverlayUntil = 0;
void checkTouchSensor();
void setConvState(ConvState newState);
static void sendWakeClip();
static float chunkRms(const int16_t* s, size_t n);
static void sendWakeStats();

// ─── v55: Cử động tự nhiên (đèn RGB, cử chỉ, lắc bánh nhẹ lúc rảnh) ──────────
// Máy chủ gửi {"type":"behavior", ...} (application/devices/robot_behavior.py) khi bắt tay
// và khi cấu hình / giờ yên lặng đổi. Mặc định dưới đây dùng khi chưa nhận được khung đó.
// Mọi cử động chạy trong TASK RIÊNG: vòng loop() không bị chặn -> WebSocket / âm thanh không giật.
struct Behavior {
    volatile bool idle = true;          // cử động lúc rảnh (servo + đèn)
    volatile bool idleWheels = true;    // lắc bánh nhẹ lúc rảnh
    volatile bool speechGestures = true;
    volatile bool led = true;
    volatile uint16_t idleMinS = 30, idleMaxS = 120;
    volatile uint16_t wheelMs = 120;    // một nhịp bánh — ngắn = nhẹ
};
Behavior behavior;

enum GestureCode : uint8_t { G_NONE = 0, G_WAVE, G_NOD, G_LOOK, G_EXCITED, G_SAD, G_SMALL_NOD };
QueueHandle_t gestureQueue = nullptr;

// Màu đèn theo cảm xúc câu đang nói (máy chủ chọn — application/voice/emotion.py).
enum LedMood : uint8_t { MOOD_NEUTRAL, MOOD_HAPPY, MOOD_SAD, MOOD_EXCITED, MOOD_WOW, MOOD_LOVE };
volatile uint8_t ledMood = MOOD_NEUTRAL;

// Trong lúc bánh / servo đang chạy (và một chút sau): KHÔNG bắt câu gọi tên — tiếng động cơ
// không được gửi lên máy chủ như một câu nói.
volatile uint32_t motionQuietUntil = 0;

static uint8_t moodFromEmotion(const String& e) {
    if (e == "happy") return MOOD_HAPPY;
    if (e == "sad" || e == "cry") return MOOD_SAD;
    if (e == "excited") return MOOD_EXCITED;
    if (e == "wow") return MOOD_WOW;
    if (e == "love") return MOOD_LOVE;
    return MOOD_NEUTRAL;
}

static void queueGesture(GestureCode g) {
    if (gestureQueue && g != G_NONE) xQueueSend(gestureQueue, &g, 0);
}

static void setRgb(uint8_t r, uint8_t g, uint8_t b) { neopixelWrite(RGB_PIN, r, g, b); }

// Đèn RGB: rảnh = thở chậm xanh ngọc · nghe = xanh dương · nghĩ = tím nhấp nháy · nói = màu cảm xúc.
static void updateRgb(uint32_t now) {
    if (!behavior.led) { setRgb(0, 0, 0); return; }
    float wave = (sinf(now / 1000.0f * 2.0f * PI / 4.0f) + 1.0f) * 0.5f;       // chu kỳ 4 s
    switch (convState) {
        case CONV_LISTENING: setRgb(0, 0, 40); break;
        case CONV_THINKING: {
            float fast = (sinf(now / 1000.0f * 2.0f * PI / 0.8f) + 1.0f) * 0.5f;
            setRgb((uint8_t)(10 + 25 * fast), 0, (uint8_t)(15 + 30 * fast));
            break;
        }
        case CONV_SPEAKING:
            switch (ledMood) {
                case MOOD_HAPPY:   setRgb(30, 30, 0);  break;
                case MOOD_SAD:     setRgb(0, 4, 20);   break;
                case MOOD_EXCITED: setRgb(45, 15, 0);  break;
                case MOOD_WOW:     setRgb(30, 30, 30); break;
                case MOOD_LOVE:    setRgb(40, 0, 15);  break;
                default:           setRgb(0, 25, 25);  break;
            }
            break;
        case CONV_IDLE:
        default:
            setRgb(0, (uint8_t)(2 + 14 * wave), (uint8_t)(2 + 12 * wave));
            break;
    }
}

static void runGesture(GestureCode g) {
    MotionCore& m = MotionCore::getInstance();
    motionQuietUntil = millis() + 2500;
    switch (g) {
        case G_WAVE:      m.waveArm(); break;
        case G_NOD:       m.nodNeck(); break;
        case G_LOOK:      m.lookAround(); break;
        case G_EXCITED:   m.excited(); break;
        case G_SAD:       m.sad(); break;
        case G_SMALL_NOD: m.smallNod(); break;
        default: break;
    }
    motionQuietUntil = millis() + 400;
}

// Một nhịp bánh rồi dừng; chờ tới khi motor đã dừng hẳn.
static void wheelPulse(RobotDirection dir, uint16_t ms) {
    if (!MotionCore::getInstance().moveRobot(dir, ms)) return;
    vTaskDelay(pdMS_TO_TICKS(ms + 60));
}

// Cử động lúc rảnh: ngẫu nhiên một việc nhỏ. Bánh: xoay trái rồi phải, hoặc nhích tiến rồi lùi
// cùng thời lượng — robot luôn về chỗ cũ; gặp mép bàn (ToF) thì không nhích tiến và không lùi.
static void idleAction() {
    MotionCore& m = MotionCore::getInstance();
    bool wheelsOk = behavior.idleWheels && isConnectedToServer && !m.isCliffDetected();
    int pick = random(0, wheelsOk ? 6 : 3);
    uint16_t ms = behavior.wheelMs;
    motionQuietUntil = millis() + 4000;
    switch (pick) {
        case 0: m.lookAround(); break;
        case 1: m.tiltHead(random(0, 2) ? 105 : 75, 600); break;
        case 2: m.smallNod(); break;
        case 3:                                                  // lắc trái - phải
            wheelPulse(DIR_LEFT, ms);
            vTaskDelay(pdMS_TO_TICKS(150));
            wheelPulse(DIR_RIGHT, ms);
            break;
        case 4:                                                  // nhích tiến rồi lùi
            if (!m.isCliffDetected()) {
                bool moved = m.moveRobot(DIR_FORWARD, ms);
                if (moved) {
                    vTaskDelay(pdMS_TO_TICKS(ms + 260));
                    wheelPulse(DIR_BACKWARD, ms);
                }
            }
            break;
        default:                                                 // lắc phải - trái + nghiêng đầu
            wheelPulse(DIR_RIGHT, ms);
            vTaskDelay(pdMS_TO_TICKS(150));
            wheelPulse(DIR_LEFT, ms);
            m.tiltHead(random(0, 2) ? 105 : 75, 400);
            break;
    }
    motionQuietUntil = millis() + 500;
}

void behaviorTaskLoop(void* arg) {
    uint32_t nextIdleAt = millis() + 15000;
    uint32_t nextNodAt = 0;
    uint32_t lastLed = 0;
    for (;;) {
        uint32_t now = millis();
        GestureCode g;
        if (xQueueReceive(gestureQueue, &g, pdMS_TO_TICKS(30)) == pdTRUE) {
            runGesture(g);
            nextNodAt = millis() + random(3500, 6000);
            continue;
        }
        if (now - lastLed >= 30) { updateRgb(now); lastLed = now; }

        if (convState == CONV_SPEAKING) {
            // Gật nhẹ theo nhịp câu nói (không chạy bánh: đang phát loa).
            if (behavior.speechGestures) {
                if (nextNodAt == 0) nextNodAt = now + random(2500, 4500);
                if (now >= nextNodAt) { runGesture(G_SMALL_NOD); nextNodAt = millis() + random(3500, 6000); }
            }
        } else {
            nextNodAt = 0;
        }

        if (convState == CONV_IDLE && behavior.idle) {
            if (now >= nextIdleAt) {
                idleAction();
                uint32_t lo = behavior.idleMinS, hi = max((uint32_t)behavior.idleMinS, (uint32_t)behavior.idleMaxS);
                nextIdleAt = millis() + 1000UL * random(lo, hi + 1);
            }
        } else if (convState != CONV_IDLE) {
            // vừa có hội thoại: chờ ít nhất idleMinS rồi mới cử động lúc rảnh
            nextIdleAt = now + 1000UL * behavior.idleMinS;
        }
    }
}

void startBehaviorTask() {
    gestureQueue = xQueueCreate(4, sizeof(GestureCode));
    randomSeed(esp_random());
    xTaskCreate(behaviorTaskLoop, "behavior", 4096, nullptr, 1, nullptr);
}

static void applyBehaviorFrame(JsonDocument& doc) {
    behavior.idle = doc["idle"] | (bool)behavior.idle;
    behavior.idleWheels = doc["idle_wheels"] | (bool)behavior.idleWheels;
    behavior.speechGestures = doc["speech_gestures"] | (bool)behavior.speechGestures;
    behavior.led = doc["led"] | (bool)behavior.led;
    uint16_t mn = doc["idle_min_s"] | (uint16_t)behavior.idleMinS;
    uint16_t mx = doc["idle_max_s"] | (uint16_t)behavior.idleMaxS;
    uint16_t wm = doc["wheel_ms"] | (uint16_t)behavior.wheelMs;
    behavior.idleMinS = constrain(mn, 5, 3600);
    behavior.idleMaxS = constrain(max(mn, mx), 5, 3600);
    behavior.wheelMs = constrain(wm, 60, 300);
    bool quiet = doc["quiet"] | false;
    Serial.printf("[Behavior] idle=%d wheels=%d speech=%d led=%d nghi %u-%us nhip banh %ums%s\n",
                  (int)behavior.idle, (int)behavior.idleWheels, (int)behavior.speechGestures, (int)behavior.led,
                  (unsigned)behavior.idleMinS, (unsigned)behavior.idleMaxS, (unsigned)behavior.wheelMs,
                  quiet ? " (gio yen lang)" : "");
}

// ─── Setup ───────────────────────────────────────────────────────────────────
void setup() {
    Serial.begin(115200);
    delay(500);
    Serial.println(F("\n======================================================="));
    Serial.println(F("   VN-MATE AI // ROBOTICS COMPANION FIRMWARE v55.0    "));
    Serial.println(F("   Pairing Code + XiaoZhi Conversation Flow           "));
    Serial.println(F("======================================================="));

    setupOLED();
    setupTouchAndLEDs();
    loadConfigFromNVS();
    generateOrLoadPairingCode();
    setupI2S();

    // Bộ đệm đoạn câu gọi: 4 s nếu có PSRAM, không thì 2,5 s trong RAM thường.
    wakeCap = psramFound() ? 64000 : 40000;
    wakeBuf = psramFound() ? (int16_t*)ps_malloc(wakeCap * 2) : (int16_t*)malloc(wakeCap * 2);
    if (!wakeBuf) { wakeCap = 0; Serial.println(F("[Wake] Không cấp được bộ đệm — tắt gọi tên.")); }
    else Serial.printf("[Wake] Bộ đệm câu gọi %u mẫu (%s)\n", (unsigned)wakeCap, psramFound() ? "PSRAM" : "RAM");

    MotionCore::getInstance().setAlertCallback(onSafetyAlert);
    MotionCore::getInstance().init();

    setupWiFi();
    setupWebSocket();

    Face::setEmotion(Face::EMO_HAPPY, 2500);       // chào khi khởi động
    MotionCore::getInstance().waveArm();
    setConvState(CONV_IDLE);
    startBehaviorTask();                           // v55: đèn RGB + cử động tự nhiên
}

// ─── Main Loop ───────────────────────────────────────────────────────────────
void loop() {
    webSocket.loop();

    // Gửi audio mic chỉ khi LISTENING
    if (isConnectedToServer && convState == CONV_LISTENING && micQueue != nullptr) {
        PcmChunk chunk;
        if (xQueueReceive(micQueue, &chunk, 0) == pdTRUE) {
            webSocket.sendBIN(reinterpret_cast<uint8_t*>(chunk.samples), chunk.len);
        }
    }

    if (isConnectedToServer && wakeReady) sendWakeClip();
    if (isConnectedToServer && convState == CONV_IDLE && WAKE_LISTEN_ENABLED) sendWakeStats();

    // Đang nghe mà không ai nói (đánh thức nhầm / chạm nhầm): về nghỉ, báo máy chủ.
    if (convState == CONV_LISTENING &&
        millis() - listenEnteredMs > listenTimeoutMs &&
        millis() - lastVoiceMs > listenTimeoutMs) {
        webSocket.sendTXT("{\"type\":\"listen\",\"state\":\"abort\"}");
        setConvState(CONV_IDLE);
    }

    checkTouchSensor();
    if (millis() >= faceOverlayUntil) Face::tick();

    delay(2);
}

// ─── Conversation State Manager ───────────────────────────────────────────────

void setConvState(ConvState newState) {
    if (convState == newState) return;
    convState = newState;

    switch (newState) {
        case CONV_IDLE:
            digitalWrite(LED1_PIN, LOW);
            digitalWrite(LED2_PIN, LOW);
            currentUiState = "idle";
            currentEmotion = "sleeping";
            Face::setMode(Face::MODE_IDLE);
            Face::relaxEmotion(3000);      // giữ biểu cảm câu trả lời thêm chút rồi về bình thường
            Serial.println(F("[State] → IDLE"));
            break;

        case CONV_LISTENING:
            digitalWrite(LED1_PIN, HIGH);
            digitalWrite(LED2_PIN, HIGH);
            currentUiState = "listening";
            currentEmotion = "focused";
            listenEnteredMs = millis();
            lastVoiceMs = millis();
            if (micQueue) {
                PcmChunk dummy;
                while (xQueueReceive(micQueue, &dummy, 0) == pdTRUE) {}
            }
            Face::setMode(Face::MODE_LISTENING);
            Serial.println(F("[State] → LISTENING"));
            break;

        case CONV_THINKING:
            digitalWrite(LED1_PIN, HIGH);
            digitalWrite(LED2_PIN, LOW);
            currentUiState = "thinking";
            currentEmotion = "thinking";
            Face::setMode(Face::MODE_THINKING);
            Serial.println(F("[State] → THINKING (chờ AI...)"));
            break;

        case CONV_SPEAKING:
            digitalWrite(LED1_PIN, HIGH);
            digitalWrite(LED2_PIN, LOW);
            currentUiState = "speaking";
            currentEmotion = "happy";
            Face::setMode(Face::MODE_SPEAKING);
            Serial.println(F("[State] → SPEAKING"));
            break;
    }
}

// ─── Hardware: I2S Audio ─────────────────────────────────────────────────────

void setupI2S() {
    Serial.println(F("[Audio] Cấu hình I2S..."));
    micQueue = xQueueCreate(6, sizeof(PcmChunk));

    i2s_config_t mic_config = {
        .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX),
        .sample_rate = 16000,
        .bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT,
        .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
        .communication_format = I2S_COMM_FORMAT_STAND_I2S,
        .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
        .dma_buf_count = 8, .dma_buf_len = 256,
        .use_apll = false, .tx_desc_auto_clear = false, .fixed_mclk = 0
    };
    i2s_pin_config_t mic_pins = {
        .bck_io_num = I2S_MIC_SCK, .ws_io_num = I2S_MIC_WS,
        .data_out_num = I2S_PIN_NO_CHANGE, .data_in_num = I2S_MIC_SD
    };
    if (i2s_driver_install(I2S_NUM_0, &mic_config, 0, NULL) == ESP_OK) {
        i2s_set_pin(I2S_NUM_0, &mic_pins);
        Serial.println(F("[Audio] Micro INMP441 OK."));
    }

    i2s_config_t spk_config = {
        .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_TX),
        .sample_rate = 16000,
        .bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT,
        .channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT,
        .communication_format = I2S_COMM_FORMAT_STAND_I2S,
        .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
        .dma_buf_count = 8, .dma_buf_len = 512,
        .use_apll = false, .tx_desc_auto_clear = true, .fixed_mclk = 0
    };
    i2s_pin_config_t spk_pins = {
        .bck_io_num = I2S_SPK_BCLK, .ws_io_num = I2S_SPK_LRC,
        .data_out_num = I2S_SPK_DIN, .data_in_num = I2S_PIN_NO_CHANGE
    };
    if (i2s_driver_install(I2S_NUM_1, &spk_config, 0, NULL) == ESP_OK) {
        i2s_set_pin(I2S_NUM_1, &spk_pins);
        Serial.println(F("[Audio] Loa MAX98357A OK."));
    }

    playStartupChime();
    xTaskCreatePinnedToCore(micRecordTaskLoop, "MicRecordTask", 4096, NULL, 2, &micTaskHandle, 0);
}

void playStartupChime() {
    int16_t stereo[256]; size_t written = 0;
    for (int k = 0; k < 8; ++k) {
        for (int i = 0; i < 128; ++i) {
            float t = (float)(k*128+i)/16000.0f;
            int16_t v = (int16_t)(sinf(2.0f*PI*523.0f*t)*8000.0f);
            stereo[i*2]=v; stereo[i*2+1]=v;
        }
        i2s_write(I2S_NUM_1, stereo, sizeof(stereo), &written, portMAX_DELAY);
    }
    for (int k = 0; k < 12; ++k) {
        for (int i = 0; i < 128; ++i) {
            float t = (float)(k*128+i)/16000.0f;
            int16_t v = (int16_t)(sinf(2.0f*PI*659.0f*t)*9000.0f);
            stereo[i*2]=v; stereo[i*2+1]=v;
        }
        i2s_write(I2S_NUM_1, stereo, sizeof(stereo), &written, portMAX_DELAY);
    }
}

void playPcmToSpeaker(const uint8_t* pcmData, size_t length) {
    if (!pcmData || length == 0) return;
    const int16_t* mono = reinterpret_cast<const int16_t*>(pcmData);
    size_t n = length / 2;
    int16_t buf[256];
    size_t offset = 0;
    while (offset < n) {
        size_t batch = min((size_t)128, n - offset);
        for (size_t i = 0; i < batch; ++i) {
            // Âm lượng phần mềm (v54): nhân tuyến tính, không thể vượt biên độ gốc.
            int32_t v = ((int32_t)mono[offset+i] * speakerVolume) / 100;
            buf[i*2] = (int16_t)v; buf[i*2+1] = (int16_t)v;
        }
        size_t bw = 0;
        i2s_write(I2S_NUM_1, buf, batch*4, &bw, portMAX_DELAY);
        Face::setSpeechLevel(chunkRms(mono + offset, batch) / 6000.0f);
        if (millis() >= faceOverlayUntil) Face::tick();      // tự giới hạn ~15 khung/giây
        offset += batch;
    }
    Face::setSpeechLevel(0.0f);
}

// ─── FreeRTOS Task: Thu âm Micro ─────────────────────────────────────────────

// INMP441: 24 bit căn trái trong khung 32 bit. >>14 giữ độ khuếch đại ×4 so với
// 16 bit chuẩn (nghe xa tốt hơn) nhưng phải KẸP — ép kiểu thẳng làm tiếng to bị
// quấn số (+32767 thành -32768…): âm thanh méo, đo được đỉnh luôn ~30.000.
static inline int16_t micToPcm16(int32_t raw) {
    int32_t v = raw >> 14;
    if (v > 32767) v = 32767;
    else if (v < -32768) v = -32768;
    return (int16_t)v;
}

static float chunkRms(const int16_t* s, size_t n) {
    if (n == 0) return 0.0f;
    double acc = 0.0;
    for (size_t i = 0; i < n; ++i) acc += (double)s[i] * (double)s[i];
    return (float)sqrt(acc / (double)n);
}

// Lọc giọng nói lúc nghỉ: vòng đệm 0,4 s; vượt ồn nền 3 khối liên tiếp -> bắt đầu
// ghi; im WAKE_END_SILENCE_MS hoặc đầy bộ đệm -> giao cho vòng loop gửi đi.
static void wakeProcessChunk(const int16_t* s, size_t n, float rms) {
    static int16_t preroll[WAKE_PREROLL_SAMPLES];
    static size_t prePos = 0, preCount = 0;
    static bool capturing = false;
    static int loud = 0;
    static uint32_t lastLoudMs = 0, cooldownUntil = 0;
    uint32_t now = millis();
    // v55: đang cử động (bánh / servo) -> bỏ đoạn này, không coi tiếng động cơ là câu gọi tên.
    if (now < motionQuietUntil) { capturing = false; loud = 0; preCount = 0; wakeLen = 0; return; }
    if (rms > wakePeakRms) wakePeakRms = rms;
    float startThr = max(wakeNoiseFloor * 2.0f, WAKE_MIN_RMS);

    if (!capturing) {
        for (size_t i = 0; i < n; ++i) {
            preroll[prePos] = s[i];
            prePos = (prePos + 1) % WAKE_PREROLL_SAMPLES;
            if (preCount < WAKE_PREROLL_SAMPLES) preCount++;
        }
        if (rms > startThr && now >= cooldownUntil) {
            if (++loud >= 3) {
                capturing = true; lastLoudMs = now; wakeLen = 0;
                size_t start = (prePos + WAKE_PREROLL_SAMPLES - preCount) % WAKE_PREROLL_SAMPLES;
                for (size_t i = 0; i < preCount && wakeLen < wakeCap; ++i)
                    wakeBuf[wakeLen++] = preroll[(start + i) % WAKE_PREROLL_SAMPLES];
            }
        } else {
            loud = 0;
            // Ồn nền: xuống nhanh khi phòng yên, lên RẤT chậm — tiếng nói không kéo
            // nó lên (trước đây lên tới ~10.000, ngưỡng bắt tiếng theo đó, gọi xa không tới).
            if (rms < wakeNoiseFloor) wakeNoiseFloor = wakeNoiseFloor * 0.90f + rms * 0.10f;
            else                      wakeNoiseFloor = wakeNoiseFloor * 0.998f + rms * 0.002f;
        }
        return;
    }
    for (size_t i = 0; i < n && wakeLen < wakeCap; ++i) wakeBuf[wakeLen++] = s[i];
    if (rms > max(wakeNoiseFloor * 1.5f, WAKE_MIN_RMS * 0.7f)) lastLoudMs = now;
    if (now - lastLoudMs > WAKE_END_SILENCE_MS || wakeLen >= wakeCap) {
        capturing = false; loud = 0; preCount = 0;
        cooldownUntil = now + WAKE_COOLDOWN_MS;
        if (wakeLen >= WAKE_PREROLL_SAMPLES / 2 + WAKE_MIN_SAMPLES) wakeReady = true;
        else wakeLen = 0;
    }
}

void micRecordTaskLoop(void* arg) {
    int32_t raw32[256]; PcmChunk chunk; size_t bytesRead = 0;
    for (;;) {
        if (isConnectedToServer && convState == CONV_LISTENING) {
            if (i2s_read(I2S_NUM_0, raw32, sizeof(raw32), &bytesRead, pdMS_TO_TICKS(50)) == ESP_OK && bytesRead > 0) {
                size_t ns = bytesRead / 4;
                for (size_t i = 0; i < ns; ++i) chunk.samples[i] = micToPcm16(raw32[i]);
                chunk.len = ns * 2;
                float lvl = chunkRms(chunk.samples, ns);
                if (lvl > max(wakeNoiseFloor * 2.0f, WAKE_MIN_RMS * 0.7f))
                    lastVoiceMs = millis();
                Face::setListenLevel(lvl / max(wakeNoiseFloor * 6.0f, 3000.0f));
                if (micQueue) xQueueSend(micQueue, &chunk, 0);
            }
        } else if (WAKE_LISTEN_ENABLED && wakeBuf && isConnectedToServer &&
                   convState == CONV_IDLE && !wakeReady) {
            if (i2s_read(I2S_NUM_0, raw32, sizeof(raw32), &bytesRead, pdMS_TO_TICKS(50)) == ESP_OK && bytesRead > 0) {
                size_t ns = bytesRead / 4;
                for (size_t i = 0; i < ns; ++i) chunk.samples[i] = micToPcm16(raw32[i]);
                wakeProcessChunk(chunk.samples, ns, chunkRms(chunk.samples, ns));
            }
        } else {
            vTaskDelay(pdMS_TO_TICKS(20));
        }
        vTaskDelay(pdMS_TO_TICKS(5));
    }
}

// Gửi đoạn nghi là câu gọi: {"type":"wake_check"} + PCM16 nhị phân + {"type":"wake_check_end"}.
static void sendWakeClip() {
    if (convState == CONV_IDLE && wakeLen > 0) {
        webSocket.sendTXT("{\"type\":\"wake_check\",\"rate\":16000}");
        for (size_t off = 0; off < wakeLen; off += 2048) {
            size_t n = min((size_t)2048, (size_t)(wakeLen - off));
            webSocket.sendBIN(reinterpret_cast<uint8_t*>(wakeBuf + off), n * 2);
        }
        webSocket.sendTXT("{\"type\":\"wake_check_end\"}");
    }
    wakeLen = 0;
    wakeReady = false;
}

// Số liệu hiệu chỉnh ngưỡng (ồn nền, đỉnh) — máy chủ ghi log; không có âm thanh.
static void sendWakeStats() {
    static uint32_t last = 0;
    if (millis() - last < 15000) return;
    last = millis();
    char buf[96];
    snprintf(buf, sizeof(buf), "{\"type\":\"wake_stats\",\"noise\":%.0f,\"peak\":%.0f}",
             (double)wakeNoiseFloor, (double)wakePeakRms);
    webSocket.sendTXT(buf);
    wakePeakRms = 0.0f;
}

// v54: trạng thái thật của robot cho Portal (âm lượng, Wi-Fi, bộ nhớ, ồn nền micro).
static void sendStatusReport(const char* reason) {
    StaticJsonDocument<384> d;
    d["type"]      = "status_report";
    d["reason"]    = reason;
    d["version"]   = FIRMWARE_VERSION;
    d["volume"]    = speakerVolume;
    d["rssi"]      = WiFi.RSSI();
    d["ip"]        = WiFi.localIP().toString();
    d["heap"]      = ESP.getFreeHeap();
    d["psram"]     = psramFound() ? ESP.getFreePsram() : 0;
    d["uptime_s"]  = millis() / 1000;
    d["noise"]     = (int)wakeNoiseFloor;
    d["peak"]      = (int)wakePeakRms;
    String out; serializeJson(d, out);
    webSocket.sendTXT(out);
}

// ─── Hardware: OLED & GPIO ───────────────────────────────────────────────────

void setupOLED() {
    Wire.begin(OLED_SDA, OLED_SCL, 400000);
    if (!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
        Serial.println(F("[OLED] Không tìm thấy SSD1306!"));
    } else {
        display.clearDisplay(); display.setTextColor(SSD1306_WHITE);
        display.setTextSize(1); display.setCursor(10, 25);
        display.println(F("VN-MateAI Robot v54")); display.display();
        Face::begin(&display);
    }
}

void setupTouchAndLEDs() {
    pinMode(TOUCH_PIN, INPUT_PULLDOWN);
    pinMode(LED1_PIN, OUTPUT); pinMode(LED2_PIN, OUTPUT); pinMode(RGB_PIN, OUTPUT);
    digitalWrite(LED1_PIN, LOW); digitalWrite(LED2_PIN, LOW);
}

// ─── NVS Config ──────────────────────────────────────────────────────────────

void loadConfigFromNVS() {
    prefs.begin("vnmate", false);
    cfg_ssid        = prefs.getString("ssid",   DEFAULT_WIFI_SSID);
    cfg_pass        = prefs.getString("pass",   DEFAULT_WIFI_PASS);
    cfg_server_host = prefs.getString("host",   DEFAULT_SERVER_HOST);
    cfg_server_port = prefs.getUShort("port",   DEFAULT_SERVER_PORT);
    cfg_device_token= prefs.getString("token",  DEFAULT_DEVICE_TOKEN);
    cfg_pairing_code= prefs.getString("pcode",  "");
    speakerVolume   = prefs.getUChar("vol", 100);
    if (speakerVolume > 100) speakerVolume = 100;
    prefs.end();
    // Ô token trên trang cài đặt để trống thì NVS lưu chuỗi rỗng — vẫn dùng token
    // biên dịch sẵn (secrets.h). Trước đây robot kết nối không token và bị từ chối.
    if (cfg_device_token.length() == 0) {
        cfg_device_token = DEFAULT_DEVICE_TOKEN;
    }
    if (cfg_server_host == "192.168.100.169" || cfg_server_host.length() == 0) {
        cfg_server_host = DEFAULT_SERVER_HOST;
    }
    Serial.printf("[Config] SSID='%s' Host='%s' Port=%d\n",
        cfg_ssid.c_str(), cfg_server_host.c_str(), cfg_server_port);
}

// ─── Pairing Code: Sinh hoặc Load từ NVS ─────────────────────────────────────

void generateOrLoadPairingCode() {
    if (cfg_pairing_code.length() == 6) {
        Serial.printf("[PairingCode] Load từ NVS: %s\n", cfg_pairing_code.c_str());
        return;
    }
    uint8_t mac[6];
    WiFi.macAddress(mac);
    uint32_t seed = ((uint32_t)mac[3]<<16)|((uint32_t)mac[4]<<8)|mac[5];
    seed ^= (uint32_t)millis();
    randomSeed(seed);
    char code[7];
    snprintf(code, sizeof(code), "%06lu", (unsigned long)(random(0, 999999)));
    cfg_pairing_code = String(code);
    prefs.begin("vnmate", false);
    prefs.putString("pcode", cfg_pairing_code);
    prefs.end();
    Serial.printf("[PairingCode] Sinh mã mới: %s\n", cfg_pairing_code.c_str());
}

// ─── SoftAP Provisioning Portal ──────────────────────────────────────────────
// Chỉ cần nhập WiFi — robot tự kết nối server qua DEFAULT_SERVER_HOST.
// Nhận dạng robot bằng PAIRING CODE 6 số, không cần nhập IP.

bool startProvisioningPortal() {
    const char* AP_SSID = "VNMate-Setup";
    const char* AP_PASS = "vnmate123";
    Serial.println(F("[Provision] Khởi động SoftAP + WiFi Scanner..."));

    display.clearDisplay();
    display.setTextSize(1);
    display.setCursor(0, 0);  display.println(F("-- SETUP MODE --"));
    display.setCursor(0, 10); display.println(F("WiFi: VNMate-Setup"));
    display.setCursor(0, 20); display.println(F("Pass: vnmate123"));
    display.setCursor(0, 30); display.println(F("Open: 192.168.4.1"));
    display.setCursor(0, 46); display.print(F("Code: "));
    display.setTextSize(2); display.print(cfg_pairing_code); display.setTextSize(1);
    display.display();

    WiFi.mode(WIFI_AP_STA);
    WiFi.softAP(AP_SSID, AP_PASS);
    delay(300);

    DNSServer dnsServer;
    dnsServer.start(53, "*", WiFi.softAPIP());

    WebServer server(80);
    bool saved = false;

    // Quét WiFi ban đầu để có sẵn danh sách ngay khi tải trang
    Serial.println(F("[Provision] Quét mạng WiFi xung quanh..."));
    WiFi.scanNetworks(false, false);

    server.on("/scan", HTTP_GET, [&]() {
        Serial.println(F("[Provision] Quét lại mạng WiFi..."));
        int n = WiFi.scanNetworks(false, false);
        String json = "[";
        for (int i = 0; i < n; ++i) {
            String s = WiFi.SSID(i);
            if (s.length() == 0) continue;
            if (json.length() > 1) json += ",";
            int r = WiFi.RSSI(i);
            bool enc = (WiFi.encryptionType(i) != WIFI_AUTH_OPEN);
            json += "{\"ssid\":\"" + s + "\",\"rssi\":" + String(r) + ",\"enc\":" + (enc ? "true" : "false") + "}";
        }
        json += "]";
        server.send(200, "application/json; charset=utf-8", json);
    });

    server.on("/", HTTP_GET, [&]() {
        String html = F(
            "<!DOCTYPE html><html lang='vi'><head><meta charset='UTF-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>VN-MateAI Setup</title>"
            "<style>body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;"
            "background:#090d16;color:#e6edf3;display:flex;justify-content:center;align-items:center;"
            "min-height:100vh;margin:0;padding:1rem;box-sizing:border-box}"
            ".card{background:#111622;border:1px solid #1f293d;border-radius:14px;"
            "padding:1.8rem;width:100%;max-width:380px;box-shadow:0 10px 30px rgba(0,0,0,.5)}"
            ".title{color:#00f2fe;font-size:1.3rem;font-weight:700;text-align:center;margin:.3rem 0}"
            ".sub{text-align:center;font-size:.8rem;color:#8b949e;margin-bottom:1rem}"
            ".code-box{background:#060a12;border:2px solid #00f2fe;border-radius:8px;"
            "text-align:center;padding:.6rem;margin-bottom:1.2rem;font-size:1.8rem;"
            "letter-spacing:.25rem;color:#00f2fe;font-weight:700;font-family:monospace}"
            "label{display:block;margin:.9rem 0 .3rem;font-size:.85rem;color:#94a3b8;font-weight:500}"
            "select,input{width:100%;box-sizing:border-box;padding:.7rem .8rem;background:#090d16;"
            "border:1px solid #1f293d;border-radius:8px;color:#e6edf3;font-size:.95rem;"
            "outline:none;transition:border-color .2s}"
            "select:focus,input:focus{border-color:#00f2fe}"
            ".row-btn{display:flex;gap:.5rem;margin-top:.4rem}"
            ".btn-scan{padding:.6rem .8rem;background:#1e293b;border:1px solid #334155;"
            "border-radius:8px;color:#38bdf8;font-size:.8rem;cursor:pointer;font-weight:600;white-space:nowrap}"
            ".btn-scan:hover{background:#334155}"
            ".btn-submit{margin-top:1.4rem;width:100%;padding:.85rem;background:#0284c7;"
            "border:none;border-radius:8px;color:#fff;font-size:1rem;cursor:pointer;"
            "font-weight:700;letter-spacing:.02rem;transition:background .2s}"
            ".btn-submit:hover{background:#0369a1}"
            ".checkbox-wrap{display:flex;align-items:center;gap:.5rem;margin-top:.6rem;"
            "font-size:.8rem;color:#94a3b8;cursor:pointer}"
            ".checkbox-wrap input{width:auto;cursor:pointer}"
            ".note{margin-top:1.2rem;font-size:.78rem;color:#64748b;text-align:center;line-height:1.4}"
            "</style></head><body><div class='card'>"
            "<div style='text-align:center;font-size:2.2rem'>🤖</div>"
            "<div class='title'>VN-MateAI Robot Setup</div>"
            "<div class='sub'>Cấu hình kết nối WiFi cho Robot</div>"
            "<div class='code-box'>");
        html += cfg_pairing_code;
        html += F(
            "</div>"
            "<div style='text-align:center;font-size:.78rem;color:#94a3b8;margin-bottom:1rem'>"
            "Mã ghép đôi Robot — Dùng trên Web Dashboard</div>"
            "<form action='/save' method='POST' id='setupForm'>"
            "<label>Chọn mạng WiFi xung quanh</label>"
            "<div id='selectBox'>"
            "<select id='wifiList' name='ssid_select' onchange='onSelectWifi(this)'>");

        int n = WiFi.scanComplete();
        if (n <= 0) n = WiFi.scanNetworks(false, false);
        if (n > 0) {
            html += F("<option value=''>-- Bấm để chọn WiFi --</option>");
            for (int i = 0; i < n; ++i) {
                String s = WiFi.SSID(i);
                if (s.length() == 0) continue;
                int r = WiFi.RSSI(i);
                bool enc = (WiFi.encryptionType(i) != WIFI_AUTH_OPEN);
                html += "<option value='" + s + "'>" + s + " (" + String(r) + " dBm" + (enc ? " 🔒" : "") + ")</option>";
            }
        } else {
            html += F("<option value=''>Không tìm thấy WiFi - Bấm Quét lại</option>");
        }

        html += F(
            "</select></div>"
            "<div class='row-btn'>"
            "<button type='button' class='btn-scan' onclick='scanWifi()' id='scanBtn'>🔄 Quét lại WiFi</button>"
            "</div>"
            "<div id='manualBox' style='display:none;margin-top:.6rem'>"
            "<label>Tên WiFi thủ công / Ẩn (SSID)</label>"
            "<input type='text' id='manualSsid' placeholder='Nhập tên WiFi thủ công'>"
            "</div>"
            "<label class='checkbox-wrap'>"
            "<input type='checkbox' id='chkManual' onchange='toggleManual(this.checked)'>"
            "<span>Nhập WiFi ẩn / thủ công</span>"
            "</label>"
            "<input type='hidden' name='ssid' id='finalSsid' required>"
            "<label>Mật khẩu WiFi</label>"
            "<input name='pass' id='passInput' type='password' placeholder='Mật khẩu WiFi'>"
            "<label class='checkbox-wrap'>"
            "<input type='checkbox' onchange='document.getElementById(\"passInput\").type=this.checked?\"text\":\"password\"'>"
            "<span>Hiện mật khẩu</span>"
            "</label>"
            "<label>Device Token (để trống nếu dùng LAN)</label>"
            "<input name='token' placeholder='optional'>"
            "<button type='submit' class='btn-submit' onclick='return prepareSubmit()'>💾 Lưu &amp; Kết nối Robot</button>"
            "</form>"
            "<p class='note'>Robot tự tìm và kết nối Master Server qua mã 6 số.<br>Không cần gõ địa chỉ IP.</p>"
            "</div>"
            "<script>"
            "function onSelectWifi(sel){"
            "  var val = sel.value;"
            "  document.getElementById('finalSsid').value = val;"
            "}"
            "function toggleManual(chk){"
            "  document.getElementById('manualBox').style.display = chk ? 'block' : 'none';"
            "  document.getElementById('selectBox').style.display = chk ? 'none' : 'block';"
            "  if(chk){ document.getElementById('manualSsid').focus(); }"
            "  else { onSelectWifi(document.getElementById('wifiList')); }"
            "}"
            "function scanWifi(){"
            "  var btn = document.getElementById('scanBtn');"
            "  btn.innerHTML = '⏳ Đang quét...'; btn.disabled = true;"
            "  fetch('/scan').then(function(r){return r.json();}).then(function(list){"
            "    var sel = document.getElementById('wifiList');"
            "    sel.innerHTML = '<option value=\"\">-- Bấm để chọn WiFi --</option>';"
            "    list.forEach(function(item){"
            "      var opt = document.createElement('option');"
            "      opt.value = item.ssid;"
            "      opt.text = item.ssid + ' (' + item.rssi + ' dBm' + (item.enc ? ' 🔒' : '') + ')';"
            "      sel.appendChild(opt);"
            "    });"
            "    btn.innerHTML = '🔄 Quét lại WiFi'; btn.disabled = false;"
            "  }).catch(function(){"
            "    btn.innerHTML = '❌ Lỗi quét - Thử lại'; btn.disabled = false;"
            "  });"
            "}"
            "function prepareSubmit(){"
            "  var isMan = document.getElementById('chkManual').checked;"
            "  var ssid = isMan ? document.getElementById('manualSsid').value.trim() : document.getElementById('wifiList').value.trim();"
            "  if(!ssid){ alert('Vui lòng chọn hoặc nhập tên WiFi!'); return false; }"
            "  document.getElementById('finalSsid').value = ssid;"
            "  return true;"
            "}"
            "</script>"
            "</body></html>");
        server.send(200, "text/html; charset=utf-8", html);
    });

    server.on("/save", HTTP_POST, [&]() {
        String newSsid = server.arg("ssid");
        newSsid.trim();
        if (newSsid.length() > 0) {
            prefs.begin("vnmate", false);
            prefs.putString("ssid",  newSsid);
            prefs.putString("pass",  server.arg("pass"));
            prefs.putString("token", server.arg("token"));
            prefs.end();
            server.send(200, "text/html; charset=utf-8",
                "<!DOCTYPE html><html><head><meta charset='UTF-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
                "<style>body{background:#090d16;color:#e6edf3;font-family:sans-serif;display:flex;"
                "align-items:center;justify-content:center;height:100vh;margin:0;text-align:center}</style></head>"
                "<body><div><div style='font-size:3.5rem'>&#x2705;</div>"
                "<h2 style='color:#38bdf8;margin:1rem 0 .5rem'>Đã lưu cấu hình WiFi!</h2>"
                "<p style='color:#94a3b8;line-height:1.6'>Đang kết nối vào <b>" + newSsid + "</b>...<br>"
                "Robot đang khởi động lại.<br>Nhập mã <b><span style='color:#00f2fe;font-size:1.4rem;letter-spacing:2px'>" + cfg_pairing_code + "</span></b> vào Web Dashboard.</p>"
                "</div></body></html>");
            saved = true;
        } else {
            server.send(400, "text/plain; charset=utf-8", "Thiếu tên WiFi.");
        }
    });

    // Captive portal redirects
    server.on("/hotspot-detect.html", HTTP_GET, [&]() { server.sendHeader("Location", "http://192.168.4.1/", true); server.send(302, "text/plain", ""); });
    server.on("/generate_204", HTTP_GET, [&]() { server.sendHeader("Location", "http://192.168.4.1/", true); server.send(302, "text/plain", ""); });
    server.on("/canonical.html", HTTP_GET, [&]() { server.sendHeader("Location", "http://192.168.4.1/", true); server.send(302, "text/plain", ""); });
    server.onNotFound([&]() {
        server.sendHeader("Location", "http://192.168.4.1/", true);
        server.send(302, "text/plain", "");
    });

    server.begin();
    while (!saved) {
        dnsServer.processNextRequest();
        server.handleClient();
        delay(5);
    }
    server.stop();
    dnsServer.stop();
    WiFi.softAPdisconnect(true);
    delay(500);

    display.clearDisplay(); display.setCursor(10, 25);
    display.println(F("Saved! Restarting...")); display.display();
    delay(1500); ESP.restart();
    return true;
}

void discoverServerViaUdp() {
    WiFiUDP udp;
    if (udp.begin(8889)) {
        udp.beginPacket(IPAddress(255,255,255,255), 8888);
        udp.print("VNMATE_DISCOVER");
        udp.endPacket();
        unsigned long start = millis();
        while (millis() - start < 1500) {
            int packetSize = udp.parsePacket();
            if (packetSize) {
                char buf[64];
                int len = udp.read(buf, sizeof(buf)-1);
                if (len > 0) {
                    buf[len] = 0;
                    String resp = String(buf);
                    if (resp.startsWith("VNMATE_BEACON:")) {
                        int idx1 = resp.indexOf(':', 14);
                        if (idx1 > 0) {
                            String newHost = resp.substring(14, idx1);
                            int newPort = resp.substring(idx1 + 1).toInt();
                            cfg_server_host = newHost;
                            if (newPort > 0) cfg_server_port = newPort;
                            Serial.printf("[UDP Discovery] Tìm thấy Server tại %s:%d!\n", cfg_server_host.c_str(), cfg_server_port);
                            prefs.begin("vnmate", false);
                            prefs.putString("host", cfg_server_host);
                            prefs.putUShort("port", cfg_server_port);
                            prefs.end();
                            break;
                        }
                    }
                }
            }
            delay(20);
        }
        udp.stop();
    }
}

// ─── WiFi ────────────────────────────────────────────────────────────────────

void setupWiFi() {
    if (cfg_ssid.length() == 0) {
        startProvisioningPortal(); return;
    }
    Serial.printf("[WiFi] Kết nối '%s'...\n", cfg_ssid.c_str());
    display.clearDisplay();
    display.setCursor(5, 10); display.println(F("Connecting WiFi..."));
    display.setCursor(5, 22); display.println(cfg_ssid); display.display();

    WiFi.mode(WIFI_STA);
    WiFi.begin(cfg_ssid.c_str(), cfg_pass.c_str());

    uint8_t retries = 0;
    while (WiFi.status() != WL_CONNECTED && retries < 40) { delay(400); Serial.print("."); retries++; }

    if (WiFi.status() == WL_CONNECTED) {
        Serial.printf("\n[WiFi] OK! IP: %s\n", WiFi.localIP().toString().c_str());
        discoverServerViaUdp();
        // Hiển thị mã pairing ngay sau khi WiFi kết nối thành công
        drawOledPairingCode(cfg_pairing_code);
        delay(2500);
    } else {
        Serial.printf("\n[WiFi] Lỗi kết nối '%s'\n", cfg_ssid.c_str());
        prefs.begin("vnmate", false); prefs.remove("ssid"); prefs.end();
        startProvisioningPortal();
    }
}

void setupWebSocket() {
    Serial.printf("[WebSocket] → %s:%d%s\n", cfg_server_host.c_str(), cfg_server_port, DEFAULT_WS_PATH);
    // Gắn id thiết bị vào đường dẫn: máy chủ ràng buộc token riêng với ĐÚNG id này.
    String wsPath = String(DEFAULT_WS_PATH) + "/" + DEFAULT_DEVICE_ID;
    if (cfg_device_token.length() > 0) {
        wsPath += (wsPath.indexOf('?') >= 0) ? "&token=" : "?token=";
        wsPath += cfg_device_token;
    }
    if (cfg_server_port == 443) {
        webSocket.beginSSL(cfg_server_host.c_str(), cfg_server_port, wsPath.c_str());
    } else {
        webSocket.begin(cfg_server_host.c_str(), cfg_server_port, wsPath.c_str());
    }
    webSocket.onEvent(webSocketEvent);
    webSocket.setReconnectInterval(2500);
}

// ─── WebSocket Event Handler ─────────────────────────────────────────────────

void webSocketEvent(WStype_t type, uint8_t * payload, size_t length) {
    switch (type) {
        case WStype_DISCONNECTED:
            isConnectedToServer = false; isPaired = false;
            MotionCore::getInstance().updateConnectionStatus(false);
            Serial.println(F("[WS] Mất kết nối!"));
            digitalWrite(LED1_PIN, LOW); digitalWrite(LED2_PIN, LOW);
            drawOledPairingCode(cfg_pairing_code);  // Vẫn hiển thị mã
            break;

        case WStype_CONNECTED:
            isConnectedToServer = true;
            MotionCore::getInstance().updateConnectionStatus(true);
            Serial.printf("[WS] Đã kết nối: %s\n", payload);
            {
                // Gửi hello kèm pairing_code — server sẽ map mã với device_id
                StaticJsonDocument<256> doc;
                doc["type"]         = "hello";
                doc["device_id"]    = DEFAULT_DEVICE_ID;
                doc["version"]      = FIRMWARE_VERSION;
                doc["pairing_code"] = cfg_pairing_code.c_str();
                doc["features"]     = "motor_l298n,tof_safety,servo_kinematics,oled_lipsync,touch_wake,i2s_audio,pairing_code,volume_ctrl,reboot,status_report,natural_behavior,rgb_led";
                String hs; serializeJson(doc, hs);
                webSocket.sendTXT(hs);
            }
            sendStatusReport("connected");
            break;

        case WStype_TEXT:
            handleIncomingJson((const char*)payload);
            break;

        case WStype_BIN:
            if (length > 0) {
                setConvState(CONV_SPEAKING);
                playPcmToSpeaker(payload, length);
            }
            break;

        default: break;
    }
}

// ─── JSON Handler ─────────────────────────────────────────────────────────────

void handleIncomingJson(const char* jsonStr) {
    StaticJsonDocument<512> doc;
    if (deserializeJson(doc, jsonStr) != DeserializationError::Ok) return;

    String type   = doc["type"]   | "";
    String action = doc["action"] | "";
    String state  = doc["state"]  | "";

    // ── Hello ACK: Server xác nhận pairing_code ──────────────────────────────
    if (type == "hello" || type == "hello_ack") {
        const char* confirmedCode = doc["pairing_code"];
        if (confirmedCode && strlen(confirmedCode) == 6) {
            cfg_pairing_code = String(confirmedCode);
            prefs.begin("vnmate", false);
            prefs.putString("pcode", cfg_pairing_code);
            prefs.end();
        }
        isPaired = true;
        Serial.printf("[PairingCode] Ghép cặp OK! Mã: %s\n", cfg_pairing_code.c_str());
        drawOledPairingCode(cfg_pairing_code);  // Hiển thị mã to để user đọc
        delay(4000);
        setConvState(CONV_IDLE);
        return;
    }

    // ── v55: cấu hình cử động tự nhiên từ máy chủ ────────────────────────────
    if (type == "behavior") {
        applyBehaviorFrame(doc);
        return;
    }

    // ── v54: điều khiển từ Portal ───────────────────────────────────────────
    if (type == "set_volume") {
        int lvl = doc["level"] | -1;
        if (lvl >= 0 && lvl <= 100) {
            speakerVolume = (uint8_t)lvl;
            prefs.begin("vnmate", false);
            prefs.putUChar("vol", speakerVolume);
            prefs.end();
        }
        sendStatusReport("set_volume");
        return;
    }
    if (type == "get_status") {
        sendStatusReport("requested");
        return;
    }
    if (type == "reboot") {
        sendStatusReport("rebooting");
        delay(300);
        ESP.restart();
        return;
    }

    // ── UI State frames ───────────────────────────────────────────────────────
    if (type == "ui" || state.length() > 0) {
        if (state.length() > 0) currentUiState = state;
        currentScreenText = doc["text"] | "";

        if (state == "listening") {
            uint32_t t = doc["listen_timeout_ms"] | 0;
            listenTimeoutMs = t ? t : LISTEN_SILENCE_TIMEOUT_MS;
            setConvState(CONV_LISTENING);
        }
        else if (state == "thinking" ||
                 state == "processing")       setConvState(CONV_THINKING);
        else if (state == "speaking")         setConvState(CONV_SPEAKING);
        else if (state == "idle")             setConvState(CONV_IDLE);
        else if (state == "alert") {
            setConvState(CONV_IDLE);
            Face::setMode(Face::MODE_ALERT);
        }
        // Biểu cảm theo nội dung câu trả lời (máy chủ chọn): vui, buồn, khóc, wow, …
        // ("idle" + "sleeping" là khung nghỉ mặc định — gương mặt tự buồn ngủ khi lâu không ai nói.)
        String emotion = doc["emotion"] | "";
        uint32_t holdMs = doc["hold_ms"] | 0;
        if (emotion.length() > 0 && !(state == "idle" && emotion == "sleeping"))
            Face::setEmotion(Face::parseEmotion(emotion), holdMs);
        if (emotion.length() > 0) ledMood = moodFromEmotion(emotion);
    }

    // ── TTS Start / Stop ──────────────────────────────────────────────────────
    if (type == "tts_start" ||
        (type == "tts" && (doc["state"] | String("")) == "start")) {
        setConvState(CONV_SPEAKING);
    } else if (type == "tts_end" || type == "tts_stop" || type == "end_of_speech" ||
               (type == "tts" && (doc["state"] | String("")) == "stop")) {
        setConvState(CONV_IDLE);
    }

    // ── ASR / LLM feedback ───────────────────────────────────────────────────
    if (type == "asr_start" || type == "llm_start") {
        if (convState != CONV_THINKING) setConvState(CONV_THINKING);
    }
    if (type == "asr_result") {
        const char* txt = doc["text"];
        if (!txt || strlen(txt) == 0) setConvState(CONV_IDLE);
        // Nếu có text → giữ THINKING, chờ LLM pipeline
    }

    // ── Lệnh cử chỉ ──────────────────────────────────────────────────────────
    if (action == "animate" || (type == "cmd" && action == "animate")) {
        // v55: chạy trong task cử động (không chặn vòng loop -> âm thanh không giật).
        String anim = doc["anim"] | "";
        if      (anim == "wave_hand")   queueGesture(G_WAVE);
        else if (anim == "nod_head")    queueGesture(G_NOD);
        else if (anim == "look_around") queueGesture(G_LOOK);
        else if (anim == "excited")     queueGesture(G_EXCITED);
        else if (anim == "sad")         queueGesture(G_SAD);
    }

    // ── Lệnh di chuyển bánh xe ────────────────────────────────────────────────
    if (action == "move" || (type == "cmd" && action == "move")) {
        String dir = doc["dir"] | "stop";
        uint32_t duration = doc["time"] | 1000;
        if (!MotionCore::getInstance().moveRobot(dir, duration)) {
            StaticJsonDocument<256> resp;
            resp["type"]="response"; resp["status"]="rejected"; resp["reason"]="cliff_detected";
            String out; serializeJson(resp, out); webSocket.sendTXT(out);
        }
    }
}

// ─── Safety Alert ─────────────────────────────────────────────────────────────

void onSafetyAlert(const char* alertMsg) {
    if (strcmp(alertMsg, "edge_detected") == 0) {
        Serial.println(F("[SAFETY] Cảnh báo mép bàn!"));
        Face::setMode(Face::MODE_ALERT);
        if (isConnectedToServer) {
            StaticJsonDocument<256> alertDoc;
            alertDoc["type"]="alert"; alertDoc["msg"]="edge_detected";
            alertDoc["device_id"]=DEFAULT_DEVICE_ID; alertDoc["timestamp"]=(uint32_t)millis();
            String j; serializeJson(alertDoc, j); webSocket.sendTXT(j);
        }
    }
}

// ─── Touch Sensor (TTP223 GPIO 17) ───────────────────────────────────────────

void checkTouchSensor() {
    int touchVal = digitalRead(TOUCH_PIN);
    unsigned long now = millis();
    if (touchVal == HIGH) {
        delay(60);
        if (digitalRead(TOUCH_PIN) == HIGH && !touchActive && (now - lastTouchTime > 4000)) {
            touchActive = true; lastTouchTime = now;
            Serial.println(F("[Touch] Chạm tay → LISTENING!"));
            listenTimeoutMs = LISTEN_SILENCE_TIMEOUT_MS;
            setConvState(CONV_LISTENING);
            if (isConnectedToServer) {
                StaticJsonDocument<256> td;
                td["type"]="touch"; td["action"]="wake"; td["device_id"]=DEFAULT_DEVICE_ID;
                String out; serializeJson(td, out); webSocket.sendTXT(out);
            }
        }
    } else { touchActive = false; }
}

// ─── Animations ──────────────────────────────────────────────────────────────

// ─── OLED Renderers ──────────────────────────────────────────────────────────

// Hiển thị mã pairing to và rõ để user đọc nhập vào Web Dashboard
void drawOledPairingCode(const String& code) {
    faceOverlayUntil = millis() + 8000;
    display.clearDisplay();
    display.setTextColor(SSD1306_WHITE);
    display.setTextSize(1);
    display.setCursor(8, 0);
    display.println(F("VN-MateAI  Ma cap:"));
    display.drawFastHLine(0, 9, 128, SSD1306_WHITE);

    // Mã 6 số to, căn giữa 128px
    display.setTextSize(3);  // ~18px/char wide
    int16_t codeW = code.length() * 18;
    int16_t codeX = (128 - codeW) / 2;
    display.setCursor(max((int16_t)2, codeX), 16);
    display.print(code);

    display.setTextSize(1);
    display.drawFastHLine(0, 44, 128, SSD1306_WHITE);
    display.setCursor(4, 48);
    display.println(F("Nhap ma vao Web UI"));
    display.display();
}
