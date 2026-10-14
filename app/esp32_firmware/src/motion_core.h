// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
#pragma once

/**
 * esp32_firmware/src/motion_core.h
 * ================================
 * Phase 52: Motion Core & Safety Interlocks Controller.
 * FreeRTOS-based motor driver, ToF edge detector & Servo kinematics.
 */

#include <Arduino.h>
#include <ESP32Servo.h>
#include <Wire.h>
#include "config.h"

// Direction enum
enum RobotDirection {
    DIR_STOP = 0,
    DIR_FORWARD,
    DIR_BACKWARD,
    DIR_LEFT,
    DIR_RIGHT
};

// Callback function prototype for sending alerts over WebSocket
typedef void (*AlertCallback)(const char* alertMsg);

class MotionCore {
public:
    static MotionCore& getInstance() {
        static MotionCore instance;
        return instance;
    }

    // Lifecycle
    bool init();
    void setAlertCallback(AlertCallback cb) { alertCallback_ = cb; }
    void updateConnectionStatus(bool connected);

    // Motor Operations
    bool moveRobot(const String& direction, uint32_t duration_ms);
    bool moveRobot(RobotDirection dir, uint32_t duration_ms);
    void stopMotors();
    void emergencyStop();

    // Servo Kinematics & Animations
    void waveArm();
    void nodNeck();
    void lookAround();
    void excited();
    void sad();
    void smallNod();      // gật rất nhẹ (nhịp khi đang nói)
    void tiltHead(int angle, uint32_t holdMs);   // nghiêng cổ rồi về giữa
    void centerServos();

    // Getters
    bool isCliffDetected() const { return cliffDetected_; }
    uint16_t getLastTofDistanceMm() const { return lastTofDistanceMm_; }
    RobotDirection getCurrentDirection() const { return currentDir_; }

private:
    MotionCore();
    ~MotionCore() = default;
    MotionCore(const MotionCore&) = delete;
    MotionCore& operator=(const MotionCore&) = delete;

    // FreeRTOS Task entry points
    static void tofSafetyTaskTrampoline(void* arg);
    static void motionWatchdogTaskTrampoline(void* arg);

    void tofSafetyTaskLoop();
    void motionWatchdogTaskLoop();

    // Hardware setup
    void setupPins();
    void setupServos();
    bool setupToF();
    uint16_t readTofDistanceMm();

    // Servo objects
    Servo servoArm_;
    Servo servoNeck_;

    // Safety & state
    volatile bool cliffDetected_{false};
    volatile uint16_t lastTofDistanceMm_{0};
    volatile RobotDirection currentDir_{DIR_STOP};
    volatile uint32_t motionRemainingMs_{0};
    volatile uint32_t watchdogTicksMs_{0};
    volatile bool wsConnected_{false};

    AlertCallback alertCallback_{nullptr};
    TaskHandle_t tofTaskHandle_{nullptr};
    TaskHandle_t watchdogTaskHandle_{nullptr};
    SemaphoreHandle_t motorMutex_{nullptr};
};
