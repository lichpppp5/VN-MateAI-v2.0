# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
client_agent/popup_ui.py
========================
Standalone interactive topmost popup for VN-MateAI Client Agent.
Runs in its own process to guarantee main-thread GUI compatibility on macOS Cocoa & Windows.
Outputs response status ('completed', 'issue', or 'dismissed' when the window is
closed without choosing) to stdout and exits.
"""

from __future__ import annotations

import sys
import tkinter as tk


def show_popup(task_id: str, message: str, sender: str = "Ban Giám Đốc") -> str:
    """Display interactive Tkinter window on main thread and return chosen status."""
    # Đóng cửa sổ mà không bấm nút = "dismissed" (việc vẫn chờ, Portal nhắc lại được) —
    # trước đây tính là "vướng mắc".
    result = "dismissed"
    root = tk.Tk()
    root.title(f"VN-MateAI — Nhắc Việc [{sender}]")
    root.geometry("450x260")
    root.minsize(400, 220)
    root.configure(bg="#0b1120")

    # Center window
    try:
        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        x = max(0, (screen_width - 450) // 2)
        y = max(0, (screen_height - 260) // 3)
        root.geometry(f"450x260+{x}+{y}")
    except Exception:
        pass

    # Window stays on top
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass

    container = tk.Frame(root, bg="#0b1120", padx=20, pady=16)
    container.pack(fill=tk.BOTH, expand=True)

    header_frame = tk.Frame(container, bg="#0b1120")
    header_frame.pack(fill=tk.X, pady=(0, 10))

    tag_lbl = tk.Label(
        header_frame,
        text="📢 YÊU CẦU CÔNG VIỆC TỪ:",
        font=("Segoe UI", 9, "bold"),
        fg="#94a3b8",
        bg="#0b1120",
    )
    tag_lbl.pack(side=tk.LEFT)

    sender_lbl = tk.Label(
        header_frame,
        text=f" {sender.upper()}",
        font=("Segoe UI", 10, "bold"),
        fg="#22d3ee",
        bg="#0b1120",
    )
    sender_lbl.pack(side=tk.LEFT)

    # Message box
    msg_frame = tk.Frame(
        container,
        bg="#1e293b",
        highlightbackground="#334155",
        highlightthickness=1,
        padx=14,
        pady=12,
    )
    msg_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 16))

    msg_lbl = tk.Label(
        msg_frame,
        text=message,
        font=("Segoe UI", 11),
        fg="#f8fafc",
        bg="#1e293b",
        wraplength=380,
        justify="left",
        anchor="nw",
    )
    msg_lbl.pack(fill=tk.BOTH, expand=True)

    # Buttons
    btn_frame = tk.Frame(container, bg="#0b1120")
    btn_frame.pack(fill=tk.X)

    def on_click(status: str):
        nonlocal result
        result = status
        root.destroy()

    btn_complete = tk.Button(
        btn_frame,
        text="✔ Đã Hoàn Thành",
        font=("Segoe UI", 10, "bold"),
        bg="#059669",
        fg="#ffffff",
        activebackground="#10b981",
        activeforeground="#ffffff",
        relief="flat",
        cursor="hand2",
        padx=12,
        pady=8,
        command=lambda: on_click("completed"),
    )
    btn_complete.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))

    btn_issue = tk.Button(
        btn_frame,
        text="⚠ Đang Xử Lý / Vướng Mắc",
        font=("Segoe UI", 10, "bold"),
        bg="#dc2626",
        fg="#ffffff",
        activebackground="#ef4444",
        activeforeground="#ffffff",
        relief="flat",
        cursor="hand2",
        padx=12,
        pady=8,
        command=lambda: on_click("issue"),
    )
    btn_issue.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(8, 0))

    root.lift()
    root.mainloop()
    return result


if __name__ == "__main__":
    task_id_arg = sys.argv[1] if len(sys.argv) > 1 else "demo"
    message_arg = sys.argv[2] if len(sys.argv) > 2 else "Công việc cần xử lý"
    sender_arg = sys.argv[3] if len(sys.argv) > 3 else "Ban Giám Đốc"
    status_chosen = show_popup(task_id_arg, message_arg, sender_arg)
    print(status_chosen)
