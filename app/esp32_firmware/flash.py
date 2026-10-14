#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
esp32_firmware/flash.py
=======================
Công cụ 1-Click tự động dò cổng COM/Serial và nạp Firmware Robot vào ESP32 / ESP32-S3.

Cách dùng:
    python3 esp32_firmware/flash.py
    hoặc:
    python3 esp32_firmware/flash.py --port /dev/cu.usbmodem1101
    python3 esp32_firmware/flash.py --port COM3
"""

import sys
import subprocess
import argparse
from pathlib import Path

try:
    import serial.tools.list_ports
except ImportError:
    print("Đang cài đặt pyserial...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pyserial", "esptool"])
    import serial.tools.list_ports

BIN_DIR = Path(__file__).resolve().parent / "binaries"
MERGED_BIN = BIN_DIR / "vnmate_robot_merged.bin"

def find_esp_ports():
    ports = serial.tools.list_ports.comports()
    esp_ports = []
    for p in ports:
        desc = (p.description or "").lower()
        hwid = (p.hwid or "").lower()
        port_name = p.device
        if any(kw in desc or kw in hwid or kw in port_name.lower() for kw in ["usb", "uart", "cp210", "ch340", "wch", "espressif", "jtag", "serial"]):
            esp_ports.append(p)
    return esp_ports

def main():
    parser = argparse.ArgumentParser(description="VN-MateAI ESP32 Robot Firmware Flasher")
    parser.add_argument("--port", "-p", help="Cổng Serial (ví dụ: /dev/cu.usbmodem101, COM3)")
    parser.add_argument("--baud", "-b", default="921600", help="Tốc độ nạp Baudrate (mặc định: 921600)")
    parser.add_argument("--erase", action="store_true", help="Xóa sạch Flash chip trước khi nạp")
    args = parser.parse_args()

    if not MERGED_BIN.exists():
        print(f"❌ Không tìm thấy file binary tại: {MERGED_BIN}")
        sys.exit(1)

    port = args.port
    if not port:
        candidates = find_esp_ports()
        if not candidates:
            print("⚠️ Không phát hiện cổng USB Serial nào tự động.")
            print("Vui lòng cắm cáp USB nối ESP32 vào máy tính và chạy lại:")
            print("   python3 esp32_firmware/flash.py --port <TÊN_CỔNG>")
            sys.exit(1)
        elif len(candidates) == 1:
            port = candidates[0].device
            print(f"🔍 Tự động phát hiện cổng ESP32: {port} ({candidates[0].description})")
        else:
            print("📋 Tìm thấy nhiều cổng USB Serial:")
            for i, p in enumerate(candidates, 1):
                print(f"  [{i}] {p.device} - {p.description}")
            choice = input(f"Chọn cổng (1-{len(candidates)}) [mặc định 1]: ").strip()
            idx = int(choice) - 1 if choice.isdigit() and 1 <= int(choice) <= len(candidates) else 0
            port = candidates[idx].device

    print(f"\n=======================================================")
    print(f"  🚀 BẮT ĐẦU NẠP FIRMWARE VN-MATEAI ROBOT VÀO ESP32-S3 ")
    print(f"=======================================================")
    print(f"  - Cổng Serial : {port}")
    print(f"  - Tốc độ Baud : {args.baud}")
    print(f"  - File nạp    : {MERGED_BIN.name} ({MERGED_BIN.stat().st_size / 1024:.1f} KB)")
    print(f"  - Địa chỉ nạp : 0x0 (All-in-One Image)")
    print(f"=======================================================\n")

    if args.erase:
        print("Đang xóa sạch chip Flash (Erase Flash)...")
        erase_cmd = [
            sys.executable, "-m", "esptool",
            "--chip", "esp32s3",
            "--port", port,
            "erase_flash"
        ]
        subprocess.run(erase_cmd, check=True)

    flash_cmd = [
        sys.executable, "-m", "esptool",
        "--chip", "esp32s3",
        "--port", port,
        "--baud", str(args.baud),
        "--before", "default_reset",
        "--after", "hard_reset",
        "write_flash",
        "-z",
        "--flash_mode", "dio",
        "--flash_size", "4MB",
        "0x0", str(MERGED_BIN)
    ]

    print("Đang ghi dữ liệu vào Flash ESP32...")
    res = subprocess.run(flash_cmd)
    if res.returncode == 0:
        print("\n🎉 NẠP FIRMWARE THÀNH CÔNG 100%! 🎉")
        print("Robot của bạn đang khởi động lại. Hãy mở Serial Monitor ở tốc độ 115200 để xem log khởi động.")
    else:
        print(f"\n❌ Nạp thất bại (Mã lỗi {res.returncode}).")
        print("Mẹo: Hãy nhấn giữ nút BOOT trên mạch ESP32 trong 2 giây khi cắm cáp USB để đưa chip về chế độ Download Mode.")

if __name__ == "__main__":
    main()
