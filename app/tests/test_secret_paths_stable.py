# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_secret_paths_stable.py
=================================
Khoá JWT và chứng chỉ TLS nằm ở <thư mục gốc dự án>/certs — không suy từ vị
trí file mã nguồn. Đổi vị trí module (refactor) hay chạy bản đóng gói mà
đường dẫn lệch là máy chủ sinh khoá JWT mới (mọi phiên đăng nhập mất hiệu lực)
và chứng chỉ mới.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_jwt_secret_and_tls_paths_are_under_project_certs():
    from mateai.config.loader import settings
    import mateai.application.security.auth_manager as am
    import mateai.infrastructure.security.tls as tls
    certs = Path(settings.PROJECT_ROOT) / "certs"
    assert Path(am.JWT_SECRET_FILE).resolve() == (certs / "jwt_secret.key").resolve()
    assert Path(tls.CERTS_DIR).resolve() == certs.resolve()
    assert Path(tls.CERT_FILE).resolve().parent == certs.resolve()
