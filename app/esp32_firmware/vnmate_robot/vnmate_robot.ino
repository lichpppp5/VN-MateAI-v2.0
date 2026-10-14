/**
 * esp32_firmware/vnmate_robot/vnmate_robot.ino
 * ============================================
 * Phase 52: Full Autonomous Robotics (Body-Mind Sync).
 * Sơ đồ chân chuẩn xác theo KST AI ROBOT (KenhSangTao.COM) ESP32-S3 YD Board N16R8.
 */

#include <Arduino.h>
#include <WiFi.h>
#include <WebSocketsClient.h>
#include <ArduinoJson.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <driver/i2s.h>
#include <math.h>

#include "config.h"
#include "motion_core.h"

// ─── Global Instances ────────────────────────────────────────────────────────
WebSocketsClient webSocket;
Adafruit_SSD1306 display(128, 64, &Wire, -1);

// State variables
volatile bool isConnectedToServer = false;
volatile bool isListening = true;
volatile bool isSpeaking = false;
String currentUiState = "idle";
String currentEmotion = "sleeping";
String currentScreenText = "";

// Lip-sync & animation state
uint8_t mouthOpenHeight = 2;
unsigned long lastLipSyncUpdate = 0;

// Touch sensor debounce
unsigned long lastTouchTime = 0;
bool touchActive = false;

// Thread-safe Audio Buffer Struct & FreeRTOS Queue
struct PcmChunk {
    int16_t samples[256];
    size_t len;
};
QueueHandle_t micQueue = nullptr;
TaskHandle_t micTaskHandle = nullptr;

// ─── Function Declarations ───────────────────────────────────────────────────
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
void drawOledFace(const String& state, const String& emotion, uint8_t mouthHeight);
void updateLipSyncAnimation();
void checkTouchSensor();

// ─── Setup ───────────────────────────────────────────────────────────────────
void setup() {
    Serial.begin(115200);
    delay(500);
    Serial.println(F("\n======================================================="));
    Serial.println(F("   VN-MATE AI // ROBOTICS COMPANION FIRMWARE v52.0    "));
    Serial.println(F("      Full Autonomous Robotics (Body-Mind Sync)        "));
    Serial.println(F("======================================================="));

    setupOLED();
    setupTouchAndLEDs();
    setupI2S();

    MotionCore::getInstance().setAlertCallback(onSafetyAlert);
    MotionCore::getInstance().init();

    setupWiFi();
    setupWebSocket();

    drawOledFace("listening", "happy", 6);
    MotionCore::getInstance().waveArm();
    drawOledFace("idle", "sleeping", 2);
}

// ─── Main Loop ───────────────────────────────────────────────────────────────
void loop() {
    webSocket.loop();

    if (isConnectedToServer && isListening && !isSpeaking && micQueue != nullptr) {
        PcmChunk chunk;
        if (xQueueReceive(micQueue, &chunk, 0) == pdTRUE) {
            webSocket.sendBIN(reinterpret_cast<uint8_t*>(chunk.samples), chunk.len);
        }
    }

    checkTouchSensor();
    updateLipSyncAnimation();
    delay(2);
}

// ─── Hardware Initializations: I2S Audio ─────────────────────────────────────

void setupI2S() {
    Serial.println(F("[Audio] Cấu hình I2S cho Micro INMP441 và Loa MAX98357A..."));

    micQueue = xQueueCreate(6, sizeof(PcmChunk));

    i2s_config_t mic_config = {
        .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX),
        .sample_rate = 16000,
        .bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT,
        .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
        .communication_format = I2S_COMM_FORMAT_STAND_I2S,
        .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
        .dma_buf_count = 8,
        .dma_buf_len = 256,
        .use_apll = false,
        .tx_desc_auto_clear = false,
        .fixed_mclk = 0
    };
    i2s_pin_config_t mic_pins = {
        .bck_io_num = I2S_MIC_SCK,    // GPIO 5
        .ws_io_num = I2S_MIC_WS,      // GPIO 4
        .data_out_num = I2S_PIN_NO_CHANGE,
        .data_in_num = I2S_MIC_SD     // GPIO 6
    };
    esp_err_t err_mic = i2s_driver_install(I2S_NUM_0, &mic_config, 0, NULL);
    if (err_mic == ESP_OK) {
        i2s_set_pin(I2S_NUM_0, &mic_pins);
        Serial.println(F("[Audio] Micro INMP441 (I2S_NUM_0) đã sẵn sàng."));
    } else {
        Serial.printf("[Audio] Lỗi khởi tạo Micro I2S: %d\n", err_mic);
    }

    i2s_config_t spk_config = {
        .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_TX),
        .sample_rate = 16000,
        .bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT,
        .channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT,
        .communication_format = I2S_COMM_FORMAT_STAND_I2S,
        .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
        .dma_buf_count = 8,
        .dma_buf_len = 512,
        .use_apll = false,
        .tx_desc_auto_clear = true,
        .fixed_mclk = 0
    };
    i2s_pin_config_t spk_pins = {
        .bck_io_num = I2S_SPK_BCLK,   // GPIO 15
        .ws_io_num = I2S_SPK_LRC,     // GPIO 16
        .data_out_num = I2S_SPK_DIN,  // GPIO 7
        .data_in_num = I2S_PIN_NO_CHANGE
    };
    esp_err_t err_spk = i2s_driver_install(I2S_NUM_1, &spk_config, 0, NULL);
    if (err_spk == ESP_OK) {
        i2s_set_pin(I2S_NUM_1, &spk_pins);
        Serial.println(F("[Audio] Loa MAX98357A (I2S_NUM_1) đã sẵn sàng."));
    } else {
        Serial.printf("[Audio] Lỗi khởi tạo Loa I2S: %d\n", err_spk);
    }

    playStartupChime();

    xTaskCreatePinnedToCore(
        micRecordTaskLoop,
        "MicRecordTask",
        4096,
        NULL,
        2,
        &micTaskHandle,
        0
    );
}

void playStartupChime() {
    Serial.println(F("[Audio] Phát âm thanh khởi động (Startup Chime) ra loa MAX98357A..."));
    int16_t stereo[256];
    size_t written = 0;

    for (int k = 0; k < 8; ++k) {
        for (int i = 0; i < 128; ++i) {
            float t = (float)(k * 128 + i) / 16000.0;
            int16_t val = (int16_t)(sin(2.0 * PI * 523.0 * t) * 8000.0);
            stereo[i * 2]     = val;
            stereo[i * 2 + 1] = val;
        }
        i2s_write(I2S_NUM_1, stereo, sizeof(stereo), &written, portMAX_DELAY);
    }

    for (int k = 0; k < 12; ++k) {
        for (int i = 0; i < 128; ++i) {
            float t = (float)(k * 128 + i) / 16000.0;
            int16_t val = (int16_t)(sin(2.0 * PI * 659.0 * t) * 9000.0);
            stereo[i * 2]     = val;
            stereo[i * 2 + 1] = val;
        }
        i2s_write(I2S_NUM_1, stereo, sizeof(stereo), &written, portMAX_DELAY);
    }
}

void playPcmToSpeaker(const uint8_t* pcmData, size_t length) {
    if (!pcmData || length == 0) return;

    const int16_t* monoSamples = reinterpret_cast<const int16_t*>(pcmData);
    size_t sampleCount = length / 2;

    int16_t stereoBuf[128 * 2];
    size_t offset = 0;
    while (offset < sampleCount) {
        size_t batch = min((size_t)128, sampleCount - offset);
        for (size_t i = 0; i < batch; ++i) {
            int16_t s = monoSamples[offset + i];
            stereoBuf[i * 2]     = s;
            stereoBuf[i * 2 + 1] = s;
        }
        size_t bytesWritten = 0;
        i2s_write(I2S_NUM_1, stereoBuf, batch * 4, &bytesWritten, portMAX_DELAY);
        offset += batch;
    }
}

void micRecordTaskLoop(void* arg) {
    const size_t SAMPLES = 256;
    int32_t raw32[SAMPLES];
    PcmChunk chunk;
    size_t bytesRead = 0;

    for (;;) {
        if (isConnectedToServer && isListening && !isSpeaking) {
            esp_err_t res = i2s_read(I2S_NUM_0, raw32, sizeof(raw32), &bytesRead, pdMS_TO_TICKS(50));
            if (res == ESP_OK && bytesRead > 0) {
                size_t numSamples = bytesRead / 4;
                for (size_t i = 0; i < numSamples; ++i) {
                    chunk.samples[i] = (int16_t)(raw32[i] >> 14);
                }
                chunk.len = numSamples * 2;
                if (micQueue != nullptr) {
                    xQueueSend(micQueue, &chunk, 0);
                }
            }
        } else {
            vTaskDelay(pdMS_TO_TICKS(20));
        }
        vTaskDelay(pdMS_TO_TICKS(5));
    }
}

void setupOLED() {
    Wire.begin(OLED_SDA, OLED_SCL, 400000);
    if (!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
        Serial.println(F("[OLED] Không tìm thấy màn hình SSD1306 tại 0x3C. Kiểm tra dây!"));
    } else {
        Serial.println(F("[OLED] Màn hình SSD1306 128x64 đã khởi tạo thành công."));
        display.clearDisplay();
        display.setTextColor(SSD1306_WHITE);
        display.setTextSize(1);
        display.setCursor(10, 25);
        display.println(F("VN-MateAI Robot 52"));
        display.display();
    }
}

void setupTouchAndLEDs() {
    pinMode(TOUCH_PIN, INPUT_PULLDOWN);
    pinMode(LED1_PIN, OUTPUT);
    pinMode(LED2_PIN, OUTPUT);
    pinMode(RGB_PIN, OUTPUT);

    digitalWrite(LED1_PIN, LOW);
    digitalWrite(LED2_PIN, LOW);
}

void setupWiFi() {
    Serial.printf("[WiFi] Bắt đầu kết nối mạng WiFi...\n");
    display.clearDisplay();
    display.setCursor(10, 25);
    display.println(F("Scanning WiFi..."));
    display.display();

    WiFi.mode(WIFI_STA);
    WiFi.disconnect();
    delay(200);

    Serial.println(F("[WiFi] Đang quét các mạng 2.4GHz khả dụng..."));
    int numNetworks = WiFi.scanNetworks();
    Serial.printf("[WiFi] Quét hoàn tất. Tìm thấy %d mạng xung quanh:\n", numNetworks);

    String targetSsid = DEFAULT_WIFI_SSID;
    for (int i = 0; i < numNetworks; ++i) {
        String scannedSsid = WiFi.SSID(i);
        int32_t rssi = WiFi.RSSI(i);
        Serial.printf("  [%02d] SSID: '%s' | Tín hiệu: %d dBm | Kênh: %d\n", i + 1, scannedSsid.c_str(), rssi, WiFi.channel(i));

        String s1 = scannedSsid; s1.toLowerCase(); s1.replace(" ", "");
        String s2 = targetSsid; s2.toLowerCase(); s2.replace(" ", "");
        if (s1 == s2) {
            targetSsid = scannedSsid;
            Serial.printf("[WiFi] -> Tìm thấy mạng phù hợp: '%s'!\n", targetSsid.c_str());
        }
    }

    display.clearDisplay();
    display.setCursor(5, 25);
    display.printf("Connecting to:\n%s", targetSsid.c_str());
    display.display();

    Serial.printf("[WiFi] Đang kết nối tới '%s'...\n", targetSsid.c_str());
    WiFi.begin(targetSsid.c_str(), DEFAULT_WIFI_PASS);

    uint8_t retries = 0;
    while (WiFi.status() != WL_CONNECTED && retries < 40) {
        delay(400);
        Serial.print(".");
        retries++;
    }

    if (WiFi.status() == WL_CONNECTED) {
        Serial.printf("\n[WiFi] Đã kết nối thành công! IP: %s\n", WiFi.localIP().toString().c_str());
        display.clearDisplay();
        display.setCursor(5, 20);
        display.println(F("WiFi Connected!"));
        display.setCursor(5, 35);
        display.println(WiFi.localIP().toString());
        display.display();
        delay(1500);
    } else {
        Serial.printf("\n[WiFi] Kết nối tới '%s' thất bại (Trạng thái mã: %d).\n", targetSsid.c_str(), WiFi.status());
    }
}

void setupWebSocket() {
    Serial.printf("[WebSocket] Cấu hình máy chủ %s:%d%s\n", DEFAULT_SERVER_HOST, DEFAULT_SERVER_PORT, DEFAULT_WS_PATH);
    if (DEFAULT_SERVER_PORT == 443) {
        webSocket.beginSSL(DEFAULT_SERVER_HOST, DEFAULT_SERVER_PORT, DEFAULT_WS_PATH);
    } else {
        webSocket.begin(DEFAULT_SERVER_HOST, DEFAULT_SERVER_PORT, DEFAULT_WS_PATH);
    }

    webSocket.onEvent(webSocketEvent);
    webSocket.setReconnectInterval(2500);
}

void webSocketEvent(WStype_t type, uint8_t * payload, size_t length) {
    switch (type) {
        case WStype_DISCONNECTED:
            isConnectedToServer = false;
            MotionCore::getInstance().updateConnectionStatus(false);
            Serial.println(F("[WebSocket] Mất kết nối tới máy chủ!"));
            digitalWrite(LED1_PIN, LOW);
            digitalWrite(LED2_PIN, LOW);
            break;

        case WStype_CONNECTED:
            isConnectedToServer = true;
            MotionCore::getInstance().updateConnectionStatus(true);
            Serial.printf("[WebSocket] Đã kết nối thành công tới máy chủ VN-MateAI: %s\n", payload);

            {
                StaticJsonDocument<256> doc;
                doc["type"] = "hello";
                doc["device_id"] = DEFAULT_DEVICE_ID;
                doc["version"] = "52.0";
                doc["features"] = "motor_l298n,tof_safety,servo_kinematics,oled_lipsync,touch_wake,i2s_audio";
                String handshake;
                serializeJson(doc, handshake);
                webSocket.sendTXT(handshake);
            }
            break;

        case WStype_TEXT:
            handleIncomingJson((const char*)payload);
            break;

        case WStype_BIN:
            if (length > 0) {
                isSpeaking = true;
                playPcmToSpeaker(payload, length);
                mouthOpenHeight = (mouthOpenHeight == 2) ? 12 : ((mouthOpenHeight == 12) ? 6 : 2);
            }
            break;

        default:
            break;
    }
}

void handleIncomingJson(const char* jsonStr) {
    StaticJsonDocument<512> doc;
    DeserializationError error = deserializeJson(doc, jsonStr);
    if (error) {
        Serial.printf("[JSON] Parse lỗi: %s\n", error.c_str());
        return;
    }

    String type = doc["type"] | "";
    String action = doc["action"] | "";
    String state = doc["state"] | "";
    String emotion = doc["emotion"] | "";

    if (type == "ui" || state.length() > 0) {
        if (state.length() > 0) currentUiState = state;
        if (emotion.length() > 0) currentEmotion = emotion;
        currentScreenText = doc["text"] | "";

        Serial.printf("[Robot UI] State: '%s', Emotion: '%s'\n", currentUiState.c_str(), currentEmotion.c_str());

        if (currentUiState == "listening") {
            digitalWrite(LED1_PIN, HIGH);
            digitalWrite(LED2_PIN, HIGH);
            isListening = true;
            isSpeaking = false;
            drawOledFace("listening", "focused", 2);
        }
        else if (currentUiState == "speaking") {
            digitalWrite(LED1_PIN, HIGH);
            digitalWrite(LED2_PIN, LOW);
            isSpeaking = true;
        }
        else if (currentUiState == "idle") {
            digitalWrite(LED1_PIN, LOW);
            digitalWrite(LED2_PIN, LOW);
            isSpeaking = false;
            isListening = true;
            drawOledFace("idle", currentEmotion.length() > 0 ? currentEmotion : "sleeping", 2);
        }
        else if (currentUiState == "alert") {
            digitalWrite(LED1_PIN, HIGH);
            digitalWrite(LED2_PIN, HIGH);
            isSpeaking = false;
            drawOledFace("alert", "shocked", 14);
        }
    }

    if (type == "tts_start") {
        isSpeaking = true;
        currentUiState = "speaking";
        digitalWrite(LED1_PIN, HIGH);
    } else if (type == "tts_stop" || type == "tts_end" || type == "end_of_speech") {
        isSpeaking = false;
        mouthOpenHeight = 2;
        drawOledFace(currentUiState, currentEmotion, 2);
    }

    if (action == "animate" || type == "cmd" && action == "animate") {
        String anim = doc["anim"] | "";
        Serial.printf("[Robot Action] Nhận lệnh cử chỉ: '%s'\n", anim.c_str());

        if (anim == "wave_hand") {
            MotionCore::getInstance().waveArm();
        } else if (anim == "nod_head") {
            MotionCore::getInstance().nodNeck();
        } else if (anim == "look_around") {
            MotionCore::getInstance().lookAround();
        } else if (anim == "excited") {
            MotionCore::getInstance().excited();
        } else if (anim == "sad") {
            MotionCore::getInstance().sad();
        }
    }

    if (action == "move" || type == "cmd" && action == "move") {
        String dir = doc["dir"] | "stop";
        uint32_t duration = doc["time"] | 1000;
        Serial.printf("[Robot Action] Nhận lệnh di chuyển: hướng='%s', thời gian=%d ms\n", dir.c_str(), duration);

        bool success = MotionCore::getInstance().moveRobot(dir, duration);
        if (!success) {
            StaticJsonDocument<256> resp;
            resp["type"] = "response";
            resp["status"] = "rejected";
            resp["reason"] = "cliff_detected";
            String outStr;
            serializeJson(resp, outStr);
            webSocket.sendTXT(outStr);
        }
    }
}

void onSafetyAlert(const char* alertMsg) {
    if (strcmp(alertMsg, "edge_detected") == 0) {
        Serial.println(F("[SAFETY ALERT] Gửi cảnh báo mép bàn lên máy chủ VN-MateAI!"));
        drawOledFace("alert", "shocked", 14);

        if (isConnectedToServer) {
            StaticJsonDocument<256> alertDoc;
            alertDoc["type"] = "alert";
            alertDoc["msg"] = "edge_detected";
            alertDoc["device_id"] = DEFAULT_DEVICE_ID;
            alertDoc["timestamp"] = millis();
            String jsonOutput;
            serializeJson(alertDoc, jsonOutput);
            webSocket.sendTXT(jsonOutput);
        }
    }
}

void checkTouchSensor() {
    int touchVal = digitalRead(TOUCH_PIN);
    unsigned long now = millis();

    // Chống nhiễu (Debounce): Phải giữ mức HIGH liên tục ít nhất 60ms
    if (touchVal == HIGH) {
        delay(60);
        if (digitalRead(TOUCH_PIN) == HIGH && !touchActive && (now - lastTouchTime > 4000)) {
            touchActive = true;
            lastTouchTime = now;
            Serial.println(F("[Touch] Chạm tay vào Robot (GPIO 17)! Đánh thức hệ thống..."));

            digitalWrite(LED1_PIN, HIGH);
            digitalWrite(LED2_PIN, HIGH);
            isListening = true;
            drawOledFace("listening", "focused", 4);

            if (isConnectedToServer) {
                StaticJsonDocument<256> touchDoc;
                touchDoc["type"] = "touch";
                touchDoc["action"] = "wake";
                touchDoc["device_id"] = DEFAULT_DEVICE_ID;
                String out;
                serializeJson(touchDoc, out);
                webSocket.sendTXT(out);
            }
        }
    } else {
        touchActive = false;
    }
}

void updateLipSyncAnimation() {
    if (!isSpeaking) return;

    unsigned long now = millis();
    if (now - lastLipSyncUpdate >= 120) {
        lastLipSyncUpdate = now;
        mouthOpenHeight = random(4, 15);
        drawOledFace("speaking", "happy", mouthOpenHeight);
    }
}

void drawOledFace(const String& state, const String& emotion, uint8_t mouthHeight) {
    display.clearDisplay();

    if (emotion == "sleeping" || state == "idle" && !isSpeaking) {
        display.drawCircle(40, 26, 12, SSD1306_WHITE);
        display.fillRect(28, 26, 26, 14, SSD1306_BLACK);
        
        display.drawCircle(88, 26, 12, SSD1306_WHITE);
        display.fillRect(76, 26, 26, 14, SSD1306_BLACK);

        display.drawPixel(64, 48, SSD1306_WHITE);
        display.drawFastHLine(61, 49, 7, SSD1306_WHITE);
    }
    else if (state == "listening" || emotion == "focused") {
        display.fillCircle(40, 24, 14, SSD1306_WHITE);
        display.fillCircle(43, 22, 4, SSD1306_BLACK);
        
        display.fillCircle(88, 24, 14, SSD1306_WHITE);
        display.fillCircle(91, 22, 4, SSD1306_BLACK);

        display.drawFastHLine(58, 48, 12, SSD1306_WHITE);
    }
    else if (state == "alert" || emotion == "shocked") {
        display.drawCircle(38, 22, 16, SSD1306_WHITE);
        display.fillCircle(38, 22, 7, SSD1306_WHITE);
        
        display.drawCircle(90, 22, 16, SSD1306_WHITE);
        display.fillCircle(90, 22, 7, SSD1306_WHITE);

        display.fillCircle(64, 48, 8, SSD1306_WHITE);
        display.fillCircle(64, 48, 6, SSD1306_BLACK);

        display.setTextSize(1);
        display.setCursor(35, 56);
        display.print(F("MEP BAN!"));
    }
    else {
        display.fillCircle(40, 24, 13, SSD1306_WHITE);
        display.fillRect(27, 24, 28, 15, SSD1306_BLACK);
        display.drawFastHLine(30, 24, 20, SSD1306_WHITE);

        display.fillCircle(88, 24, 13, SSD1306_WHITE);
        display.fillRect(75, 24, 28, 15, SSD1306_BLACK);
        display.drawFastHLine(78, 24, 20, SSD1306_WHITE);

        uint8_t h = max((uint8_t)2, min((uint8_t)16, mouthHeight));
        display.fillRoundRect(56, 46 - (h / 2), 16, h, 3, SSD1306_WHITE);
    }

    display.display();
}
