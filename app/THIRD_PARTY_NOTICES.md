# Third-party notices

VN-MateAI © 2026 Dương Thanh Lịch, giấy phép Apache-2.0 (xem `LICENSE`, `NOTICE`). Các thành phần dưới đây thuộc về tác giả của chúng và giữ giấy phép riêng. Tệp này được sinh bởi `scripts/gen_third_party_notices.py` từ metadata các gói đã cài.

## Thư viện Python (requirements*.txt)

| Gói | Giấy phép (theo metadata) |
|---|---|
| aiofiles | Apache-2.0 |
| boto3 | (chưa cài — cần kiểm tra) |
| chromadb | Apache Software License |
| cryptography | Apache-2.0 OR BSD-3-Clause |
| customtkinter | Creative Commons Zero v1.0 Universal |
| edge-tts | GNU Lesser General Public License v3 (LGPLv3) |
| fastapi | MIT |
| faster-whisper | MIT |
| h2 | MIT |
| httpx | BSD-3-Clause |
| matplotlib | Python Software Foundation License |
| minio | Apache-2.0 |
| mss | MIT License |
| networkx | BSD-3-Clause |
| numpy | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| oci | (chưa cài — cần kiểm tra) |
| openai | Apache-2.0 |
| openpyxl | MIT |
| opentelemetry-exporter-otlp-proto-grpc | Apache-2.0 |
| opentelemetry-sdk | Apache-2.0 |
| oracledb | (chưa cài — cần kiểm tra) |
| pandas | BSD License |
| passlib | BSD |
| Pillow | MIT-CMU |
| psutil | BSD-3-Clause |
| psycopg | LGPL-3.0-only |
| psycopg-pool | LGPL-3.0-only |
| pyaudio | MIT |
| pydantic | MIT |
| pydantic-settings | MIT |
| pydub | MIT |
| pyjwt | MIT |
| pymssql | (chưa cài — cần kiểm tra) |
| pymysql | (chưa cài — cần kiểm tra) |
| pypdf | BSD-3-Clause |
| pyperclip | BSD |
| pystray | LGPLv3 |
| python-docx | MIT |
| python-dotenv | BSD-3-Clause |
| python-multipart | Apache-2.0 |
| python-telegram-bot | LGPL-3.0-only |
| pywin32 | PSF |
| pywinauto | BSD 3-clause |
| rank-bm25 | Apache2.0 |
| redis | MIT |
| silero-vad | MIT License |
| SpeechRecognition | BSD-3-Clause |
| starlette | BSD-3-Clause |
| torch | Apache-2.0 AND Apache-2.0 WITH LLVM-exception AND BSD-2-Clause AND BSD-3-Clause AND BSL-1.0 AND MIT |
| uvicorn | BSD-3-Clause |
| websockets | BSD-3-Clause |
| wmi | http://www.opensource.org/licenses/mit-license.php |

### Lưu ý về LGPL

Các gói sau dùng giấy phép LGPL (hoặc tương tự). Chúng được dùng như thư viện Python độc lập (người dùng thay được bằng `pip install`); nếu bạn đóng gói chúng cùng ứng dụng (ví dụ PyInstaller), hãy giữ nguyên thông báo giấy phép của chúng và cho phép người nhận thay thế thư viện:

`edge-tts`, `psycopg`, `psycopg-pool`, `pystray`, `python-telegram-bot`

## Tài nguyên giao diện được đóng kèm (`web/`)

| Thành phần | Giấy phép | Vị trí | Nguồn |
|---|---|---|---|
| Chart.js 4.4.1 | MIT | `web/vendor/chart.umd.min.js` | https://www.chartjs.org |
| Tailwind CSS (CSS đã build) | MIT | `web/tailwind.css, web/tailwind-hud.css` | https://tailwindcss.com |
| Be Vietnam Pro | SIL OFL 1.1 | `web/fonts/BeVietnamPro-*` | https://fonts.google.com/specimen/Be+Vietnam+Pro |
| JetBrains Mono | SIL OFL 1.1 | `web/fonts/JetBrainsMono-*` | https://www.jetbrains.com/lp/mono/ |
| Inter | SIL OFL 1.1 | `web/fonts/Inter-*` | https://rsms.me/inter/ |
| Orbitron | SIL OFL 1.1 | `web/fonts/Orbitron-*` | https://fonts.google.com/specimen/Orbitron |
| Rajdhani | SIL OFL 1.1 | `web/fonts/Rajdhani-*` | https://fonts.google.com/specimen/Rajdhani |
| Share Tech Mono | SIL OFL 1.1 | `web/fonts/ShareTechMono-*` | https://fonts.google.com/specimen/Share+Tech+Mono |

Font theo SIL Open Font License 1.1: được dùng, đóng kèm và phân phối cùng phần mềm; không được bán riêng font.

## Ứng dụng quản trị Next.js (`admin/`)

Dùng các gói npm (Next.js, React, React Flow…) theo giấy phép của từng gói (chủ yếu MIT); danh sách đầy đủ nằm ở `admin/package.json` / `package-lock.json`.

## Firmware ESP32 (`esp32_firmware/`)

Xây trên Arduino-ESP32 / PlatformIO và các thư viện khai báo trong `platformio.ini`, mỗi thư viện theo giấy phép riêng.
