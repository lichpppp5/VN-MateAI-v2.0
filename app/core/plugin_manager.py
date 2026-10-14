# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/plugin_manager.py
======================
Hot-reloadable Plugin Loader for VN-MateAI.

Responsibilities:
  - Scan the `skills/` directory for Python skill modules.
  - Dynamically import / reload each module using importlib.
  - Detect functions decorated with @export_skill(...) via `inspect`.
  - Maintain an in-memory skill registry and persist it to skills/registry.json.
  - Build the OpenAI Function-Calling `tools` array consumed by llm_engine.
  - Execute skills by name with standardised result envelopes.

Design Notes:
  - Thread-safe: all mutable state is guarded by a threading.RLock.
  - Zero-Vision: no screenshot, no GUI injection — pure structural automation.
"""

from __future__ import annotations

import asyncio
import ast
import importlib
import importlib.util
import inspect
import json
import logging
import re
import sys
import threading
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from mateai.config.loader import settings


def _call_with_com(func: Callable[..., Any], kwargs: Dict[str, Any]) -> Any:
    """Gọi func trong thread hiện tại, khởi tạo COM cho thread này nếu có (Windows).

    Skill dùng Excel (win32com) hay WMI cần COM đã khởi tạo trên CHÍNH thread
    gọi nó. Thread pool tái sử dụng thread, nên Init/Uninit theo cặp mỗi lần.
    """
    try:
        import pythoncom  # type: ignore[import]
    except ImportError:
        return func(**kwargs)
    pythoncom.CoInitialize()
    try:
        return func(**kwargs)
    finally:
        pythoncom.CoUninitialize()


async def run_blocking(func: Callable[..., Any], **kwargs: Any) -> Any:
    """
    Chạy một hàm ĐỒNG BỘ (skill, LLM client đồng bộ, hộp thoại...) ngoài event loop.

    Trước đây skill đồng bộ chạy thẳng trên event loop: một lệnh PowerShell
    hay một lần gọi LLM đồng bộ 60 s làm đứng mọi kênh voice/WebSocket của
    mọi người dùng trong lúc đó.
    """
    return await asyncio.to_thread(_call_with_com, func, kwargs)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Decorator used by skill modules to self-register
# ---------------------------------------------------------------------------

# Global registry used by the decorator at import time.
# Key: fully-qualified function reference, Value: metadata dict.
_DECORATOR_STAGING: Dict[Callable, Dict[str, Any]] = {}


#: Mức phân loại dữ liệu (prompt cuối §62) — cấp bảo mật tối thiểu người hỏi phải có ở
#: `security_guard.CLEARANCE_FOR_CLASSIFICATION`.
DATA_CLASSIFICATIONS = ("PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED")


def export_skill(
    name: str,
    description: str,
    parameters_schema: Dict[str, Any],
    *,
    data_classification: str = "INTERNAL",
    reversible: Optional[bool] = None,
    output_schema: Optional[Dict[str, Any]] = None,
    data_scope: Optional[str] = None,
    verification_required: Optional[bool] = None,
    needs_desktop: Optional[bool] = None,
) -> Callable:
    """
    Decorator that marks a function as an exportable skill tool.

    Usage (inside any skills/*.py file):

        from core.plugin_manager import export_skill

        @export_skill(
            name="manage_windows_service",
            description="Start, stop, or restart a Windows service.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "service_name": {"type": "string", "description": "Service name"},
                    "action":       {"type": "string", "enum": ["start", "stop", "restart"]},
                },
                "required": ["service_name", "action"],
            },
        )
        def manage_windows_service(service_name: str, action: str) -> dict:
            ...
    """

    if data_classification not in DATA_CLASSIFICATIONS:
        raise ValueError(f"data_classification phải thuộc {DATA_CLASSIFICATIONS}, nhận '{data_classification}'")
    # Tool contract (prompt cuối §39 / §50): mức dữ liệu, có hoàn tác được không, schema kết
    # quả, phạm vi dữ liệu. Không đưa vào schema gửi LLM — chỉ cổng chính sách đọc.
    contract = {
        "data_classification": data_classification,
        "reversible": reversible,
        "output_schema": output_schema,
        "data_scope": data_scope,
        "verification_required": verification_required,
        "needs_desktop": needs_desktop,
    }

    def decorator(func: Callable) -> Callable:
        if contract["needs_desktop"] is None:
            contract["needs_desktop"] = _uses_desktop(func)
        _DECORATOR_STAGING[func] = {
            "name": name,
            "description": description,
            "parameters": parameters_schema,
        }
        # Attach metadata directly to the function for later inspect-based discovery
        func.__skill_meta__ = {  # type: ignore[attr-defined]
            "name": name,
            "description": description,
            "parameters": parameters_schema,
            "contract": contract,
        }
        return func

    return decorator


#: Mã gọi tới màn hình / phiên đăng nhập (mở trình duyệt, ứng dụng, chuột-bàn phím, chụp màn hình, clipboard).
_DESKTOP_CALLS = re.compile(r"webbrowser\.|pyautogui|pynput|os\.startfile|ImageGrab|pyperclip|win32clipboard|win32gui|mss|Start-Process")


def _uses_desktop(func: Callable) -> bool:
    """Skill không khai `needs_desktop` (vd skill AI tự sinh): suy ra từ mã của chính hàm."""
    try:
        return bool(_DESKTOP_CALLS.search(inspect.getsource(func)))
    except (OSError, TypeError):
        return False


# ---------------------------------------------------------------------------
# PluginManager
# ---------------------------------------------------------------------------


_SAFE_TOP_LEVEL = (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                   ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Pass)


def _is_safe_top_level(node: ast.stmt) -> bool:
    if isinstance(node, _SAFE_TOP_LEVEL):
        return True
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):   # docstring
        return True
    if isinstance(node, ast.If) and "__name__" in ast.unparse(node.test):    # khối chạy tay
        return True
    if isinstance(node, ast.Try):                                            # import tuỳ chọn
        blocks = [node.body, node.orelse, node.finalbody] + [h.body for h in node.handlers]
        return all(_is_safe_top_level(x) for b in blocks for x in b)
    return False


def top_level_side_effects(path: Path) -> List[str]:
    """Câu lệnh cấp module KHÔNG phải import / định nghĩa / gán (vd `with open(...)`,
    `print(...)`, vòng lặp): import tệp là chạy chúng — trước mọi cổng chính sách.
    Trả "dòng:loại:đoạn mã"; rỗng = an toàn để nạp. Tệp lỗi cú pháp coi là vi phạm."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError, OSError) as exc:
        return [f"0:{type(exc).__name__}:{exc}"]
    return [f"{n.lineno}:{type(n).__name__}:{ast.unparse(n).splitlines()[0][:60]}"
            for n in tree.body if not _is_safe_top_level(n)]


class PluginManager:
    """
    Manages discovery, import, hot-reload, and execution of skill plugins.
    """

    def __init__(self) -> None:
        self._lock: threading.RLock = threading.RLock()
        # Map skill_name -> {"func": Callable, "meta": dict, "module": str}
        self._registry: Dict[str, Dict[str, Any]] = {}
        self._skills_dir: Path = settings.SKILLS_DIR  # type: ignore[assignment]
        self._registry_json_path: Path = self._skills_dir / "registry.json"
        #: Tệp trong skills/ bị từ chối nạp vì có lệnh cấp module (tên tệp -> vi phạm).
        self.rejected_modules: Dict[str, List[str]] = {}
        #: Kỹ năng ĐỘNG (không có tệp, vd công cụ của MCP server ngoài): giữ qua load_plugins(), không ghi registry.json.
        self._dynamic: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_plugins(self) -> int:
        """
        Scan skills/, import/reload each .py module, discover @export_skill
        decorated functions, and synchronise registry.json.

        Returns the number of skills successfully loaded.
        """
        if not self._skills_dir.exists():
            logger.warning("skills/ directory not found at %s — creating.", self._skills_dir)
            self._skills_dir.mkdir(parents=True, exist_ok=True)

        # Invalidate import caches to immediately detect new or modified .py files on disk
        importlib.invalidate_caches()

        loaded_count = 0

        # Ensure the skills package is on sys.path so importlib can find it.
        project_root_str = str(settings.PROJECT_ROOT)
        if project_root_str not in sys.path:
            sys.path.insert(0, project_root_str)

        skill_files: List[Path] = sorted(
            f for f in self._skills_dir.glob("*.py") if f.name != "__init__.py"
        )

        # Pre-read existing registry.json to preserve enabled flags
        existing_enabled_map: Dict[str, bool] = {}
        if self._registry_json_path.exists():
            try:
                raw_data = json.loads(self._registry_json_path.read_text(encoding="utf-8"))
                for k, v in raw_data.items():
                    if isinstance(v, dict) and "enabled" in v:
                        existing_enabled_map[k] = bool(v["enabled"])
            except Exception:  # pylint: disable=broad-except
                pass

        new_registry: Dict[str, Dict[str, Any]] = {}
        rejected: Dict[str, List[str]] = {}

        with self._lock:
            for skill_file in skill_files:
                module_name = f"skills.{skill_file.stem}"
                # Prompt cuối §106/§118: import = chạy code cấp module. Tệp có lệnh ở cấp
                # module (script đặt nhầm, tệp sinh tự động) KHÔNG được import.
                violations = top_level_side_effects(skill_file)
                if violations:
                    rejected[skill_file.name] = violations
                    logger.warning("Từ chối nạp '%s': có lệnh chạy ở cấp module (%s) — skill chỉ được "
                                   "chứa import / định nghĩa / gán.", skill_file.name, "; ".join(violations[:3]))
                    continue
                try:
                    if module_name in sys.modules:
                        module = importlib.reload(sys.modules[module_name])
                        logger.debug("Reloaded skill module: %s", module_name)
                    else:
                        module = importlib.import_module(module_name)
                        logger.debug("Imported skill module: %s", module_name)

                    count = self._extract_skills_from_module(
                        module, module_name, existing_enabled_map, target_registry=new_registry
                    )
                    loaded_count += count

                except Exception:  # pylint: disable=broad-except
                    logger.error(
                        "Failed to load skill module '%s':\n%s",
                        module_name,
                        traceback.format_exc(),
                    )

            self._persist_registry_of(new_registry)
            for name, entry in self._dynamic.items():
                if name in new_registry:
                    logger.warning("Kỹ năng động '%s' trùng tên kỹ năng có tệp — giữ kỹ năng có tệp.", name)
                    continue
                new_registry[name] = entry
            self._registry = new_registry
            self.rejected_modules = rejected

        # Phase 8: Dynamic Skill Loading — cập nhật chỉ mục bộ định tuyến kỹ năng động
        try:
            from mateai.application.skills.skill_router import dynamic_skill_router
            dynamic_skill_router.rebuild_index()
        except Exception as _idx_err:
            logger.debug("[PluginManager] Không thể cập nhật chỉ mục dynamic_skill_router: %s", _idx_err)

        logger.info(
            "Plugin load complete: %d skill(s) across %d module(s).",
            loaded_count,
            len(skill_files),
        )
        return loaded_count

    def register_dynamic_skill(self, name: str, func: Callable, description: str, parameters: Dict[str, Any],
                               *, source: str, contract: Optional[Dict[str, Any]] = None) -> bool:
        """Thêm kỹ năng không có tệp (vd công cụ MCP). Trả False nếu trùng kỹ năng có tệp (không ghi đè)."""
        self._ensure_loaded()
        entry = {"func": func, "module": f"dynamic:{source}", "attr": name, "enabled": True, "dynamic": True, "source": source,
                 "meta": {"name": name, "description": description, "parameters": parameters,
                          "contract": {"data_classification": "INTERNAL", "reversible": None, "output_schema": None,
                                       "data_scope": None, "verification_required": None, "needs_desktop": False, **(contract or {})}}}
        with self._lock:
            existing = self._registry.get(name)
            if existing is not None and not existing.get("dynamic"):
                return False
            self._dynamic[name] = entry
            self._registry[name] = entry
        self._rebuild_router()
        return True

    def unregister_dynamic_skills(self, source: str) -> int:
        """Gỡ mọi kỹ năng động của một nguồn. Trả số kỹ năng đã gỡ."""
        with self._lock:
            names = [n for n, e in self._dynamic.items() if e.get("source") == source]
            for n in names:
                self._dynamic.pop(n, None)
                if (self._registry.get(n) or {}).get("dynamic"):
                    self._registry.pop(n, None)
        if names:
            self._rebuild_router()
        return len(names)

    @staticmethod
    def _rebuild_router() -> None:
        try:
            from mateai.application.skills.skill_router import dynamic_skill_router
            dynamic_skill_router.rebuild_index()
        except Exception as exc:  # noqa: BLE001
            logger.debug("[PluginManager] Không cập nhật được chỉ mục bộ định tuyến: %s", exc)

    def _ensure_loaded(self) -> None:
        """Lazily load plugins if the registry is empty."""
        if not self._registry:
            self.load_plugins()

    def get_all_tools(self) -> List[Dict[str, Any]]:
        """
        Return the `tools` array in OpenAI Function-Calling format.
        Only returns skills that are currently enabled.
        """
        self._ensure_loaded()
        with self._lock:
            return [
                {
                    "type": "function",
                    "function": {
                        "name": entry["meta"]["name"],
                        "description": entry["meta"]["description"],
                        "parameters": entry["meta"]["parameters"],
                    },
                }
                for entry in self._registry.values()
                if entry.get("enabled", True)
            ]

    def get_tools_for_query(
        self,
        query: str,
        max_tools: int = 5,
        domain_hint: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Phase 8: Trả về danh sách schema công cụ được chọn lọc động theo ý định người dùng.
        Đàm thoại thông thường -> 0 tools; Tác vụ kỹ thuật -> top 3-5 tools phù hợp nhất.
        """
        self._ensure_loaded()
        from mateai.application.skills.skill_router import dynamic_skill_router
        return dynamic_skill_router.get_tools_for_query(query, max_tools=max_tools, domain_hint=domain_hint)

    def get_tools_by_domain(self, domain: str) -> List[Dict[str, Any]]:
        """
        Phase 8: Trả về danh sách schema công cụ theo miền nghiệp vụ cụ thể.
        """
        self._ensure_loaded()
        from mateai.application.skills.skill_router import dynamic_skill_router
        return dynamic_skill_router.get_tools_by_domain(domain)

    def get_domain_stats(self) -> Dict[str, int]:
        """
        Phase 8: Thống kê số lượng kỹ năng theo từng miền nghiệp vụ.
        """
        self._ensure_loaded()
        from mateai.application.skills.skill_router import dynamic_skill_router
        return dynamic_skill_router.get_domain_stats()

    async def execute_skill(
        self,
        skill_name: str,
        arguments: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Dynamically call a registered skill by name.

        Returns a standardised envelope:
            {"success": bool, "data": Any, "error": str | None}
        """
        self._ensure_loaded()
        with self._lock:
            entry = self._registry.get(skill_name)

        if entry is None:
            return {
                "success": False,
                "data": None,
                "error": f"Skill '{skill_name}' not found in registry.",
            }

        if not entry.get("enabled", True):
            return {
                "success": False,
                "data": None,
                "error": f"Kỹ năng '{skill_name}' hiện đang bị tạm tắt trong cấu hình.",
            }

        func: Callable = entry["func"]
        try:
            sig = inspect.signature(func)
            has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
            call_args = arguments if has_var_kw else {k: v for k, v in arguments.items() if k in sig.parameters}
            
            # Handle async functions
            if inspect.iscoroutinefunction(func):
                # Run async function in the event loop
                try:
                    loop = asyncio.get_running_loop()
                    # We're in an async context, create task and await
                    result = await func(**call_args)
                except RuntimeError:
                    # No running loop, create new one
                    result = asyncio.run(func(**call_args))
            else:
                result = await run_blocking(func, **call_args)

            return {"success": True, "data": result, "error": None}
        except TypeError as exc:
            return {
                "success": False,
                "data": None,
                "error": f"Argument error calling '{skill_name}': {exc}",
            }
        except Exception as exc:  # pylint: disable=broad-except
            return {
                "success": False,
                "data": None,
                "error": f"Runtime error in '{skill_name}': {traceback.format_exc()}",
            }

    def toggle_skill(self, skill_name: str, enabled: Optional[bool] = None) -> bool:
        """
        Bật hoặc tắt một kỹ năng trong kho runtime.
        Tự động lưu trạng thái vào skills/registry.json.
        """
        self._ensure_loaded()
        with self._lock:
            if skill_name not in self._registry:
                raise KeyError(f"Kỹ năng '{skill_name}' không tồn tại trong kho kỹ năng.")
            if enabled is None:
                new_state = not self._registry[skill_name].get("enabled", True)
            else:
                new_state = bool(enabled)
            self._registry[skill_name]["enabled"] = new_state
            self._persist_registry()
            logger.info("Kỹ năng '%s' đã được đổi trạng thái enabled=%s", skill_name, new_state)
            return new_state

    def register_custom_skill(
        self,
        name: str,
        description: str,
        python_code: str,
        parameters: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Thêm một kỹ năng mới do người dùng định nghĩa vào skills/custom_skills.py,
        kiểm tra cú pháp và tự động nạp vào runtime.
        """
        name = name.strip()
        if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", name):
            raise ValueError("Tên hàm kỹ năng chỉ được gồm chữ cái không dấu, số và dấu gạch dưới (_), không bắt đầu bằng số.")

        custom_file = self._skills_dir / "custom_skills.py"
        if not custom_file.exists():
            custom_file.write_text(
                '"""\nskills/custom_skills.py\n=======================\nCác kỹ năng tự định nghĩa do người dùng thêm thủ công.\n"""\nfrom core.plugin_manager import export_skill\nimport subprocess\nimport os\nimport sys\n\n',
                encoding="utf-8",
            )

        schema = parameters or {
            "type": "object",
            "properties": {},
            "required": [],
        }

        schema_json = json.dumps(schema, ensure_ascii=False, indent=4)

        # Chuẩn bị thụt đầu dòng cho python_code
        code_lines = python_code.strip().split("\n")
        indented_code = "\n".join(f"    {line}" for line in code_lines)

        func_definition = f"""

@export_skill(
    name="{name}",
    description="{description}",
    parameters_schema={schema_json},
)
def {name}(**kwargs) -> dict:
{indented_code}
"""
        # Kiểm tra tính hợp lệ của cú pháp Python
        try:
            compile(func_definition, "<custom_skill_test>", "exec")
        except SyntaxError as syn_err:
            raise ValueError(f"Mã Python có lỗi cú pháp: {syn_err}")

        # Ghi thêm vào custom_skills.py
        with open(custom_file, "a", encoding="utf-8") as f:
            f.write(func_definition)

        # Nạp lại toàn bộ plugins
        self.load_plugins()
        return {
            "name": name,
            "description": description,
            "module": "skills.custom_skills",
            "enabled": True,
        }

    def get_tool_contract(self, name: str) -> Optional[Dict[str, Any]]:
        """Tool contract đã khai ở `@export_skill` (None nếu không phải skill đã nạp)."""
        self._ensure_loaded()
        with self._lock:
            entry = self._registry.get(name)
            return dict((entry or {}).get("meta", {}).get("contract") or {}) if entry else None

    def get_skill_count(self) -> int:
        """Return the number of currently loaded skills."""
        self._ensure_loaded()
        with self._lock:
            return len(self._registry)

    def get_skill_names(self) -> List[str]:
        """Return sorted list of registered skill names."""
        self._ensure_loaded()
        with self._lock:
            return sorted(self._registry.keys())

    def get_skill_modules(self) -> Dict[str, str]:
        """Tên kỹ năng -> module định nghĩa nó (vd ``skills.auto_play_music``)."""
        self._ensure_loaded()
        with self._lock:
            return {name: entry.get("module", "") for name, entry in self._registry.items()}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _extract_skills_from_module(
        self,
        module: Any,
        module_name: str,
        existing_enabled_map: Optional[Dict[str, bool]] = None,
        target_registry: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> int:
        """
        Inspect a loaded module for @export_skill-decorated callables.
        Register each one in target_registry (or self._registry).
        Returns the count of skills found in this module.
        """
        count = 0
        registry = target_registry if target_registry is not None else self._registry
        enabled_map = existing_enabled_map or {}
        for attr_name, obj in inspect.getmembers(module, inspect.isfunction):
            meta: Optional[Dict[str, Any]] = getattr(obj, "__skill_meta__", None)
            if meta is None:
                continue  # Not a decorated skill

            skill_name: str = meta["name"]
            is_enabled = enabled_map.get(skill_name, self._registry.get(skill_name, {}).get("enabled", True))

            registry[skill_name] = {
                "func": obj,
                "meta": meta,
                "module": module_name,
                "attr": attr_name,
                "enabled": is_enabled,
            }
            logger.debug("Registered skill: '%s' (enabled=%s) from %s.%s", skill_name, is_enabled, module_name, attr_name)
            count += 1
        return count

    def _persist_registry(self) -> None:
        """Write the current registry metadata (no callables) to registry.json."""
        self._persist_registry_of(self._registry)

    def _persist_registry_of(self, registry: Dict[str, Dict[str, Any]]) -> None:
        serialisable: Dict[str, Any] = {
            skill_name: {
                "module": entry["module"],
                "attr": entry["attr"],
                "meta": entry["meta"],
                "enabled": entry.get("enabled", True),
            }
            for skill_name, entry in registry.items()
            if not entry.get("dynamic")          # kỹ năng động không có tệp — không ghi
        }
        try:
            self._registry_json_path.write_text(
                json.dumps(serialisable, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("Could not write registry.json: %s", exc)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

plugin_manager = PluginManager()
client_plugin_manager = plugin_manager
