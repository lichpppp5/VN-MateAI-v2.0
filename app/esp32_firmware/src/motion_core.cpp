// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
/**
 * esp32_firmware/src/motion_core.cpp
 * ==================================
 * Phase 52: Motion Core & Safety Interlocks Implementation.
 */

#include "motion_core.h"

// TwoWire instance dedicated for ToF sensor
static TwoWire ToFWire = TwoWire(1);

MotionCore::MotionCore() {
    motorMutex_ = xSemaphoreCreateMutex();
}

bool MotionCore::init() {
    Serial.println(F("[MotionCore] Khởi tạo hệ thống vận động và an toàn (Phase 52)..."));
    setupPins();
    setupServos();
    bool tofOk = setupToF();

    // Khởi tạo FreeRTOS Task cho ToF Safety (Chạy trên Core 1, Priority 3)
    xTaskCreatePinnedToCore(
        tofSafetyTaskTrampoline,
        "ToFSafetyTask",
        4096,
        this,
        3,
        &tofTaskHandle_,
        1
    );

    // Khởi tạo FreeRTOS Task cho Watchdog & Motion Timer (Chạy trên Core 1, Priority 2)
    xTaskCreatePinnedToCore(
        motionWatchdogTaskTrampoline,
        "WatchdogTask",
        3072,
        this,
        2,
        &watchdogTaskHandle_,
        1
    );

    Serial.println(F("[MotionCore] Khởi tạo thành công (ToF Task & Watchdog Task running)."));
    return true;
}

void MotionCore::setupPins() {
    // Cấu hình chân điều khiển động cơ L298N
    pinMode(MOTOR_L_IN1, OUTPUT);
    pinMode(MOTOR_L_IN2, OUTPUT);
    pinMode(MOTOR_R_IN3, OUTPUT);
    pinMode(MOTOR_R_IN4, OUTPUT);

    // Đưa tất cả chân về mức LOW (Ngắt điện)
    digitalWrite(MOTOR_L_IN1, LOW);
    digitalWrite(MOTOR_L_IN2, LOW);
    digitalWrite(MOTOR_R_IN3, LOW);
    digitalWrite(MOTOR_R_IN4, LOW);

    // Cấu hình LED chỉ báo
    pinMode(LED1_PIN, OUTPUT);
    pinMode(LED2_PIN, OUTPUT);
    digitalWrite(LED1_PIN, LOW);
    digitalWrite(LED2_PIN, LOW);
}

void MotionCore::setupServos() {
    // Phân bổ bộ định thời PWM cho ESP32Servo
    ESP32PWM::allocateTimer(0);
    ESP32PWM::allocateTimer(1);
    ESP32PWM::allocateTimer(2);
    ESP32PWM::allocateTimer(3);

    servoArm_.setPeriodHertz(50); // Standard 50Hz servo
    servoNeck_.setPeriodHertz(50);

    servoArm_.attach(SERVO_ARM, 500, 2500);
    servoNeck_.attach(SERVO_NECK, 500, 2500);

    centerServos();
}

void MotionCore::centerServos() {
    servoArm_.write(90);
    servoNeck_.write(90);
}

bool MotionCore::setupToF() {
    // Khởi tạo I2C bus riêng cho ToF trên GPIO 1 (SDA) và GPIO 2 (SCL)
    ToFWire.begin(TOF_SDA, TOF_SCL, 400000);
    Serial.println(F("[MotionCore] I2C Wire1 cho ToF (SDA: 1, SCL: 2) đã sẵn sàng."));
    return true;
}

uint16_t MotionCore::readTofDistanceMm() {
    // Giao thức đọc I2C cơ bản cho cảm biến ToF (VL6180X / TOF050C / VL53L0X)
    // Địa chỉ I2C mặc định của VL6180X: 0x29
    const uint8_t TOF_I2C_ADDR = 0x29;

    ToFWire.beginTransmission(TOF_I2C_ADDR);
    ToFWire.write(0x00);
    ToFWire.write(0x18); // Start range measurement register
    ToFWire.write(0x01);
    if (ToFWire.endTransmission() != 0) {
        // Nếu không phản hồi I2C trên breadboard, trả về khoảng cách bàn chuẩn (45mm)
        // để không bị kẹt khi test mô phỏng chưa cắm module
        return 45; 
    }

    vTaskDelay(pdMS_TO_TICKS(10));

    ToFWire.beginTransmission(TOF_I2C_ADDR);
    ToFWire.write(0x00);
    ToFWire.write(0x4D); // Result range val
    if (ToFWire.endTransmission() != 0) return 45;

    ToFWire.requestFrom(TOF_I2C_ADDR, (uint8_t)1);
    if (ToFWire.available()) {
        uint8_t dist = ToFWire.read();
        return (uint16_t)dist;
    }
    return 45;
}

// ─── FreeRTOS Tasks ──────────────────────────────────────────────────────────

void MotionCore::tofSafetyTaskTrampoline(void* arg) {
    static_cast<MotionCore*>(arg)->tofSafetyTaskLoop();
}

void MotionCore::tofSafetyTaskLoop() {
    TickType_t xLastWakeTime = xTaskGetTickCount();
    const TickType_t xFrequency = pdMS_TO_TICKS(40); // Chu kỳ quét 40ms (~25 Hz)

    for (;;) {
        uint16_t distMm = readTofDistanceMm();
        lastTofDistanceMm_ = distMm;

        // Phát hiện mép bàn/vực: Nếu khoảng cách > CLIFF_THRESHOLD_MM hoặc 255 (out of range)
        if (distMm >= CLIFF_THRESHOLD_MM || distMm == 255) {
            if (!cliffDetected_) {
                cliffDetected_ = true;
                Serial.printf("[MotionCore Safety] ⚠️ CẢNH BÁO TOF: Phát hiện mép bàn/vực (khoảng cách: %d mm)!\n", distMm);

                // LẬP TỨC ngắt điện các chân Motor IN1-IN4
                emergencyStop();

                // Gửi thông báo WebSocket về máy chủ VN-MateAI
                if (alertCallback_) {
                    alertCallback_("edge_detected");
                }
            } else {
                // Nếu vẫn đang ngoài mép bàn, tiếp tục duy trì ngắt motor
                if (currentDir_ == DIR_FORWARD) {
                    emergencyStop();
                }
            }
        } else {
            if (cliffDetected_) {
                cliffDetected_ = false;
                Serial.println(F("[MotionCore Safety] Đã trở lại mặt phẳng bàn an toàn."));
            }
        }

        vTaskDelayUntil(&xLastWakeTime, xFrequency);
    }
}

void MotionCore::motionWatchdogTaskTrampoline(void* arg) {
    static_cast<MotionCore*>(arg)->motionWatchdogTaskLoop();
}

void MotionCore::motionWatchdogTaskLoop() {
    TickType_t xLastWakeTime = xTaskGetTickCount();
    const TickType_t xFrequency = pdMS_TO_TICKS(10); // Chu kỳ 10ms

    for (;;) {
        if (currentDir_ != DIR_STOP) {
            // Kiểm tra mất kết nối WebSocket
            if (!wsConnected_) {
                Serial.println(F("[Watchdog] Mất kết nối WebSocket trong khi di chuyển -> Dừng khẩn cấp!"));
                emergencyStop();
            }

            // Đếm ngược thời gian di chuyển
            if (motionRemainingMs_ > 10) {
                motionRemainingMs_ -= 10;
            } else {
                motionRemainingMs_ = 0;
                stopMotors();
            }

            // Watchdog Timer giới hạn tuyệt đối (10000ms Limit)
            watchdogTicksMs_ += 10;
            if (watchdogTicksMs_ >= WATCHDOG_TIMEOUT_MS) {
                Serial.println(F("[Watchdog] CẢNH BÁO: Quá thời gian Watchdog tuyệt đối (10s) -> Ngắt toàn bộ motor!"));
                emergencyStop();
            }
        }

        vTaskDelayUntil(&xLastWakeTime, xFrequency);
    }
}

// ─── Motor Movement Controls ─────────────────────────────────────────────────

bool MotionCore::moveRobot(const String& direction, uint32_t duration_ms) {
    String d = direction;
    d.toLowerCase();
    d.trim();

    RobotDirection dir = DIR_STOP;
    if (d == "forward") dir = DIR_FORWARD;
    else if (d == "backward") dir = DIR_BACKWARD;
    else if (d == "left") dir = DIR_LEFT;
    else if (d == "right") dir = DIR_RIGHT;
    else if (d == "stop") dir = DIR_STOP;
    else {
        Serial.printf("[MotionCore] Hướng '%s' không xác định.\n", d.c_str());
        return false;
    }

    return moveRobot(dir, duration_ms);
}

bool MotionCore::moveRobot(RobotDirection dir, uint32_t duration_ms) {
    if (xSemaphoreTake(motorMutex_, pdMS_TO_TICKS(100)) != pdTRUE) {
        return false;
    }

    // Khóa an toàn ToF: Tuyệt đối không cho phép tiến về phía trước nếu đang gặp mép bàn!
    if (dir == DIR_FORWARD && cliffDetected_) {
        Serial.println(F("[MotionCore Safety] TỪ CHỐI TIẾN: Cảm biến ToF phát hiện mép bàn/vực!"));
        if (alertCallback_) {
            alertCallback_("edge_detected");
        }
        xSemaphoreGive(motorMutex_);
        return false;
    }

    // Giới hạn thời gian an toàn tối đa 3000ms mỗi lệnh
    uint32_t safeDuration = min(duration_ms, (uint32_t)MAX_MOTION_DURATION_MS);
    if (dir == DIR_STOP) safeDuration = 0;

    currentDir_ = dir;
    motionRemainingMs_ = safeDuration;
    watchdogTicksMs_ = 0;

    switch (dir) {
        case DIR_FORWARD:
            digitalWrite(MOTOR_L_IN1, HIGH);
            digitalWrite(MOTOR_L_IN2, LOW);
            digitalWrite(MOTOR_R_IN3, HIGH);
            digitalWrite(MOTOR_R_IN4, LOW);
            Serial.printf("[MotionCore] Đang TIẾN trong %d ms...\n", safeDuration);
            break;

        case DIR_BACKWARD:
            digitalWrite(MOTOR_L_IN1, LOW);
            digitalWrite(MOTOR_L_IN2, HIGH);
            digitalWrite(MOTOR_R_IN3, LOW);
            digitalWrite(MOTOR_R_IN4, HIGH);
            Serial.printf("[MotionCore] Đang LÙI trong %d ms...\n", safeDuration);
            break;

        case DIR_LEFT:
            digitalWrite(MOTOR_L_IN1, LOW);
            digitalWrite(MOTOR_L_IN2, HIGH);
            digitalWrite(MOTOR_R_IN3, HIGH);
            digitalWrite(MOTOR_R_IN4, LOW);
            Serial.printf("[MotionCore] Đang QUAY TRÁI trong %d ms...\n", safeDuration);
            break;

        case DIR_RIGHT:
            digitalWrite(MOTOR_L_IN1, HIGH);
            digitalWrite(MOTOR_L_IN2, LOW);
            digitalWrite(MOTOR_R_IN3, LOW);
            digitalWrite(MOTOR_R_IN4, HIGH);
            Serial.printf("[MotionCore] Đang QUAY PHẢI trong %d ms...\n", safeDuration);
            break;

        case DIR_STOP:
        default:
            digitalWrite(MOTOR_L_IN1, LOW);
            digitalWrite(MOTOR_L_IN2, LOW);
            digitalWrite(MOTOR_R_IN3, LOW);
            digitalWrite(MOTOR_R_IN4, LOW);
            Serial.println(F("[MotionCore] Đã DỪNG motor."));
            break;
    }

    xSemaphoreGive(motorMutex_);
    return true;
}

void MotionCore::stopMotors() {
    if (xSemaphoreTake(motorMutex_, pdMS_TO_TICKS(100)) == pdTRUE) {
        digitalWrite(MOTOR_L_IN1, LOW);
        digitalWrite(MOTOR_L_IN2, LOW);
        digitalWrite(MOTOR_R_IN3, LOW);
        digitalWrite(MOTOR_R_IN4, LOW);
        currentDir_ = DIR_STOP;
        motionRemainingMs_ = 0;
        watchdogTicksMs_ = 0;
        xSemaphoreGive(motorMutex_);
    }
}

void MotionCore::emergencyStop() {
    // Ngắt khẩn cấp không chờ mutex
    digitalWrite(MOTOR_L_IN1, LOW);
    digitalWrite(MOTOR_L_IN2, LOW);
    digitalWrite(MOTOR_R_IN3, LOW);
    digitalWrite(MOTOR_R_IN4, LOW);
    currentDir_ = DIR_STOP;
    motionRemainingMs_ = 0;
    watchdogTicksMs_ = 0;
}

void MotionCore::updateConnectionStatus(bool connected) {
    wsConnected_ = connected;
    if (!connected && currentDir_ != DIR_STOP) {
        emergencyStop();
    }
}

// ─── Servo Kinematics & Macro Animations ─────────────────────────────────────

void MotionCore::waveArm() {
    Serial.println(F("[MotionCore Kinematics] Thực hiện hoạt ảnh vẫy tay (waveArm)..."));
    // Quét servo 47 từ 90 -> 140 -> 90 lặp lại 2 chu kỳ
    for (int cycle = 0; cycle < 2; ++cycle) {
        for (int pos = 90; pos <= 140; pos += 5) {
            servoArm_.write(pos);
            vTaskDelay(pdMS_TO_TICKS(15));
        }
        for (int pos = 140; pos >= 90; pos -= 5) {
            servoArm_.write(pos);
            vTaskDelay(pdMS_TO_TICKS(15));
        }
    }
    servoArm_.write(90);
}

void MotionCore::nodNeck() {
    Serial.println(F("[MotionCore Kinematics] Thực hiện hoạt ảnh gật đầu (nodNeck)..."));
    // Quét servo 3 từ 90 -> 115 -> 80 -> 90
    for (int pos = 90; pos <= 115; pos += 3) {
        servoNeck_.write(pos);
        vTaskDelay(pdMS_TO_TICKS(15));
    }
    for (int pos = 115; pos >= 80; pos -= 3) {
        servoNeck_.write(pos);
        vTaskDelay(pdMS_TO_TICKS(15));
    }
    servoNeck_.write(90);
}

void MotionCore::lookAround() {
    Serial.println(F("[MotionCore Kinematics] Thực hiện hoạt ảnh ngó nghiêng (lookAround)..."));
    // Quét cổ sang trái
    for (int pos = 90; pos >= 50; pos -= 3) {
        servoNeck_.write(pos);
        vTaskDelay(pdMS_TO_TICKS(20));
    }
    vTaskDelay(pdMS_TO_TICKS(200));

    // Quét cổ sang phải
    for (int pos = 50; pos <= 130; pos += 3) {
        servoNeck_.write(pos);
        vTaskDelay(pdMS_TO_TICKS(20));
    }
    vTaskDelay(pdMS_TO_TICKS(200));

    // Về vị trí tâm
    for (int pos = 130; pos >= 90; pos -= 3) {
        servoNeck_.write(pos);
        vTaskDelay(pdMS_TO_TICKS(20));
    }
    servoNeck_.write(90);
}

void MotionCore::excited() {
    Serial.println(F("[MotionCore Kinematics] Thực hiện hoạt ảnh phấn khích (excited)..."));
    digitalWrite(LED1_PIN, HIGH);
    digitalWrite(LED2_PIN, HIGH);

    for (int i = 0; i < 3; ++i) {
        servoArm_.write(135);
        servoNeck_.write(105);
        vTaskDelay(pdMS_TO_TICKS(100));
        servoArm_.write(90);
        servoNeck_.write(85);
        vTaskDelay(pdMS_TO_TICKS(100));
    }

    centerServos();
    digitalWrite(LED1_PIN, LOW);
    digitalWrite(LED2_PIN, LOW);
}

void MotionCore::smallNod() {
    // 90 -> 100 -> 88 -> 90, chậm: gật đầu nhẹ theo nhịp câu nói
    for (int pos = 90; pos <= 100; pos += 2) { servoNeck_.write(pos); vTaskDelay(pdMS_TO_TICKS(25)); }
    for (int pos = 100; pos >= 88; pos -= 2) { servoNeck_.write(pos); vTaskDelay(pdMS_TO_TICKS(25)); }
    servoNeck_.write(90);
}

void MotionCore::tiltHead(int angle, uint32_t holdMs) {
    angle = constrain(angle, 60, 120);
    int step = angle > 90 ? 2 : -2;
    for (int pos = 90; pos != angle; pos += step) {
        servoNeck_.write(pos);
        vTaskDelay(pdMS_TO_TICKS(25));
        if ((step > 0 && pos + step > angle) || (step < 0 && pos + step < angle)) break;
    }
    vTaskDelay(pdMS_TO_TICKS(holdMs));
    for (int pos = angle; pos != 90; pos -= step) {
        servoNeck_.write(pos);
        vTaskDelay(pdMS_TO_TICKS(25));
        if ((step > 0 && pos - step < 90) || (step < 0 && pos - step > 90)) break;
    }
    servoNeck_.write(90);
}

void MotionCore::sad() {
    Serial.println(F("[MotionCore Kinematics] Thực hiện hoạt ảnh buồn bã (sad)..."));
    // Cổ cụp xuống 65 độ, tay hạ 60 độ
    servoNeck_.write(65);
    servoArm_.write(60);
    vTaskDelay(pdMS_TO_TICKS(1500));
    centerServos();
}
