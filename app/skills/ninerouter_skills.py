# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
skills/ninerouter_skills.py
===========================
Bộ kỹ năng tích hợp cổng AI Gateway đa nhà cung cấp 9Router cho VN-MateAI.
Bao gồm: Chat, Image Generation, Text-to-Speech, Speech-to-Text, Embeddings, Web Search, Web Fetch,
và công cụ cài đặt kỹ năng từ URL vào kho kỹ năng hệ thống.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.plugin_manager import export_skill
from mateai.config.loader import settings

logger = logging.getLogger(__name__)


def _get_9router_config() -> tuple[str, str]:
    """Lấy base URL và API key của 9Router từ cấu hình hệ thống."""
    base_url = getattr(settings.llm, "base_url", "http://localhost:20128/v1").rstrip("/")
    # Chuẩn hóa về root URL (loại bỏ /v1 ở cuối nếu có)
    if base_url.endswith("/v1"):
        root_url = base_url[:-3]
    else:
        root_url = base_url
    api_key = getattr(settings.llm, "api_key", "")
    return root_url, api_key


def _send_9router_request(
    endpoint: str,
    payload: Optional[Dict[str, Any]] = None,
    method: str = "POST",
    headers_extra: Optional[Dict[str, str]] = None,
    timeout: int = 45,
) -> Dict[str, Any]:
    """Gửi HTTP request đến 9Router gateway."""
    root_url, api_key = _get_9router_config()
    url = f"{root_url}{endpoint}"

    headers = {
        "User-Agent": "VN-MateAI-Skill/1.0",
        "Accept": "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if headers_extra:
        headers.update(headers_extra)

    data_bytes = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(url, data=data_bytes, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content_type = resp.headers.get("Content-Type", "")
            raw_body = resp.read()
            if "application/json" in content_type:
                return json.loads(raw_body.decode("utf-8"))
            return {"status": "ok", "raw": raw_body.decode("utf-8", errors="replace")}
    except urllib.error.HTTPError as http_err:
        err_body = http_err.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(err_body)
            err_msg = parsed.get("error", {}).get("message") or parsed.get("message") or err_body
        except Exception:
            err_msg = err_body
        raise RuntimeError(f"9Router HTTP {http_err.code}: {err_msg}")
    except Exception as exc:
        raise RuntimeError(f"Lỗi kết nối đến 9Router ({url}): {exc}")


# ─── 1. 9Router Web Search ──────────────────────────────────────────────────

@export_skill(
    name="ninerouter_web_search",
    description="Tìm kiếm thông tin web, tin tức và bài viết trực tuyến thông qua cổng 9Router (hỗ trợ Tavily, Brave, Exa, Serper, Google PSE). Dùng khi cần tra cứu thông tin thời gian thực trên Internet.",
    parameters_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Câu truy vấn tìm kiếm cần tra cứu trên Internet."
            },
            "model": {
                "type": "string",
                "description": "Nhà cung cấp / model tìm kiếm (ví dụ: 'tavily', 'brave-search', 'exa', 'serper'). Mặc định 'tavily'.",
                "default": "tavily"
            },
            "max_results": {
                "type": "integer",
                "description": "Số lượng kết quả tìm kiếm tối đa cần lấy (mặc định 5).",
                "default": 5
            }
        },
        "required": ["query"]
    }
)
def ninerouter_web_search(query: str, model: str = "tavily", max_results: int = 5, **kwargs) -> Dict[str, Any]:
    """Tìm kiếm web qua 9Router /v1/search."""
    payload = {
        "model": model,
        "query": query,
        "max_results": max_results,
    }
    try:
        res = _send_9router_request("/v1/search", payload=payload)
        return {
            "query": query,
            "provider": res.get("provider", model),
            "results": res.get("results", []),
            "answer": res.get("answer"),
        }
    except Exception as e:
        return {"error": str(e), "query": query}


# ─── 2. 9Router Web Fetch ───────────────────────────────────────────────────

@export_skill(
    name="ninerouter_web_fetch",
    description="Cào và trích xuất nội dung văn bản sạch của một trang web thành định dạng Markdown, Text hoặc HTML qua cổng 9Router (sử dụng Jina Reader, Firecrawl, Tavily Extract). Dùng khi cần đọc bài báo, tài liệu, nội dung URL.",
    parameters_schema={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Địa chỉ URL của trang web cần cào nội dung."
            },
            "model": {
                "type": "string",
                "description": "Nhà cung cấp trích xuất: 'jina-reader' (nhanh, miễn phí), 'firecrawl' (hỗ trợ JS), 'tavily', 'ollama'.",
                "default": "jina-reader"
            },
            "format": {
                "type": "string",
                "enum": ["markdown", "text", "html"],
                "description": "Định dạng kết quả trả về. Mặc định là 'markdown'.",
                "default": "markdown"
            },
            "max_characters": {
                "type": "integer",
                "description": "Giới hạn độ dài ký tự tối đa để tránh vượt quá ngữ cảnh. Mặc định 10000.",
                "default": 10000
            }
        },
        "required": ["url"]
    }
)
def ninerouter_web_fetch(url: str, model: str = "jina-reader", format: str = "markdown", max_characters: int = 10000, **kwargs) -> Dict[str, Any]:
    """Cào và trích xuất URL thành markdown qua 9Router /v1/web/fetch."""
    payload = {
        "model": model,
        "url": url,
        "format": format,
        "max_characters": max_characters,
    }
    try:
        res = _send_9router_request("/v1/web/fetch", payload=payload)
        content_obj = res.get("content", {})
        text = content_obj.get("text") if isinstance(content_obj, dict) else str(content_obj)
        if max_characters and len(text) > max_characters:
            text = text[:max_characters] + "\n\n...[Đã cắt bớt do giới hạn độ dài]..."
        return {
            "url": url,
            "title": res.get("title", ""),
            "provider": res.get("provider", model),
            "content": text,
            "length": len(text),
        }
    except Exception as e:
        return {"error": str(e), "url": url}


# ─── 3. 9Router Image Generation ─────────────────────────────────────────────

@export_skill(
    name="ninerouter_generate_image",
    description="Tạo hình ảnh nghệ thuật từ mô tả văn bản (Text-to-Image) thông qua cổng 9Router bằng DALL-E, Gemini Imagen, FLUX, SDWebUI, ComfyUI. Dùng khi người dùng yêu cầu vẽ tranh, tạo ảnh minh họa.",
    parameters_schema={
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "Mô tả chi tiết bức ảnh cần tạo bằng tiếng Anh hoặc tiếng Việt."
            },
            "model": {
                "type": "string",
                "description": "Tên model tạo ảnh (ví dụ: 'openai/dall-e-3', 'flux', 'imagen').",
                "default": "openai/dall-e-3"
            },
            "size": {
                "type": "string",
                "enum": ["1024x1024", "1792x1024", "1024x1792", "512x512"],
                "description": "Kích thước bức ảnh.",
                "default": "1024x1024"
            }
        },
        "required": ["prompt"]
    }
)
def ninerouter_generate_image(prompt: str, model: str = "openai/dall-e-3", size: str = "1024x1024", **kwargs) -> Dict[str, Any]:
    """Tạo hình ảnh qua 9Router /v1/images/generations."""
    payload = {
        "model": model,
        "prompt": prompt,
        "size": size,
        "response_format": "url",
    }
    try:
        res = _send_9router_request("/v1/images/generations", payload=payload)
        data = res.get("data", [])
        urls = [item.get("url") for item in data if "url" in item]
        return {
            "prompt": prompt,
            "image_urls": urls,
            "count": len(urls),
        }
    except Exception as e:
        return {"error": str(e), "prompt": prompt}


# ─── 4. 9Router Text-to-Speech ───────────────────────────────────────────────

@export_skill(
    name="ninerouter_text_to_speech",
    description="Chuyển văn bản thành giọng nói (Text-to-Speech) qua 9Router hỗ trợ giọng Edge TTS tiếng Việt, ElevenLabs, OpenAI TTS, Google TTS. Dùng khi muốn xuất file âm thanh đọc văn bản.",
    parameters_schema={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "Đoạn văn bản cần chuyển thành giọng nói."
            },
            "voice_model": {
                "type": "string",
                "description": "Mã giọng đọc (ví dụ: 'edge-tts/vi-VN-HoaiMyNeural', 'edge-tts/vi-VN-NamMinhNeural', 'openai/tts-1').",
                "default": "edge-tts/vi-VN-HoaiMyNeural"
            }
        },
        "required": ["text"]
    }
)
async def ninerouter_text_to_speech(text: str, voice_model: str = "edge-tts/vi-VN-HoaiMyNeural", **kwargs) -> Dict[str, Any]:
    """Tạo giọng nói qua engine TTS CHUẨN (`TTSStreamEngine`: cache, 9Router, Edge dự phòng).

    Trước đây skill tự gọi `/v1/audio/speech` — đường TTS thứ hai, ngoài cache / dự phòng
    và ngoài kiểm tra RULE-012. Giọng dạng "edge-tts/<tên>" được chuyển thành tên giọng."""
    from mateai.infrastructure.tts.tts_stream_engine import get_tts_engine
    voice = voice_model.split("/", 1)[1] if voice_model.startswith("edge-tts/") else None
    try:
        audio = await get_tts_engine().synthesise(text, voice=voice)
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e), "text_preview": text[:50]}
    if not audio:
        return {"status": "error", "error": "Không tổng hợp được giọng nói (mọi nguồn TTS đều lỗi).",
                "text_preview": text[:50]}
    return {
        "status": "success",
        "voice_model": voice_model,
        "length_chars": len(text),
        "audio_format": "mp3",
        "audio_bytes": len(audio),
    }


# ─── 5. 9Router Embeddings ───────────────────────────────────────────────────

@export_skill(
    name="ninerouter_create_embeddings",
    description="Tạo vector biểu diễn ngữ nghĩa (Embeddings) cho văn bản qua 9Router phục vụ tra cứu ngữ nghĩa, RAG và tìm kiếm tương đồng.",
    parameters_schema={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "Văn bản cần trích xuất vector embeddings."
            },
            "model": {
                "type": "string",
                "description": "Tên model embedding (ví dụ: 'openai/text-embedding-3-small').",
                "default": "openai/text-embedding-3-small"
            }
        },
        "required": ["text"]
    }
)
def ninerouter_create_embeddings(text: str, model: str = "openai/text-embedding-3-small", **kwargs) -> Dict[str, Any]:
    """Tạo vector embeddings qua 9Router /v1/embeddings."""
    payload = {
        "model": model,
        "input": text,
    }
    try:
        res = _send_9router_request("/v1/embeddings", payload=payload)
        data = res.get("data", [])
        vector_dim = len(data[0].get("embedding", [])) if data else 0
        return {
            "model": model,
            "vector_dimension": vector_dim,
            "usage": res.get("usage", {}),
        }
    except Exception as e:
        return {"error": str(e), "model": model}


# ─── 6. 9Router Chat Direct ──────────────────────────────────────────────────

@export_skill(
    name="ninerouter_chat",
    description="Gửi truy vấn hoặc yêu cầu sinh mã nguồn/phân tích chuyên sâu đến một mô hình LLM cụ thể trên 9Router (Gemini, Claude, GPT, DeepSeek).",
    parameters_schema={
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "Nội dung câu hỏi hoặc yêu cầu dành cho mô hình."
            },
            "model": {
                "type": "string",
                "description": "Tên model cụ thể trên 9Router (ví dụ: 'ag/gemini-3.8-flash-low', 'ag/claude-sonnet-4-6').",
                "default": "ag/gemini-3.8-flash-low"
            },
            "system_prompt": {
                "type": "string",
                "description": "Chỉ thị hệ thống tùy chọn (System instruction).",
                "default": ""
            }
        },
        "required": ["prompt"]
    }
)
def ninerouter_chat(prompt: str, model: str = "ag/gemini-3.8-flash-low", system_prompt: str = "", **kwargs) -> Dict[str, Any]:
    """Gọi trực tiếp chat completion qua 9Router /v1/chat/completions."""
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": 1500,
        "stream": False,
    }
    try:
        res = _send_9router_request("/v1/chat/completions", payload=payload)
        reply = res.get("choices", [{}])[0].get("message", {}).get("content", "")
        return {
            "model": model,
            "reply": reply,
            "usage": res.get("usage", {}),
        }
    except Exception as e:
        return {"error": str(e), "model": model}


# ─── 7. Trình Tải & Cài Đặt Kỹ Năng Từ URL (Skill Installer) ─────────────────

@export_skill(
    name="install_skill_from_url",
    description="Tải tài liệu SKILL.md hoặc mã nguồn kỹ năng từ đường dẫn URL (như GitHub raw, Gist, web link), lưu vào kho lưu trữ kỹ năng 'skills/' và tự động nạp vào runtime hệ thống. Dùng khi người dùng yêu cầu 'tải skill về', 'thêm skill từ URL', 'đọc và thêm vào kho kỹ năng'.",
    parameters_schema={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Đường dẫn URL chứa file kỹ năng (ví dụ: raw.githubusercontent.com/.../SKILL.md hoặc .py)."
            },
            "skill_name": {
                "type": "string",
                "description": "Tên định danh kỹ năng hoặc thư mục lưu trữ (tùy chọn, tự động nhận diện nếu để trống).",
                "default": ""
            }
        },
        "required": ["url"]
    }
)
def install_skill_from_url(url: str, skill_name: str = "", **kwargs) -> Dict[str, Any]:
    """Tải và lưu trữ file kỹ năng từ URL vào kho lưu trữ hệ thống."""
    url = url.strip()
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            content = resp.read().decode("utf-8")
    except Exception as exc:
        return {"success": False, "error": f"Không thể tải từ URL '{url}': {exc}"}

    # Xác định tên kỹ năng và đường dẫn lưu
    skills_dir = settings.SKILLS_DIR
    target_name = skill_name.strip()
    if not target_name:
        # Lấy từ URL, ví dụ /skills/9router-chat/SKILL.md -> 9router-chat
        parts = [p for p in url.split("/") if p]
        if parts[-1].upper() == "SKILL.MD" and len(parts) >= 2:
            target_name = parts[-2]
        else:
            target_name = parts[-1].replace(".py", "").replace(".md", "")

    # Chuẩn hóa tên an toàn
    safe_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", target_name)
    skill_subfolder = skills_dir / safe_name
    skill_subfolder.mkdir(parents=True, exist_ok=True)
    target_file = skill_subfolder / "SKILL.md"

    try:
        target_file.write_text(content, encoding="utf-8")
        logger.info("[SkillInstaller] Đã tải và lưu thành công kỹ năng '%s' vào %s", safe_name, target_file)

        # Nạp lại plugins
        from core.plugin_manager import plugin_manager
        plugin_manager.load_plugins()

        return {
            "success": True,
            "skill_name": safe_name,
            "saved_path": str(target_file),
            "size_bytes": len(content),
            "message": f"Đã tải thành công tài liệu kỹ năng '{safe_name}' và lưu vào kho '{target_file}'."
        }
    except Exception as exc:
        return {"success": False, "error": f"Lỗi khi ghi file kỹ năng: {exc}"}
