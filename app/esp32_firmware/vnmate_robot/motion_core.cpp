// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
/**
 * esp32_firmware/vnmate_robot/motion_core.cpp
 * ===========================================
 * Phase 52: Motion Core & Safety Interlocks Implementation.
 */

#include "motion_core.h"

static TwoWire ToFWire = TwoWire(1);

MotionCore::MotionCore() {
    motorMutex_ = xSemaphoreCreateMutex();
}

bool MotionCore::init() {
    Serial.println(F("[MotionCore] Khởi tạo hệ thống vận động và an toàn (Phase 52)..."));
    setupPins();
    setupServos();
    bool tofOk = setupToF();

    xTaskCreatePinnedToCore(
        tofSafetyTaskTrampoline,
        "ToFSafetyTask",
        4096,
        this,
        3,
        &tofTaskHandle_,
        1
    );

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
    pinMode(MOTOR_L_IN1, OUTPUT);
    pinMode(MOTOR_L_IN2, OUTPUT);
    pinMode(MOTOR_R_IN3, OUTPUT);
    pinMode(MOTOR_R_IN4, OUTPUT);

    digitalWrite(MOTOR_L_IN1, LOW);
    digitalWrite(MOTOR_L_IN2, LOW);
    digitalWrite(MOTOR_R_IN3, LOW);
    digitalWrite(MOTOR_R_IN4, LOW);

    pinMode(LED1_PIN, OUTPUT);
    pinMode(LED2_PIN, OUTPUT);
    digitalWrite(LED1_PIN, LOW);
    digitalWrite(LED2_PIN, LOW);
}

void MotionCore::setupServos() {
    ESP32PWM::allocateTimer(0);
    ESP32PWM::allocateTimer(1);
    ESP32PWM::allocateTimer(2);
    ESP32PWM::allocateTimer(3);

    servoArm_.setPeriodHertz(50);
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
    ToFWire.begin(TOF_SDA, TOF_SCL, 400000);
    Serial.println(F("[MotionCore] I2C Wire1 cho ToF (SDA: 1, SCL: 2) đã sẵn sàng."));
    return true;
}

uint16_t MotionCore::readTofDistanceMm() {
    const uint8_t TOF_I2C_ADDR = 0x29;

    ToFWire.beginTransmission(TOF_I2C_ADDR);
    ToFWire.write(0x00);
    ToFWire.write(0x18);
    ToFWire.write(0x01);
    if (ToFWire.endTransmission() != 0) {
        return 45; 
    }

    vTaskDelay(pdMS_TO_TICKS(10));

    ToFWire.beginTransmission(TOF_I2C_ADDR);
    ToFWire.write(0x00);
    ToFWire.write(0x4D);
    if (ToFWire.endTransmission() != 0) return 45;

    ToFWire.requestFrom(TOF_I2C_ADDR, (uint8_t)1);
    if (ToFWire.available()) {
        uint8_t dist = ToFWire.read();
        return (uint16_t)dist;
    }
    return 45;
}

void MotionCore::tofSafetyTaskTrampoline(void* arg) {
    static_cast<MotionCore*>(arg)->tofSafetyTaskLoop();
}

void MotionCore::tofSafetyTaskLoop() {
    TickType_t xLastWakeTime = xTaskGetTickCount();
    const TickType_t xFrequency = pdMS_TO_TICKS(40);

    for (;;) {
        uint16_t distMm = readTofDistanceMm();
        lastTofDistanceMm_ = distMm;

        if (distMm >= CLIFF_THRESHOLD_MM || distMm == 255) {
            if (!cliffDetected_) {
                cliffDetected_ = true;
                Serial.printf("[MotionCore Safety] ⚠️ CẢNH BÁO TOF: Phát hiện mép bàn/vực (khoảng cách: %d mm)!\n", distMm);
                emergencyStop();
                if (alertCallback_) {
                    alertCallback_("edge_detected");
                }
            } else {
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
    const TickType_t xFrequency = pdMS_TO_TICKS(10);

    for (;;) {
        if (currentDir_ != DIR_STOP) {
            if (!wsConnected_) {
                Serial.println(F("[Watchdog] Mất kết nối WebSocket trong khi di chuyển -> Dừng khẩn cấp!"));
                emergencyStop();
            }

            if (motionRemainingMs_ > 10) {
                motionRemainingMs_ -= 10;
            } else {
                motionRemainingMs_ = 0;
                stopMotors();
            }

            watchdogTicksMs_ += 10;
            if (watchdogTicksMs_ >= WATCHDOG_TIMEOUT_MS) {
                Serial.println(F("[Watchdog] CẢNH BÁO: Quá thời gian Watchdog tuyệt đối (10s) -> Ngắt toàn bộ motor!"));
                emergencyStop();
            }
        }

        vTaskDelayUntil(&xLastWakeTime, xFrequency);
    }
}

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

    if (dir == DIR_FORWARD && cliffDetected_) {
        Serial.println(F("[MotionCore Safety] TỪ CHỐI TIẾN: Cảm biến ToF phát hiện mép bàn/vực!"));
        if (alertCallback_) {
            alertCallback_("edge_detected");
        }
        xSemaphoreGive(motorMutex_);
        return false;
    }

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

void MotionCore::waveArm() {
    Serial.println(F("[MotionCore Kinematics] Thực hiện hoạt ảnh vẫy tay (waveArm)..."));
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
    for (int pos = 90; pos >= 50; pos -= 3) {
        servoNeck_.write(pos);
        vTaskDelay(pdMS_TO_TICKS(20));
    }
    vTaskDelay(pdMS_TO_TICKS(200));

    for (int pos = 50; pos <= 130; pos += 3) {
        servoNeck_.write(pos);
        vTaskDelay(pdMS_TO_TICKS(20));
    }
    vTaskDelay(pdMS_TO_TICKS(200));

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

void MotionCore::sad() {
    Serial.println(F("[MotionCore Kinematics] Thực hiện hoạt ảnh buồn bã (sad)..."));
    servoNeck_.write(65);
    servoArm_.write(60);
    vTaskDelay(pdMS_TO_TICKS(1500));
    centerServos();
}
