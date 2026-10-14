# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
scripts/license_headers.py
==========================
Gắn / kiểm tra tiêu đề bản quyền + SPDX ở đầu mỗi tệp mã do tác giả viết (chỉ tệp đang được git theo dõi).

    python scripts/license_headers.py --check     # liệt kê tệp thiếu tiêu đề (mã thoát 1 nếu có)
    python scripts/license_headers.py --apply     # thêm tiêu đề còn thiếu (idempotent, giữ CRLF/LF, giữ shebang / coding)

Bỏ qua: tệp sinh tự động (tailwind*.css, fonts-*.css, *.d.ts), tài nguyên bên thứ ba (web/vendor, web/fonts), tệp rỗng,
và kỹ năng do AI tự sinh khi chạy (`skills/auto_*.py`).
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPDX = "SPDX-License-Identifier: Apache-2.0"
COPYRIGHT = "Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE."

LINE_COMMENT = {".py": "#", ".js": "//", ".mjs": "//", ".ts": "//", ".tsx": "//", ".cpp": "//", ".h": "//"}
BLOCK_COMMENT = {".css", ".html"}
SKIP = re.compile(r"(^|/)(web/vendor/|web/fonts/|node_modules/|\.venv/|build/|dist/|backups/)"
                  r"|(^|/)tailwind(-hud)?\.css$|(^|/)fonts-[\w-]+\.css$|\.d\.ts$|(^|/)skills/auto_[\w]+\.py$|(^|/)\.next/|(^|/)out/")


def tracked_sources() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    files = []
    for rel in out.decode("utf-8").split("\0"):
        if not rel or SKIP.search(rel):
            continue
        p = ROOT / rel
        if p.suffix in LINE_COMMENT or p.suffix in BLOCK_COMMENT:
            if p.is_file() and p.stat().st_size > 0:
                files.append(p)
    return files


def has_header(text: str) -> bool:
    return SPDX in "\n".join(text.splitlines()[:8])


def header_for(suffix: str, nl: str) -> str:
    if suffix in LINE_COMMENT:
        c = LINE_COMMENT[suffix]
        return f"{c} {SPDX}{nl}{c} {COPYRIGHT}{nl}"
    return f"/* {SPDX}{nl}   {COPYRIGHT} */{nl}" if suffix == ".css" else f"<!-- {SPDX}{nl}     {COPYRIGHT} -->{nl}"


def with_header(text: str, suffix: str) -> str:
    nl = "\r\n" if "\r\n" in text[:4096] else "\n"
    bom = "﻿" if text.startswith("﻿") else ""
    body = text[len(bom):]
    lines = body.split(nl) if nl in body else body.split("\n")
    keep = 0                                          # dòng bắt buộc đứng đầu: shebang, khai báo coding, <!DOCTYPE>
    if suffix in LINE_COMMENT and lines and lines[0].startswith("#!"):
        keep = 1
    if suffix == ".py":
        while keep < min(len(lines), 2) and re.match(r"#.*coding[:=]\s*[-\w.]+", lines[keep]):
            keep += 1
    if suffix == ".html" and lines and lines[0].lower().startswith("<!doctype"):
        keep = 1
    head = nl.join(lines[:keep]) + (nl if keep else "")
    rest = nl.join(lines[keep:])
    return bom + head + header_for(suffix, nl) + rest


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    apply = "--apply" in argv
    missing, changed = [], 0
    for path in tracked_sources():
        try:
            text = path.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            print(f"bỏ qua (không phải UTF-8): {path.relative_to(ROOT)}")
            continue
        if has_header(text):
            continue
        missing.append(path)
        if apply:
            path.write_bytes(with_header(text, path.suffix).encode("utf-8"))
            changed += 1
    if apply:
        print(f"Đã thêm tiêu đề vào {changed} tệp.")
        return 0
    for p in missing:
        print(p.relative_to(ROOT).as_posix())
    print(f"{len(missing)} tệp thiếu tiêu đề bản quyền." if missing else "Mọi tệp mã đều có tiêu đề bản quyền.")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
