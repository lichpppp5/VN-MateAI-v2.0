# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
main.py
=======
VN-MateAI — Entry Point & Application Orchestrator.

Startup sequence:
  1. Load and validate config (via config_loader singleton).
  2. Build a tray icon using PIL + pystray.
  3. Launch Uvicorn (FastAPI) in a daemon thread.
  4. Hand control to the pystray event loop (blocks until Quit is selected).
  5. Graceful shutdown: stop Uvicorn, release tray icon, join threads.

Global exception handler:
  - Uncaught exceptions are caught, logged, and displayed as a native
    Windows MessageBox so the user is notified even without a console.

PyInstaller compatibility:
  - sys.frozen detection is handled inside config_loader.py.
  - Icon and assets are bundled via --add-data "skills;skills".
"""

from __future__ import annotations

import ctypes
import logging
import platform
import re
import signal
import sys
import threading
import traceback
from io import BytesIO
from typing import Optional

# ---------------------------------------------------------------------------
# Bootstrap: ensure project root is on sys.path before local imports.
# This is required for PyInstaller --onefile / --onedir bundles.
# ---------------------------------------------------------------------------
import os
from pathlib import Path

if getattr(sys, "frozen", False):
    _ROOT = Path(sys.executable).parent
else:
    _ROOT = Path(__file__).resolve().parent

if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ---------------------------------------------------------------------------
# Python Windows asyncio Proactor Fix:
# Silence WinError 10054 ConnectionResetError in _call_connection_lost
# when browser tabs or WebSockets disconnect.
# ---------------------------------------------------------------------------
if sys.platform == "win32":
    try:
        import asyncio.proactor_events
        _orig_call_connection_lost = asyncio.proactor_events._ProactorBasePipeTransport._call_connection_lost

        def _safe_call_connection_lost(self, exc):
            try:
                _orig_call_connection_lost(self, exc)
            except (ConnectionResetError, ConnectionAbortedError, OSError):
                pass

        asyncio.proactor_events._ProactorBasePipeTransport._call_connection_lost = _safe_call_connection_lost
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Local imports (after path fix)
# ---------------------------------------------------------------------------
from mateai.config.loader import settings  # noqa: E402  (path must be set first)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional Windows imports
# ---------------------------------------------------------------------------
try:
    import pystray  # type: ignore[import]
    from PIL import Image, ImageDraw  # type: ignore[import]

    _TRAY_AVAILABLE = True
except ImportError:
    _TRAY_AVAILABLE = False
    logger.warning(
        "pystray / Pillow not installed. Running in headless (no-tray) mode. "
        "Install with: pip install pystray Pillow"
    )

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------
_server_thread: Optional[threading.Thread] = None
_uvicorn_server: Optional[object] = None  # uvicorn.Server instance
_tray_icon: Optional[object] = None  # pystray.Icon instance
_shutdown_event: threading.Event = threading.Event()

# Phase 13: Voice Controller (wake word + floating widget)
try:
    from mateai.interfaces.desktop.voice_controller import voice_controller as _voice_controller
    _VOICE_AVAILABLE = True
except ImportError:
    _VOICE_AVAILABLE = False
    logger.warning("VoiceController not available (missing deps). Run: pip install SpeechRecognition pyaudio numpy")

# Phase 23: Preload local Whisper ASR model into RAM in background thread
try:
    from mateai.infrastructure.audio.audio_processor import preload_whisper_model
    preload_whisper_model()
    # Làm nóng cache TTS chạy trong lúc server khởi động (warmup_acoustic_ack_cache).
except Exception as _audio_preload_exc:
    logger.debug("Audio preload/prewarm skipped: %s", _audio_preload_exc)


# ---------------------------------------------------------------------------
# Utility: Native Windows error dialog
# ---------------------------------------------------------------------------


def _show_error_dialog(title: str, message: str) -> None:
    """Display a native error MessageBox on Windows; print to stderr elsewhere."""
    if platform.system() == "Windows":
        try:
            ctypes.windll.user32.MessageBoxW(0, message, title, 0x10)  # MB_ICONERROR
            return
        except AttributeError:
            pass
    print(f"[ERROR] {title}\n{message}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Global exception hook
# ---------------------------------------------------------------------------


def _global_exception_handler(
    exc_type: type,
    exc_value: BaseException,
    exc_tb: object,
) -> None:
    """
    Catch-all handler for uncaught exceptions.
    Logs to file and shows a Windows dialog so the user is not left wondering.
    """
    if issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return

    tb_str = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    logger.critical("Uncaught exception:\n%s", tb_str)

    _show_error_dialog(
        "VN-MateAI — Lỗi nghiêm trọng",
        f"Đã xảy ra lỗi không mong muốn:\n\n{exc_type.__name__}: {exc_value}\n\n"
        f"Kiểm tra log để biết chi tiết đầy đủ.",
    )


sys.excepthook = _global_exception_handler


# ---------------------------------------------------------------------------
# Tray icon generator
# ---------------------------------------------------------------------------


def _build_tray_icon_image() -> "Image.Image":
    """
    Generate a simple 64×64 tray icon using PIL.
    Returns a PIL Image object (no external file dependency).
    """
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Background circle — deep navy
    draw.ellipse([2, 2, size - 2, size - 2], fill=(18, 40, 80, 255))

    # Stylised "AI" text in centre
    try:
        draw.text((14, 18), "AI", fill=(0, 200, 255, 255))
    except Exception:  # PIL default font fallback
        draw.text((18, 22), "V", fill=(0, 200, 255, 255))

    # Small accent dot (status indicator — green = running)
    draw.ellipse([46, 46, 60, 60], fill=(0, 220, 100, 255))

    return img


# ---------------------------------------------------------------------------
# Uvicorn server lifecycle
# ---------------------------------------------------------------------------


def _install_log_filters() -> None:
    """Che token trong access log của uvicorn (token qua query string ở <audio src>, WebSocket…)
    và gắn request_id — dùng CHUNG bộ lọc với log ứng dụng (`mateai.config.log_setup`)."""
    from mateai.config.log_setup import ContextFilter
    for name in ("uvicorn.access", "uvicorn.error", "uvicorn"):
        log = logging.getLogger(name)
        if not any(isinstance(f, ContextFilter) for f in log.filters):
            log.addFilter(ContextFilter())


def _start_uvicorn() -> None:
    """
    Run both Primary HTTPS (settings.PORT) and IoT Plain WS (settings.IOT_PORT) concurrently
    on the EXACT SAME asyncio event loop.
    Eliminates cross-loop Future exceptions and guarantees thread-safe WebSocket/audio streaming.
    """
    import asyncio
    import uvicorn  # type: ignore[import]
    from mateai.interfaces.http.server import app  # noqa: F401
    from mateai.infrastructure.security.tls import ensure_ssl_certs

    cert_path, key_path = ensure_ssl_certs()

    _install_log_filters()

    config_ssl = uvicorn.Config(
        app=app,
        host=settings.HOST,
        port=settings.PORT,
        ssl_keyfile=key_path,
        ssl_certfile=cert_path,
        log_level=settings.LOG_LEVEL.lower(),
        loop="asyncio",
        reload=False,
    )
    from mateai.interfaces.http.server import iot_listener_app

    config_iot = uvicorn.Config(
        # Cổng không TLS: chỉ đường WS của thiết bị + health probe (xem server.iot_listener_app).
        app=iot_listener_app,
        host="0.0.0.0",
        port=settings.IOT_PORT,
        log_level="warning",
        loop="asyncio",
        reload=False,
    )

    global _uvicorn_server
    _uvicorn_server = uvicorn.Server(config_ssl)
    server_iot = uvicorn.Server(config_iot)

    async def _serve_both():
        logger.info(
            "Uvicorn Dual Listeners starting: HTTPS on https://%s:%d & IoT WS on ws://0.0.0.0:%d",
            settings.HOST,
            settings.PORT,
            settings.IOT_PORT,
        )
        await asyncio.gather(
            _uvicorn_server.serve(),
            server_iot.serve(),
        )

    asyncio.run(_serve_both())


def _launch_server_thread() -> threading.Thread:
    """Start Uvicorn dual-listener server in a daemon thread and return the thread handle."""
    thread = threading.Thread(
        target=_start_uvicorn,
        name="uvicorn-server",
        daemon=True,
    )
    thread.start()
    logger.info("Server thread started (tid=%d).", thread.ident or 0)
    return thread


# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------


def _shutdown(icon: Optional[object] = None, item: Optional[object] = None) -> None:
    """
    Graceful shutdown sequence:
      1. Stop VoiceController (Phase 13).
      2. Signal Uvicorn to stop.
      3. Remove the tray icon.
      4. Set the global shutdown event so the main loop exits.
    """
    logger.info("Shutdown initiated.")

    global _uvicorn_server, _tray_icon

    # Phase 13: Stop wake word engine & voice widget
    if _VOICE_AVAILABLE:
        try:
            _voice_controller.stop()
            logger.info("VoiceController stopped.")
        except Exception as exc:
            logger.warning("Could not stop VoiceController: %s", exc)

    if _uvicorn_server is not None:
        try:
            _uvicorn_server.should_exit = True  # type: ignore[attr-defined]
            logger.info("Uvicorn shutdown signal sent.")
        except Exception as exc:
            logger.warning("Could not signal Uvicorn: %s", exc)

    if _tray_icon is not None:
        try:
            _tray_icon.stop()  # type: ignore[attr-defined]
        except Exception as exc:
            logger.warning("Could not stop tray icon: %s", exc)

    _shutdown_event.set()


# ---------------------------------------------------------------------------
# Signal handlers (Ctrl+C / SIGTERM)
# ---------------------------------------------------------------------------


def _handle_sigint(signum: int, frame: object) -> None:
    logger.info("SIGINT received — shutting down.")
    _shutdown()


signal.signal(signal.SIGINT, _handle_sigint)
if hasattr(signal, "SIGTERM"):
    signal.signal(signal.SIGTERM, _handle_sigint)


# ---------------------------------------------------------------------------
# Tray menu builder
# ---------------------------------------------------------------------------


def _get_mic_menu_label(item=None) -> str:
    """Return current mic state label for tray menu."""
    try:
        from mateai.infrastructure.audio.wake_word_engine import is_mic_enabled
        return "🎙 Tắt Lắng Nghe Ngầm" if is_mic_enabled() else "🎙 Bật Lắng Nghe Ngầm (Wake Word)"
    except Exception:
        return "🎙 Lắng Nghe Ngầm"


def _toggle_mic_listening(icon: object, item: object) -> None:
    """Toggle microphone hardware state and rebuild tray menu."""
    try:
        from mateai.infrastructure.audio.wake_word_engine import is_mic_enabled, set_mic_enabled
        new_state = not is_mic_enabled()
        set_mic_enabled(new_state)
        state_label = "BẬT" if new_state else "TẮT"
        logger.info("Tray: Mic listening toggled -> %s", state_label)
        # Rebuild menu to reflect new label
        if _tray_icon is not None:
            try:
                _tray_icon.menu = _build_tray_menu()  # type: ignore[attr-defined]
                _tray_icon.update_menu()              # type: ignore[attr-defined]
            except Exception:
                pass
        # Show tray notification
        if _tray_icon and hasattr(_tray_icon, "notify"):
            try:
                _tray_icon.notify(  # type: ignore[attr-defined]
                    f"Micro lắng nghe ngầm: {state_label}",
                    "VN-MateAI",
                )
            except Exception:
                pass
    except Exception as exc:
        logger.error("Tray: Failed to toggle mic: %s", exc)


def _build_tray_menu() -> "pystray.Menu":
    """Build the right-click menu for the system tray icon."""
    return pystray.Menu(  # type: ignore[name-defined]
        pystray.MenuItem(  # type: ignore[name-defined]
            "VN-MateAI v1.0",
            lambda icon, item: None,
            enabled=False,
        ),
        pystray.Menu.SEPARATOR,  # type: ignore[name-defined]
        pystray.MenuItem(  # type: ignore[name-defined]
            f"API: https://{settings.HOST}:{settings.PORT}",
            lambda icon, item: None,
            enabled=False,
        ),
        pystray.MenuItem(  # type: ignore[name-defined]
            f"Model: {settings.MODEL_NAME}",
            lambda icon, item: None,
            enabled=False,
        ),
        pystray.Menu.SEPARATOR,  # type: ignore[name-defined]
        pystray.MenuItem(  # type: ignore[name-defined]
            _get_mic_menu_label,
            _toggle_mic_listening,
        ),
        pystray.Menu.SEPARATOR,  # type: ignore[name-defined]
        pystray.MenuItem(  # type: ignore[name-defined]
            "Mở Tài liệu API",
            _open_docs,
        ),
        pystray.MenuItem(  # type: ignore[name-defined]
            "Tải lại Skills",
            _reload_skills,
        ),
        pystray.Menu.SEPARATOR,  # type: ignore[name-defined]
        pystray.MenuItem(  # type: ignore[name-defined]
            "Thoát",
            _shutdown,
            default=True,
        ),
    )


def _open_docs(icon: object, item: object) -> None:
    """Open the FastAPI Swagger UI in the default browser."""
    import webbrowser
    url = f"https://localhost:{settings.PORT}/docs"
    webbrowser.open(url)
    logger.info("Opened docs at %s", url)


def _reload_skills(icon: object, item: object) -> None:
    """Hot-reload all skill plugins from the skills/ directory."""
    from core.plugin_manager import plugin_manager
    count = plugin_manager.load_plugins()
    logger.info("Manual skill reload: %d skill(s) loaded.", count)
    if _tray_icon and hasattr(_tray_icon, "notify"):
        try:
            _tray_icon.notify(  # type: ignore[attr-defined]
                f"Đã tải lại {count} skill(s).",
                "VN-MateAI",
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """
    Application entry point.

    Launch order:
      1. Pre-flight checks.
      2. Start Uvicorn in daemon thread.
      3. Start pystray icon (blocks the main thread until Quit).
      4. Fallback: headless mode if pystray/PIL is unavailable.
    """
    global _server_thread, _tray_icon

    from mateai.infrastructure.security.tls import ensure_ssl_certs
    ensure_ssl_certs()

    from mateai.application.security.identity_guard import verify_and_restore_identity
    verify_and_restore_identity()

    logger.info(
        "=== VN-MateAI starting up (HTTPS/WSS) | Model: %s | API: https://%s:%d ===",
        settings.MODEL_NAME,
        settings.HOST,
        settings.PORT,
    )

    # --- Launch Uvicorn ---
    _server_thread = _launch_server_thread()

    # --- Phase 13: Start Wake Word Engine (after a short delay for server init) ---
    if _VOICE_AVAILABLE:
        def _delayed_voice_start() -> None:
            import time as _time
            _time.sleep(2.0)  # Wait for uvicorn to be ready
            try:
                import asyncio as _asyncio
                # Get the running loop from uvicorn's thread
                loop = None
                for t in threading.enumerate():
                    if t.name == "uvicorn-server" and hasattr(t, "_target"):
                        break
                _voice_controller.start(loop=loop)
                logger.info("Phase 13: VoiceController started — Say 'Hey Lyly' to activate!")
            except Exception as exc:
                logger.warning("Phase 13: VoiceController start failed: %s", exc)

        _voice_thread = threading.Thread(target=_delayed_voice_start, daemon=True)
        _voice_thread.start()

    is_windows = platform.system() == "Windows"
    if _TRAY_AVAILABLE and is_windows:
        # --- Build and run tray icon (Windows Desktop) ---
        icon_image = _build_tray_icon_image()
        menu = _build_tray_menu()
        global _tray_icon
        _tray_icon = pystray.Icon(  # type: ignore[name-defined]
            name="vn_mate_ai",
            icon=icon_image,
            title="VN-MateAI — Đang chạy",
            menu=menu,
        )
        logger.info("System tray icon initialised. Right-click to manage.")
        _tray_icon.run()  # Blocks main thread until icon.stop() is called

    else:
        # --- Headless mode (no tray; e.g., on macOS dev box / Linux server) ---
        logger.info(
            "Running in headless mode. "
            "API available at https://%s:%d  Press Ctrl+C to exit.",
            settings.HOST,
            settings.PORT,
        )
        _shutdown_event.wait()  # Block main thread until shutdown event fires

    # Wait for server thread to finish
    if _server_thread and _server_thread.is_alive():
        _server_thread.join(timeout=5)

    logger.info("=== VN-MateAI shutdown complete ===")


if __name__ == "__main__":
    main()
