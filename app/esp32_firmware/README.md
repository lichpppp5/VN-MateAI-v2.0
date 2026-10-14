# VN-MateAI // Firmware Robot Companion ESP32 (Phase 52)
> Hệ thống nhúng điều khiển Robot tự hành để bàn đồng bộ tư duy và cử chỉ với Trợ lý AI VN-MateAI.

---

## 📁 DANH MỤC TỆP TIN ĐÃ SẴN SÀNG

| Đường dẫn tệp | Mô tả | Định dạng |
|---|---|---|
| [`esp32_firmware/binaries/vnmate_robot_merged.bin`](file:///Users/thanhlich/Desktop/VNMATEAI/esp32_firmware/binaries/vnmate_robot_merged.bin) | **File Binary Hợp Nhất (All-in-One)** nạp tại địa chỉ `0x0` | `.bin` |
| [`esp32_firmware/binaries/firmware.bin`](file:///Users/thanhlich/Desktop/VNMATEAI/esp32_firmware/binaries/firmware.bin) | File App Firmware chính (nạp tại `0x10000`) | `.bin` |
| [`esp32_firmware/binaries/bootloader.bin`](file:///Users/thanhlich/Desktop/VNMATEAI/esp32_firmware/binaries/bootloader.bin) | Bootloader ESP32-S3 (nạp tại `0x0000`) | `.bin` |
| [`esp32_firmware/binaries/partitions.bin`](file:///Users/thanhlich/Desktop/VNMATEAI/esp32_firmware/binaries/partitions.bin) | Bảng phân vùng bộ nhớ Flash (nạp tại `0x8000`) | `.bin` |
| [`esp32_firmware/vnmate_robot/vnmate_robot.ino`](file:///Users/thanhlich/Desktop/VNMATEAI/esp32_firmware/vnmate_robot/vnmate_robot.ino) | **Source code phác thảo hoàn chỉnh cho Arduino IDE** | `.ino` |
| [`esp32_firmware/flash.py`](file:///Users/thanhlich/Desktop/VNMATEAI/esp32_firmware/flash.py) | Script Python tự động dò cổng COM và nạp 1-click | `.py` |

---

## 🚀 HƯỚNG DẪN NẠP VÀO ESP32 (3 CÁCH DỄ DÀNG)

### CÁCH 1: NẠP 1-CLICK BẰNG FILE `.BIN` (NHANH NHẤT — KHÔNG CẦN CÀI IDE)
Chỉ cần cắm cáp USB nối ESP32 vào máy tính và chạy script:

```bash
# Tự động dò cổng COM và nạp file binary vnmate_robot_merged.bin:
python3 esp32_firmware/flash.py

# Hoặc chỉ định cổng cụ thể nếu có nhiều thiết bị:
# Trên macOS:
python3 esp32_firmware/flash.py --port /dev/cu.usbmodem1101

# Trên Windows:
python esp32_firmware/flash.py --port COM3
```

> **Hoặc nạp bằng lệnh `esptool` thủ công:**
> ```bash
> python3 -m esptool --chip esp32s3 --port /dev/cu.usbmodem1101 --baud 921600 write_flash 0x0 esp32_firmware/binaries/vnmate_robot_merged.bin
> ```

---

### CÁCH 2: MỞ VÀ NẠP BẰNG ARDUINO IDE (FILE `.INO`)
1. Mở Arduino IDE (phiên bản 1.8.x hoặc 2.x).
2. Vào menu: **File -> Open...** -> chọn file:
   `esp32_firmware/vnmate_robot/vnmate_robot.ino`
3. Cài đặt các thư viện phụ thuộc qua **Tools -> Manage Libraries...**:
   - `WebSockets` (bởi Markus Sattler)
   - `ArduinoJson` (phiên bản 6.x)
   - `Adafruit SSD1306` & `Adafruit GFX Library`
   - `ESP32Servo` (bởi Kevin Harrington)
4. Thiết lập phần cứng trong menu **Tools**:
   - **Board**: `"ESP32S3 Dev Module"` (hoặc `"ESP32 Dev Module"`)
   - **Flash Size**: `4MB` (hoặc `8MB/16MB` theo mạch của bạn)
   - **Partition Scheme**: `Huge APP (3MB No OTA / 1MB SPIFFS)`
   - **USB CDC On Boot**: `Enabled` (nếu dùng cổng USB gốc của ESP32-S3)
5. Mở file tab `config.h`, cập nhật tên WiFi và mật khẩu của bạn.
6. Nhấn nút **Upload** (Mũi tên sang phải `->`) để nạp.

---

### CÁCH 3: BIÊN DỊCH VÀ NẠP BẰNG PLATFORMIO (VSCODE)
```bash
# Di chuyển vào thư mục firmware:
cd esp32_firmware

# Biên dịch và nạp trực tiếp qua cổng USB:
python3 -m platformio run --target upload
```

---

## 🔌 SƠ ĐỒ CHÂN NỐI DÂY (GPIO PINOUT CHUẨN)

| Khối Chức Năng | Tên Chân / Module | GPIO ESP32 | Ghi Chú Kỹ Thuật |
|---|---|---|---|
| **Âm Thanh (Micro)** | INMP441 WS (L/R) | **GPIO 4** | I2S Microphone Word Select |
| | INMP441 SCK (BCLK) | **GPIO 5** | I2S Bit Clock |
| | INMP441 SD (Data Out) | **GPIO 6** | I2S Serial Data |
| **Âm Thanh (Loa)** | MAX98357A DIN | **GPIO 7** | I2S DAC Data In |
| | MAX98357A BCLK | **GPIO 15** | I2S DAC Bit Clock |
| | MAX98357A LRC | **GPIO 16** | I2S DAC Left/Right Clock |
| **Giao Diện & Cảm Biến** | Chạm Điện Dung (Touch) | **GPIO 17** | Chạm tay để đánh thức Robot |
| | Màn hình OLED SSD1306 | **GPIO 41 (SDA), 42 (SCL)** | I2C Hiển thị biểu cảm & Lip-sync |
| | Cảm biến ToF VL6180X | **GPIO 1 (SDA), 2 (SCL)** | I2C Wire1 chống rơi mép bàn/vực |
| **Động Cơ Bánh Xe (L298N)** | Motor Trái IN1, IN2 | **GPIO 11, GPIO 12** | Điều khiển tiến / lùi bánh trái |
| | Motor Phải IN3, IN4 | **GPIO 13, GPIO 14** | Điều khiển tiến / lùi bánh phải |
| **Servo & Đèn Báo** | Servo Cánh Tay (Arm) | **GPIO 47** | Quét góc 90° -> 140° vẫy tay chào |
| | Servo Cổ (Neck) | **GPIO 3** | Quét góc 90° -> 115° gật đầu đồng ý |
| | Đèn LED Chỉ Báo 1 & 2 | **GPIO 38, GPIO 18** | Sáng khi nghe/nói, nháy khi vui |
