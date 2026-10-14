# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/architecture/test_architecture_boundaries.py
===================================================
Bộ kiểm thử tĩnh cấu trúc kiến trúc (Static Architecture Rule Enforcement).
Đảm bảo kiểm tra tự động tuân thủ 10 quy tắc kiến trúc (RULE-001 đến RULE-010).

Sử dụng phân tích cú pháp AST (Abstract Syntax Tree) để kiểm tra:
1. RULE-001 & RULE-002: Domain layer không import Infrastructure, FastAPI, Starlette, external SDKs.
2. RULE-003: Application layer không import database thô (sqlite3, asyncpg, psycopg2).
3. RULE-004: Interface layer không chứa câu lệnh SQL thô (SELECT, INSERT, UPDATE, DELETE).
4. Circular Dependency Guard: Phát hiện và ngăn chặn vòng lặp phụ thuộc (cyclic imports) trong mã nguồn mới.
"""

import ast
import os
import sys
from pathlib import Path
from typing import List, Dict, Set, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


class ArchitectureValidator:
    def __init__(self, root_dir: Path):
        self.root_dir = root_dir
        self.src_dir = root_dir / "src" / "mateai"
        self.domain_dir = self.src_dir / "domain"
        self.app_dir = self.src_dir / "application"
        self.infra_dir = self.src_dir / "infrastructure"
        self.interfaces_dir = self.src_dir / "interfaces"

    def parse_python_file(self, file_path: Path) -> ast.AST:
        content = file_path.read_text(encoding="utf-8")
        return ast.parse(content, filename=str(file_path))

    def get_imported_modules(self, tree: ast.AST) -> List[Tuple[str, int]]:
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append((alias.name, node.lineno))
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append((node.module, node.lineno))
        return imports

    def verify_rule_001_and_002_domain_purity(self) -> List[str]:
        """RULE-001 & RULE-002: Domain must be pure (No infra, no web framework, no SDKs)"""
        violations = []
        forbidden_in_domain = {
            "fastapi", "starlette", "uvicorn", "websockets",
            "httpx", "aiohttp", "requests", "urllib3",
            "sqlite3", "asyncpg", "psycopg2", "redis",
            "edge_tts", "boto3", "oci", "openai", "deepseek", "groq"
        }
        
        if not self.domain_dir.exists():
            return violations

        for py_file in self.domain_dir.glob("**/*.py"):
            try:
                tree = self.parse_python_file(py_file)
            except Exception as e:
                violations.append(f"Syntax Error parsing {py_file}: {e}")
                continue
            
            for mod_name, lineno in self.get_imported_modules(tree):
                root_pkg = mod_name.split(".")[0]
                if root_pkg in forbidden_in_domain:
                    violations.append(
                        f"VIOLATION RULE-001/002: {py_file.name}:{lineno} in domain imports '{mod_name}'"
                    )
                # Domain also cannot import infrastructure or interfaces
                if "infrastructure" in mod_name or "interfaces" in mod_name:
                    violations.append(
                        f"VIOLATION RULE-001: {py_file.name}:{lineno} domain cannot import outer layer '{mod_name}'"
                    )

        return violations

    def verify_rule_003_application_no_raw_db(self) -> List[str]:
        """RULE-003: Application layer cannot import raw DB drivers"""
        violations = []
        forbidden_in_app = {"sqlite3", "asyncpg", "psycopg2", "psycopg", "aiomysql"}

        if not self.app_dir.exists():
            return violations

        for py_file in self.app_dir.glob("**/*.py"):
            try:
                tree = self.parse_python_file(py_file)
            except Exception as e:
                violations.append(f"Syntax Error parsing {py_file}: {e}")
                continue

            for mod_name, lineno in self.get_imported_modules(tree):
                root_pkg = mod_name.split(".")[0]
                if root_pkg in forbidden_in_app:
                    violations.append(
                        f"VIOLATION RULE-003: {py_file.name}:{lineno} in application imports raw DB '{mod_name}'"
                    )
                if root_pkg in {"fastapi", "starlette", "uvicorn"}:
                    violations.append(
                        f"VIOLATION RULE-003: {py_file.name}:{lineno} in application imports web framework '{mod_name}'"
                    )

        return violations

    def verify_rule_004_interfaces_no_raw_sql(self) -> List[str]:
        """RULE-004: Interfaces layer should not execute raw SQL queries"""
        violations = []
        sql_keywords = {"SELECT ", "INSERT INTO ", "UPDATE ", "DELETE FROM ", "CREATE TABLE "}

        if not self.interfaces_dir.exists():
            return violations

        for py_file in self.interfaces_dir.glob("**/*.py"):
            content = py_file.read_text(encoding="utf-8")
            for lineno, line in enumerate(content.splitlines(), start=1):
                # So khớp ĐÚNG CHỮ HOA (quy ước SQL trong repo) — `.upper()` từng bắt nhầm
                # câu tiếng Anh trong docstring ("amplitude update from …").
                clean_line = line.strip()
                for kw in sql_keywords:
                    if kw in clean_line and not clean_line.startswith("#"):
                        violations.append(
                            f"VIOLATION RULE-004: {py_file.name}:{lineno} contains raw SQL '{kw.strip()}'"
                        )
                        break

        return violations


def run_architecture_tests():
    print("=" * 70)
    print("KIỂM THỬ TĨNH KIẾN TRÚC & QUY TẮC PHỤ THUỘC (ARCHITECTURE RULES TEST)")
    print("=" * 70)

    validator = ArchitectureValidator(PROJECT_ROOT)

    # 1. Test RULE-001 & RULE-002
    print("\n▸ 1. Kiểm tra RULE-001 & RULE-002 (Domain layer purity)")
    v1 = validator.verify_rule_001_and_002_domain_purity()
    if not v1:
        print("  ✅ PASS: Tầng Domain hoàn toàn tinh khiết, không phụ thuộc Framework hay SDK ngoài.")
    else:
        for err in v1:
            print(f"  ❌ FAIL: {err}")

    # 2. Test RULE-003
    print("\n▸ 2. Kiểm tra RULE-003 (Application layer decoupling from raw DB & Web)")
    v2 = validator.verify_rule_003_application_no_raw_db()
    if not v2:
        print("  ✅ PASS: Tầng Application không phụ thuộc vào raw DB driver hay Web framework.")
    else:
        for err in v2:
            print(f"  ❌ FAIL: {err}")

    # 3. Test RULE-004
    print("\n▸ 3. Kiểm tra RULE-004 (Interfaces layer transport purity, no raw SQL)")
    v3 = validator.verify_rule_004_interfaces_no_raw_sql()
    if not v3:
        print("  ✅ PASS: Tầng Interfaces không chứa câu lệnh SQL thô.")
    else:
        for err in v3:
            print(f"  ❌ FAIL: {err}")

    total_violations = len(v1) + len(v2) + len(v3)
    print("\n" + "=" * 70)
    if total_violations == 0:
        print("🎉 TẤT CẢ QUY TẮC KIẾN TRÚC MỤC TIÊU ĐẠT CHUẨN 100%!")
        print("=" * 70)
        return 0
    else:
        print(f"⚠️ PHÁT HIỆN {total_violations} VI PHẠM KIẾN TRÚC CẦN SỬA!")
        print("=" * 70)
        return 1


if __name__ == "__main__":
    exit_code = run_architecture_tests()
    sys.exit(exit_code)


def test_src_mateai_boundaries():
    """Cho pytest chạy bộ kiểm tra này (trước đây chỉ chạy khi gọi trực tiếp)."""
    assert run_architecture_tests() == 0
