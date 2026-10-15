# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/db_manager.py
==================
Quản lý cơ sở dữ liệu SQLite trung tâm (vnmateai.db) cho VN-MateAI.

Tính năng:
  - Tự động tạo bảng (CREATE TABLE IF NOT EXISTS) cho `users` và `tasks` khi khởi động.
  - Tự động tạo tài khoản quản trị viên mặc định 'admin' (mật khẩu bcrypt 'admin123').
  - Hỗ trợ đồng bộ hai chiều với `users.json` và `logs/kpi_logs.csv` để đảm bảo tương thích 100%.
  - Cung cấp API truy vấn dữ liệu thực tế (CRUD User, Task KPI, System Hardware Stats).
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import bcrypt
import psutil

from mateai.infrastructure.database.erp_database import ensure_tasks_table, open_sqlite

logger = logging.getLogger("mateai.infrastructure.database.db_manager")

# Thư mục gốc dự án (đúng cả bản đóng gói) — KHÔNG suy từ vị trí file mã nguồn:
# chuyển module mà đường dẫn lệch là máy chủ mở một CSDL rỗng mới.
from mateai.config.loader import settings as _settings  # noqa: E402

_PROJECT_ROOT = Path(_settings.PROJECT_ROOT)
DB_PATH = Path(os.environ.get("VNMATEAI_DB_PATH") or _PROJECT_ROOT / "vnmateai.db")
USERS_JSON_PATH = _PROJECT_ROOT / "users.json"
KPI_CSV_PATH = _PROJECT_ROOT / "logs" / "kpi_logs.csv"


class DatabaseManager:
    """Quản lý kết nối và thao tác dữ liệu trên SQLite database."""

    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        self.init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Tạo kết nối SQLite với Row factory để dễ dàng truy xuất cột dạng dict."""
        return open_sqlite(self.db_path, timeout=20.0)

    def init_db(self) -> None:
        """Tự động tạo bảng và dữ liệu khởi tạo ban đầu nếu chưa tồn tại."""
        with self._lock:
            try:
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
                with self._get_connection() as conn:
                    cursor = conn.cursor()

                    # 1. Bảng users
                    cursor.execute(
                        """
                        CREATE TABLE IF NOT EXISTS users (
                            id TEXT PRIMARY KEY,
                            username TEXT UNIQUE NOT NULL,
                            full_name TEXT NOT NULL,
                            role TEXT NOT NULL DEFAULT 'viewer',
                            password_hash TEXT NOT NULL,
                            created_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL
                        );
                        """
                    )
                    cursor.execute(
                        "CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);"
                    )
                    # ABAC (prompt cuối §35, §64): phòng ban + cấp bảo mật của tài khoản.
                    # NULL = chưa gán (phòng ban: không thấy dữ liệu theo phòng ban trừ admin;
                    # cấp bảo mật: lấy theo vai trò — security_guard.CLEARANCE_BY_ROLE).
                    _ucols = {r[1] for r in cursor.execute("PRAGMA table_info(users);").fetchall()}
                    if "department" not in _ucols:
                        cursor.execute("ALTER TABLE users ADD COLUMN department TEXT;")
                    if "clearance_level" not in _ucols:
                        cursor.execute("ALTER TABLE users ADD COLUMN clearance_level INTEGER;")
                    # Đăng nhập an toàn: MFA (TOTP) + nguồn tài khoản (local | sso). Mã bí mật MFA mã hoá khi lưu.
                    # must_change_password: bắt đổi mật khẩu ở lần đăng nhập tới (tài khoản mặc định, admin vừa đặt lại,
                    # mật khẩu dev công khai) — thẩm định thương mại 14/10/2026 P0-10.
                    for _col, _ddl in (("mfa_secret", "TEXT"), ("mfa_enabled", "INTEGER NOT NULL DEFAULT 0"), ("mfa_recovery", "TEXT"),
                                       ("mfa_last_step", "INTEGER"), ("auth_source", "TEXT"),
                                       ("must_change_password", "INTEGER NOT NULL DEFAULT 0")):
                        if _col not in _ucols:
                            cursor.execute(f"ALTER TABLE users ADD COLUMN {_col} {_ddl};")

                    # 2. Bảng tasks — schema chung, định nghĩa ở mateai.infrastructure.database.erp_database.
                    ensure_tasks_table(cursor)

                    # 3. Token riêng của từng thiết bị IoT — chỉ lưu SHA-256.
                    cursor.execute(
                        """
                        CREATE TABLE IF NOT EXISTS device_tokens (
                            device_id TEXT PRIMARY KEY,
                            token_sha256 TEXT NOT NULL,
                            created_at TEXT NOT NULL,
                            created_by TEXT,
                            last_seen_at TEXT
                        );
                        """
                    )
                    # Quyền riêng của thiết bị (NULL = chưa đặt). Thêm cột cho DB cũ.
                    _cols = {r[1] for r in cursor.execute("PRAGMA table_info(device_tokens);").fetchall()}
                    if "role" not in _cols:
                        cursor.execute("ALTER TABLE device_tokens ADD COLUMN role TEXT;")

                    # 5. Máy trạm (Agent): mã đăng ký DÙNG MỘT LẦN + khoá riêng từng máy.
                    #    Chỉ lưu SHA-256; thu hồi từng máy (revoked_at) mà không ảnh hưởng máy khác.
                    cursor.execute(
                        """
                        CREATE TABLE IF NOT EXISTS worker_enroll_codes (
                            code_sha256 TEXT PRIMARY KEY,
                            label TEXT,
                            created_by TEXT,
                            created_at TEXT NOT NULL,
                            expires_at TEXT NOT NULL,
                            used_at TEXT,
                            used_by_client TEXT
                        );
                        """
                    )
                    cursor.execute(
                        """
                        CREATE TABLE IF NOT EXISTS worker_devices (
                            client_id TEXT PRIMARY KEY,
                            token_sha256 TEXT NOT NULL UNIQUE,
                            label TEXT,
                            hostname TEXT,
                            platform TEXT,
                            package TEXT,
                            agent_version TEXT,
                            enrolled_at TEXT NOT NULL,
                            enrolled_by TEXT,
                            last_seen_at TEXT,
                            revoked_at TEXT,
                            revoked_by TEXT
                        );
                        """
                    )

                    # 6. Lịch sử cấu hình: mỗi lần lưu một phiên bản (bản chụp ĐÃ CHE khoá bí
                    #    mật) + danh sách thay đổi; khôi phục được về bất kỳ phiên bản nào.
                    cursor.execute(
                        """
                        CREATE TABLE IF NOT EXISTS config_history (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            saved_at TEXT NOT NULL,
                            saved_by TEXT,
                            note TEXT,
                            changes_json TEXT NOT NULL,
                            snapshot_json TEXT NOT NULL
                        );
                        """
                    )

                    # 6b. Yêu cầu quên mật khẩu (màn đăng nhập): admin xác minh người thật rồi cấp mật khẩu tạm.
                    cursor.execute(
                        """
                        CREATE TABLE IF NOT EXISTS password_reset_requests (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            username TEXT NOT NULL,
                            note TEXT,
                            source_ip TEXT,
                            created_at TEXT NOT NULL,
                            status TEXT NOT NULL DEFAULT 'open',
                            handled_by TEXT,
                            handled_at TEXT
                        );
                        """
                    )

                    # 4. Phê duyệt đã nhớ: thiết bị đã được duyệt tác vụ X một lần thì
                    #    lần sau không hỏi lại (chủ hệ thống chọn, thu hồi ở Web Portal).
                    cursor.execute(
                        """
                        CREATE TABLE IF NOT EXISTS approval_grants (
                            principal TEXT NOT NULL,
                            tool_name TEXT NOT NULL,
                            granted_by TEXT,
                            granted_at TEXT NOT NULL,
                            PRIMARY KEY (principal, tool_name)
                        );
                        """
                    )

                    # 5. Sổ tác vụ vận hành của AI (prompt Supervisor §21–§27): tác vụ,
                    #    từng bước (quyết định chính sách + kết quả + kiểm chứng), bằng chứng.
                    cursor.executescript(
                        """
                        CREATE TABLE IF NOT EXISTS op_tasks (
                            task_id TEXT PRIMARY KEY,
                            kind TEXT NOT NULL,
                            title TEXT NOT NULL,
                            goal TEXT,
                            created_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL,
                            created_by TEXT,
                            agent_id TEXT,
                            channel TEXT,
                            priority TEXT NOT NULL,
                            risk INTEGER NOT NULL DEFAULT 1,
                            status TEXT NOT NULL,
                            current_step INTEGER NOT NULL DEFAULT 0,
                            approval_id TEXT,
                            result_summary TEXT,
                            verification_status TEXT,
                            source TEXT,
                            trace_id TEXT
                        );
                        CREATE INDEX IF NOT EXISTS idx_op_tasks_status ON op_tasks(status);
                        CREATE INDEX IF NOT EXISTS idx_op_tasks_created ON op_tasks(created_at);
                        CREATE TABLE IF NOT EXISTS op_task_steps (
                            step_id TEXT PRIMARY KEY,
                            task_id TEXT NOT NULL,
                            seq INTEGER NOT NULL,
                            tool TEXT NOT NULL,
                            target TEXT,
                            args_hash TEXT,
                            decision TEXT,
                            policy_rule TEXT,
                            policy_version TEXT,
                            risk INTEGER,
                            level TEXT,
                            started_at TEXT NOT NULL,
                            ended_at TEXT,
                            outcome TEXT,
                            message TEXT,
                            verification_level TEXT,
                            verification_status TEXT,
                            evidence_id TEXT
                        );
                        CREATE INDEX IF NOT EXISTS idx_op_steps_task ON op_task_steps(task_id);
                        CREATE TABLE IF NOT EXISTS op_evidence (
                            evidence_id TEXT PRIMARY KEY,
                            task_id TEXT NOT NULL,
                            collected_at TEXT NOT NULL,
                            source TEXT NOT NULL,
                            kind TEXT NOT NULL,
                            summary TEXT NOT NULL,
                            ref TEXT,
                            verified INTEGER NOT NULL DEFAULT 0
                        );
                        CREATE INDEX IF NOT EXISTS idx_op_evidence_task ON op_evidence(task_id);
                        CREATE TABLE IF NOT EXISTS dev_projects (
                            project_id TEXT PRIMARY KEY,
                            name TEXT NOT NULL,
                            repository TEXT,
                            goal_id TEXT,
                            description TEXT,
                            preferred_workers TEXT,
                            status TEXT NOT NULL DEFAULT 'ACTIVE',
                            created_at TEXT NOT NULL,
                            created_by TEXT
                        );
                        CREATE TABLE IF NOT EXISTS dev_runs (
                            run_id TEXT PRIMARY KEY,
                            task_id TEXT NOT NULL,
                            project_id TEXT,
                            attempt INTEGER NOT NULL DEFAULT 1,
                            worker_id TEXT,
                            agent_id TEXT,
                            master_task_id TEXT,
                            idempotency_key TEXT NOT NULL UNIQUE,
                            status TEXT NOT NULL,
                            dispatched_at TEXT NOT NULL,
                            last_progress_at TEXT,
                            finished_at TEXT,
                            exit_code INTEGER,
                            spec_json TEXT,
                            result_json TEXT
                        );
                        CREATE INDEX IF NOT EXISTS idx_dev_runs_task ON dev_runs(task_id);
                        CREATE INDEX IF NOT EXISTS idx_dev_runs_status ON dev_runs(status);
                        CREATE TABLE IF NOT EXISTS dev_leases (
                            lease_key TEXT PRIMARY KEY,
                            owner_run_id TEXT NOT NULL,
                            owner_task_id TEXT NOT NULL,
                            worker_id TEXT,
                            acquired_at TEXT NOT NULL,
                            expires_at TEXT NOT NULL
                        );
                        CREATE TABLE IF NOT EXISTS dev_events (
                            event_id TEXT PRIMARY KEY,
                            ts TEXT NOT NULL,
                            kind TEXT NOT NULL,
                            task_id TEXT,
                            run_id TEXT,
                            worker_id TEXT,
                            message TEXT
                        );
                        CREATE INDEX IF NOT EXISTS idx_dev_events_ts ON dev_events(ts);
                        CREATE TABLE IF NOT EXISTS pb_playbooks (
                            playbook_id TEXT PRIMARY KEY,
                            name TEXT NOT NULL,
                            definition_json TEXT NOT NULL,
                            enabled INTEGER NOT NULL DEFAULT 1,
                            version INTEGER NOT NULL DEFAULT 1,
                            created_by TEXT,
                            created_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL
                        );
                        CREATE TABLE IF NOT EXISTS pb_runs (
                            run_id TEXT PRIMARY KEY,
                            playbook_id TEXT NOT NULL,
                            task_id TEXT,
                            status TEXT NOT NULL,
                            caller TEXT,
                            agent_id TEXT,
                            params_json TEXT,
                            definition_json TEXT,
                            plan_json TEXT,
                            plan_hash TEXT,
                            approval_id TEXT,
                            idempotency_key TEXT UNIQUE,
                            results_json TEXT,
                            error TEXT,
                            created_at TEXT NOT NULL,
                            started_at TEXT,
                            finished_at TEXT
                        );
                        CREATE TABLE IF NOT EXISTS rag_doc_acl (
                            doc_name TEXT PRIMARY KEY,
                            classification TEXT NOT NULL,
                            departments TEXT,
                            updated_by TEXT,
                            updated_at TEXT NOT NULL
                        );
                        CREATE TABLE IF NOT EXISTS llm_usage (
                            usage_id TEXT PRIMARY KEY,
                            ts TEXT NOT NULL,
                            day TEXT NOT NULL,
                            principal TEXT,
                            model TEXT,
                            prompt_tokens INTEGER NOT NULL DEFAULT 0,
                            completion_tokens INTEGER NOT NULL DEFAULT 0,
                            total_tokens INTEGER NOT NULL DEFAULT 0,
                            cost REAL,
                            task_id TEXT
                        );
                        CREATE INDEX IF NOT EXISTS idx_llm_usage_day ON llm_usage(day);
                        CREATE TABLE IF NOT EXISTS ai_eval_sets (
                            set_id TEXT PRIMARY KEY,
                            name TEXT NOT NULL,
                            cases_json TEXT NOT NULL,
                            created_by TEXT,
                            created_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL
                        );
                        CREATE TABLE IF NOT EXISTS ai_eval_runs (
                            run_id TEXT PRIMARY KEY,
                            set_id TEXT NOT NULL,
                            status TEXT NOT NULL,
                            actor TEXT,
                            models TEXT,
                            passed INTEGER NOT NULL DEFAULT 0,
                            total INTEGER NOT NULL DEFAULT 0,
                            total_tokens INTEGER NOT NULL DEFAULT 0,
                            results_json TEXT,
                            error TEXT,
                            created_at TEXT NOT NULL,
                            finished_at TEXT
                        );
                        CREATE INDEX IF NOT EXISTS idx_ai_eval_runs_set ON ai_eval_runs(set_id);
                        CREATE TABLE IF NOT EXISTS pb_webhook_tokens (
                            playbook_id TEXT PRIMARY KEY,
                            token_sha256 TEXT NOT NULL,
                            created_by TEXT,
                            created_at TEXT NOT NULL
                        );
                        CREATE TABLE IF NOT EXISTS orch_executors (
                            executor_id TEXT PRIMARY KEY,
                            kind TEXT NOT NULL,
                            name TEXT NOT NULL,
                            description TEXT,
                            capabilities_json TEXT NOT NULL,
                            tools_json TEXT,
                            ref TEXT,
                            max_risk INTEGER NOT NULL DEFAULT 2,
                            max_concurrent INTEGER NOT NULL DEFAULT 2,
                            status TEXT NOT NULL,
                            version INTEGER NOT NULL DEFAULT 1,
                            created_by TEXT,
                            created_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL
                        );
                        CREATE TABLE IF NOT EXISTS orch_orders (
                            order_id TEXT PRIMARY KEY,
                            task_id TEXT,
                            executor_id TEXT,
                            capability TEXT NOT NULL,
                            status TEXT NOT NULL,
                            attempt INTEGER NOT NULL DEFAULT 0,
                            risk INTEGER NOT NULL DEFAULT 2,
                            requested_by TEXT NOT NULL,
                            channel TEXT,
                            idempotency_key TEXT UNIQUE,
                            approval_id TEXT,
                            spec_json TEXT NOT NULL,
                            routing_json TEXT,
                            report_json TEXT,
                            verification_json TEXT,
                            history_json TEXT,
                            error TEXT,
                            created_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL,
                            dispatched_at TEXT,
                            finished_at TEXT
                        );
                        CREATE INDEX IF NOT EXISTS idx_orch_orders_status ON orch_orders(status);
                        CREATE INDEX IF NOT EXISTS idx_orch_orders_executor ON orch_orders(executor_id);
                        CREATE TABLE IF NOT EXISTS agent_directory (
                            agent_id TEXT PRIMARY KEY,
                            kind TEXT NOT NULL,
                            name TEXT NOT NULL,
                            owner TEXT,
                            description TEXT,
                            status TEXT NOT NULL,
                            role TEXT,
                            token_sha256 TEXT,
                            url TEXT,
                            auth_header TEXT,
                            auth_value TEXT,
                            timeout_s INTEGER NOT NULL DEFAULT 60,
                            risk INTEGER NOT NULL DEFAULT 3,
                            daily_token_limit INTEGER NOT NULL DEFAULT 0,
                            card_json TEXT,
                            last_seen_at TEXT,
                            last_error TEXT,
                            created_by TEXT,
                            created_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL
                        );
                        CREATE INDEX IF NOT EXISTS idx_pb_runs_pb ON pb_runs(playbook_id);
                        CREATE INDEX IF NOT EXISTS idx_pb_runs_status ON pb_runs(status);
                        CREATE TABLE IF NOT EXISTS voice_traces (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            created_at TEXT NOT NULL,
                            channel TEXT,
                            outcome TEXT,
                            data_json TEXT NOT NULL
                        );
                        CREATE INDEX IF NOT EXISTS idx_voice_traces_created ON voice_traces(created_at);
                        """
                    )
                    # Di trú: cột token / số lần gọi LLM cho sổ tác vụ (chi phí thật, §73, §79).
                    _op_cols = {r[1] for r in cursor.execute("PRAGMA table_info(op_tasks);").fetchall()}
                    for _col in ("llm_calls", "total_tokens"):
                        if _col not in _op_cols:
                            cursor.execute(f"ALTER TABLE op_tasks ADD COLUMN {_col} INTEGER NOT NULL DEFAULT 0;")
                    # Vòng đời sự cố (prompt cuối §88): pha, người phụ trách, tài sản bị ảnh hưởng.
                    # Chi phí LLM theo bảng giá model registry (NULL = không có phần nào có giá).
                    if "llm_cost" not in _op_cols:
                        cursor.execute("ALTER TABLE op_tasks ADD COLUMN llm_cost REAL;")
                    if "llm_unpriced_tokens" not in _op_cols:
                        cursor.execute("ALTER TABLE op_tasks ADD COLUMN llm_unpriced_tokens INTEGER NOT NULL DEFAULT 0;")
                    for _col in ("incident_phase", "owner", "affected_assets", "goal_id"):
                        if _col not in _op_cols:
                            cursor.execute(f"ALTER TABLE op_tasks ADD COLUMN {_col} TEXT;")
                    # Phân cấp mục tiêu (prompt cuối §42): company -> department -> operational.
                    cursor.execute(
                        "CREATE TABLE IF NOT EXISTS op_goals ("
                        " goal_id TEXT PRIMARY KEY, parent_id TEXT, level TEXT NOT NULL,"
                        " title TEXT NOT NULL, department TEXT, owner TEXT,"
                        " status TEXT NOT NULL DEFAULT 'ACTIVE', due_date TEXT, created_at TEXT NOT NULL);")
                    cursor.execute("CREATE INDEX IF NOT EXISTS idx_op_goals_parent ON op_goals(parent_id);")

                    conn.commit()

                # Bảng users là kho tài khoản DUY NHẤT. Chỉ khi bảng rỗng (lần
                # đầu): nhập từ users.json cũ trước — giữ mật khẩu người dùng đã
                # đặt — rồi mới tạo tài khoản mặc định nếu vẫn rỗng. Thứ tự cũ
                # (tạo mặc định trước) làm mật khẩu riêng trong users.json bị
                # thay bằng admin123; nhập lại MỖI lần khởi động thì tài khoản đã
                # xoá sống lại.
                if self._user_count() == 0:
                    self._import_users_json_once()
                if self._user_count() == 0:
                    self._seed_default_users()
                logger.info("Khởi tạo cơ sở dữ liệu SQLite thành công tại: %s", self.db_path)
            except Exception as exc:
                logger.error("Lỗi khởi tạo SQLite database: %s", exc)

    @staticmethod
    def hash_password(password: str) -> str:
        """Tạo chuỗi băm bcrypt an toàn."""
        pwd_bytes = password.encode("utf-8")[:72]
        salt = bcrypt.gensalt()
        return bcrypt.hashpw(pwd_bytes, salt).decode("utf-8")

    def _user_count(self) -> int:
        with self._get_connection() as conn:
            return conn.execute("SELECT COUNT(*) FROM users;").fetchone()[0]

    #: Mật khẩu dev công khai (README / mã nguồn) — tài khoản còn dùng thì bị bắt đổi.
    _KNOWN_DEV_PASSWORDS = {"admin": "admin123", "manager": "manager123", "viewer": "viewer123"}

    def flag_known_dev_passwords(self) -> List[str]:
        """Đánh dấu bắt đổi mật khẩu cho tài khoản mặc định còn dùng mật khẩu dev công khai. Trả tên đã đánh dấu."""
        from mateai.application.security.auth_manager import AuthManager
        flagged = []
        for username, pwd in self._KNOWN_DEV_PASSWORDS.items():
            u = self.get_user_by_username_or_id(username)
            if u and not u.get("must_change_password") and AuthManager.verify_password(pwd, u.get("password_hash") or ""):
                with self._lock:
                    with self._get_connection() as conn:
                        conn.execute("UPDATE users SET must_change_password = 1 WHERE id = ?;", (u["id"],))
                        conn.commit()
                flagged.append(username)
        if flagged:
            logger.warning("Tài khoản còn dùng mật khẩu dev công khai — bắt đổi ở lần đăng nhập tới: %s", ", ".join(flagged))
        return flagged

    def _seed_default_users(self) -> None:
        """
        Tạo 3 tài khoản mặc định khi bảng users rỗng.

        Mật khẩu: biến môi trường VNMATEAI_DEFAULT_<ROLE>_PASSWORD, không có thì
        giá trị dev (kèm cảnh báo). Trước đây gán cứng admin123/... nên đặt biến
        môi trường khi triển khai không có tác dụng.
        """
        fallback = {"admin": "admin123", "manager": "manager123", "viewer": "viewer123"}
        names = {"admin": "Quản Trị Viên Hệ Thống", "manager": "Quản Lý Vận Hành",
                 "viewer": "Nhân Viên Giám Sát"}
        used_fallback = []
        now_str = datetime.utcnow().isoformat()
        rows = []
        for role, dev_pwd in fallback.items():
            pwd = os.getenv(f"VNMATEAI_DEFAULT_{role.upper()}_PASSWORD", "").strip()
            if not pwd:
                pwd = dev_pwd
                used_fallback.append(role)
            rows.append((f"usr_{role}", role, names[role], role, self.hash_password(pwd), now_str, now_str))
        with self._get_connection() as conn:
            # Mật khẩu ban đầu (kể cả mật khẩu ngẫu nhiên bộ cài in ra màn hình) phải đổi ở lần đăng nhập đầu.
            conn.executemany(
                """
                INSERT INTO users (id, username, full_name, role, password_hash, created_at, updated_at, must_change_password)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1);
                """,
                rows,
            )
            conn.commit()
        if used_fallback:
            logger.warning(
                "Đã tạo tài khoản với mật khẩu MẶC ĐỊNH yếu (%s) — chỉ dùng cho dev. Đặt "
                "VNMATEAI_DEFAULT_<ROLE>_PASSWORD trước lần khởi động đầu, hoặc đổi mật "
                "khẩu ngay trong trang quản trị.", ", ".join(used_fallback),
            )
        logger.info("Đã khởi tạo 3 tài khoản mặc định trong SQLite: admin, manager, viewer.")

    def _import_users_json_once(self) -> None:
        """
        Di trú một lần từ users.json (kho cũ) khi bảng users còn rỗng. File không
        bị sửa hay xoá. Tài khoản không có hash mật khẩu bị BỎ QUA — trước đây
        được gán mật khẩu "123456".
        """
        if not USERS_JSON_PATH.exists():
            return
        try:
            import json
            data = json.loads(USERS_JSON_PATH.read_text(encoding="utf-8"))
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("Không đọc được users.json để di trú: %s", exc)
            return
        now_str = datetime.utcnow().isoformat()
        imported, skipped = [], []
        with self._get_connection() as conn:
            for uname, uinfo in (data or {}).items():
                if not isinstance(uinfo, dict):
                    continue
                username = (uinfo.get("username") or uname).strip().lower()
                pwd_hash = uinfo.get("password_hash") or uinfo.get("hashed_password")
                if not username or not pwd_hash:
                    skipped.append(username or uname)
                    continue
                conn.execute(
                    """
                    INSERT OR IGNORE INTO users (id, username, full_name, role, password_hash, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?);
                    """,
                    (uinfo.get("id") or f"usr_{username}", username, uinfo.get("full_name") or username,
                     uinfo.get("role") or "viewer", pwd_hash,
                     uinfo.get("created_at") or now_str, uinfo.get("updated_at") or now_str),
                )
                imported.append(username)
            conn.commit()
        logger.info("Đã di trú %d tài khoản từ users.json vào SQLite.", len(imported))
        if skipped:
            logger.warning("Bỏ qua tài khoản không có hash mật khẩu trong users.json: %s", ", ".join(skipped))

    # -----------------------------------------------------------------------
    # User CRUD Operations
    # -----------------------------------------------------------------------

    def get_all_users(self) -> List[Dict[str, Any]]:
        """Lấy toàn bộ danh sách người dùng (KHÔNG trả về password_hash)."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT id, username, full_name, role, department, clearance_level, mfa_enabled, auth_source, created_at, updated_at
                    FROM users
                    ORDER BY created_at ASC;
                    """
                )
                rows = cursor.fetchall()
                return [dict(row) for row in rows]

    def get_user_by_username_or_id(self, identifier: str) -> Optional[Dict[str, Any]]:
        """Tìm người dùng theo username hoặc id."""
        clean_id = identifier.strip().lower()
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT id, username, full_name, role, department, clearance_level, password_hash,
                           mfa_enabled, auth_source, must_change_password, created_at, updated_at
                    FROM users
                    WHERE LOWER(username) = ? OR LOWER(id) = ?;
                    """,
                    (clean_id, clean_id),
                )
                row = cursor.fetchone()
                return dict(row) if row else None

    _SECURITY_COLS = ("mfa_secret", "mfa_enabled", "mfa_recovery", "mfa_last_step", "auth_source")

    def user_security_get(self, user_id: str) -> Dict[str, Any]:
        """Trạng thái bảo mật của tài khoản (MFA, nguồn đăng nhập). KHÔNG nằm trong bản ghi người dùng trả ra ngoài."""
        with self._lock:
            with self._get_connection() as conn:
                row = conn.execute(f"SELECT {', '.join(self._SECURITY_COLS)} FROM users WHERE id = ?;", (user_id,)).fetchone()
                return dict(row) if row else {}

    def user_security_set(self, user_id: str, **fields: Any) -> None:
        bad = [k for k in fields if k not in self._SECURITY_COLS]
        if bad or not fields:
            raise ValueError(f"Trường không hợp lệ: {bad}")
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self._lock:
            with self._get_connection() as conn:
                conn.execute(f"UPDATE users SET {sets}, updated_at = ? WHERE id = ?;",
                             [*fields.values(), datetime.utcnow().isoformat(), user_id])
                conn.commit()

    def create_user(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Tạo người dùng mới trong SQLite. Bản thương mại: không vượt hạn mức người dùng của bản quyền (licensing)."""
        from mateai.application.licensing import ensure_can_add
        ensure_can_add("users")
        username = str(data.get("username", "")).strip().lower()
        if not username or len(username) < 3:
            raise ValueError("Tên đăng nhập phải có ít nhất 3 ký tự.")

        raw_password = data.get("password") or data.get("plain_password") or ""
        if not raw_password or len(raw_password) < 6:
            raise ValueError("Mật khẩu phải có độ dài tối thiểu 6 ký tự.")

        role = str(data.get("role", "viewer")).strip().lower()
        if role not in ("admin", "manager", "viewer"):
            role = "viewer"

        full_name = str(data.get("full_name", "")).strip() or username
        user_id = data.get("id") or f"usr_{uuid.uuid4().hex[:8]}"
        hashed = self.hash_password(raw_password)
        now_str = datetime.utcnow().isoformat()

        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                # Kiểm tra trùng lặp
                cursor.execute("SELECT id FROM users WHERE username = ?;", (username,))
                if cursor.fetchone():
                    raise ValueError(f"Tên đăng nhập '{username}' đã tồn tại trong hệ thống.")

                cursor.execute(
                    """
                    INSERT INTO users (id, username, full_name, role, password_hash, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?);
                    """,
                    (user_id, username, full_name, role, hashed, now_str, now_str),
                )
                conn.commit()

        logger.info("Đã tạo người dùng mới trong SQLite: %s (%s)", username, role)
        return {
            "id": user_id,
            "username": username,
            "full_name": full_name,
            "role": role,
            "created_at": now_str,
        }

    @staticmethod
    def public_user(user: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Bản ghi người dùng để trả ra ngoài — KHÔNG có password_hash."""
        return {k: v for k, v in (user or {}).items() if k != "password_hash"}

    def update_user(self, user_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Cập nhật full_name / role / department / clearance_level. Trả bản ghi KHÔNG có
        password_hash (trước đây PUT /api/v1/users/{id} trả nguyên hash ra API).
        `department=""` = bỏ gán phòng ban; `clearance_level=None` (gửi tường minh) = theo vai trò."""
        existing = self.get_user_by_username_or_id(user_id)
        if not existing:
            raise ValueError(f"Không tìm thấy người dùng với định danh '{user_id}'.")

        full_name = data.get("full_name")
        role = data.get("role")
        now_str = datetime.utcnow().isoformat()

        updates = []
        params = []
        if full_name is not None:
            updates.append("full_name = ?")
            params.append(str(full_name).strip())
        if role is not None:
            r = str(role).strip().lower()
            if r in ("admin", "manager", "viewer"):
                updates.append("role = ?")
                params.append(r)
        if "department" in data:
            updates.append("department = ?")
            params.append(str(data["department"]).strip() or None if data["department"] is not None else None)
        if "clearance_level" in data:
            updates.append("clearance_level = ?")
            params.append(int(data["clearance_level"]) if data["clearance_level"] is not None else None)

        if not updates:
            return self.public_user(existing)

        updates.append("updated_at = ?")
        params.append(now_str)
        params.append(existing["id"])

        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    f"UPDATE users SET {', '.join(updates)} WHERE id = ?;",
                    params,
                )
                conn.commit()

        updated = self.get_user_by_username_or_id(existing["id"])
        return self.public_user(updated)

    def delete_user(self, user_id: str) -> None:
        """Xóa tài khoản người dùng."""
        existing = self.get_user_by_username_or_id(user_id)
        if not existing:
            raise ValueError(f"Không tìm thấy người dùng với định danh '{user_id}'.")

        if existing.get("username") == "admin":
            raise ValueError("Không thể xóa tài khoản 'admin' mặc định của hệ thống.")

        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM users WHERE id = ?;", (existing["id"],))
                conn.commit()
        logger.info("Đã xóa tài khoản user id: %s (%s)", existing["id"], existing["username"])

    def change_user_password(self, user_id: str, new_password: str, must_change: bool = False) -> None:
        """Cấp lại mật khẩu mới cho người dùng. `must_change=True` (admin đặt lại cho người khác): người đó phải đổi
        ở lần đăng nhập tới; người tự đổi thì cờ được xoá."""
        if not new_password or len(new_password) < 6:
            raise ValueError("Mật khẩu mới phải có tối thiểu 6 ký tự.")

        existing = self.get_user_by_username_or_id(user_id)
        if not existing:
            raise ValueError(f"Không tìm thấy người dùng với định danh '{user_id}'.")

        hashed = self.hash_password(new_password)
        now_str = datetime.utcnow().isoformat()

        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE users SET password_hash = ?, updated_at = ?, must_change_password = ? WHERE id = ?;",
                    (hashed, now_str, 1 if must_change else 0, existing["id"]),
                )
                conn.commit()
        logger.info("Đã đặt lại mật khẩu cho user: %s", existing["username"])

    # -----------------------------------------------------------------------
    # Task Operations
    # -----------------------------------------------------------------------

    # -----------------------------------------------------------------------
    # Device tokens (một token cho MỘT thiết bị)
    # -----------------------------------------------------------------------

    @staticmethod
    def _sha256(token: str) -> str:
        import hashlib
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def issue_device_token(self, device_id: str, created_by: str = "") -> str:
        """Cấp (hoặc xoay) token cho device_id. Trả token GỐC — chỉ hiện một lần."""
        import secrets as _secrets
        token = _secrets.token_urlsafe(32)
        now = datetime.utcnow().isoformat()
        with self._lock:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO device_tokens (device_id, token_sha256, created_at, created_by, last_seen_at)
                    VALUES (?, ?, ?, ?, NULL)
                    ON CONFLICT(device_id) DO UPDATE SET
                        token_sha256 = excluded.token_sha256,
                        created_at = excluded.created_at,
                        created_by = excluded.created_by,
                        last_seen_at = NULL;
                    """,
                    (device_id, self._sha256(token), now, created_by),
                )
                conn.commit()
        return token

    def verify_device_token(self, device_id: str, token: str) -> bool:
        """Token khớp với ĐÚNG device_id này (so sánh hằng thời gian trên hash)."""
        import hmac
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT token_sha256 FROM device_tokens WHERE device_id = ?;", (device_id,)
            ).fetchone()
            if not row or not hmac.compare_digest(row["token_sha256"], self._sha256(token)):
                return False
            conn.execute("UPDATE device_tokens SET last_seen_at = ? WHERE device_id = ?;",
                         (datetime.utcnow().isoformat(), device_id))
            conn.commit()
            return True

    def list_device_tokens(self) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT device_id, created_at, created_by, last_seen_at, role FROM device_tokens ORDER BY device_id;"
            ).fetchall()
            return [dict(r) for r in rows]

    def set_device_role(self, device_id: str, role: Optional[str]) -> bool:
        """Đặt quyền của thiết bị đã có token (None = bỏ, về mặc định). False nếu không có thiết bị."""
        with self._lock:
            with self._get_connection() as conn:
                cur = conn.execute("UPDATE device_tokens SET role = ? WHERE device_id = ?;", (role, device_id))
                conn.commit()
                return cur.rowcount > 0

    def get_device_role(self, device_id: str) -> Optional[str]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT role FROM device_tokens WHERE device_id = ?;", (device_id,)).fetchone()
            return row["role"] if row and row["role"] else None

    def add_approval_grant(self, principal: str, tool_name: str, granted_by: str = "") -> None:
        with self._lock:
            with self._get_connection() as conn:
                conn.execute(
                    # Duyệt lại sau khi hết hạn phải làm mới ngày cấp (trước: OR IGNORE giữ ngày cũ).
                    "INSERT OR REPLACE INTO approval_grants (principal, tool_name, granted_by, granted_at) "
                    "VALUES (?, ?, ?, ?);",
                    (principal, tool_name, granted_by, datetime.utcnow().isoformat()),
                )
                conn.commit()

    def has_approval_grant(self, principal: str, tool_name: str, max_age_days: Optional[float] = None) -> bool:
        """Uỷ quyền còn hiệu lực. `max_age_days`: quá hạn (tính từ granted_at, UTC) thì coi như không có."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT granted_at FROM approval_grants WHERE principal = ? AND tool_name = ?;", (principal, tool_name)
            ).fetchone()
        if row is None:
            return False
        if max_age_days is None:
            return True
        try:
            age = datetime.utcnow() - datetime.fromisoformat(str(row["granted_at"]))
        except (TypeError, ValueError):
            return False  # ngày cấp hỏng: fail-closed
        return age.total_seconds() <= max_age_days * 86400

    def list_approval_grants(self, principal: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            if principal:
                rows = conn.execute("SELECT * FROM approval_grants WHERE principal = ? ORDER BY tool_name;",
                                    (principal,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM approval_grants ORDER BY principal, tool_name;").fetchall()
            return [dict(r) for r in rows]

    def revoke_approval_grant(self, principal: str, tool_name: Optional[str] = None) -> int:
        """Thu hồi một phê duyệt đã nhớ (hoặc tất cả của principal khi tool_name=None)."""
        with self._lock:
            with self._get_connection() as conn:
                if tool_name:
                    cur = conn.execute("DELETE FROM approval_grants WHERE principal = ? AND tool_name = ?;",
                                       (principal, tool_name))
                else:
                    cur = conn.execute("DELETE FROM approval_grants WHERE principal = ?;", (principal,))
                conn.commit()
                return cur.rowcount

    # ── Lịch sử cấu hình ─────────────────────────────────────────────────────

    def add_config_history(self, saved_by: str, note: str, changes_json: str, snapshot_json: str) -> int:
        with self._lock:
            with self._get_connection() as conn:
                cur = conn.execute(
                    "INSERT INTO config_history (saved_at, saved_by, note, changes_json, snapshot_json) "
                    "VALUES (?, ?, ?, ?, ?);",
                    (datetime.utcnow().isoformat(), saved_by, note, changes_json, snapshot_json),
                )
                conn.commit()
                return int(cur.lastrowid)

    def list_config_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT id, saved_at, saved_by, note, changes_json FROM config_history ORDER BY id DESC LIMIT ?;",
                (int(limit),),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_config_history(self, entry_id: int) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM config_history WHERE id = ?;", (int(entry_id),)).fetchone()
            return dict(row) if row else None

    def count_config_history(self) -> int:
        with self._get_connection() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM config_history;").fetchone()[0])

    # ── Máy trạm: mã đăng ký dùng một lần + khoá riêng từng máy ─────────────

    def add_worker_enroll_code(self, code_sha256: str, label: str, created_by: str, expires_at: str) -> None:
        with self._lock:
            with self._get_connection() as conn:
                conn.execute(
                    "INSERT INTO worker_enroll_codes (code_sha256, label, created_by, created_at, expires_at) "
                    "VALUES (?, ?, ?, ?, ?);",
                    (code_sha256, label, created_by, datetime.utcnow().isoformat(), expires_at),
                )
                conn.commit()

    def consume_worker_enroll_code(self, code_sha256: str, client_id: str) -> Optional[Dict[str, Any]]:
        """Đánh dấu mã đã dùng — NGUYÊN TỬ: chỉ một lần gọi thắng. None nếu sai / hết hạn / đã dùng."""
        now = datetime.utcnow().isoformat()
        with self._lock:
            with self._get_connection() as conn:
                cur = conn.execute(
                    "UPDATE worker_enroll_codes SET used_at = ?, used_by_client = ? "
                    "WHERE code_sha256 = ? AND used_at IS NULL AND expires_at > ?;",
                    (now, client_id, code_sha256, now),
                )
                conn.commit()
                if cur.rowcount != 1:
                    return None
                row = conn.execute("SELECT * FROM worker_enroll_codes WHERE code_sha256 = ?;",
                                   (code_sha256,)).fetchone()
                return dict(row) if row else None

    def list_worker_enroll_codes(self) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT substr(code_sha256, 1, 12) AS code_ref, label, created_by, created_at, expires_at, "
                "used_at, used_by_client FROM worker_enroll_codes ORDER BY created_at DESC LIMIT 200;"
            ).fetchall()
            return [dict(r) for r in rows]

    def delete_worker_enroll_code(self, code_ref: str) -> int:
        """Huỷ mã CHƯA dùng theo 12 ký tự đầu của hash (giao diện không bao giờ thấy mã thật)."""
        with self._lock:
            with self._get_connection() as conn:
                cur = conn.execute(
                    "DELETE FROM worker_enroll_codes WHERE substr(code_sha256, 1, 12) = ? AND used_at IS NULL;",
                    (code_ref,))
                conn.commit()
                return cur.rowcount

    def worker_client_id_exists(self, client_id: str) -> bool:
        with self._get_connection() as conn:
            return conn.execute("SELECT 1 FROM worker_devices WHERE client_id = ?;",
                                (client_id,)).fetchone() is not None

    def add_worker_device(self, client_id: str, token_sha256: str, label: str, hostname: str,
                          platform: str, package: str, agent_version: str, enrolled_by: str) -> None:
        with self._lock:
            with self._get_connection() as conn:
                conn.execute(
                    "INSERT INTO worker_devices (client_id, token_sha256, label, hostname, platform, package, "
                    "agent_version, enrolled_at, enrolled_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);",
                    (client_id, token_sha256, label, hostname, platform, package, agent_version,
                     datetime.utcnow().isoformat(), enrolled_by),
                )
                conn.commit()

    def get_worker_device_by_token(self, token_sha256: str) -> Optional[Dict[str, Any]]:
        """Máy trạm CÒN HIỆU LỰC (chưa thu hồi) có khoá này."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM worker_devices WHERE token_sha256 = ? AND revoked_at IS NULL;", (token_sha256,)
            ).fetchone()
            return dict(row) if row else None

    def touch_worker_device(self, client_id: str, agent_version: Optional[str] = None,
                            package: Optional[str] = None) -> None:
        with self._lock:
            with self._get_connection() as conn:
                conn.execute(
                    "UPDATE worker_devices SET last_seen_at = ?, agent_version = COALESCE(?, agent_version), "
                    "package = COALESCE(?, package) WHERE client_id = ?;",
                    (datetime.utcnow().isoformat(), agent_version, package, client_id),
                )
                conn.commit()

    def list_worker_devices(self) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT client_id, label, hostname, platform, package, agent_version, enrolled_at, enrolled_by, "
                "last_seen_at, revoked_at, revoked_by FROM worker_devices ORDER BY enrolled_at DESC;"
            ).fetchall()
            return [dict(r) for r in rows]

    def revoke_worker_device(self, client_id: str, revoked_by: str) -> bool:
        with self._lock:
            with self._get_connection() as conn:
                cur = conn.execute(
                    "UPDATE worker_devices SET revoked_at = ?, revoked_by = ? "
                    "WHERE client_id = ? AND revoked_at IS NULL;",
                    (datetime.utcnow().isoformat(), revoked_by, client_id),
                )
                conn.commit()
                return cur.rowcount > 0

    def revoke_device_token(self, device_id: str) -> bool:
        with self._lock:
            with self._get_connection() as conn:
                cur = conn.execute("DELETE FROM device_tokens WHERE device_id = ?;", (device_id,))
                conn.commit()
                return cur.rowcount > 0

    def add_or_update_task(self, task_data: Dict[str, Any]) -> Dict[str, Any]:
        """Lưu hoặc cập nhật tác vụ vào bảng tasks."""
        task_id = task_data.get("task_id") or task_data.get("id") or f"task_{uuid.uuid4().hex[:8]}"
        timestamp = task_data.get("timestamp") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        client_id = task_data.get("client_id", "unknown")
        task_message = task_data.get("task_message") or task_data.get("message", "")
        sender = task_data.get("sender", "Ban Giám Đốc")
        status = task_data.get("status", "pending")
        now_str = datetime.utcnow().isoformat()

        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO tasks (id, timestamp, client_id, task_message, sender, status, title,
                                       created_at, updated_at, dispatched_by, due_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        status = excluded.status,
                        updated_at = excluded.updated_at;
                    """,
                    # title: cột NOT NULL khi bảng do phía ERP tạo trước.
                    (task_id, timestamp, client_id, task_message, sender, status,
                     task_message[:200], now_str, now_str,
                     task_data.get("dispatched_by"), task_data.get("due_at")),
                )
                conn.commit()

        return {
            "id": task_id,
            "timestamp": timestamp,
            "client_id": client_id,
            "task_message": task_message,
            "sender": sender,
            "status": status,
        }

    def get_tasks(
        self,
        limit: int = 100,
        client_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Truy vấn danh sách công việc từ SQLite."""
        query = "SELECT id as task_id, timestamp, client_id, task_message, sender, status FROM tasks"
        clauses = []
        params = []

        if client_id:
            clauses.append("LOWER(client_id) = ?")
            params.append(client_id.strip().lower())
        if status:
            clauses.append("LOWER(status) = ?")
            params.append(status.strip().lower())

        if clauses:
            query += " WHERE " + " AND ".join(clauses)

        query += " ORDER BY timestamp DESC, rowid DESC LIMIT ?;"
        params.append(limit)

        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, params)
                rows = cursor.fetchall()
                return [dict(row) for row in rows]

    # ── Giao việc cho máy trạm: chỉ các dòng micro-task (không phải công việc ERP) ──
    _MICRO = "dept_id IS NULL AND client_id IS NOT NULL AND LOWER(client_id) NOT IN ('', 'master', 'erp')"

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            with self._get_connection() as conn:
                row = conn.execute("SELECT * FROM tasks WHERE id = ?;", (task_id,)).fetchone()
                return dict(row) if row else None

    def close_pending_task(self, task_id: str, status: str, *, client_id: Optional[str] = None,
                           responded_at: Optional[str] = None, note: Optional[str] = None) -> bool:
        """Chuyển một micro-task đang 'pending' sang trạng thái cuối — NGUYÊN TỬ.
        `client_id` (nếu có) phải là máy được giao. Trả False nếu task không tồn tại,
        đã đóng, hoặc thuộc máy khác: không tạo dòng mới, không ghi đè kết quả cũ."""
        sql = ("UPDATE tasks SET status = ?, responded_at = COALESCE(?, responded_at), "
               "resolution_notes = COALESCE(?, resolution_notes), updated_at = ? "
               "WHERE id = ? AND LOWER(status) = 'pending' AND " + self._MICRO)
        params: List[Any] = [status, responded_at, note, datetime.utcnow().isoformat(), task_id]
        if client_id is not None:
            sql += " AND client_id = ?"
            params.append(client_id)
        with self._lock:
            with self._get_connection() as conn:
                cur = conn.execute(sql + ";", params)
                conn.commit()
                return cur.rowcount == 1

    def list_micro_tasks(self, since: Optional[str] = None, limit: int = 5000) -> List[Dict[str, Any]]:
        """Micro-task (mới nhất trước), tuỳ chọn từ thời điểm `since` ("%Y-%m-%d %H:%M:%S")."""
        sql = ("SELECT id AS task_id, timestamp, client_id, task_message, sender, status, dispatched_by, "
               "due_at, responded_at, resolution_notes FROM tasks WHERE " + self._MICRO)
        params: List[Any] = []
        if since:
            sql += " AND timestamp >= ?"
            params.append(since)
        sql += " ORDER BY timestamp DESC, rowid DESC LIMIT ?;"
        params.append(limit)
        with self._lock:
            with self._get_connection() as conn:
                return [dict(r) for r in conn.execute(sql, params).fetchall()]

    # ── Sổ tác vụ vận hành của AI (op_tasks / op_task_steps / op_evidence) ──

    _OP_TASK_COLS = ("task_id", "kind", "title", "goal", "created_at", "updated_at", "created_by", "agent_id",
                     "channel", "priority", "risk", "status", "current_step", "approval_id", "result_summary",
                     "verification_status", "source", "trace_id")

    def op_get_goal(self, goal_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM op_goals WHERE goal_id = ?;", (goal_id,)).fetchone()
            return dict(row) if row else None

    def op_list_goals(self, parent_id: Optional[str] = None, top_level: bool = False) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            if top_level:
                rows = conn.execute("SELECT * FROM op_goals WHERE parent_id IS NULL ORDER BY created_at;").fetchall()
            else:
                rows = conn.execute("SELECT * FROM op_goals WHERE parent_id = ? ORDER BY created_at;",
                                    (parent_id,)).fetchall()
            return [dict(r) for r in rows]

    def op_goal_task_counts(self, goal_ids: List[str]) -> Dict[str, int]:
        if not goal_ids:
            return {"tasks": 0, "completed": 0}
        marks = ", ".join("?" * len(goal_ids))
        with self._get_connection() as conn:
            total, done = conn.execute(
                f"SELECT COUNT(*), SUM(CASE WHEN status = 'COMPLETED' THEN 1 ELSE 0 END) FROM op_tasks "
                f"WHERE goal_id IN ({marks});", goal_ids).fetchone()
            return {"tasks": int(total or 0), "completed": int(done or 0)}

    def op_insert(self, table: str, row: Dict[str, Any]) -> None:
        if table not in ("op_tasks", "op_task_steps", "op_evidence", "op_goals"):
            raise ValueError(table)
        cols = list(row)
        with self._lock:
            with self._get_connection() as conn:
                conn.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))});",
                             [row[c] for c in cols])
                conn.commit()

    def op_update(self, table: str, key: str, key_value: str, fields: Dict[str, Any]) -> None:
        if table not in ("op_tasks", "op_task_steps") or key not in ("task_id", "step_id") or not fields:
            raise ValueError(table)
        sets = ", ".join(f"{c} = ?" for c in fields)
        with self._lock:
            with self._get_connection() as conn:
                conn.execute(f"UPDATE {table} SET {sets} WHERE {key} = ?;", [*fields.values(), key_value])
                conn.commit()

    def op_get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM op_tasks WHERE task_id = ?;", (task_id,)).fetchone()
            if not row:
                return None
            task = dict(row)
            task["steps"] = [dict(r) for r in conn.execute(
                "SELECT * FROM op_task_steps WHERE task_id = ? ORDER BY seq;", (task_id,)).fetchall()]
            task["evidence"] = [dict(r) for r in conn.execute(
                "SELECT * FROM op_evidence WHERE task_id = ? ORDER BY collected_at;", (task_id,)).fetchall()]
            return task

    def op_list_tasks(self, status: Optional[str] = None, kind: Optional[str] = None,
                      since: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
        sql, params = "SELECT * FROM op_tasks WHERE 1 = 1", []
        if status:
            sql += " AND status = ?"
            params.append(status)
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        if since:
            sql += " AND created_at >= ?"
            params.append(since)
        sql += " ORDER BY created_at DESC LIMIT ?;"
        params.append(limit)
        with self._get_connection() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def op_steps(self, task_id: str) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM op_task_steps WHERE task_id = ? ORDER BY seq;", (task_id,)).fetchall()]

    # ── Dev Fleet (dev_projects / dev_runs / dev_leases / dev_events) ──

    # Dùng chung cho Dev Fleet và Kịch bản vận hành (pb_*): cùng một bộ hàm CRUD có danh sách bảng cho phép.
    _DEV_TABLES = {"dev_projects": "project_id", "dev_runs": "run_id", "dev_events": "event_id",
                   "pb_playbooks": "playbook_id", "pb_runs": "run_id", "rag_doc_acl": "doc_name",
                   "llm_usage": "usage_id", "ai_eval_sets": "set_id", "ai_eval_runs": "run_id",
                   "agent_directory": "agent_id", "pb_webhook_tokens": "playbook_id",
                   "orch_executors": "executor_id", "orch_orders": "order_id"}

    def dev_insert(self, table: str, row: Dict[str, Any]) -> None:
        if table not in self._DEV_TABLES:
            raise ValueError(table)
        cols = list(row)
        with self._lock:
            with self._get_connection() as conn:
                conn.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))});",
                             [row[c] for c in cols])
                conn.commit()

    def dev_update(self, table: str, key_value: str, fields: Dict[str, Any]) -> None:
        if table not in self._DEV_TABLES or not fields:
            raise ValueError(table)
        sets = ", ".join(f"{c} = ?" for c in fields)
        with self._lock:
            with self._get_connection() as conn:
                conn.execute(f"UPDATE {table} SET {sets} WHERE {self._DEV_TABLES[table]} = ?;",
                             [*fields.values(), key_value])
                conn.commit()

    def llm_usage_sum(self, since_day: str, principal: Optional[str] = None) -> Dict[str, Any]:
        """Tổng token / tiền / số lượt từ `since_day` (YYYY-MM-DD, gồm cả ngày đó). Tiền chỉ cộng dòng đã có giá."""
        sql = ("SELECT COALESCE(SUM(total_tokens), 0) AS tokens, COALESCE(SUM(cost), 0) AS cost, COUNT(*) AS calls, "
               "COALESCE(SUM(CASE WHEN cost IS NULL THEN total_tokens ELSE 0 END), 0) AS unpriced_tokens FROM llm_usage WHERE day >= ?")
        params: List[Any] = [since_day]
        if principal is not None:
            sql += " AND principal = ?"
            params.append(principal)
        with self._get_connection() as conn:
            row = conn.execute(sql + ";", params).fetchone()
        r = dict(row)
        return {"tokens": int(r["tokens"] or 0), "cost": float(r["cost"] or 0.0), "calls": int(r["calls"] or 0),
                "unpriced_tokens": int(r["unpriced_tokens"] or 0)}

    def llm_usage_group(self, since_day: str, group_by: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Gộp theo day | model | principal (chỉ ba cột này)."""
        if group_by not in ("day", "model", "principal"):
            raise ValueError(group_by)
        order = "day ASC" if group_by == "day" else "tokens DESC"
        with self._get_connection() as conn:
            rows = conn.execute(
                f"SELECT {group_by} AS key, COALESCE(SUM(total_tokens), 0) AS tokens, COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens, "
                f"COALESCE(SUM(completion_tokens), 0) AS completion_tokens, COALESCE(SUM(cost), 0) AS cost, COUNT(*) AS calls, "
                f"COALESCE(SUM(CASE WHEN cost IS NULL THEN total_tokens ELSE 0 END), 0) AS unpriced_tokens "
                f"FROM llm_usage WHERE day >= ? GROUP BY {group_by} ORDER BY {order} LIMIT ?;", (since_day, int(limit))).fetchall()
        return [{"key": r["key"], "tokens": int(r["tokens"] or 0), "prompt_tokens": int(r["prompt_tokens"] or 0),
                 "completion_tokens": int(r["completion_tokens"] or 0), "cost": float(r["cost"] or 0.0), "calls": int(r["calls"] or 0),
                 "unpriced_tokens": int(r["unpriced_tokens"] or 0)} for r in map(dict, rows)]

    def dev_delete(self, table: str, key_value: str) -> int:
        if table not in self._DEV_TABLES:
            raise ValueError(table)
        with self._lock:
            with self._get_connection() as conn:
                cur = conn.execute(f"DELETE FROM {table} WHERE {self._DEV_TABLES[table]} = ?;", (key_value,))
                conn.commit()
                return int(cur.rowcount or 0)

    def dev_get(self, table: str, key_value: str) -> Optional[Dict[str, Any]]:
        if table not in self._DEV_TABLES:
            raise ValueError(table)
        with self._get_connection() as conn:
            row = conn.execute(f"SELECT * FROM {table} WHERE {self._DEV_TABLES[table]} = ?;", (key_value,)).fetchone()
            return dict(row) if row else None

    def dev_list(self, table: str, where: Optional[Dict[str, Any]] = None, order_by: str = "",
                 limit: int = 200) -> List[Dict[str, Any]]:
        """`where`: cột = giá trị (AND); giá trị là list/tuple -> IN. `order_by` chỉ nhận tên cột + ASC/DESC."""
        if table not in self._DEV_TABLES:
            raise ValueError(table)
        clauses, params = [], []
        for col, val in (where or {}).items():
            if not str(col).replace("_", "").isalnum():
                raise ValueError(col)
            if isinstance(val, (list, tuple)):
                if not val:
                    return []
                clauses.append(f"{col} IN ({', '.join('?' * len(val))})")
                params.extend(val)
            else:
                clauses.append(f"{col} = ?")
                params.append(val)
        sql = f"SELECT * FROM {table}" + (" WHERE " + " AND ".join(clauses) if clauses else "")
        if order_by:
            col, _, direction = order_by.partition(" ")
            if not col.replace("_", "").isalnum() or direction.upper() not in ("", "ASC", "DESC"):
                raise ValueError(order_by)
            sql += f" ORDER BY {col} {direction.upper()}".rstrip()
        with self._get_connection() as conn:
            return [dict(r) for r in conn.execute(sql + " LIMIT ?;", [*params, max(1, min(int(limit), 1000))]).fetchall()]

    def dev_lease_acquire(self, key: str, run_id: str, task_id: str, worker_id: str,
                          ttl_s: float) -> Optional[Dict[str, Any]]:
        """Thuê khoá (workspace / nhánh / máy). Thành công -> bản ghi thuê; đang bị chủ khác giữ -> None.
        Cùng task thuê lại = gia hạn. Thuê hết hạn tự được thu hồi."""
        now = datetime.now()
        stamp = now.strftime("%Y-%m-%d %H:%M:%S")
        expires = (now + timedelta(seconds=ttl_s)).strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            with self._get_connection() as conn:
                row = conn.execute("SELECT * FROM dev_leases WHERE lease_key = ?;", (key,)).fetchone()
                if row and row["expires_at"] > stamp and row["owner_task_id"] != task_id:
                    return None
                if row:
                    conn.execute("UPDATE dev_leases SET owner_run_id = ?, owner_task_id = ?, worker_id = ?, "
                                 "acquired_at = ?, expires_at = ? WHERE lease_key = ?;",
                                 (run_id, task_id, worker_id, stamp, expires, key))
                else:
                    conn.execute("INSERT INTO dev_leases (lease_key, owner_run_id, owner_task_id, worker_id, "
                                 "acquired_at, expires_at) VALUES (?, ?, ?, ?, ?, ?);",
                                 (key, run_id, task_id, worker_id, stamp, expires))
                conn.commit()
        return {"lease_key": key, "owner_run_id": run_id, "owner_task_id": task_id, "worker_id": worker_id,
                "acquired_at": stamp, "expires_at": expires}

    def dev_lease_release(self, task_id: str) -> int:
        with self._lock:
            with self._get_connection() as conn:
                cur = conn.execute("DELETE FROM dev_leases WHERE owner_task_id = ?;", (task_id,))
                conn.commit()
                return int(cur.rowcount or 0)

    def dev_leases(self) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM dev_leases ORDER BY acquired_at;").fetchall()]

    def dev_event_trim(self, keep: int = 2000) -> None:
        with self._lock:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM dev_events WHERE event_id NOT IN "
                             "(SELECT event_id FROM dev_events ORDER BY ts DESC, event_id DESC LIMIT ?);", (keep,))
                conn.commit()

    # ── Trace thoại bền (trước: chỉ RAM, mất sau mỗi lần khởi động lại) ──

    def add_voice_trace(self, data: Dict[str, Any], retention_days: Optional[int] = None) -> None:
        """Hạn lưu: `observability.voice_trace_retention_days`, mặc định 400 ngày (~13 tháng — đủ so sánh cùng kỳ năm
        trước cho báo cáo SLA; thẩm định 14/10/2026 P1-15). Trước: 30 ngày."""
        if retention_days is None:
            from mateai.config.loader import get_config_section
            try:
                retention_days = max(1, int(get_config_section("observability").get("voice_trace_retention_days", 400)))
            except (TypeError, ValueError):
                retention_days = 400
        now = datetime.now()
        with self._lock:
            with self._get_connection() as conn:
                cur = conn.execute(
                    "INSERT INTO voice_traces (created_at, channel, outcome, data_json) VALUES (?, ?, ?, ?);",
                    (now.strftime("%Y-%m-%d %H:%M:%S"), data.get("channel"), data.get("outcome"),
                     json.dumps(data, ensure_ascii=False, default=str)))
                if cur.lastrowid and cur.lastrowid % 200 == 0:  # dọn theo hạn lưu giữ, thỉnh thoảng
                    cutoff = (now - timedelta(days=retention_days)).strftime("%Y-%m-%d %H:%M:%S")
                    conn.execute("DELETE FROM voice_traces WHERE created_at < ?;", (cutoff,))
                conn.commit()

    def recent_voice_traces(self, limit: int = 500) -> List[Dict[str, Any]]:
        """Mới nhất SAU CÙNG (thứ tự thời gian) để nạp lại bộ đệm vòng."""
        with self._get_connection() as conn:
            rows = conn.execute("SELECT data_json FROM voice_traces ORDER BY id DESC LIMIT ?;", (limit,)).fetchall()
        out = []
        for r in reversed(rows):
            try:
                out.append(json.loads(r["data_json"]))
            except (TypeError, ValueError):
                continue
        return out

    def op_stats(self, since: str) -> Dict[str, Any]:
        """Số liệu sổ tác vụ từ thời điểm `since`: tác vụ theo trạng thái / loại, bước theo
        quyết định chính sách / kết quả kiểm chứng, token."""
        with self._get_connection() as conn:
            def pairs(sql: str) -> Dict[str, int]:
                return {str(k): int(v) for k, v in conn.execute(sql, (since,)).fetchall()}
            return {
                "tasks_by_status": pairs("SELECT status, COUNT(*) FROM op_tasks WHERE created_at >= ? GROUP BY status;"),
                "tasks_by_kind": pairs("SELECT kind, COUNT(*) FROM op_tasks WHERE created_at >= ? GROUP BY kind;"),
                "steps_by_decision": pairs("SELECT COALESCE(decision, '?'), COUNT(*) FROM op_task_steps "
                                           "WHERE started_at >= ? GROUP BY decision;"),
                "steps_by_rule": pairs("SELECT COALESCE(policy_rule, '?'), COUNT(*) FROM op_task_steps "
                                       "WHERE started_at >= ? GROUP BY policy_rule;"),
                "steps_by_verification": pairs("SELECT COALESCE(verification_status, '?'), COUNT(*) FROM op_task_steps "
                                               "WHERE started_at >= ? GROUP BY verification_status;"),
                "tokens": int(conn.execute("SELECT COALESCE(SUM(total_tokens), 0) FROM op_tasks WHERE created_at >= ?;",
                                           (since,)).fetchone()[0]),
                "llm_calls": int(conn.execute("SELECT COALESCE(SUM(llm_calls), 0) FROM op_tasks WHERE created_at >= ?;",
                                              (since,)).fetchone()[0]),
                "llm_cost": conn.execute("SELECT SUM(llm_cost) FROM op_tasks WHERE created_at >= ?;",
                                         (since,)).fetchone()[0],
                "unpriced_tokens": int(conn.execute("SELECT COALESCE(SUM(llm_unpriced_tokens), 0) FROM op_tasks "
                                                    "WHERE created_at >= ?;", (since,)).fetchone()[0]),
            }

    def op_find_open_incident(self, source: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM op_tasks WHERE kind = 'incident' AND source = ? "
                "AND status NOT IN ('COMPLETED', 'FAILED', 'CANCELLED') ORDER BY created_at DESC LIMIT 1;",
                (source,)).fetchone()
            return dict(row) if row else None

    def task_responses_in_month(self, month_prefix: str) -> List[Dict[str, Any]]:
        """Việc giao máy trạm đã có phản hồi trong tháng `YYYY-MM` (thay logs/kpi_logs.csv): client_id, status, responded_at."""
        with self._get_connection() as conn:
            rows = conn.execute("SELECT client_id, status, responded_at FROM tasks "
                                "WHERE responded_at IS NOT NULL AND responded_at LIKE ?;", (f"{month_prefix}%",)).fetchall()
        return [dict(r) for r in rows]

    def count_tasks(self) -> Dict[str, int]:
        """Thống kê tổng số task theo trạng thái."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM tasks;")
                total = cursor.fetchone()[0]

                cursor.execute("SELECT COUNT(*) FROM tasks WHERE LOWER(status) = 'completed';")
                completed = cursor.fetchone()[0]

                cursor.execute("SELECT COUNT(*) FROM tasks WHERE LOWER(status) IN ('issue', 'error');")
                issues = cursor.fetchone()[0]

                return {
                    "total": total,
                    "completed": completed,
                    "issues": issues,
                    "pending": max(0, total - completed - issues),
                }

    # -----------------------------------------------------------------------
    # System Telemetry & Hardware Stats (100% Real, Zero Mock)
    # -----------------------------------------------------------------------

    def get_system_hardware_stats(self) -> Dict[str, Any]:
        """Thu thập thông số phần cứng thực tế (CPU, RAM, Disk, Uptime) qua psutil."""
        try:
            cpu_percent = psutil.cpu_percent(interval=None)
            mem = psutil.virtual_memory()
            disk = psutil.disk_usage("/")
            boot_time = psutil.boot_time()
            uptime_seconds = int(datetime.now().timestamp() - boot_time)

            return {
                "cpu_percent": round(cpu_percent, 1),
                "ram_percent": round(mem.percent, 1),
                "ram_used_gb": round(mem.used / (1024 ** 3), 2),
                "ram_total_gb": round(mem.total / (1024 ** 3), 2),
                "disk_percent": round(disk.percent, 1),
                "uptime_seconds": uptime_seconds,
            }
        except Exception as exc:
            logger.warning("Lỗi thu thập thông số phần cứng: %s", exc)
            return {
                "cpu_percent": 0.0,
                "ram_percent": 0.0,
                "ram_used_gb": 0.0,
                "ram_total_gb": 0.0,
                "disk_percent": 0.0,
                "uptime_seconds": 0,
            }


# Singleton instance
db_manager = DatabaseManager()
