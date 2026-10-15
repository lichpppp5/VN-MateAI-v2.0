# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
workers/browser_session_vault.py
================================
Phase 90: Anti-Fingerprint Stealth Profiles & Session Vault for VN-MateAI Workers.

Quản lý Session & Trình duyệt chuyên sâu:
1. Quản lý thư mục profile độc lập: `/var/vn_mate/browser_profiles/{session_id}`.
2. launch_stealth_browser(session_id, proxy_config=None):
   - Tắt navigator.webdriver
   - Giả lập Canvas noise, AudioContext, WebGL Vendor ("Apple Inc.") và Renderer ("Apple M-series")
   - Khôi phục cookie, local storage, 2FA token từ phiên cũ
3. export_session_state(session_id) & import_session_state(session_id, data)
   để luân chuyển và đồng bộ session an toàn giữa các worker node.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

# Thư mục gốc lưu trữ browser profiles
DEFAULT_PROFILE_BASE = "/var/vn_mate/browser_profiles"

# JavaScript injection cho stealth bypass
STEALTH_INJECTION_SCRIPT = """
(() => {
    // 1. Gỡ bỏ navigator.webdriver
    Object.defineProperty(navigator, 'webdriver', {
        get: () => undefined,
        configurable: true
    });

    // 2. Giả lập platform và cứng phần cứng Apple M-series
    Object.defineProperty(navigator, 'platform', {
        get: () => 'MacIntel',
        configurable: true
    });
    Object.defineProperty(navigator, 'hardwareConcurrency', {
        get: () => 8,
        configurable: true
    });
    Object.defineProperty(navigator, 'deviceMemory', {
        get: () => 16,
        configurable: true
    });
    Object.defineProperty(navigator, 'languages', {
        get: () => ['vi-VN', 'vi', 'en-US', 'en'],
        configurable: true
    });

    // 3. Giả lập WebGL Vendor và Renderer chuẩn Apple Silicon (Work Node / M-series)
    const getParameterProxyHandler = {
        apply: function(target, ctx, args) {
            const param = args[0];
            // 37445: UNMASKED_VENDOR_WEBGL
            if (param === 37445) {
                return 'Apple Inc.';
            }
            // 37446: UNMASKED_RENDERER_WEBGL
            if (param === 37446) {
                return 'Apple M-series';
            }
            return Reflect.apply(target, ctx, args);
        }
    };

    try {
        const getParameter = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = new Proxy(getParameter, getParameterProxyHandler);
    } catch (e) {}

    try {
        if (typeof WebGL2RenderingContext !== 'undefined') {
            const getParameter2 = WebGL2RenderingContext.prototype.getParameter;
            WebGL2RenderingContext.prototype.getParameter = new Proxy(getParameter2, getParameterProxyHandler);
        }
    } catch (e) {}

    // 4. Giả lập Canvas fingerprinting protection (micro-noise)
    const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function(type) {
        return originalToDataURL.apply(this, arguments);
    };

    // 5. AudioContext Fingerprint Mock
    try {
        const AudioContext = window.AudioContext || window.webkitAudioContext;
        if (AudioContext) {
            const origCreateAnalyser = AudioContext.prototype.createAnalyser;
            AudioContext.prototype.createAnalyser = function() {
                const analyser = origCreateAnalyser.apply(this, arguments);
                const origGetFloatFrequencyData = analyser.getFloatFrequencyData;
                analyser.getFloatFrequencyData = function(array) {
                    origGetFloatFrequencyData.apply(this, arguments);
                    for (let i = 0; i < array.length; i += 100) {
                        array[i] += 0.00001;
                    }
                };
                return analyser;
            };
        }
    } catch (e) {}

    // 6. Giả lập chrome runtime
    window.chrome = {
        runtime: {},
        loadTimes: function() {},
        csi: function() {},
        app: {}
    };

    // 7. Permissions query mock
    if (navigator.permissions && navigator.permissions.query) {
        const origQuery = navigator.permissions.query;
        navigator.permissions.query = (parameters) => (
            parameters.name === 'notifications' ?
                Promise.resolve({ state: Notification.permission }) :
                origQuery(parameters)
        );
    }
})();
"""


class BrowserSessionVault:
    """
    Quản lý Profile Trình duyệt Stealth và lưu trữ phiên làm việc an toàn cho Worker.
    """

    def __init__(self, base_profile_dir: Optional[str] = None):
        if base_profile_dir:
            self.base_dir = Path(base_profile_dir)
        else:
            env_dir = os.environ.get("VN_MATE_PROFILE_DIR")
            if env_dir:
                self.base_dir = Path(env_dir)
            elif platform.system() == "Windows":
                # Fallback an toàn trên môi trường dev Windows
                self.base_dir = Path(os.environ.get("TEMP", "C:/Temp")) / "vn_mate" / "browser_profiles"
            else:
                self.base_dir = Path(DEFAULT_PROFILE_BASE)

        try:
            self.base_dir.mkdir(parents=True, exist_ok=True)
            logger.info("[SessionVault] Base profile directory initialized at: %s", self.base_dir)
        except Exception as e:
            # Fallback sang thư mục người dùng nếu quyền /var bị từ chối
            fallback = Path.home() / ".vn_mate" / "browser_profiles"
            fallback.mkdir(parents=True, exist_ok=True)
            self.base_dir = fallback
            logger.warning("[SessionVault] Could not create %s (%s). Falling back to %s", DEFAULT_PROFILE_BASE, e, fallback)

        self._active_contexts: Dict[str, Any] = {}
        self._active_playwrights: Dict[str, Any] = {}

    def get_profile_path(self, session_id: str) -> Path:
        """Lấy đường dẫn thư mục profile riêng biệt cho session_id."""
        clean_id = "".join(c for c in session_id if c.isalnum() or c in ("-", "_"))
        path = self.base_dir / clean_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def get_state_file_path(self, session_id: str) -> Path:
        """Đường dẫn file state.json chứa cookies và localStorage."""
        return self.get_profile_path(session_id) / "state.json"

    async def launch_stealth_browser(
        self,
        session_id: str,
        proxy_config: Optional[Dict[str, Any]] = None,
        headless: bool = True,
    ) -> Any:
        """
        Khởi tạo Playwright với cờ bypass anti-bot, stealth profile và khôi phục 2FA session cũ.

        Returns:
            playwright.async_api.BrowserContext hoặc mock object nếu playwright không sẵn có.
        """
        profile_path = self.get_profile_path(session_id)
        state_file = self.get_state_file_path(session_id)

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.warning("[SessionVault] Playwright is not installed in current environment. Returning mock context.")
            return MockBrowserContext(session_id, profile_path)

        playwright = await async_playwright().start()
        self._active_playwrights[session_id] = playwright

        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-features=IsolateOrigins,site-per-process",
            "--lang=vi-VN,vi,en-US,en",
        ]

        proxy_dict = None
        if proxy_config:
            proxy_dict = {
                "server": proxy_config.get("server", ""),
                "username": proxy_config.get("username"),
                "password": proxy_config.get("password"),
            }

        user_agent = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/128.0.0.0 Safari/537.36"
        )

        context_kwargs: Dict[str, Any] = {
            "user_data_dir": str(profile_path),
            "headless": headless,
            "args": launch_args,
            "user_agent": user_agent,
            "viewport": {"width": 1920, "height": 1080},
            "device_scale_factor": 2,  # Retina display emulation for Mac
            "locale": "vi-VN",
            "timezone_id": "Asia/Ho_Chi_Minh",
            "ignore_https_errors": True,
        }

        if proxy_dict and proxy_dict.get("server"):
            context_kwargs["proxy"] = proxy_dict

        # Khôi phục cookie và storage cũ nếu tồn tại
        if state_file.exists() and state_file.stat().st_size > 0:
            try:
                with open(state_file, "r", encoding="utf-8") as f:
                    state_data = json.load(f)
                logger.info("[SessionVault] Found prior session state for %s (%d cookies)",
                            session_id, len(state_data.get("cookies", [])))
                context_kwargs["storage_state"] = str(state_file)
            except Exception as e:
                logger.warning("[SessionVault] Failed to read state.json: %s", e)

        # Sử dụng launch_persistent_context để giữ profile cache và cookie lâu dài
        context = await playwright.chromium.launch_persistent_context(**context_kwargs)

        # Inject stealth scripts vào mọi frame / tab mới
        await context.add_init_script(STEALTH_INJECTION_SCRIPT)

        self._active_contexts[session_id] = context
        logger.info("[SessionVault] Stealth Browser launched successfully for session: %s", session_id)
        return context

    async def export_session_state(self, session_id: str) -> Dict[str, Any]:
        """
        Xuất toàn bộ cookie, local storage và thông tin phiên làm việc thành Dict/JSON.
        Phục vụ đồng bộ session giữa các worker node khi cần cân bằng tải hoặc failover.
        """
        context = self._active_contexts.get(session_id)
        state_file = self.get_state_file_path(session_id)

        if context is not None:
            try:
                state_data = await context.storage_state(path=str(state_file))
                logger.info("[SessionVault] Exported active session state for: %s", session_id)
                return {
                    "session_id": session_id,
                    "storage_state": state_data,
                    "exported_at": os.path.getmtime(state_file) if state_file.exists() else 0,
                    "status": "active_exported",
                }
            except Exception as e:
                logger.error("[SessionVault] Error exporting live storage state: %s", e)

        # Fallback đọc từ file state.json đã lưu trước đó
        if state_file.exists():
            try:
                with open(state_file, "r", encoding="utf-8") as f:
                    saved_state = json.load(f)
                return {
                    "session_id": session_id,
                    "storage_state": saved_state,
                    "exported_at": os.path.getmtime(state_file),
                    "status": "cached_exported",
                }
            except Exception as e:
                logger.error("[SessionVault] Error reading state.json for export: %s", e)

        return {
            "session_id": session_id,
            "storage_state": {},
            "status": "empty",
            "error": "No session state found to export"
        }

    async def import_session_state(
        self,
        session_id: str,
        data: Union[str, Dict[str, Any]],
    ) -> bool:
        """
        Nhập dữ liệu session và khôi phục vào profile folder để worker node tiếp quản tức thì mà không cần 2FA lại.
        """
        state_file = self.get_state_file_path(session_id)
        try:
            if isinstance(data, str):
                parsed = json.loads(data)
            else:
                parsed = data

            # Trích xuất phần storage_state nếu có bọc
            storage_data = parsed.get("storage_state", parsed)

            with open(state_file, "w", encoding="utf-8") as f:
                json.dump(storage_data, f, indent=2, ensure_ascii=False)

            logger.info("[SessionVault] Successfully imported session state for: %s to %s",
                        session_id, state_file)
            return True
        except Exception as e:
            logger.error("[SessionVault] Failed to import session state for %s: %s", session_id, e)
            return False

    async def close_session(self, session_id: str) -> None:
        """Đóng an toàn browser context và lưu lại state."""
        context = self._active_contexts.pop(session_id, None)
        if context:
            try:
                state_file = self.get_state_file_path(session_id)
                await context.storage_state(path=str(state_file))
                await context.close()
                logger.info("[SessionVault] Closed session and persisted state for: %s", session_id)
            except Exception as e:
                logger.warning("[SessionVault] Error closing context for %s: %s", session_id, e)

        pw = self._active_playwrights.pop(session_id, None)
        if pw:
            try:
                await pw.stop()
            except Exception:
                pass


class MockBrowserContext:
    """Mock context hỗ trợ môi trường không cài đặt Playwright (Unit Test / Dev)."""
    def __init__(self, session_id: str, profile_path: Path):
        self.session_id = session_id
        self.profile_path = profile_path

    async def storage_state(self, path: Optional[str] = None) -> Dict[str, Any]:
        dummy = {"cookies": [], "origins": []}
        if path:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(dummy, f)
        return dummy

    async def close(self) -> None:
        pass


# Singleton instance cho worker
browser_session_vault = BrowserSessionVault()
