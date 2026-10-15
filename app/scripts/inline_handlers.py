# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
Danh sách cho phép của web/inline-handlers.js (CSP chặt — thẩm định §8).

Giao diện còn ~450 thuộc tính `onclick="fn('x')"` (trong HTML và trong mẫu HTML của app.js / hud.js). Với
`script-src 'self'` trình duyệt không chạy chúng; web/inline-handlers.js đọc và chạy hộ theo một ngữ pháp nhỏ, nhưng CHỈ
gọi được các tên gốc nằm trong danh sách cho phép — danh sách đó là đúng những tên mà mã nguồn của ta dùng trong handler.
HTML bị chèn (XSS) vì thế không gọi được `fetch`, `apiFetch`, `localStorage`…

    python scripts/inline_handlers.py            # kiểm tra danh sách có khớp mã nguồn không (exit 1 nếu lệch)
    python scripts/inline_handlers.py --write    # ghi lại danh sách vào web/inline-handlers.js

tests/test_inline_handlers.py chạy chế độ kiểm tra.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
PAGES = ("index.html", "hud.html", "roi_dashboard.html")
#: Chính tệp chạy hộ có ví dụ `onclick="…"` trong chú thích — không quét.
SKIP = {"inline-handlers.js"}


def sources(root: Path = ROOT) -> list[str]:
    js = sorted(p.name for p in (root / "web").glob("*.js") if p.name not in SKIP)
    return list(PAGES) + js
EVENTS = ("click", "dblclick", "change", "input", "submit", "reset", "keyup", "keydown", "keypress", "focus", "blur",
          "error", "load", "mouseenter", "mouseleave", "mouseover", "mouseout", "contextmenu")
ATTR = re.compile(r"""\son(""" + "|".join(EVENTS) + r""")=(?:"([^"]*)"|'([^']*)')""")
#: Tên gốc luôn có (ngữ cảnh handler), không cần nằm trong danh sách.
BUILTIN_ROOTS = {"this", "event", "true", "false", "null", "undefined", "return"}
BEGIN, END = "/* @allow-begin (scripts/inline_handlers.py --write) */", "/* @allow-end */"


def strip_template_exprs(src: str) -> str:
    """`${...}` trong template literal → `0` (giữ chỗ), đếm ngoặc để biểu thức chứa dấu nháy / ngoặc không làm vỡ regex."""
    out, i, n = [], 0, len(src)
    while i < n:
        if src.startswith("${", i):
            depth, j = 1, i + 2
            while j < n and depth:
                c = src[j]
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                j += 1
            out.append("0")
            i = j
        else:
            out.append(src[i])
            i += 1
    return "".join(out)


def handlers(root: Path = ROOT) -> list[tuple[str, str, str]]:
    """(tệp, sự kiện, mã) của mọi thuộc tính on* trong giao diện."""
    out = []
    for name in sources(root):
        f = root / "web" / name
        if not f.exists():
            continue
        src = f.read_text(encoding="utf-8")
        if name.endswith(".js"):
            src = strip_template_exprs(src)
        for m in ATTR.finditer(src):
            code = m.group(2) if m.group(2) is not None else m.group(3)
            out.append((name, m.group(1), code.replace("&quot;", '"').replace("&#39;", "'").replace("&amp;", "&")))
    return out


_STR = re.compile(r"""'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*"|`[^`]*`""")
_ROOT = re.compile(r"(?<![\w$.\]\)])\s*([A-Za-z_$][\w$]*)")


def roots(code: str) -> set[str]:
    """Tên gốc (đầu chuỗi member / lời gọi) trong một handler — bỏ khoá của object literal."""
    body = _STR.sub("''", code)
    body = re.sub(r"([{,]\s*)[A-Za-z_$][\w$]*\s*:", r"\1", body)  # {query: ''} → khoá không phải tên gốc
    return {m.group(1) for m in _ROOT.finditer(body)} - BUILTIN_ROOTS


def required_roots(root: Path = ROOT) -> list[str]:
    names: set[str] = set()
    for _, _, code in handlers(root):
        names |= roots(code)
    return sorted(names)


def current_allowlist(root: Path = ROOT) -> list[str]:
    src = (root / "web" / "inline-handlers.js").read_text(encoding="utf-8")
    block = src[src.index(BEGIN) + len(BEGIN):src.index(END)]
    return sorted(re.findall(r"'([^']+)'", block))


def render_block(names: list[str]) -> str:
    lines, cur = [], "  "
    for n in names:
        item = f"'{n}', "
        if len(cur) + len(item) > 118:
            lines.append(cur.rstrip())
            cur = "  "
        cur += item
    lines.append(cur.rstrip())
    return BEGIN + "\n" + "\n".join(lines) + "\n  " + END


def write(root: Path = ROOT) -> None:
    f = root / "web" / "inline-handlers.js"
    src = f.read_text(encoding="utf-8")
    nl = "\r\n" if "\r\n" in src else "\n"
    a, b = src.index(BEGIN), src.index(END) + len(END)
    f.write_text(src[:a] + render_block(required_roots(root)).replace("\n", nl) + src[b:], encoding="utf-8", newline="")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if "--write" in sys.argv:
        write()
        print(f"đã ghi {len(required_roots())} tên vào web/inline-handlers.js")
    else:
        need, have = set(required_roots()), set(current_allowlist())
        if need != have:
            print("thiếu:", sorted(need - have))
            print("thừa:", sorted(have - need))
            sys.exit(1)
        print(f"khớp: {len(need)} tên")
