// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
#pragma once

/**
 * esp32_firmware/vnmate_robot/motion_core.h
 * =========================================
 * Phase 52: Motion Core & Safety Interlocks Controller.
 */

#include <Arduino.h>
#include <ESP32Servo.h>
#include <Wire.h>
#include "config.h"

enum RobotDirection {
    DIR_STOP = 0,
    DIR_FORWARD,
    DIR_BACKWARD,
    DIR_LEFT,
    DIR_RIGHT
};

typedef void (*AlertCallback)(const char* alertMsg);

class MotionCore {
public:
    static MotionCore& getInstance() {
        static MotionCore instance;
        return instance;
    }

    bool init();
    void setAlertCallback(AlertCallback cb) { alertCallback_ = cb; }
    void updateConnectionStatus(bool connected);

    bool moveRobot(const String& direction, uint32_t duration_ms);
    bool moveRobot(RobotDirection dir, uint32_t duration_ms);
    void stopMotors();
    void emergencyStop();

    void waveArm();
    void nodNeck();
    void lookAround();
    void excited();
    void sad();
    void centerServos();

    bool isCliffDetected() const { return cliffDetected_; }
    uint16_t getLastTofDistanceMm() const { return lastTofDistanceMm_; }
    RobotDirection getCurrentDirection() const { return currentDir_; }

private:
    MotionCore();
    ~MotionCore() = default;
    MotionCore(const MotionCore&) = delete;
    MotionCore& operator=(const MotionCore&) = delete;

    static void tofSafetyTaskTrampoline(void* arg);
    static void motionWatchdogTaskTrampoline(void* arg);

    void tofSafetyTaskLoop();
    void motionWatchdogTaskLoop();

    void setupPins();
    void setupServos();
    bool setupToF();
    uint16_t readTofDistanceMm();

    Servo servoArm_;
    Servo servoNeck_;

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
