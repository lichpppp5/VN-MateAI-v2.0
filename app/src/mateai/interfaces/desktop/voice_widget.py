# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/voice_widget.py
====================
Siri-Style Floating Voice Widget — Phase 13.

Giao diện nổi (Floating UI) hiển thị ở góc dưới phải màn hình khi wake word
được phát hiện. Thiết kế: vòng tròn sóng âm động (circular wave animation)
kiểu Siri/Google Assistant.

Tính năng:
  - Cửa sổ trong suốt, không có viền (overrideredirect), luôn nổi trên cùng.
  - Hiệu ứng sóng âm đồng tâm (3 vòng) pulsing theo biên độ microphone.
  - 3 trạng thái: IDLE (ẩn), LISTENING (sóng xanh), PROCESSING (vòng quay).
  - Tự động ẩn sau timeout hoặc khi nhận được kết quả từ LLM.
  - Hiển thị text cuối cùng của transcript và phản hồi AI.

Ràng buộc kỹ thuật:
  - Tkinter PHẢI chạy trên main thread (macOS: NSThread restriction).
  - Trên Windows: có thể chạy trong thread bình thường nhưng khuyến nghị
    dùng process riêng (subprocess) tương tự popup_ui.py.
  - File này được thiết kế để chạy ĐỘC LẬP qua: python -m mateai.interfaces.desktop.voice_widget
    hoặc được import và gọi run_widget() từ main thread.

IPC Protocol (stdin JSON lines):
  Khi chạy độc lập (subprocess mode), widget đọc JSON từ stdin:
    {"cmd": "show", "mode": "listening"}
    {"cmd": "show", "mode": "processing"}
    {"cmd": "hide"}
    {"cmd": "text", "content": "Xin chào Anh!"}
    {"cmd": "amplitude", "value": 0.75}
    {"cmd": "quit"}
"""

from __future__ import annotations

import json
import logging
import math
import sys
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Guard: skip Tkinter import errors gracefully
# ---------------------------------------------------------------------------
try:
    import tkinter as tk
    from tkinter import font as tkfont
    _TK_AVAILABLE = True
except ImportError:
    _TK_AVAILABLE = False

# ---------------------------------------------------------------------------
# Color Palette & Config
# ---------------------------------------------------------------------------

COLORS = {
    "bg": "#000000",              # Background (transparent key color)
    "canvas_bg": "#000000",
    "idle_wave": "#1a3a5c",       # Deep navy when idle
    "listen_core": "#00c8ff",     # Cyan core (Siri blue)
    "listen_wave1": "#0088cc",    # Wave ring 1
    "listen_wave2": "#004477",    # Wave ring 2
    "listen_wave3": "#002244",    # Wave ring 3
    "process_arc": "#7c3aed",     # Purple arc (processing)
    "process_trail": "#3b0764",   # Purple trail
    "text_primary": "#e2e8f0",    # Primary text
    "text_sub": "#64748b",        # Subtitle text
    "text_ai": "#00c8ff",         # AI response text
    "dot_active": "#22c55e",      # Green status dot
    "dot_process": "#f59e0b",     # Amber status dot
}

WIDGET_SIZE = 200          # Canvas width & height
CORE_RADIUS = 32           # Central circle radius
WAVE_RINGS = 3             # Number of concentric wave rings
ANIMATION_FPS = 30         # Frames per second
FRAME_MS = 1000 // ANIMATION_FPS
IDLE_TIMEOUT_SEC = 8.0     # Auto-hide after this many seconds of LISTENING
CORNER_PADDING = 20        # Distance from screen edge


# ---------------------------------------------------------------------------
# VoiceWidget
# ---------------------------------------------------------------------------

class VoiceWidget:
    """
    Floating Siri-style voice widget.
    Must be created and run on the main thread.
    """

    # State constants
    STATE_IDLE = "idle"
    STATE_LISTENING = "listening"
    STATE_PROCESSING = "processing"

    def __init__(self) -> None:
        if not _TK_AVAILABLE:
            raise RuntimeError("tkinter not available.")

        self._state = self.STATE_IDLE
        self._amplitude: float = 0.0
        self._target_amplitude: float = 0.0
        self._phase: float = 0.0              # Wave animation phase
        self._arc_angle: float = 0.0          # Processing arc rotation
        self._transcript_text: str = ""
        self._response_text: str = ""
        self._hide_timer: Optional[str] = None
        self._visible = False

        # External amplitude injection (thread-safe)
        self._amplitude_lock = threading.Lock()
        self._pending_amplitude: Optional[float] = None

        self._build_ui()

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        """Build the floating transparent Tkinter window."""
        self._root = tk.Tk()
        self._root.title("VN-MateAI Voice")
        self._root.overrideredirect(True)          # No title bar / border
        self._root.attributes("-topmost", True)    # Always on top
        self._root.resizable(False, False)
        self._root.configure(bg=COLORS["bg"])

        # Transparent background (show desktop through black pixels)
        _platform = sys.platform
        if _platform == "win32":
            self._root.attributes("-transparentcolor", COLORS["bg"])
        elif _platform == "darwin":
            self._root.attributes("-transparent", True)
            self._root.configure(bg="systemTransparent")

        # Position: bottom-right corner
        self._root.geometry(f"{WIDGET_SIZE}x{WIDGET_SIZE + 60}")
        self._reposition()

        # Canvas for wave animation
        self._canvas = tk.Canvas(
            self._root,
            width=WIDGET_SIZE,
            height=WIDGET_SIZE,
            bg=COLORS["canvas_bg"],
            highlightthickness=0,
            bd=0,
        )
        self._canvas.pack(fill=tk.BOTH, expand=True)
        if _platform == "darwin":
            self._canvas.configure(bg="systemTransparent")

        # Text label: transcript / AI response
        self._text_var = tk.StringVar(value="")
        self._label = tk.Label(
            self._root,
            textvariable=self._text_var,
            bg=COLORS["bg"] if _platform == "win32" else "#0a0f1e",
            fg=COLORS["text_primary"],
            font=("Helvetica Neue", 9, "bold") if _platform == "darwin" else ("Segoe UI", 9, "bold"),
            wraplength=WIDGET_SIZE - 10,
            justify=tk.CENTER,
        )
        self._label.pack(pady=(0, 4))

        # Status dot label
        self._status_var = tk.StringVar(value="⬤  Idle")
        self._status_label = tk.Label(
            self._root,
            textvariable=self._status_var,
            bg=COLORS["bg"] if _platform == "win32" else "#0a0f1e",
            fg=COLORS["text_sub"],
            font=("Helvetica Neue", 8) if _platform == "darwin" else ("Segoe UI", 8),
        )
        self._status_label.pack()

        # Allow dragging
        self._drag_start_x = 0
        self._drag_start_y = 0
        self._canvas.bind("<ButtonPress-1>", self._on_drag_start)
        self._canvas.bind("<B1-Motion>", self._on_drag_motion)

        # Initially hidden
        self._root.withdraw()

    def _reposition(self) -> None:
        """Move widget to bottom-right corner of primary screen."""
        sw = self._root.winfo_screenwidth()
        sh = self._root.winfo_screenheight()
        x = sw - WIDGET_SIZE - CORNER_PADDING
        y = sh - WIDGET_SIZE - 60 - CORNER_PADDING - 40  # 40px taskbar offset
        self._root.geometry(f"{WIDGET_SIZE}x{WIDGET_SIZE + 60}+{x}+{y}")

    # ------------------------------------------------------------------
    # Drag support
    # ------------------------------------------------------------------

    def _on_drag_start(self, event: tk.Event) -> None:
        self._drag_start_x = event.x
        self._drag_start_y = event.y

    def _on_drag_motion(self, event: tk.Event) -> None:
        dx = event.x - self._drag_start_x
        dy = event.y - self._drag_start_y
        x = self._root.winfo_x() + dx
        y = self._root.winfo_y() + dy
        self._root.geometry(f"+{x}+{y}")

    # ------------------------------------------------------------------
    # State control (thread-safe via after())
    # ------------------------------------------------------------------

    def show_listening(self) -> None:
        """Switch to LISTENING state and show widget."""
        self._root.after(0, self._do_show_listening)

    def show_processing(self) -> None:
        """Switch to PROCESSING state."""
        self._root.after(0, self._do_show_processing)

    def hide(self) -> None:
        """Hide widget and reset to IDLE."""
        self._root.after(0, self._do_hide)

    def set_amplitude(self, value: float) -> None:
        """Thread-safe amplitude update from WakeWordEngine."""
        with self._amplitude_lock:
            self._pending_amplitude = max(0.0, min(1.0, value))

    def set_text(self, text: str) -> None:
        """Update displayed text (thread-safe)."""
        self._root.after(0, lambda: self._text_var.set(text[:80]))

    def _do_show_listening(self) -> None:
        self._state = self.STATE_LISTENING
        self._transcript_text = ""
        self._text_var.set("🎙  Đang lắng nghe...")
        self._status_var.set("⬤  Listening")
        self._status_label.configure(fg=COLORS["dot_active"])
        if not self._visible:
            self._root.deiconify()
            self._reposition()
            self._visible = True
        # Auto-hide after timeout
        if self._hide_timer:
            self._root.after_cancel(self._hide_timer)
        self._hide_timer = self._root.after(
            int(IDLE_TIMEOUT_SEC * 1000), self._do_hide
        )

    def _do_show_processing(self) -> None:
        self._state = self.STATE_PROCESSING
        self._text_var.set("⚙  Đang xử lý...")
        self._status_var.set("⬤  Processing")
        self._status_label.configure(fg=COLORS["dot_process"])
        # Cancel auto-hide during processing
        if self._hide_timer:
            self._root.after_cancel(self._hide_timer)
            self._hide_timer = None

    def _do_hide(self) -> None:
        self._state = self.STATE_IDLE
        self._visible = False
        self._amplitude = 0.0
        self._target_amplitude = 0.0
        self._text_var.set("")
        self._status_var.set("⬤  Idle")
        self._status_label.configure(fg=COLORS["text_sub"])
        self._root.withdraw()
        if self._hide_timer:
            self._root.after_cancel(self._hide_timer)
            self._hide_timer = None

    # ------------------------------------------------------------------
    # Animation Loop
    # ------------------------------------------------------------------

    def _animate(self) -> None:
        """Called every FRAME_MS milliseconds to redraw canvas."""
        # Pull amplitude from lock
        with self._amplitude_lock:
            if self._pending_amplitude is not None:
                self._target_amplitude = self._pending_amplitude
                self._pending_amplitude = None

        # Smooth amplitude interpolation
        self._amplitude += (self._target_amplitude - self._amplitude) * 0.18

        # Advance phase
        self._phase += 0.08
        self._arc_angle = (self._arc_angle + 4.0) % 360.0

        # Draw frame
        self._draw_frame()

        # Schedule next frame
        self._root.after(FRAME_MS, self._animate)

    def _draw_frame(self) -> None:
        """Redraw the canvas for the current state."""
        c = self._canvas
        c.delete("all")

        cx = WIDGET_SIZE // 2
        cy = WIDGET_SIZE // 2

        state = self._state

        if state == self.STATE_IDLE:
            self._draw_idle(c, cx, cy)
        elif state == self.STATE_LISTENING:
            self._draw_listening(c, cx, cy)
        elif state == self.STATE_PROCESSING:
            self._draw_processing(c, cx, cy)

    def _draw_idle(self, c: tk.Canvas, cx: int, cy: int) -> None:
        """Dim pulsing circle when idle."""
        pulse = 0.3 + 0.05 * math.sin(self._phase)
        r = int(CORE_RADIUS * pulse * 0.8)
        if r > 0:
            c.create_oval(
                cx - r, cy - r, cx + r, cy + r,
                fill=COLORS["idle_wave"], outline="",
            )

    def _draw_listening(self, c: tk.Canvas, cx: int, cy: int) -> None:
        """Siri-style concentric wave rings expanding outward."""
        amp = self._amplitude

        # Draw rings from outermost to innermost (painter's algorithm)
        ring_configs = [
            (CORE_RADIUS + 55 + amp * 30, COLORS["listen_wave3"], 0.15 + amp * 0.1),
            (CORE_RADIUS + 35 + amp * 22, COLORS["listen_wave2"], 0.25 + amp * 0.15),
            (CORE_RADIUS + 18 + amp * 15, COLORS["listen_wave1"], 0.45 + amp * 0.2),
        ]

        for i, (base_r, color, alpha_hint) in enumerate(ring_configs):
            phase_offset = i * (math.pi * 2 / WAVE_RINGS)
            wave_r = base_r + 5 * math.sin(self._phase + phase_offset)
            r = max(1, int(wave_r))
            # Simulate alpha via stipple on Windows; direct color on others
            self._draw_circle_aa(c, cx, cy, r, color)

        # Inner glow rings
        for i in range(2):
            glow_r = CORE_RADIUS + 6 - i * 3 + 2 * math.sin(self._phase + i)
            glow_r = max(1, int(glow_r))
            self._draw_circle_aa(c, cx, cy, glow_r, COLORS["listen_core"])

        # Core circle
        c.create_oval(
            cx - CORE_RADIUS, cy - CORE_RADIUS,
            cx + CORE_RADIUS, cy + CORE_RADIUS,
            fill=COLORS["listen_core"],
            outline=COLORS["listen_wave1"],
            width=2,
        )

        # Mic icon (text symbol)
        c.create_text(
            cx, cy,
            text="🎙",
            font=("Segoe UI Emoji", 18) if sys.platform == "win32" else ("Apple Color Emoji", 18),
            fill="white",
        )

        # Amplitude bar at bottom
        bar_w = int((WIDGET_SIZE - 40) * amp)
        if bar_w > 0:
            c.create_rectangle(
                cx - bar_w // 2, WIDGET_SIZE - 12,
                cx + bar_w // 2, WIDGET_SIZE - 8,
                fill=COLORS["listen_core"], outline="",
            )

    def _draw_processing(self, c: tk.Canvas, cx: int, cy: int) -> None:
        """Spinning arc (loading ring) for processing state."""
        # Background circle
        r = CORE_RADIUS + 20
        c.create_oval(
            cx - r, cy - r, cx + r, cy + r,
            fill=COLORS["process_trail"], outline="",
        )

        # Spinning arc
        start = self._arc_angle
        c.create_arc(
            cx - r, cy - r, cx + r, cy + r,
            start=start, extent=200,
            style=tk.ARC,
            outline=COLORS["process_arc"],
            width=4,
        )

        # Inner secondary arc (counter-rotate)
        r2 = CORE_RADIUS + 8
        c.create_arc(
            cx - r2, cy - r2, cx + r2, cy + r2,
            start=-self._arc_angle * 1.5, extent=120,
            style=tk.ARC,
            outline="#a855f7",
            width=3,
        )

        # Core
        c.create_oval(
            cx - CORE_RADIUS, cy - CORE_RADIUS,
            cx + CORE_RADIUS, cy + CORE_RADIUS,
            fill="#1e1b4b",
            outline=COLORS["process_arc"],
            width=2,
        )

        c.create_text(cx, cy, text="⚙", font=("Arial", 20), fill=COLORS["process_arc"])

    @staticmethod
    def _draw_circle_aa(c: tk.Canvas, cx: int, cy: int, r: int, color: str) -> None:
        """Draw filled circle (no native anti-aliasing in Tkinter)."""
        c.create_oval(
            cx - r, cy - r, cx + r, cy + r,
            fill=color, outline="",
        )

    # ------------------------------------------------------------------
    # Main run
    # ------------------------------------------------------------------

    def run(self) -> None:
        """
        Start the Tkinter event loop (BLOCKS — must be called from main thread).
        Starts animation loop before entering mainloop.
        """
        self._root.after(FRAME_MS, self._animate)
        self._root.mainloop()

    def quit(self) -> None:
        """Cleanly destroy the Tkinter window from any thread."""
        self._root.after(0, self._root.quit)


# ---------------------------------------------------------------------------
# Subprocess / Standalone mode
# ---------------------------------------------------------------------------

def _run_subprocess_mode() -> None:
    """
    Run widget as standalone subprocess. Reads JSON commands from stdin.
    This avoids Tkinter thread-safety issues in the main process.
    """
    try:
        widget = VoiceWidget()
    except Exception as exc:
        logger.warning("Không thể khởi tạo VoiceWidget (môi trường không hỗ trợ GUI): %s", exc)
        return

    def _stdin_reader() -> None:
        """Read commands from stdin in background thread."""
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
                cmd = msg.get("cmd", "")
                if cmd == "show":
                    mode = msg.get("mode", "listening")
                    if mode == "processing":
                        widget.show_processing()
                    else:
                        widget.show_listening()
                elif cmd == "hide":
                    widget.hide()
                elif cmd == "text":
                    widget.set_text(msg.get("content", ""))
                elif cmd == "amplitude":
                    widget.set_amplitude(float(msg.get("value", 0.0)))
                elif cmd == "quit":
                    widget.quit()
                    break
            except (json.JSONDecodeError, ValueError) as exc:
                logger.warning("VoiceWidget stdin parse error: %s", exc)

    reader_thread = threading.Thread(target=_stdin_reader, daemon=True)
    reader_thread.start()
    try:
        widget.run()  # Blocks main thread
    except Exception as exc:
        logger.warning("VoiceWidget run error: %s", exc)


# ---------------------------------------------------------------------------
# Entry point (subprocess mode)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if not _TK_AVAILABLE:
        print("WARNING: tkinter not available. GUI voice widget disabled.", file=sys.stderr)
        sys.exit(0)
    try:
        _run_subprocess_mode()
    except Exception as exc:
        logger.warning("VoiceWidget process exited: %s", exc)
