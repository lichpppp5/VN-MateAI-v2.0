# Đánh giá codec realtime — Opus so với MP3 (prompt cuối §27)

> Đo ngày 2026-10-06 bằng `scripts/bench_codec.py` trên **60 câu TTS thật** lấy từ `storage/audio_cache` (giọng Edge TTS, trung vị 6,06 s/câu). Mã hoá / giải mã bằng ffmpeg 7.1 (libopus, libmp3lame) qua `imageio-ffmpeg`. Thời gian gồm cả khởi động tiến trình ffmpeg, nên chỉ dùng để **so sánh tương đối**.

## Kết quả

| Chỉ số (trung vị) | MP3 hiện tại | Opus 16 kbps | Opus 24 kbps | Opus 32 kbps |
|---|---|---|---|---|
| Bitrate | 32 kbps | 16 | 24 | 32 |
| Dung lượng so với MP3 | 1,00 | **0,47** | 0,68 | 0,93 |
| Mã hoá (ms CPU / 1 s âm thanh) | — (TTS trả MP3 sẵn) | 16,2 | 16,4 | 17,6 |
| Giải mã (ms / 1 s âm thanh) | 4,0 | 4,4 | 4,7 | 4,8 |
| Khung | cả câu | 20 ms | 20 ms | 20 ms |

## Đọc kết quả theo từng chiều

**Tải xuống (TTS → trình duyệt / HUD / robot): GIỮ MP3.**
- Edge TTS trả MP3 sẵn. Đổi sang Opus phải giải mã rồi mã hoá lại trên máy chủ: thêm khoảng 16 ms CPU cho mỗi giây âm thanh, cộng một bước vào đường nóng.
- Băng thông không phải nút cổ chai: 32 kbps trên LAN là không đáng kể.
- Độ trễ đo được nằm ở chỗ khác (`docs/realtime/performance-before-after.md`, `/api/v1/ops/overview`): token LLM đầu tiên p50 khoảng 2,6 s, TTS câu đầu khoảng 2,0 s. Đổi codec không rút ngắn hai con số này.
- Trình duyệt giải mã cả hai qua `decodeAudioData`. MP3 tương thích rộng hơn (Safari cũ).

**Tải lên (micro robot ESP32 → máy chủ): lý thuyết thì Opus có lợi; đo thật cho thấy chưa cần (mục dưới).**
- Robot gửi PCM 16 kHz / 16 bit = **256 kbps**. Opus 16 kbps (chất lượng thoại tốt) bằng khoảng **1/16** con số đó, giúp Wi-Fi yếu bớt rớt khung.
- Máy chủ đã nhận diện cờ `FLAG_OPUS` (`infrastructure/websocket/binary_transport.py`) và tham số `format` (`xiaozhi_gateway`). Phần còn thiếu là **bộ mã hoá Opus trong firmware** (ESP32-S3 chạy được libopus / esp-adf) và giải mã phía máy chủ trước STT.
- Muốn bật thì cần bộ mã hoá trong firmware và bộ giải mã trước STT; chỉ đáng làm khi số đo dưới đây đổi.

## Đo thực tế chiều tải lên (robot `vnmate_robot_01`, firmware 54.0, 2026-10-06)

Firmware chỉ gửi **đoạn có tiếng nói** (bộ lọc trên chip), không gửi liên tục. Đo trong 11 phút (18:26–18:37): robot gửi **88 đoạn, tổng 210,6 s** âm thanh (khoảng 32 % thời gian). Như vậy PCM 16 kHz chỉ tốn trung bình khoảng **82 kbps**, trong khi Wi-Fi của robot ở −41 dBm (mạnh). Opus sẽ bớt khoảng 78 kbps trung bình, nhưng **băng thông hiện không phải nút cổ chai**. Vì thế chưa đổi codec ở chiều này: chỉ làm khi có robot ở vùng Wi-Fi yếu (rớt khung đo được).

Quan sát kèm theo: 88 đoạn trong 11 phút, gần hết là "không gọi tên". Bộ lọc tiếng nói đang để lọt tiếng ồn nền (RMS khoảng 1 000–2 800), nên nếu muốn bớt lưu lượng thì nâng ngưỡng bộ lọc sẽ hiệu quả hơn đổi codec.

## Quyết định

| Chiều | Codec | Lý do |
|---|---|---|
| TTS → client | MP3 (giữ) | không phải nút cổ chai; đổi thì tốn thêm CPU và một bước trên đường nóng |
| Micro robot → máy chủ | PCM (giữ) — Opus 16 kbps chỉ khi Wi-Fi yếu | đo thật trung bình khoảng 82 kbps nhờ lọc tiếng nói trên chip; Wi-Fi −41 dBm |

Chạy lại: `pip install -r requirements-dev.txt && python scripts/bench_codec.py --n 60`.
