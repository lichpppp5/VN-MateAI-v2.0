# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/config_loader.py
=====================
Pydantic v2 Settings model for VN-MateAI — Phase 22 Thin Client.

Responsibilities:
  - Validate runtime configuration from config.json.
  - Expose a single LLMConfig (base_url, model_name, api_key) for 9router/proxy.
  - Auto-generate a default config.json template if the file is absent.
  - Expose a single module-level `settings` singleton for import-anywhere use.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Resolve project root (works both in dev tree and PyInstaller bundle)
# ---------------------------------------------------------------------------
def _resolve_project_root() -> Path:
    """
    Thư mục gốc dự án — NGUỒN DUY NHẤT (mọi module dùng settings.PROJECT_ROOT).

    1. Biến môi trường VNMATEAI_PROJECT_ROOT (triển khai đặc thù).
    2. Bản đóng gói PyInstaller: thư mục chứa file thực thi.
    3. Đi lên từ vị trí file này tới thư mục có `main.py` và `web/` — không phụ
       thuộc file này nằm sâu bao nhiêu cấp (core/ hay src/mateai/config/).
    4. Thư mục làm việc hiện tại.
    """
    env = os.environ.get("VNMATEAI_PROJECT_ROOT", "").strip()
    if env:
        return Path(env).resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    for parent in Path(__file__).resolve().parents:
        if (parent / "main.py").is_file() and (parent / "web").is_dir():
            return parent
    return Path.cwd()


def _resolve_config_path() -> Path:
    data_dir = os.environ.get("VNMATEAI_DATA_DIR", "").strip()
    if data_dir:
        p = Path(data_dir) / "config.json"
        if p.is_file() or Path(data_dir).is_dir():
            return p
    root_cfg = _PROJECT_ROOT / "config.json"
    if root_cfg.is_symlink():
        try:
            return root_cfg.resolve()
        except OSError:
            pass
    return root_cfg


_PROJECT_ROOT = _resolve_project_root()

CONFIG_PATH: Path = _resolve_config_path()

_DEFAULT_CONFIG: dict = {
    "llm": {
        "base_url": "http://localhost:20128/v1",
        "router_model": "",
        "specialist_model": "",
        "model_name": "",
        "api_key": "sk-dummy",
        # Dual-mode routing (Phase 91)
        "routing_mode": "router",   # "router" | "direct" | "auto"
        "direct_url": "",           # URL trỏ thẳng vào model (LM Studio / Ollama)
        "direct_model": "",         # Tên model khi dùng direct mode
        "direct_api_key": "sk-dummy",
    },
    "auto_execute": False,
    "ASR_BACKEND": "google",
    "GROQ_API_KEY": "",
    "GROQ_BASE_URL": "https://api.groq.com/openai/v1",
    "MIC_AUTO_START": True,
    "HOST": "0.0.0.0",
    "PORT": 443,
    "LOG_LEVEL": "INFO",
}


def _ensure_config_file_exists() -> None:
    """Write a default config.json if the file does not yet exist."""
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(
            json.dumps(_DEFAULT_CONFIG, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.warning(
            "config.json not found — created default template at %s.",
            CONFIG_PATH,
        )


def _load_raw_config() -> dict:
    """Read and parse config.json, returning a plain dict."""
    _ensure_config_file_exists()
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("config.json must contain a JSON object at the top level.")
        from mateai.config.secret_box import decrypt_tree
        return decrypt_tree(raw)
    except json.JSONDecodeError as exc:
        logger.critical("config.json is malformed JSON: %s", exc)
        raise SystemExit(1) from exc


_CONFIG_LOCK = threading.RLock()


def read_raw_config(strict: bool = False) -> dict:
    """
    Nội dung config.json hiện tại (đọc MỚI mỗi lần, để thấy thay đổi từ portal).

    strict=False (mặc định, cho code chỉ ĐỌC): thiếu/hỏng file → {} — một mục cấu
    hình hỏng không được làm sập hội thoại. strict=True (cho code sắp GHI lại):
    ném lỗi thay vì trả {} — ghi đè file hỏng bằng bản rỗng sẽ mất toàn bộ cấu hình.
    """
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("config.json must contain a JSON object at the top level.")
        # Khoá bí mật lưu mã hoá trên đĩa (secret_box) — mọi nơi đọc qua đây thấy bản thật.
        from mateai.config.secret_box import decrypt_tree
        return decrypt_tree(raw)
    except FileNotFoundError:
        if strict:
            raise
        return {}
    except (OSError, ValueError) as exc:
        if strict:
            raise
        logger.warning("Không đọc được config.json (%s) — dùng mặc định.", exc)
        return {}


def get_config_section(name: str) -> dict:
    """Một mục cấp cao của config.json dưới dạng dict ({} nếu thiếu / không phải dict)."""
    section = read_raw_config().get(name)
    return section if isinstance(section, dict) else {}


def get_assistant_name() -> str:
    """
    Tên trợ lý AI — MỘT nguồn cho prompt, HUD, lời chào, robot.

    Đọc thẳng config.json vì `AppSettings` bỏ khoá lạ (`extra="ignore"`): trước
    đây `settings.AI_NAME` luôn None nên server và lời chào luôn nói "Ly Ly" dù
    người dùng đã đổi tên. Thứ tự giống giao diện: `persona.ai_name` → `AI_NAME`
    → `ASSISTANT_NAME` → "Ly Ly".
    """
    raw = read_raw_config()
    persona = raw.get("persona") if isinstance(raw.get("persona"), dict) else {}
    for value in (persona.get("ai_name"), raw.get("AI_NAME"), raw.get("ASSISTANT_NAME")):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "Ly Ly"


def write_raw_config(raw: dict) -> None:
    """
    Ghi config.json NGUYÊN TỬ: ghi file tạm cùng thư mục rồi os.replace. Mất điện
    hay lỗi giữa chừng không để lại file cụt; hai thread không xen kẽ nhau.
    """
    from mateai.config.secret_box import encrypt_tree
    on_disk = encrypt_tree(raw)          # khoá bí mật không bao giờ ghi dạng chữ thường
    with _CONFIG_LOCK:
        target_path = CONFIG_PATH.resolve()
        target_dir = target_path.parent
        target_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(target_dir), prefix=".config.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(on_disk, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, target_path)
            root_cfg = _PROJECT_ROOT / "config.json"
            if root_cfg != target_path and (not root_cfg.exists() or not root_cfg.is_symlink()):
                try:
                    root_cfg.unlink(missing_ok=True)
                    root_cfg.symlink_to(target_path)
                except OSError:
                    pass
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


def encrypt_existing_secrets() -> bool:
    """Chuyển khoá còn dạng chữ thường trong config.json sang mã hoá (gọi lúc khởi
    động). Trả True nếu đã ghi lại. Kiểm đọc-lại trước khi coi là xong."""
    from mateai.config import secret_box
    if not secret_box.enabled():
        return False
    with _CONFIG_LOCK:
        try:
            on_disk = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if not secret_box.has_plaintext_secrets(on_disk):
            return False
        plain = secret_box.decrypt_tree(on_disk)
        enc = secret_box.encrypt_tree(plain)
        if secret_box.decrypt_tree(enc) != plain:      # khoá hỏng -> KHÔNG ghi
            logger.error("Kiểm tra mã hoá thất bại — giữ nguyên config.json.")
            return False
        write_raw_config(plain)
    logger.info("Đã mã hoá các khoá bí mật trong config.json (khoá giải mã: certs/config_secret.key "
                "hoặc VNMATEAI_CONFIG_KEY).")
    return True


def update_config_section(name: str, updates: dict) -> dict:
    """Đọc-sửa-ghi một mục dưới khoá (strict). Trả toàn bộ cấu hình sau khi ghi."""
    with _CONFIG_LOCK:
        raw = read_raw_config(strict=True)
        section = raw.get(name)
        section = dict(section) if isinstance(section, dict) else {}
        section.update(updates)
        raw[name] = section
        write_raw_config(raw)
        return raw


# ---------------------------------------------------------------------------
# LLM Config (Phase 22 & Phase 40 — Dual-LLM Orchestration)
# ---------------------------------------------------------------------------


class LLMConfig(BaseModel):
    """Dual-model endpoint configuration for 9router (Gemini Router & Claude Specialist)."""
    # Trường "model_name" / "model_registry" là tên nghiệp vụ, không đụng namespace nội bộ pydantic.
    model_config = {"protected_namespaces": ()}
    base_url: str = Field(
        default="http://localhost:20128/v1",
        description="Base URL of the OpenAI-compatible proxy (9router, LMStudio, Ollama, etc.).",
    )
    #: Model registry (prompt cuối §79): {model_id: {status, privacy, use_cases, note}}.
    #: status: APPROVED / EXPERIMENTAL / DEPRECATED / BLOCKED. Model chưa đăng ký vẫn dùng được
    #: (hiện "UNREGISTERED"); BLOCKED không bao giờ được gọi; DEPRECATED chỉ làm dự phòng cuối.
    model_registry: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    router_models: List[str] = Field(
        default_factory=list,
        description="Priority list of router models for auto-fallback. Empty = no fallback; "
                    "populated from the live router when config is saved (Phase 68).",
    )
    specialist_models: List[str] = Field(
        default_factory=list,
        description="Priority list of specialist models for auto-fallback. Empty = no fallback; "
                    "populated from the live router when config is saved (Phase 68).",
    )
    router_model: str = Field(
        default="",
        description="Fast router model for primary conversational interaction (e.g. Gemini 3.8 Flash).",
    )
    specialist_model: str = Field(
        default="",
        description="High-capability specialist model for deep troubleshooting and analysis.",
    )
    model_name: str = Field(
        default="",
        description="Active or default model ID for backward compatibility.",
    )
    api_key: str = Field(
        default="sk-dummy",
        description="API key / secret for the proxy endpoint.",
    )

    # --------------- Phase 91: Dual-Mode Routing ---------------
    routing_mode: str = Field(
        default="router",
        description=(
            "LLM routing mode:\n"
            "  'router'  — qua 9router proxy (mặc định, hỗ trợ nhiều model).\n"
            "  'direct'  — gọi thẳng model (bỏ proxy, giảm latency ~150-300ms).\n"
            "  'auto'    — thử direct trước, fallback sang router nếu lỗi."
        ),
    )
    direct_url: str = Field(
        default="",
        description="Base URL khi dùng direct mode (e.g. http://localhost:1234/v1 cho LM Studio).",
    )
    direct_model: str = Field(
        default="",
        description="Tên model khi dùng direct mode (để trống = dùng model_name).",
    )
    direct_api_key: str = Field(
        default="sk-dummy",
        description="API key cho endpoint direct (thường là 'lm-studio' hoặc 'ollama').",
    )

    # --------------- Phase 94: Tri-Brain Specialized Architecture ---------------
    tri_brain_enabled: bool = Field(
        default=True,
        description="Kích hoạt kiến trúc 3 Bộ Não Chuyên Biệt (Controller, Voice, Operations).",
    )
    controller_model: str = Field(
        default="",
        description="Bộ Não 1: Model kiểm soát & điều phối ý định (Supervisor / Controller).",
    )
    voice_model: str = Field(
        default="",
        description="Bộ Não 2: Model giao tiếp & thoại siêu tốc không gánh tool (Voice & Linguistic).",
    )
    ops_model: str = Field(
        default="",
        description="Bộ Não 3: Model thực thi kỹ năng & vận hành hệ thống (Operations & Tool Specialist).",
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # Strip whitespace
            for f in ("base_url", "model_name", "router_model", "specialist_model", "api_key"):
                if f in data and isinstance(data[f], str):
                    data[f] = data[f].strip()

            # Phase 68: KHÔNG ghi cứng tên model của bất kỳ provider nào.
            #
            # Trước đây danh sách dự phòng ghi cứng `ag/...`. Khi provider đó
            # mất khoá hoặc hết tiền, mọi câu hỏi đều thử 3-4 model chết trước
            # khi tới model thật — mỗi lần một lần gọi ra ngoài rồi báo lỗi.
            # Người dùng thấy "chậm" trong khi thực ra hệ thống đang lãng phí
            # thời gian gọi vào những model không tồn tại.
            #
            # Nay để trống. Danh sách dự phòng lấy từ router lúc lưu cấu hình
            # (xem _router_model_pool), và router chỉ liệt kê model thật. Không
            # có cấu hình thì không có model dự phòng — thà báo lỗi thật còn hơn
            # gọi vào chỗ chết.
            DEFAULT_ROUTER: list = []
            DEFAULT_SPECIALIST: list = []

            # Normalize router_models (convert string to list if necessary)
            rm = data.get("router_models")
            candidate_rm: List[str] = []
            if isinstance(rm, str) and rm.strip():
                candidate_rm = [rm.strip()]
            elif isinstance(rm, list):
                candidate_rm = [str(x).strip() for x in rm if str(x).strip()]

            # Always ensure model_name/router_model is at the front if provided
            active_m = data.get("model_name") or data.get("router_model")
            final_rm: List[str] = []
            if active_m and isinstance(active_m, str) and active_m.strip():
                final_rm.append(active_m.strip())

            for m in candidate_rm:
                if m not in final_rm:
                    final_rm.append(m)

            # Crucial: Always merge in default reliable fallbacks to guarantee auto-fallback never has 0 fallbacks!
            for dm in DEFAULT_ROUTER:
                if dm not in final_rm:
                    final_rm.append(dm)

            data["router_models"] = final_rm
            data["router_model"] = final_rm[0] if final_rm else ""
            data["model_name"] = final_rm[0] if final_rm else ""

            # Normalize specialist_models
            sm = data.get("specialist_models")
            candidate_sm: List[str] = []
            if isinstance(sm, str) and sm.strip():
                candidate_sm = [sm.strip()]
            elif isinstance(sm, list):
                candidate_sm = [str(x).strip() for x in sm if str(x).strip()]

            active_sm = data.get("specialist_model")
            final_sm: List[str] = []
            if active_sm and isinstance(active_sm, str) and active_sm.strip():
                final_sm.append(active_sm.strip())

            for m in candidate_sm:
                if m not in final_sm:
                    final_sm.append(m)

            for dm in DEFAULT_SPECIALIST:
                if dm not in final_sm:
                    final_sm.append(dm)

            # Không cấu hình specialist thì dùng tạm model chính, tránh IndexError
            # khi config mới tạo từ config.example.json (specialist_models rỗng).
            data["specialist_models"] = final_sm
            data["specialist_model"] = final_sm[0] if final_sm else data["model_name"]

            # Ensure base_url ends with /v1
            bu = data.get("base_url", "")
            if bu and not bu.rstrip("/").endswith("/v1"):
                data["base_url"] = bu.rstrip("/") + "/v1"
        return data


# ---------------------------------------------------------------------------
# Security & Telegram configs (unchanged)
# ---------------------------------------------------------------------------


class SecurityConfig(BaseModel):
    """Enterprise Zero-Trust Security Configuration."""
    #: Vai trò BẮT BUỘC dùng MFA (vd ["admin"]). Rỗng (mặc định) = MFA tuỳ chọn. Người thuộc vai trò này chưa bật MFA
    #: đăng nhập xong chỉ được vào màn cài MFA (token `mfa_enroll`), chưa dùng được hệ thống.
    require_mfa_roles: List[str] = Field(default_factory=list)
    #: Giới hạn tần suất (prompt cuối §97) — mỗi người; 0 = tắt. Xem application/security/rate_limit.
    rate_limits: Dict[str, int] = Field(default_factory=lambda: {
        "voice_turns_per_min": 30, "agent_turns_per_min": 40, "ws_voice_sessions_per_user": 5})
    forbidden_keywords: List[str] = Field(
        default_factory=lambda: [
            "rmdir /s", "format c:", "drop database", "del /f /s",
            "Remove-Item -Recurse", "net user", "shutdown /r",
            "cipher /w", "mkfs", ":(){ :|:& };:", "eval(", "exec("
        ]
    )
    require_confirmation_actions: List[str] = Field(
        default_factory=lambda: [
            "kill_process", "run_powershell_command", "manage_windows_service",
            "deploy_skill", "delete_records", "write_file", "delete_item"
        ]
    )
    protected_directories: List[str] = Field(
        default_factory=lambda: [
            "C:\\Windows", "C:\\Program Files", "/System", "/etc", "/var", "/usr"
        ]
    )
    mask_patterns: List[str] = Field(
        default_factory=lambda: [
            r"(?i)(?:password|passwd|pwd)\s*[:=]\s*['\"]?([^\s\"',;]+)",
            r"(?i)(?:api[_-]?key|secret[_-]?key|access[_-]?token)\s*[:=]\s*['\"]?([^\s\"',;]+)",
            r"(?i)bearer\s+([a-zA-Z0-9_\-\.]{20,})",
            r"(?i)(?:postgres|mysql|mongodb|redis|mssql):\/\/[^\s\"']+",
            r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3})\b"
        ]
    )


#: Tác vụ L5 mặc định — không bao giờ chạy qua AI, kể cả admin, kể cả đã duyệt
#: (hành động phá huỷ / tài chính / cài mã từ ngoài). Doanh nghiệp thêm được, xem
#: `docs/autonomy/autonomy-model.md` §2.
DEFAULT_NEVER_AUTONOMOUS = [
    "delete_item", "delete_records", "drop_database", "wipe_system", "format_drive",
    "wipe_all_data", "execute_financial_transfer", "install_skill_from_url",
]


class AutonomyConfig(BaseModel):
    """Giới hạn tự trị của AI — đọc ở cổng kiểm soát (`policy_engine`), ngoài LLM."""
    #: Công tắc toàn cục: chỉ còn tác vụ chỉ đọc (rủi ro 1). §95–§96.
    kill_switch: bool = False
    #: Tác nhân bị tắt (agent_id, vd "VN-MATEAI-TELEGRAM") — mọi tool đều bị từ chối.
    disabled_agents: List[str] = Field(default_factory=list)
    #: Tool bị tắt tạm thời.
    disabled_tools: List[str] = Field(default_factory=list)
    #: L5: không bao giờ chạy qua AI.
    never_autonomous_tools: List[str] = Field(default_factory=lambda: list(DEFAULT_NEVER_AUTONOMOUS))
    #: Uỷ quyền "duyệt rồi nhớ" (L4) hết hạn sau số ngày này.
    approval_grant_ttl_days: int = Field(default=30, ge=1, le=365)
    #: Ngân sách mỗi lượt agent (§35).
    max_agent_seconds: float = Field(default=180.0, ge=10.0, le=3600.0)
    max_tool_calls_per_turn: int = Field(default=12, ge=1, le=100)
    #: Một tool lỗi quá số lần này trong cùng lượt -> không chạy tiếp, leo thang (§55, §158).
    max_tool_failures_per_turn: int = Field(default=3, ge=1, le=20)
    #: Chế độ khẩn cấp (§54): AI tự chạy (không người duyệt) quá số hành động có tác dụng
    #: phụ này trong 60 s -> tự bật kill switch (chỉ đọc), chờ người tắt. 0 = tắt cơ chế.
    emergency_max_actions_per_minute: int = Field(default=30, ge=0, le=1000)
    #: Email gateway: tự trả lời ra ngoài chỉ khi bật và người gửi thuộc miền được phép (§148).
    email_auto_reply: bool = False
    email_auto_reply_domains: List[str] = Field(default_factory=list)


class DevFleetConfig(BaseModel):
    """Module ngoại vi Dev Fleet: VN-MateAI điều phối cụm Dev QUA Ubuntu Master (docs/integrations/dev-fleet.md).
    Mặc định TẮT — tắt thì mọi chức năng khác của hệ thống không bị ảnh hưởng."""
    enabled: bool = False
    #: disabled | read_only | controlled | autonomous. `autonomous` hiện hành xử như `controlled`
    #: (tự trị có giới hạn là Phase E, chưa làm).
    mode: str = "read_only"
    #: Địa chỉ Master Control API (https://master.congty.local:8443). KHÔNG bao giờ trỏ thẳng vào từng Mac.
    endpoint: str = ""
    api_token: str = ""
    tls_verify: bool = True
    ca_bundle: str = ""
    timeout_s: float = Field(default=5.0, ge=1.0, le=60.0)
    #: Quá ngưỡng này kể từ lần thấy gần nhất: ONLINE -> STALE; quá `offline_after_s`: OFFLINE.
    stale_after_s: float = Field(default=60.0, ge=5.0, le=3600.0)
    offline_after_s: float = Field(default=180.0, ge=10.0, le=86400.0)
    #: Bộ nhớ đệm đọc trạng thái (giây) — tránh dồn truy vấn vào Master.
    cache_ttl_s: float = Field(default=5.0, ge=0.0, le=300.0)
    #: Worker bị tắt thủ công (kill switch theo máy).
    disabled_workers: List[str] = Field(default_factory=list)
    #: Một tác vụ không có tiến triển quá số giây này bị coi là treo.
    stuck_after_s: float = Field(default=900.0, ge=30.0, le=86400.0)
    #: Hạn thuê workspace / nhánh (giây); hết hạn mà không gia hạn thì giải phóng.
    lease_ttl_s: float = Field(default=1800.0, ge=30.0, le=86400.0)


class MonitoringConfig(BaseModel):
    """Giám sát hạ tầng qua Prometheus / Grafana (nguồn dữ liệu có `monitor_type`). Không có nguồn nào thì không làm gì."""
    enabled: bool = True
    interval_s: float = Field(default=60.0, ge=10.0, le=3600.0)
    cache_ttl_s: float = Field(default=20.0, ge=0.0, le=600.0)
    #: Mức tối thiểu của cảnh báo ngoài hệ thống để mở SỰ CỐ + báo kênh cảnh báo: critical | warning.
    incident_min_severity: str = "critical"
    max_incidents_per_cycle: int = Field(default=10, ge=1, le=100)
    #: Token (Bearer) cho `GET /metrics` — số đo của chính VN-MateAI cho Prometheus. Trống = endpoint TẮT (404).
    metrics_token: str = ""
    #: Ngưỡng (%) cho CPU / RAM / đĩa suy ra từ node_exporter / windows_exporter.
    cpu_warn: float = 85.0
    cpu_crit: float = 95.0
    memory_warn: float = 90.0
    memory_crit: float = 95.0
    disk_warn: float = 85.0
    disk_crit: float = 95.0


class PlaybookTriggersConfig(BaseModel):
    """Kịch bản tự kích hoạt (lịch / cảnh báo / webhook) — docs/integrations/playbooks.md."""
    enabled: bool = True
    #: Trần lượt tự kích hoạt toàn hệ thống mỗi giờ (chống bão cảnh báo).
    max_auto_runs_per_hour: int = Field(default=20, ge=1, le=500)


class AgentGatewayConfig(BaseModel):
    """Cổng nối agent / MCP server có sẵn của doanh nghiệp (docs/integrations/agent-gateway.md)."""
    #: Cho phép MCP server kiểu stdio (máy chủ CHẠY một chương trình cục bộ). Mặc định tắt: bật khi thật sự cần.
    allow_stdio: bool = False
    #: Số công cụ tối đa nhận từ một MCP server (mỗi công cụ là một mục trong danh mục đưa cho model).
    max_tools_per_server: int = Field(default=64, ge=1, le=500)


class LlmOpsConfig(BaseModel):
    """Ngân sách dùng AI (token / tiền). 0 = không giới hạn. Mặc định chỉ CẢNH BÁO, chưa chặn."""
    #: off: không theo dõi hạn mức | warn: cảnh báo khi gần / vượt | block: từ chối lượt AI mới khi đã vượt
    budget_mode: str = "warn"
    daily_token_limit: int = Field(default=0, ge=0)
    user_daily_token_limit: int = Field(default=0, ge=0)
    #: Tiền chỉ tính cho model ĐÃ khai giá trong llm.model_registry (không đoán giá).
    daily_cost_limit: float = Field(default=0.0, ge=0.0)
    monthly_cost_limit: float = Field(default=0.0, ge=0.0)
    #: Tỉ lệ dùng bắt đầu cảnh báo (0.8 = 80 %).
    warn_ratio: float = Field(default=0.8, gt=0.0, le=1.0)
    #: Số lượt tối đa trong một lần chạy bộ đánh giá.
    eval_max_cases: int = Field(default=50, ge=1, le=500)
    #: Xin token ở cuối stream giọng nói (`stream_options.include_usage`). Tắt nếu router / model từ chối tham số này.
    stream_usage: bool = True


class SsoConfig(BaseModel):
    """Đăng nhập một lần OpenID Connect (Azure AD / Entra, Keycloak, Okta, Google Workspace…). Mặc định TẮT."""
    enabled: bool = False
    display_name: str = "Đăng nhập bằng tài khoản công ty"
    issuer: str = ""                    # vd https://login.microsoftonline.com/<tenant>/v2.0 · https://keycloak.congty/realms/vn
    client_id: str = ""
    client_secret: str = ""             # để trống nếu IdP dùng public client + PKCE
    redirect_uri: str = ""              # https://<máy chủ>/api/v1/sso/callback — phải trùng khai báo ở IdP
    scopes: str = "openid profile email"
    username_claim: str = "email"
    name_claim: str = "name"
    groups_claim: str = "groups"
    #: Nhóm / role của IdP -> vai trò VN-MateAI (admin | manager | viewer). KHÔNG có ánh xạ = vai trò mặc định (không bao giờ tự là admin).
    role_map: Dict[str, str] = Field(default_factory=dict)
    default_role: str = "viewer"
    allowed_email_domains: List[str] = Field(default_factory=list)
    auto_provision: bool = True
    verify_ssl: bool = True
    ca_bundle: str = ""


class TelegramConfig(BaseModel):
    """Telegram Gateway and Alerting Configuration (Phase 18)."""
    bot_token: str = Field(default="", description="Telegram Bot API Token from @BotFather.")
    admin_chat_ids: List[str] = Field(default_factory=list, description="List of authorized Telegram admin chat IDs.")
    incident_group_id: str = Field(default="", description="Telegram Group/Channel ID for incident alerts.")


class MemoryDbConfig(BaseModel):
    """Phase 37.5: Portable & Scalable ChromaDB Vector Memory Configuration."""
    mode: str = Field(default="local", description="'local' or 'microservice'")
    local_path: str = Field(default="./storage/vector_db", description="Local directory path for PersistentClient")
    microservice_host: str = Field(default="http://localhost:8000", description="Microservice host URL or hostname")
    microservice_port: int = Field(default=8000, description="Microservice port")


# ---------------------------------------------------------------------------
# Main AppSettings Model
# ---------------------------------------------------------------------------


class AppSettings(BaseSettings):
    """
    Validated runtime configuration for VN-MateAI — Phase 22 Thin Client.
    """

    model_config = SettingsConfigDict(
        env_prefix="VNMATE_",
        populate_by_name=True,
        extra="ignore",
    )

    # Phase 22: Single LLM Endpoint
    llm: LLMConfig = Field(default_factory=LLMConfig)

    # Auto-execute
    auto_execute: bool = Field(
        default=False,
        description="Auto-execute newly synthesised code without confirmation messagebox.",
    )

    # Phase 9: Enterprise Zero-Trust Security
    security: SecurityConfig = Field(default_factory=SecurityConfig)

    # Phiên bản sản phẩm: internal | commercial (mateai.config.edition — VNMATEAI_EDITION ưu tiên)
    edition: str = Field(default="internal")
    # Bản thương mại: kỹ năng demo vẫn bật cho khách hàng này (có nguồn dữ liệu thật)
    edition_allow_skills: List[str] = Field(default_factory=list)

    # Supervisor control plane: kill switch, L5, budgets (docs/autonomy/*)
    autonomy: AutonomyConfig = Field(default_factory=AutonomyConfig)

    # Phase 18: Telegram Gateway
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)

    # Giám sát hạ tầng (Prometheus / Grafana)
    monitoring: MonitoringConfig = Field(default_factory=MonitoringConfig)

    # Đăng nhập một lần OIDC
    sso: SsoConfig = Field(default_factory=SsoConfig)

    # Ngân sách AI + đánh giá chất lượng
    llmops: LlmOpsConfig = Field(default_factory=LlmOpsConfig)

    # Cổng agent (MCP server ngoài)
    agent_gateway: AgentGatewayConfig = Field(default_factory=AgentGatewayConfig)

    # Kịch bản tự kích hoạt
    playbook_triggers: PlaybookTriggersConfig = Field(default_factory=PlaybookTriggersConfig)

    # Dev Fleet: điều phối cụm Dev (Ubuntu Master -> Mac mini/OpenClaw) — mặc định tắt
    dev_fleet: DevFleetConfig = Field(default_factory=DevFleetConfig)

    # Phase 28: Enterprise Reporting & Template Engine
    report_templates: Dict[str, str] = Field(default_factory=dict)

    # Phase 37.5: Vector Database & Long-term Memory
    memory_db: MemoryDbConfig = Field(default_factory=MemoryDbConfig)

    # Legacy compatibility fields (synced from llm config)
    API_KEY: str = Field(default="")
    BASE_URL: str = Field(default="http://localhost:20128/v1")
    MODEL_NAME: str = Field(default="")
    AUTO_EXECUTE_UNVERIFIED_CODE: bool = Field(default=False)

    # Server settings
    HOST: str = Field(default="0.0.0.0", description="Uvicorn bind host.")
    PORT: int = Field(default=443, ge=1, le=65535, description="Uvicorn bind port.")
    IOT_PORT: int = Field(default=8000, ge=1, le=65535,
                          description="Cổng WS không TLS cho mạch ESP32/Xiaozhi (chỉ đường thiết bị + probe).")
    DISCOVERY_PORT: int = Field(default=8888, ge=1, le=65535,
                                description="Cổng UDP beacon để robot mới tự tìm máy chủ.")
    LOG_LEVEL: str = Field(default="INFO", description="Python logging level.")
    REDIS_URL: str = Field(default="", description="Kho trạng thái dùng chung (§67). Trống = RAM một tiến trình.")
    DATABASE_URL: str = Field(default="", description="postgresql://… -> nguồn sự thật là PostgreSQL (§65). Trống = SQLite.")
    OTEL_EXPORTER: str = Field(default="none", pattern="^(none|console|otlp)$",
                               description="Trace OpenTelemetry (§93): none / console / otlp.")
    LOG_FORMAT: str = Field(default="text", pattern="^(text|json)$",
                            description="text: đọc bằng mắt; json: một JSON mỗi dòng (§95).")

    # Audio Pipeline Settings
    ASR_BACKEND: str = Field(default="local_whisper", description="ASR backend (local_whisper, google, groq, whisper).")
    GROQ_API_KEY: str = Field(default="", description="Groq API key.")
    GROQ_BASE_URL: str = Field(default="https://api.groq.com/openai/v1")
    MIC_AUTO_START: bool = Field(
        default=True,
        description="Auto-start microphone background wake-word listening on application launch.",
    )
    TTS_VOICE: str = Field(
        default="vi-VN-HoaiMyNeural",
        description="Microsoft Neural TTS voice for Vietnamese (vi-VN-HoaiMyNeural or vi-VN-NamMinhNeural).",
    )
    TTS_RATE: str = Field(
        default="+15%",
        description="TTS speed rate adjustment (e.g. +15% for brisk, natural assistant tone).",
    )

    # Derived paths (computed post-init)
    PROJECT_ROOT: Optional[Path] = Field(default=None, exclude=True)
    SKILLS_DIR: Optional[Path] = Field(default=None, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def _normalize_config(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        # Normalize auto_execute
        if "auto_execute" in data:
            data["AUTO_EXECUTE_UNVERIFIED_CODE"] = bool(data["auto_execute"])
        elif "AUTO_EXECUTE_UNVERIFIED_CODE" in data:
            data["auto_execute"] = bool(data["AUTO_EXECUTE_UNVERIFIED_CODE"])

        # --- Sync audio block → top-level TTS_RATE / TTS_VOICE ---
        # Frontend lưu audio.speech_rate (int) và audio.tts_voice (str).
        # AppSettings chỉ có TTS_RATE (str) và TTS_VOICE (str) ở top-level.
        # Nếu thiếu bước này, _get_tts_rate() luôn trả default "+15%".
        audio_block = data.get("audio")
        if isinstance(audio_block, dict):
            # speech_rate: int → "+15%" format
            if "speech_rate" in audio_block and "TTS_RATE" not in data:
                sr = audio_block["speech_rate"]
                try:
                    sr_int = int(sr)
                    data["TTS_RATE"] = ("+" if sr_int >= 0 else "") + str(sr_int) + "%"
                except (TypeError, ValueError):
                    pass
            # tts_voice string sync
            if "tts_voice" in audio_block and "TTS_VOICE" not in data:
                data["TTS_VOICE"] = audio_block["tts_voice"]
            # asr_engine sync
            if "asr_engine" in audio_block and "ASR_BACKEND" not in data:
                data["ASR_BACKEND"] = audio_block["asr_engine"]

        # --- BACKWARD COMPAT: migrate old 3-tier routing → new llm block ---
        if "llm" not in data:
            # Try to extract from old routing/router structure
            routing = data.get("routing") or data.get("router") or {}
            primary = {}
            if isinstance(routing, dict):
                primary = routing.get("primary", {})
            if isinstance(primary, dict) and primary:
                data["llm"] = {
                    "base_url": primary.get("api_base") or primary.get("base_url") or "http://localhost:20128/v1",
                    "model_name": primary.get("provider_model") or primary.get("model") or "",
                    "api_key": primary.get("api_key") or "sk-dummy",
                }
            elif data.get("MODEL_NAME") or data.get("API_KEY"):
                data["llm"] = {
                    "base_url": data.get("BASE_URL", "http://localhost:20128/v1"),
                    "model_name": data.get("MODEL_NAME", ""),
                    "api_key": data.get("API_KEY", "sk-dummy"),
                }

        # --- Secret overrides from environment (Zero-Trust) ---
        # Cho phép giữ khóa API/token NGOÀI config.json, qua biến môi trường.
        # Biến môi trường luôn thắng giá trị trong file.
        llm_block = data.get("llm")
        if isinstance(llm_block, dict):
            env_llm_key = os.getenv("VNMATEAI_LLM_API_KEY", "").strip()
            if env_llm_key:
                llm_block["api_key"] = env_llm_key
        env_legacy_key = os.getenv("VNMATEAI_LLM_API_KEY", "").strip()
        if env_legacy_key:
            data["API_KEY"] = env_legacy_key

        env_groq = os.getenv("GROQ_API_KEY", "").strip()
        if env_groq:
            data["GROQ_API_KEY"] = env_groq

        sso_block = data.get("sso")
        env_sso = os.getenv("VNMATEAI_SSO_CLIENT_SECRET", "").strip()
        if env_sso:
            if not isinstance(sso_block, dict):
                sso_block = data["sso"] = {}
            sso_block["client_secret"] = env_sso

        mon_block = data.get("monitoring")
        env_metrics = os.getenv("VNMATEAI_METRICS_TOKEN", "").strip()
        if env_metrics:
            if not isinstance(mon_block, dict):
                mon_block = data["monitoring"] = {}
            mon_block["metrics_token"] = env_metrics

        fleet_block = data.get("dev_fleet")
        if isinstance(fleet_block, dict):
            env_fleet = os.getenv("VNMATEAI_DEV_FLEET_TOKEN", "").strip()
            if env_fleet:
                fleet_block["api_token"] = env_fleet

        telegram_block = data.get("telegram")
        if isinstance(telegram_block, dict):
            env_tg = os.getenv("VNMATEAI_TELEGRAM_BOT_TOKEN", "").strip()
            if env_tg:
                telegram_block["bot_token"] = env_tg

        return data

    @field_validator("ASR_BACKEND")
    @classmethod
    def _validate_asr_backend(cls, v: str) -> str:
        if not v or not str(v).strip():
            return "google"
        # Phase 73: gỡ "mock" — backend giả lập trả câu mẫu theo độ dài byte âm
        # thanh, khiến hệ thống tưởng người dùng đã nói câu đó.
        valid = {"local_whisper", "whisper", "groq", "google"}
        lower = str(v).strip().lower()
        if lower == "openai":
            return "whisper"
        if lower == "mock":
            logger.warning("ASR_BACKEND='mock' đã bị gỡ (Phase 73), chuyển về mặc định 'google'.")
            return "google"
        if lower not in valid:
            logger.warning("Giá trị ASR_BACKEND '%s' không hợp lệ, chuyển về mặc định 'google'", v)
            return "google"
        return lower

    @field_validator("LOG_LEVEL")
    @classmethod
    def _validate_log_level(cls, v: str) -> str:
        if not v or not str(v).strip():
            return "INFO"
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = str(v).strip().upper()
        if upper not in valid:
            return "INFO"
        return upper

    def model_post_init(self, __context: object) -> None:
        """Compute derived paths and sync legacy fields after validation."""
        object.__setattr__(self, "PROJECT_ROOT", _PROJECT_ROOT)
        object.__setattr__(self, "SKILLS_DIR", _PROJECT_ROOT / "skills")
        # Sync legacy fields from llm config
        object.__setattr__(self, "MODEL_NAME", self.llm.model_name)
        object.__setattr__(self, "API_KEY", self.llm.api_key)
        object.__setattr__(self, "BASE_URL", self.llm.base_url)
        object.__setattr__(self, "AUTO_EXECUTE_UNVERIFIED_CODE", self.auto_execute)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------


def _build_settings() -> AppSettings:
    raw = _load_raw_config()
    return AppSettings(**raw)


def _config_stamp() -> Optional[tuple]:
    try:
        st = CONFIG_PATH.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


#: Dấu (mtime, size) của config.json ở lần nạp gần nhất — cho `reload_settings_if_changed`.
_loaded_stamp: Optional[tuple] = None


def reload_settings_if_changed() -> AppSettings:
    """Nạp lại chỉ khi config.json đã đổi. Vòng hỏi tin Telegram gọi mỗi ~10 s: nạp lại vô điều kiện = đọc + giải mã
    config.json liên tục và một dòng "AppSettings reloaded" mỗi vòng làm ngập nhật ký."""
    stamp = _config_stamp()
    if stamp is not None and stamp == _loaded_stamp:
        return settings
    return reload_settings()


def reload_settings() -> AppSettings:
    """
    Re-read config.json and reload the in-memory settings singleton in-place.
    Ensures hot updates without server restart.
    """
    global settings, _loaded_stamp
    _loaded_stamp = _config_stamp()
    raw = _load_raw_config()
    new_s = AppSettings(**raw)
    for k, v in new_s.__dict__.items():
        object.__setattr__(settings, k, v)
    logger.info(
        "AppSettings reloaded successfully. LLM: %s → %s",
        settings.llm.model_name,
        settings.llm.base_url,
    )
    return settings


_loaded_stamp = _config_stamp()
settings: AppSettings = _build_settings()

from mateai.config.log_setup import configure_logging, default_log_file  # noqa: E402

# Một cấu hình log cho tiến trình (prompt cuối §95): request_id + che bí mật; LOG_FORMAT=json
# cho hệ thống thu log. VNMATEAI_LOG_FORMAT ghi đè cấu hình. Bản ra tệp: logs/vnmateai.log (VNMATEAI_LOG_FILE).
configure_logging(settings.LOG_LEVEL, os.environ.get("VNMATEAI_LOG_FORMAT") or settings.LOG_FORMAT,
                  log_file=default_log_file(_PROJECT_ROOT))
