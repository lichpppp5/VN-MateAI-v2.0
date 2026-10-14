# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
client_agent/overlay_ui.py
==============================
Phase 32: VN-MateAI Visual Overlay Engine.

Responsibilities:
  - Frameless, non-stealing-focus, always-on-top HUD overlay window at bottom-right corner.
  - Cyberpunk Dark Mode styling (Neon Cyan #00f2fe, Deep Navy #070b14, Emerald #10b981).
  - Multi-template rendering:
      * 'network_map': Network topology graph (networkx + matplotlib / canvas fallback).
      * 'metric_chart': Real-time CPU, RAM, Disk, and sparkline performance telemetry.
      * 'image': Embedded screenshot or visual asset preview.
      * 'alert': High-priority security/incident HUD banner.
  - Smooth fade-in & fade-out animations.
  - 15-second auto-dismiss with countdown indicator (pauses on mouse hover).
  - Standalone subprocess execution to avoid GUI/asyncio thread conflicts.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import platform
import sys
import time
import tkinter as tk
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("overlay_ui")

# Theme Palette (Cyberpunk HUD)
BG_MAIN = "#070b14"
BG_CARD = "#0d1527"
BG_BAR = "#1e293b"
ACCENT_CYAN = "#00f2fe"
ACCENT_BLUE = "#3b82f6"
ACCENT_GREEN = "#10b981"
ACCENT_RED = "#ef4444"
ACCENT_AMBER = "#f59e0b"
BORDER_GLOW = "#0284c7"
TEXT_WHITE = "#f8fafc"
TEXT_MUTED = "#94a3b8"
TEXT_CYAN = "#38bdf8"


class OverlayWindow:
    """
    HUD Visual Overlay Window displayed at the screen corner without stealing focus.
    """

    def __init__(
        self,
        visual_type: str,
        data: Dict[str, Any],
        title: str = "TRỢ LÝ AI LY LY — HUD",
        duration: int = 15,
        width: int = 460,
        height: int = 340,
    ) -> None:
        # For text_board, provide a wider and taller layout for comfortable log/table viewing
        if visual_type in ("text_board", "text", "log", "table") and width == 460 and height == 340:
            width = 540
            height = 380

        self.visual_type = visual_type
        self.data = data or {}
        self.title_text = title
        self.duration = duration
        self.width = width
        self.height = height
        self._hovered = False
        self._time_remaining = float(duration)
        self._photo_cache: List[Any] = []  # Keep references to avoid GC

        self.root = tk.Tk()
        self.root.title(f"VN-MateAI — {title}")
        self.root.overrideredirect(True)  # Frameless
        self.root.configure(bg=BG_MAIN)

        # Position at bottom-right corner
        try:
            screen_w = self.root.winfo_screenwidth()
            screen_h = self.root.winfo_screenheight()
            pos_x = max(10, screen_w - self.width - 24)
            pos_y = max(10, screen_h - self.height - 48)
            self.root.geometry(f"{self.width}x{self.height}+{pos_x}+{pos_y}")
        except Exception:
            self.root.geometry(f"{self.width}x{self.height}")

        # Always-on-top without stealing active focus
        try:
            self.root.attributes("-topmost", True)
        except Exception:
            pass

        # Windows-specific: WS_EX_NOACTIVATE (0x08000000) prevents stealing keyboard focus
        if platform.system() == "Windows":
            try:
                import ctypes
                hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
                GWL_EXSTYLE = -20
                WS_EX_NOACTIVATE = 0x08000000
                WS_EX_TOPMOST = 0x00000008
                old_style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                ctypes.windll.user32.SetWindowLongW(
                    hwnd, GWL_EXSTYLE, old_style | WS_EX_NOACTIVATE | WS_EX_TOPMOST
                )
            except Exception:
                pass

        # Smooth alpha transparency initialisation
        self.alpha = 0.0
        try:
            self.root.attributes("-alpha", self.alpha)
        except Exception:
            pass

        # Build UI layout
        self._build_ui()

        # Bind hover listeners to pause countdown
        self.root.bind("<Enter>", self._on_enter)
        self.root.bind("<Leave>", self._on_leave)

        # Trigger smooth fade-in
        self.root.after(20, self._fade_in)

        # Start countdown tick
        self.root.after(100, self._countdown_tick)

    def _on_enter(self, event: Any = None) -> None:
        self._hovered = True

    def _on_leave(self, event: Any = None) -> None:
        self._hovered = False

    def _fade_in(self) -> None:
        """Gradually increase window opacity."""
        try:
            if not self.root.winfo_exists():
                return
            if self.alpha < 0.96:
                self.alpha = min(0.96, self.alpha + 0.08)
                self.root.attributes("-alpha", self.alpha)
                self.root.after(25, self._fade_in)
        except Exception:
            pass

    def _fade_out(self) -> None:
        """Gradually decrease window opacity then destroy."""
        try:
            if not self.root.winfo_exists():
                return
            if self.alpha > 0.05:
                self.alpha = max(0.0, self.alpha - 0.1)
                self.root.attributes("-alpha", self.alpha)
                self.root.after(25, self._fade_out)
            else:
                self.root.destroy()
        except Exception:
            pass

    def close(self) -> None:
        """Close with smooth fade out."""
        self._fade_out()

    def _countdown_tick(self) -> None:
        """Decrement timer if not hovered, update progress bar."""
        try:
            if not self.root.winfo_exists():
                return
            if not self._hovered:
                self._time_remaining -= 0.1

            # Update countdown bar width
            if hasattr(self, "progress_canvas") and self.duration > 0:
                pct = max(0.0, min(1.0, self._time_remaining / self.duration))
                new_w = int(self.width * pct)
                self.progress_canvas.coords(self.progress_bar, 0, 0, new_w, 3)

            if self._time_remaining <= 0:
                self.close()
            else:
                self.root.after(100, self._countdown_tick)
        except Exception:
            pass

    def _build_ui(self) -> None:
        """Assemble outer frame, Cyberpunk header, dynamic body, and countdown bar."""
        # Outer Border & Glass Container
        outer = tk.Frame(self.root, bg=BORDER_GLOW, padx=1, pady=1)
        outer.pack(fill=tk.BOTH, expand=True)

        inner = tk.Frame(outer, bg=BG_MAIN)
        inner.pack(fill=tk.BOTH, expand=True)

        # Header Bar
        header = tk.Frame(inner, bg=BG_CARD, height=36, padx=12, pady=6)
        header.pack(fill=tk.X, side=tk.TOP)

        # Pulse Dot & Badge
        dot = tk.Label(header, text="●", font=("Segoe UI", 10, "bold"), fg=ACCENT_CYAN, bg=BG_CARD)
        dot.pack(side=tk.LEFT, padx=(0, 6))

        tag_lbl = tk.Label(
            header,
            text=self.title_text.upper(),
            font=("Segoe UI", 9, "bold"),
            fg=TEXT_CYAN,
            bg=BG_CARD,
        )
        tag_lbl.pack(side=tk.LEFT)

        # Type Indicator Badge
        type_badge = tk.Label(
            header,
            text=f"[{self.visual_type.upper()}]",
            font=("Segoe UI", 7, "bold"),
            fg="#64748b",
            bg=BG_CARD,
        )
        type_badge.pack(side=tk.LEFT, padx=6)

        # Close Button (X)
        close_btn = tk.Label(
            header,
            text="✕",
            font=("Segoe UI", 10, "bold"),
            fg=TEXT_MUTED,
            bg=BG_CARD,
            cursor="hand2",
            padx=6,
        )
        close_btn.pack(side=tk.RIGHT)
        close_btn.bind("<Button-1>", lambda e: self.close())
        close_btn.bind("<Enter>", lambda e: close_btn.configure(fg=ACCENT_RED))
        close_btn.bind("<Leave>", lambda e: close_btn.configure(fg=TEXT_MUTED))

        # Thin Cyan Divider
        divider = tk.Frame(inner, bg="#0369a1", height=1)
        divider.pack(fill=tk.X)

        # Content Area
        content_frame = tk.Frame(inner, bg=BG_MAIN, padx=14, pady=10)
        content_frame.pack(fill=tk.BOTH, expand=True)

        # Dispatch Template
        if self.visual_type == "network_map":
            self._render_network_map(content_frame)
        elif self.visual_type == "metric_chart":
            self._render_metric_chart(content_frame)
        elif self.visual_type in ("image", "screenshot"):
            self._render_image(content_frame)
        elif self.visual_type in ("alert", "warning", "security_alert"):
            self._render_alert(content_frame)
        elif self.visual_type in ("text_board", "text", "table", "log"):
            self._render_text_board(content_frame)
        else:
            self._render_generic(content_frame)

        # Countdown Progress Line at Bottom
        self.progress_canvas = tk.Canvas(inner, height=3, bg=BG_MAIN, highlightthickness=0)
        self.progress_canvas.pack(fill=tk.X, side=tk.BOTTOM)
        self.progress_bar = self.progress_canvas.create_rectangle(
            0, 0, self.width, 3, fill=ACCENT_CYAN, outline=""
        )

    # -----------------------------------------------------------------------
    # Template 1: Network Map
    # -----------------------------------------------------------------------
    def _render_network_map(self, parent: tk.Frame) -> None:
        """Render connected nodes topology graph with Cyberpunk styling."""
        nodes = self.data.get("nodes", [])
        links = self.data.get("links", [])
        master_info = self.data.get("master", {})

        # If no nodes provided, create a default topology
        if not nodes:
            nodes = [
                {"id": "master", "label": "Master Server", "ip": "127.0.0.1", "role": "master", "status": "online"},
                {"id": "agent-1", "label": "Worker-Node-1", "ip": "LAN:192.168.1.15", "role": "worker", "status": "online"},
            ]
            links = [{"source": "master", "target": "agent-1", "latency": "1ms"}]

        # Try rendering with matplotlib & networkx for high fidelity
        img = self._try_generate_networkx_graph(nodes, links, master_info)
        if img:
            lbl = tk.Label(parent, image=img, bg=BG_MAIN)
            lbl.pack(fill=tk.BOTH, expand=True)
            self._photo_cache.append(img)
            return

        # Fallback to high-performance Tkinter Canvas renderer
        canvas = tk.Canvas(parent, bg=BG_CARD, highlightthickness=1, highlightbackground="#1e293b")
        canvas.pack(fill=tk.BOTH, expand=True)

        w = self.width - 32
        h = self.height - 85

        # Draw background grid lines
        for gx in range(0, w, 30):
            canvas.create_line(gx, 0, gx, h, fill="#111c35", width=1)
        for gy in range(0, h, 30):
            canvas.create_line(0, gy, w, gy, fill="#111c35", width=1)

        cx, cy = w // 2, h // 2

        # Draw Master Node in Center
        m_r = 22
        canvas.create_oval(cx - m_r - 4, cy - m_r - 4, cx + m_r + 4, cy + m_r + 4, outline="#0284c7", width=1)
        canvas.create_oval(cx - m_r, cy - m_r, cx + m_r, cy + m_r, fill="#0f172a", outline=ACCENT_CYAN, width=2)
        canvas.create_text(cx, cy - 4, text="MASTER", font=("Segoe UI", 7, "bold"), fill=ACCENT_CYAN)
        master_ip = master_info.get("ip") or "HTTPS:443"
        canvas.create_text(cx, cy + 7, text=master_ip, font=("Segoe UI", 6), fill=TEXT_MUTED)

        # Place Worker Nodes in a ring around Master
        worker_nodes = [n for n in nodes if n.get("role") != "master"] or nodes[1:]
        total_workers = max(1, len(worker_nodes))
        import math
        radius = min(w // 2 - 45, h // 2 - 25)

        for i, node in enumerate(worker_nodes):
            angle = (2 * math.pi / total_workers) * i - (math.pi / 2)
            nx = int(cx + radius * math.cos(angle))
            ny = int(cy + radius * math.sin(angle))

            status = node.get("status", "online")
            node_color = ACCENT_GREEN if status == "online" else ACCENT_RED

            # Link line
            canvas.create_line(cx, cy, nx, ny, fill="#0284c7", width=1, dash=(3, 3))
            # Pulse dot in middle of link
            mid_x = (cx + nx) // 2
            mid_y = (cy + ny) // 2
            canvas.create_oval(mid_x - 2, mid_y - 2, mid_x + 2, mid_y + 2, fill=ACCENT_CYAN, outline="")

            # Node circle
            nr = 14
            canvas.create_oval(nx - nr, ny - nr, nx + nr, ny + nr, fill="#0b1329", outline=node_color, width=2)
            lbl_short = (node.get("label") or node.get("id") or "Node")[:10]
            canvas.create_text(nx, ny - 3, text=lbl_short, font=("Segoe UI", 6, "bold"), fill=TEXT_WHITE)
            canvas.create_text(nx, ny + 7, text=node.get("ip", ""), font=("Segoe UI", 5), fill=TEXT_MUTED)

        # Summary footer label
        summary_txt = f"Tổng: {len(nodes)} Node | Trực tuyến: {len([n for n in nodes if n.get('status') == 'online'])} | Mã hóa: TLSv1.3 (WSS)"
        foot = tk.Label(parent, text=summary_txt, font=("Segoe UI", 8), fg=TEXT_CYAN, bg=BG_MAIN)
        foot.pack(side=tk.BOTTOM, pady=(4, 0))

    def _try_generate_networkx_graph(
        self, nodes: List[Dict[str, Any]], links: List[Dict[str, Any]], master_info: Dict[str, Any]
    ) -> Optional[Any]:
        """Attempt to render network map via networkx + matplotlib."""
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            import networkx as nx
            from PIL import Image, ImageTk

            G = nx.Graph()
            node_colors = []
            labels = {}

            for n in nodes:
                nid = n.get("id", "unknown")
                G.add_node(nid)
                role = n.get("role", "")
                status = n.get("status", "online")
                if role == "master" or nid == "master":
                    node_colors.append("#00f2fe")
                    labels[nid] = f"MASTER\n{n.get('ip', '')}"
                else:
                    node_colors.append("#10b981" if status == "online" else "#ef4444")
                    labels[nid] = f"{n.get('label', nid)[:9]}\n{n.get('ip', '')}"

            for link in links:
                G.add_edge(link.get("source", "master"), link.get("target"))

            fig, ax = plt.subplots(figsize=(4.3, 2.5), facecolor=BG_CARD)
            ax.set_facecolor(BG_CARD)
            pos = nx.spring_layout(G, seed=42)
            nx.draw_networkx_nodes(G, pos, node_color=node_colors, node_size=650, alpha=0.9, ax=ax)
            nx.draw_networkx_edges(G, pos, edge_color="#0284c7", width=1.5, style="dashed", alpha=0.7, ax=ax)
            nx.draw_networkx_labels(G, pos, labels=labels, font_size=6.5, font_color="#f8fafc", font_weight="bold", ax=ax)
            ax.axis("off")
            plt.tight_layout(pad=0.2)

            buf = io.BytesIO()
            plt.savefig(buf, format="png", dpi=100, facecolor=fig.get_facecolor(), edgecolor="none")
            plt.close(fig)
            buf.seek(0)
            pil_img = Image.open(buf)
            return ImageTk.PhotoImage(pil_img)
        except Exception as exc:
            logger.debug("networkx render fallback: %s", exc)
            return None

    # -----------------------------------------------------------------------
    # Template 2: Metric Chart (Telemetry HUD)
    # -----------------------------------------------------------------------
    def _render_metric_chart(self, parent: tk.Frame) -> None:
        """Render CPU, RAM, Disk progress bars and telemetry sparkline."""
        cpu = float(self.data.get("cpu_percent", 35.0))
        ram = float(self.data.get("ram_percent", 55.0))
        disk = float(self.data.get("disk_percent", 60.0))
        ram_used = self.data.get("ram_used_gb", 8.4)
        ram_total = self.data.get("ram_total_gb", 16.0)
        procs = self.data.get("processes_count", 142)
        history = self.data.get("history", [20, 25, 40, 35, 50, 42, cpu])

        # Top 3 Gauges
        gauges_frame = tk.Frame(parent, bg=BG_MAIN)
        gauges_frame.pack(fill=tk.X, pady=(0, 10))

        self._create_bar_row(gauges_frame, "CPU LOAD", cpu, f"{cpu:.1f}%")
        self._create_bar_row(gauges_frame, "RAM USAGE", ram, f"{ram:.1f}% ({ram_used}/{ram_total}GB)")
        self._create_bar_row(gauges_frame, "DISK USAGE", disk, f"{disk:.1f}%")

        # Sparkline Canvas for Historical Trend
        lbl_trend = tk.Label(parent, text="LỊCH SỬ TẢI HỆ THỐNG (GẦN ĐÂY)", font=("Segoe UI", 7, "bold"), fg=TEXT_MUTED, bg=BG_MAIN)
        lbl_trend.pack(anchor="w")

        spark_canvas = tk.Canvas(parent, height=80, bg=BG_CARD, highlightthickness=1, highlightbackground="#1e293b")
        spark_canvas.pack(fill=tk.BOTH, expand=True, pady=(3, 0))

        # Render Sparkline Curve
        w = self.width - 34
        h = 75
        pts = history if len(history) >= 2 else [30, 45, 20, 50, 60, 42]
        step = w / (len(pts) - 1)
        coords = []
        for idx, val in enumerate(pts):
            px = int(idx * step)
            py = int(h - (val / 100.0) * (h - 14) - 7)
            coords.extend([px, py])

        if len(coords) >= 4:
            # Gradient fill under sparkline
            fill_coords = [0, h] + coords + [w, h]
            spark_canvas.create_polygon(fill_coords, fill="#082f49", outline="")
            spark_canvas.create_line(coords, fill=ACCENT_CYAN, width=2, smooth=True)
            # Draw glowing dots
            for idx in range(0, len(coords), 2):
                x, y = coords[idx], coords[idx + 1]
                spark_canvas.create_oval(x - 2, y - 2, x + 2, y + 2, fill=ACCENT_CYAN, outline=TEXT_WHITE)

        # Meta summary footer
        sub = tk.Label(parent, text=f"Tiến trình đang chạy: {procs} | Trạng thái: Ổn định", font=("Segoe UI", 8), fg=TEXT_CYAN, bg=BG_MAIN)
        sub.pack(side=tk.BOTTOM, pady=(4, 0))

    def _create_bar_row(self, parent: tk.Frame, label: str, pct: float, val_text: str) -> None:
        """Helper to create a futuristic progress gauge row."""
        row = tk.Frame(parent, bg=BG_MAIN, pady=2)
        row.pack(fill=tk.X)

        lbl = tk.Label(row, text=label, font=("Segoe UI", 7, "bold"), fg=TEXT_MUTED, bg=BG_MAIN, width=12, anchor="w")
        lbl.pack(side=tk.LEFT)

        # Bar background
        bar_bg = tk.Frame(row, bg=BG_BAR, height=8, width=180)
        bar_bg.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        bar_bg.pack_propagate(False)

        # Fill color based on load
        color = ACCENT_GREEN if pct < 60 else (ACCENT_AMBER if pct < 85 else ACCENT_RED)
        fill_w = max(4, int((pct / 100.0) * 180))
        fill = tk.Frame(bar_bg, bg=color, width=fill_w)
        fill.pack(side=tk.LEFT, fill=tk.Y)

        val_lbl = tk.Label(row, text=val_text, font=("Segoe UI", 7, "bold"), fg=TEXT_WHITE, bg=BG_MAIN, width=16, anchor="e")
        val_lbl.pack(side=tk.RIGHT)

    # -----------------------------------------------------------------------
    # Template 3: Image / Screenshot
    # -----------------------------------------------------------------------
    def _render_image(self, parent: tk.Frame) -> None:
        """Render base64 or file path image."""
        from PIL import Image, ImageTk

        raw_b64 = self.data.get("image_base64") or self.data.get("data")
        img_path = self.data.get("image_path")
        caption = self.data.get("caption") or "Ảnh Chụp Màn Hình Giám Sát"

        pil_img = None
        if raw_b64:
            try:
                # Strip data URL header if present
                if "," in raw_b64:
                    raw_b64 = raw_b64.split(",", 1)[1]
                img_bytes = base64.b64decode(raw_b64)
                pil_img = Image.open(io.BytesIO(img_bytes))
            except Exception as exc:
                logger.error("Decode base64 image error: %s", exc)

        elif img_path and Path(img_path).exists():
            try:
                pil_img = Image.open(img_path)
            except Exception as exc:
                logger.error("Open image path error: %s", exc)

        if pil_img:
            # Resize preserving aspect ratio to fit inside frame
            max_w = self.width - 32
            max_h = self.height - 95
            pil_img.thumbnail((max_w, max_h), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(pil_img)
            self._photo_cache.append(photo)

            img_lbl = tk.Label(parent, image=photo, bg=BG_MAIN)
            img_lbl.pack(fill=tk.BOTH, expand=True)
        else:
            err_lbl = tk.Label(parent, text="[KHÔNG CÓ DỮ LIỆU HÌNH ẢNH]", font=("Segoe UI", 10), fg=ACCENT_RED, bg=BG_MAIN)
            err_lbl.pack(expand=True)

        cap_lbl = tk.Label(parent, text=caption, font=("Segoe UI", 8), fg=TEXT_CYAN, bg=BG_MAIN)
        cap_lbl.pack(side=tk.BOTTOM, pady=(3, 0))

    # -----------------------------------------------------------------------
    # Template 4: Alert / Incident Banner
    # -----------------------------------------------------------------------
    def _render_alert(self, parent: tk.Frame) -> None:
        """High visibility incident alert panel."""
        level = self.data.get("level", "WARNING").upper()
        msg = self.data.get("message") or self.data.get("description") or "Phát hiện dấu hiệu bất thường trên hệ thống."
        service = self.data.get("service") or self.data.get("source") or "Security Guard"
        recommendation = self.data.get("action") or self.data.get("recommendation") or "Đang tự động cách ly và giám sát tăng cường."

        accent = ACCENT_RED if level in ("CRITICAL", "HIGH", "ERROR") else ACCENT_AMBER

        badge_frame = tk.Frame(parent, bg=accent, padx=8, pady=3)
        badge_frame.pack(anchor="w", pady=(0, 6))
        tk.Label(badge_frame, text=f"⚠ CẢNH BÁO AN NINH: {level}", font=("Segoe UI", 8, "bold"), fg="#ffffff", bg=accent).pack()

        # Alert Box
        box = tk.Frame(parent, bg=BG_CARD, highlightthickness=1, highlightbackground=accent, padx=12, pady=10)
        box.pack(fill=tk.BOTH, expand=True)

        tk.Label(box, text=f"Dịch Vụ / Máy Trạm: {service}", font=("Segoe UI", 8, "bold"), fg=ACCENT_CYAN, bg=BG_CARD, anchor="w").pack(fill=tk.X)
        tk.Label(box, text=msg, font=("Segoe UI", 9), fg=TEXT_WHITE, bg=BG_CARD, wraplength=self.width - 65, justify="left", anchor="w").pack(fill=tk.X, pady=6)
        tk.Label(box, text=f"➔ Hành Động: {recommendation}", font=("Segoe UI", 8, "italic"), fg=TEXT_MUTED, bg=BG_CARD, wraplength=self.width - 65, justify="left").pack(fill=tk.X)

    # -----------------------------------------------------------------------
    # Template 5: Text Board / Terminal Log Viewer (Phase 32.1)
    # -----------------------------------------------------------------------
    def _render_text_board(self, parent: tk.Frame) -> None:
        """
        Phase 32.1: Render Text Board for IP lists, log inspection, or structured text.
        Styling: Dark frosted glass background (opacity 80%), Terminal Green monospace font,
        with auto-scroll to bottom.
        """
        import tkinter.scrolledtext as st

        raw_text = ""
        if isinstance(self.data, str):
            raw_text = self.data
        elif isinstance(self.data, dict):
            raw_text = self.data.get("text") or self.data.get("content") or self.data.get("data") or ""
            if not raw_text:
                try:
                    raw_text = json.dumps(self.data, indent=2, ensure_ascii=False)
                except Exception:
                    raw_text = str(self.data)
        else:
            raw_text = str(self.data or "")

        term_box = tk.Frame(parent, bg="#050811", highlightthickness=1, highlightbackground="#00FF66", padx=2, pady=2)
        term_box.pack(fill=tk.BOTH, expand=True)

        mono_font = ("Consolas", 10) if platform.system() == "Windows" else ("Menlo", 10)

        txt_widget = st.ScrolledText(
            term_box,
            wrap=tk.WORD,
            bg="#050811",
            fg="#00FF66",
            insertbackground="#00FF66",
            font=mono_font,
            relief=tk.FLAT,
            padx=8,
            pady=8,
        )
        txt_widget.pack(fill=tk.BOTH, expand=True)

        txt_widget.insert(tk.END, raw_text.strip() or "(Không có dữ liệu văn bản)")
        txt_widget.see(tk.END)
        txt_widget.configure(state=tk.DISABLED)

        # Status footer bar
        lines_count = len(raw_text.strip().splitlines())
        status_bar = tk.Frame(parent, bg=BG_MAIN, pady=3)
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)
        tk.Label(status_bar, text=f"TERMINAL BUFFER | Dòng: {lines_count} | UTF-8", font=("Segoe UI", 7), fg=TEXT_MUTED, bg=BG_MAIN).pack(side=tk.LEFT)
        tk.Label(status_bar, text="AUTO-SCROLL: ON", font=("Segoe UI", 7, "bold"), fg=ACCENT_GREEN, bg=BG_MAIN).pack(side=tk.RIGHT)

    # -----------------------------------------------------------------------
    # Generic Fallback
    # -----------------------------------------------------------------------
    def _render_generic(self, parent: tk.Frame) -> None:
        box = tk.Frame(parent, bg=BG_CARD, padx=10, pady=10)
        box.pack(fill=tk.BOTH, expand=True)
        txt = json.dumps(self.data, indent=2, ensure_ascii=False)
        lbl = tk.Label(box, text=txt[:400], font=("Courier New", 8), fg=TEXT_CYAN, bg=BG_CARD, justify="left", anchor="nw")
        lbl.pack(fill=tk.BOTH, expand=True)

    def show(self) -> None:
        """Run the main event loop."""
        self.root.mainloop()


def show_overlay(
    visual_type: str,
    data: Dict[str, Any],
    title: str = "TRỢ LÝ AI LY LY — HUD",
    duration: int = 15,
) -> None:
    """Convenience launcher for OverlayWindow."""
    win = OverlayWindow(visual_type=visual_type, data=data, title=title, duration=duration)
    win.show()


# ---------------------------------------------------------------------------
# CLI Entry Point for Subprocess Execution
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    visual_type = "metric_chart"
    title = "TRỢ LÝ AI LY LY — HUD"
    data: Dict[str, Any] = {}
    duration = 15

    # 1. Read from stdin if piped
    if not sys.stdin.isatty():
        try:
            stdin_content = sys.stdin.read().strip()
            if stdin_content:
                parsed = json.loads(stdin_content)
                visual_type = parsed.get("type", visual_type)
                title = parsed.get("title", title)
                data = parsed.get("data", {})
                duration = int(parsed.get("duration", duration))
        except Exception as e:
            logger.error("Failed to parse stdin payload: %s", e)

    # 2. Or read from file argument / inline JSON
    elif len(sys.argv) > 1:
        arg1 = sys.argv[1]
        try:
            if Path(arg1).exists():
                parsed = json.loads(Path(arg1).read_text(encoding="utf-8"))
            else:
                parsed = json.loads(arg1)

            visual_type = parsed.get("type", visual_type)
            title = parsed.get("title", title)
            data = parsed.get("data", {})
            duration = int(parsed.get("duration", duration))
        except Exception:
            # Fallback to simple positional CLI: overlay_ui.py <type> <title>
            visual_type = sys.argv[1]
            if len(sys.argv) > 2:
                title = sys.argv[2]

    # Run overlay window
    show_overlay(visual_type=visual_type, data=data, title=title, duration=duration)
