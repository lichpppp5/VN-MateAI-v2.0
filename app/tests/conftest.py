# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/conftest.py
=================
Cho phép chạy TOÀN BỘ test bằng một lệnh: `python -m pytest`.

Repo có ba kiểu test:
  1. Hàm `test_*` chuẩn pytest — pytest tự thu thập như thường.
  2. File dạng script: chạy check ngay khi import rồi `sys.exit(...)` ở cấp
     module. Import trong tiến trình pytest sẽ làm hỏng cả phiên, nên mỗi file
     được chạy ở một tiến trình Python riêng; mã thoát 0 = pass.
  3. File `.mjs` (Node) — chạy `node <file>` ở tiến trình riêng.

Ngoài ra conftest sao lưu các file cấu hình thật trước phiên test và trả lại
nguyên trạng sau phiên, vì một số test cũ ghi thẳng vào chúng.
"""
from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# DB thật không được đụng tới khi chạy test: đặt trước khi bất kỳ test nào
# import `core` (các singleton như `erp_db` đọc đường dẫn lúc import). Tiến
# trình con (test dạng script) kế thừa biến môi trường này.
# Trước khi có bước này, test HITL ghi sự kiện giả vào bảng audit_logs thật
# (bảng chỉ-ghi) và test repository chèn task giả vào vnmateai.db.
_TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="vnmateai-test-data-"))
os.environ["VNMATEAI_DB_PATH"] = str(_TEST_DATA_DIR / "vnmateai.db")
os.environ["VNMATEAI_HR_DB_PATH"] = str(_TEST_DATA_DIR / "hr_kpi.db")
# config.json thật có token bot Telegram thật: test không được gửi tin chủ động
# (cảnh báo, yêu cầu duyệt) vào nhóm vận hành. Tiến trình con kế thừa biến này.
os.environ["VNMATEAI_TELEGRAM_OUTBOUND"] = "off"
# Không gửi trace của bộ test tới OTel collector thật (config.json có thể đặt otlp).
os.environ["VNMATEAI_OTEL_EXPORTER"] = "none"
# Không ghi log của bộ test vào logs/vnmateai.log của máy chủ đang chạy.
os.environ["VNMATEAI_LOG_FILE"] = "none"
# Nguồn đọc trả phí (Vbee / ElevenLabs trong config.json thật): test không được gọi — tốn điểm API thật, chậm, và kết
# quả phụ thuộc mạng. Tắt = engine dùng đường 9Router / Edge như mặc định (test nào cần thì tự giả lập).
os.environ["VNMATEAI_TTS_PAID_PROVIDERS"] = "off"

# Backend CSDL của bộ test — KHÔNG BAO GIỜ là DB vận hành (config.json có DATABASE_URL sau cutover):
#   mặc định  : SQLite tạm ở trên
#   VNMATEAI_TEST_BACKEND=pg : PostgreSQL THẬT (pgserver) với schema riêng mỗi lượt chạy —
#                              chạy cả bộ test trên PostgreSQL (prompt cuối §66: kiểm trước cutover).
_PG_SERVER = None
if os.environ.get("VNMATEAI_TEST_BACKEND", "").lower() == "pg":
    import uuid as _uuid
    import pgserver as _pgserver
    _PG_SERVER = _pgserver.get_server(str(_TEST_DATA_DIR / "pg"), cleanup_mode="stop")
    _suffix = _uuid.uuid4().hex[:8]
    os.environ["VNMATEAI_DATABASE_URL"] = _PG_SERVER.get_uri()
    os.environ["VNMATEAI_PG_SCHEMA"] = f"t_vnmate_{_suffix}"
    os.environ["VNMATEAI_PG_HR_SCHEMA"] = f"t_hr_{_suffix}"
else:
    os.environ["VNMATEAI_DATABASE_URL"] = "sqlite"

#: File cấu hình thật mà test có thể ghi vào. DB SQLite KHÔNG nằm trong danh
#: sách: server có thể đang mở DB, chép đè lên DB đang mở sẽ làm hỏng dữ liệu.
_PROTECTED_FILES = [
    ROOT / "config.json",
    ROOT / "users.json",
    ROOT / "skills" / "registry.json",
    ROOT / "config" / "data_sources.json",
    ROOT / "config" / "mcp_servers.json",
]

#: Nhãn cho các file script (không gắn được decorator vào file script).
_SCRIPT_MARKERS = {
    # Gọi model thật qua 9Router; kết quả đổi theo danh sách model ngoài mạng.
    "test_phase68_no_hardcoded_models.py": ["network"],
}

_SCRIPT_TIMEOUT_S = 240


def _is_main_guard(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.If)
        and "__name__" in ast.unparse(node.test)
        and "__main__" in ast.unparse(node.test)
    )


def _is_script_style(path: Path) -> bool:
    """File chạy check ngay khi import (có `sys.exit` ngoài `if __name__ == ...`)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return False
    for node in tree.body:
        if _is_main_guard(node):
            continue
        if "sys.exit" in ast.unparse(node):
            return True
    return False


def _subprocess_env() -> dict:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


class ScriptItem(pytest.Item):
    def __init__(self, *, command, **kwargs):
        super().__init__(**kwargs)
        self.command = command
        for marker in _SCRIPT_MARKERS.get(self.path.name, []):
            self.add_marker(marker)

    def runtest(self):
        try:
            proc = subprocess.run(
                self.command,
                cwd=ROOT,
                env=_subprocess_env(),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_SCRIPT_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired as exc:
            raise ScriptFailure(f"quá {_SCRIPT_TIMEOUT_S}s", str(exc.stdout or ""))
        if proc.returncode != 0:
            raise ScriptFailure(f"mã thoát {proc.returncode}", proc.stdout + proc.stderr)

    def repr_failure(self, excinfo):
        if isinstance(excinfo.value, ScriptFailure):
            reason, output = excinfo.value.args
            tail = "\n".join(output.strip().splitlines()[-40:])
            return f"{self.path.name}: {reason}\n{tail}"
        return super().repr_failure(excinfo)

    def reportinfo(self):
        return self.path, 0, f"script: {self.path.name}"


class ScriptFailure(Exception):
    pass


class PythonScriptFile(pytest.File):
    def collect(self):
        yield ScriptItem.from_parent(
            self, name=self.path.name, command=[sys.executable, str(self.path)]
        )


class NodeScriptFile(pytest.File):
    def collect(self):
        node = shutil.which("node")
        item = ScriptItem.from_parent(
            self, name=self.path.name, command=[node or "node", str(self.path)]
        )
        if node is None:
            item.add_marker(pytest.mark.skip(reason="không tìm thấy node"))
        yield item


def pytest_pycollect_makemodule(module_path, parent):
    if _is_script_style(Path(module_path)):
        return PythonScriptFile.from_parent(parent, path=Path(module_path))
    return None


def pytest_collect_file(parent, file_path):
    if file_path.suffix == ".mjs" and file_path.name.startswith("test_"):
        return NodeScriptFile.from_parent(parent, path=file_path)
    return None


@pytest.fixture(scope="session", autouse=True)
def _protect_real_config_files():
    backup_dir = Path(tempfile.mkdtemp(prefix="vnmateai-test-backup-"))
    saved = {}
    for path in _PROTECTED_FILES:
        if path.exists():
            dst = backup_dir / f"{len(saved)}_{path.name}"
            shutil.copy2(path, dst)
            saved[path] = dst
    try:
        yield
    finally:
        for path in _PROTECTED_FILES:
            if path in saved:
                shutil.copy2(saved[path], path)
            elif path.exists():
                # Test tạo ra file vốn không tồn tại -> xoá để trả lại nguyên trạng.
                path.unlink()
        shutil.rmtree(backup_dir, ignore_errors=True)
        shutil.rmtree(_TEST_DATA_DIR, ignore_errors=True)


@pytest.fixture(autouse=True)
def _neutral_login_policy(monkeypatch):
    """Chính sách đăng nhập của config.json thật (vd `require_mfa_roles: ["admin"]` khi vận hành) không áp lên test:
    test đăng nhập admin bằng mật khẩu sẽ nhận `mfa_setup_required` thay vì token. Test cần MFA tự bật (test_mfa.py)."""
    from mateai.config.loader import settings
    monkeypatch.setattr(settings.security, "require_mfa_roles", [])


@pytest.fixture(autouse=True)
def _all_sources_ready(monkeypatch):
    """Máy chạy test không có Prometheus / AWS / eInvoice…: không để `tool_readiness` ẩn công cụ hay chặn kịch bản của mọi
    test. Test của chính tính năng này (test_tool_readiness.py) tự bật lại kiểm tra thật."""
    from mateai.application.skills import tool_readiness
    monkeypatch.setattr(tool_readiness, "REQUIREMENTS", {})
    tool_readiness.clear_cache()


@pytest.fixture(autouse=True)
def _reset_emergency_counter():
    """Bộ đếm chế độ khẩn cấp (§54) là theo tiến trình: không để hàng trăm test cộng dồn
    thành "AI hành động dồn dập" rồi tự bật kill switch giữa bộ test."""
    from mateai.infrastructure.cache import shared_state
    from mateai.infrastructure.files import object_storage
    # Mỗi test một kho RAM riêng — KHÔNG dùng Redis của máy chủ dù config.json có REDIS_URL
    # (reset() trên Redis thật sẽ xoá khoá đăng nhập / rate limit của máy chủ đang chạy).
    shared_state._STORE = shared_state.MemoryStore()
    # Object storage (§68) của test vào thư mục tạm — không ghi vào storage/objects thật
    # (đã xảy ra 2026-10-06: test upload để lại tệp trong kho thật).
    object_storage._STORE = object_storage.LocalObjectStore(_TEST_DATA_DIR / "objects")
    yield
