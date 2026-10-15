# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
#
# Image ứng dụng VN-MateAI cho máy chủ Linux / Docker (bản chuyển giao). Ngữ cảnh build = thư mục gốc mã nguồn:
#   docker build -f deploy/docker/Dockerfile -t vnmateai/app:2.0.1 .
# Gói sẵn model nhận dạng giọng nói (faster-whisper tiny + small) và model embedding của kho tri thức — khởi động không cần
# tải gì thêm. Chức năng chỉ có trên Windows (điều khiển màn hình / COM / micro máy chủ / khay hệ thống) không có trong
# container; máy trạm Windows vẫn điều khiển được qua Agent máy trạm (client_agent).
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/opt/models/hf \
    HOME=/home/vnmate \
    VNMATEAI_PROJECT_ROOT=/app \
    VNMATEAI_DATA_DIR=/data \
    VNMATEAI_LOG_FORMAT=text

# ffmpeg: đổi MP3 ↔ PCM cho robot; libgomp1: faster-whisper (CTranslate2); tini: tiến trình init chuẩn (tín hiệu dừng)
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg libgomp1 tini curl ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 1000 --shell /usr/sbin/nologin vnmate \
 && mkdir -p /app /data /opt/models && chown -R vnmate:vnmate /app /data /opt/models

WORKDIR /app

# Thư viện trước mã nguồn (tận dụng cache khi chỉ đổi mã). PyTorch bản CPU (không kéo CUDA ~2 GB).
# Bỏ các gói chỉ có trên Windows / giao diện máy bàn: pywinauto, pywin32, pyaudio, pystray, customtkinter.
# gcc chỉ có trong lúc pip chạy: vài gói không có wheel arm64 (vd psutil 5.9.8) phải biên dịch khi build bản ARM (Mac M1–M4).
COPY requirements.txt requirements-connectors.txt ./
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev \
 && pip install --index-url https://download.pytorch.org/whl/cpu "torch>=2.0.0" \
 && grep -vE '^\s*(pywinauto|pywin32|pyaudio|pystray|customtkinter|-e\s)' requirements.txt > /tmp/requirements-docker.txt \
 && pip install -r /tmp/requirements-docker.txt -r requirements-connectors.txt \
 && apt-get purge -y --auto-remove gcc libc6-dev && rm -rf /var/lib/apt/lists/*

# Model offline (đúng thư mục user chạy ứng dụng đọc): nhận câu gọi tên "Ly Ly ơi" (tiny + small), embedding kho tri thức.
USER vnmate
RUN python -c "from faster_whisper import WhisperModel; [WhisperModel(m, device='cpu', compute_type='int8') for m in ('tiny', 'small')]" \
 && python -c "from chromadb.utils import embedding_functions as ef; ef.DefaultEmbeddingFunction()(['khởi động'])"
USER root

COPY --chown=vnmate:vnmate . .
# MCP server tham chiếu chính thức đã rà soát (vendor/mcp-servers: time, fetch; git không cài — container không có mã nguồn
# Git lẫn chương trình git) — môi trường Python RIÊNG: thư viện `mcp` kéo
# starlette / uvicorn bản khác, không lẫn vào môi trường ứng dụng (fastapi / starlette ghim). Đăng ký lúc khởi động (entrypoint).
RUN python -m venv /opt/mcp-venv \
 && /opt/mcp-venv/bin/pip install --no-cache-dir -c vendor/mcp-servers/constraints.txt \
      vendor/mcp-servers/time vendor/mcp-servers/fetch
RUN pip install --no-deps -e . \
 && chmod +x deploy/docker/entrypoint.sh \
 && cp -a skills /app/skills.dist

USER vnmate
VOLUME ["/data"]
# 8443 = Portal / HUD / API (HTTPS); 8000 = cổng robot / IoT (WebSocket thường)
EXPOSE 8443 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=5 \
  CMD curl -fsk https://127.0.0.1:8443/livez || exit 1
ENTRYPOINT ["/usr/bin/tini", "--", "/app/deploy/docker/entrypoint.sh"]
CMD ["python", "main.py"]
