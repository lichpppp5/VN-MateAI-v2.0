# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_device_paths_stable.py
=================================
Đường dẫn của nhóm Devices trỏ đúng file/thư mục thật dù module nằm ở đâu:
thư mục gốc dự án (xiaozhi, task_manager), file mẫu wake word, script widget.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mateai.config.loader import settings  # noqa: E402

ROOT = Path(settings.PROJECT_ROOT).resolve()


def test_device_paths():
    xg = importlib.import_module("mateai.interfaces.websocket.xiaozhi_gateway")
    tm = importlib.import_module("mateai.application.devices.task_manager")
    ww = importlib.import_module("mateai.infrastructure.audio.wake_word_engine")
    vc = importlib.import_module("mateai.interfaces.desktop.voice_controller")
    assert Path(xg._PROJECT_ROOT).resolve() == ROOT
    assert Path(tm._LOGS_DIR).resolve() == ROOT / "logs"
    assert Path(ww._PATTERNS_FILE).resolve() == ROOT / "wake_word_patterns.json"
    assert os.path.isfile(vc.WIDGET_SCRIPT), vc.WIDGET_SCRIPT
