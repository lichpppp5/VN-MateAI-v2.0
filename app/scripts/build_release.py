# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
Đóng gói bản chuyển giao VN-MateAI chạy trên Docker (bản SẠCH: không dữ liệu / khoá / token của máy build).

    python scripts/build_release.py D:\\VNmatebuildv1.0 [--version 1.0.0] [--no-image] [--arm64] [--force]

Kết quả:
    <đích>/app/                 mã nguồn đúng commit HEAD (git archive — chỉ tệp được git theo dõi) + giao diện admin đã build
    <đích>/docker-compose.yml   ứng dụng + PostgreSQL + Redis
    <đích>/env.example          mẫu .env
    <đích>/install.sh|.ps1      trình cài Linux / Windows
    <đích>/backup.sh            sao lưu
    <đích>/README-CAI-DAT.md    hướng dẫn
    <đích>/images/*.tar         image dựng sẵn (docker save) — cài không cần Internet
                                (--arm64: thêm *-arm64-images.tar cho Mac Apple Silicon / máy chủ ARM, build giả lập — chậm)
    <đích>/VERSION.txt, SHA256SUMS.txt

An toàn: trước khi xong, quét mọi tệp văn bản trong gói tìm BÍ MẬT THẬT của máy build (giá trị trong config.json đã giải mã,
khoá trong certs/) — thấy là dừng và báo lỗi, không để gói lọt bí mật ra ngoài.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKER = ROOT / "deploy" / "docker"
TEXT_EXT = {".py", ".js", ".html", ".css", ".json", ".md", ".txt", ".yml", ".yaml", ".sh", ".ps1", ".toml", ".cfg", ".ini",
            ".mjs", ".ts", ".tsx", ".bat", ".example", ".env", ""}


def run(cmd, **kw) -> subprocess.CompletedProcess:
    print("  $", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run(cmd, check=True, **kw)


def _secrets() -> set:
    """Giá trị bí mật THẬT của máy build (đủ dài để không trùng nhầm)."""
    sys.path.insert(0, str(ROOT / "src"))
    found: set = set()
    try:
        from mateai.config.loader import read_raw_config
        from mateai.config.secret_box import SECRET_FIELD_NAMES

        def walk(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    if str(k).lower() in SECRET_FIELD_NAMES and isinstance(v, str) and len(v) >= 12:
                        found.add(v)
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)
        walk(read_raw_config())
    except Exception as exc:  # noqa: BLE001
        print(f"  (không đọc được config.json để quét bí mật: {exc})")
    for f in (ROOT / "certs").glob("*.key"):
        try:
            text = f.read_text(encoding="utf-8", errors="ignore").strip()
            if len(text) >= 16:
                found.add(text.splitlines()[1] if text.startswith("-----BEGIN") and len(text.splitlines()) > 1 else text)
        except OSError:
            pass
    # bỏ chuỗi đã mã hoá và ô GIỮ CHỖ còn sót trong config (YOUR_…_HERE) — không phải bí mật thật
    return {s for s in found if not s.startswith(("enc:", "YOUR_")) and "_HERE" not in s}


def scan_for_secrets(out: Path) -> list:
    secrets = _secrets()
    print(f"  quét {len(secrets)} bí mật của máy build trong gói…")
    hits = []
    for p in out.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in TEXT_EXT or "images" in p.relative_to(out).parts:
            continue
        try:
            data = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for s in secrets:
            if s in data:
                hits.append(str(p.relative_to(out)))
                break
    return hits


def export_source(out: Path) -> str:
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                            check=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "--", "deploy/docker", "scripts/build_release.py"], cwd=ROOT,
                           capture_output=True, text=True).stdout.strip()
    if dirty:
        print("  CẢNH BÁO: deploy/docker hoặc build_release.py có thay đổi CHƯA commit — gói lấy bản đã commit (HEAD).")
    app = out / "app"
    data = subprocess.run(["git", "archive", "--format=tar", "HEAD"], cwd=ROOT, capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(app, filter="data")
    admin_out = ROOT / "admin" / "out"
    if admin_out.is_dir():                                  # giao diện /admin/topology (Next.js đã build, không có trong git)
        shutil.copytree(admin_out, app / "admin" / "out", dirs_exist_ok=True)
    else:
        print("  CẢNH BÁO: chưa có admin/out (trang /admin/topology) — chạy 'npm run build' trong admin/ rồi đóng gói lại.")
    copy_agent_builds(app)
    return commit


def copy_agent_builds(app: Path) -> None:
    """Agent máy trạm đã build (dist/agent/<nền tảng>, không có trong git) → Portal "Tải Agent" phát bản không cần Python.

    Bản build phải đúng AGENT_VERSION của mã nguồn: bản cũ hơn thì máy trạm tải về sẽ thiếu kỹ năng mới — dừng hẳn.
    """
    m = re.search(r'^AGENT_VERSION\s*=\s*"([^"]+)"', (app / "client_agent" / "agent.py").read_text(encoding="utf-8"), re.M)
    want = m.group(1) if m else None
    for plat, exe in (("windows", "VNMateAgent.exe"), ("macos", "VNMateAgent")):
        src = ROOT / "dist" / "agent" / plat
        if not (src / exe).is_file():
            print(f"  CẢNH BÁO: chưa có dist/agent/{plat}/{exe} — máy trạm {plat} sẽ nhận gói Python "
                  "(build: python scripts/build_agent.py trên máy " + ("Windows" if plat == "windows" else "Mac") + ").")
            continue
        got = (src / "version.txt").read_text(encoding="utf-8").strip() if (src / "version.txt").is_file() else "?"
        if got != want:
            sys.exit(f"DỪNG: dist/agent/{plat} là Agent {got}, mã nguồn là {want} — build lại: python scripts/build_agent.py")
        dst = app / "dist" / "agent" / plat
        dst.mkdir(parents=True, exist_ok=True)
        for name in (exe, "version.txt"):
            shutil.copy2(src / name, dst / name)
        print(f"  Agent {plat} {got}: {(src / exe).stat().st_size / 1e6:.1f} MB")


def copy_package_files(out: Path) -> None:
    for name in ("docker-compose.yml", "env.example", "README-CAI-DAT.md"):
        shutil.copy2(DOCKER / name, out / name)
    for name in ("install.sh", "backup.sh"):                  # Linux: xuống dòng LF
        (out / name).write_bytes((DOCKER / name).read_bytes().replace(b"\r\n", b"\n"))
    ps1 = (DOCKER / "install.ps1").read_text(encoding="utf-8").replace("\r\n", "\n").replace("\n", "\r\n")
    (out / "install.ps1").write_text(ps1, encoding="utf-8-sig")   # PowerShell 5.1 cần BOM để đọc đúng tiếng Việt
    entry = out / "app" / "deploy" / "docker" / "entrypoint.sh"
    entry.write_bytes(entry.read_bytes().replace(b"\r\n", b"\n"))


def build_images(out: Path, version: str) -> list:
    tag = f"vnmateai/app:{version}"
    run(["docker", "build", "-f", "deploy/docker/Dockerfile", "-t", tag, "."], cwd=out / "app")
    for img in ("postgres:16-alpine", "redis:7-alpine"):            # luôn pull: tag cục bộ có thể chỉ còn bản arm64
        run(["docker", "pull", "--platform", "linux/amd64", img])
    (out / "images").mkdir(exist_ok=True)
    tar = out / "images" / f"vnmateai-{version}-images.tar"
    run(["docker", "save", "--platform", "linux/amd64", "-o", str(tar), tag, "postgres:16-alpine", "redis:7-alpine"])
    return [tag, "postgres:16-alpine", "redis:7-alpine"]


def build_images_arm64(out: Path, version: str) -> list:
    """Image linux/arm64 (Mac Apple Silicon). install.sh trên máy ARM nạp tệp này và gắn lại tag vnmateai/app:<version>."""
    tag = f"vnmateai/app:{version}-arm64"
    run(["docker", "build", "--platform", "linux/arm64", "-f", "deploy/docker/Dockerfile", "-t", tag, "."], cwd=out / "app")
    for img in ("postgres:16-alpine", "redis:7-alpine"):
        run(["docker", "pull", "--platform", "linux/arm64", img])
    tar = out / "images" / f"vnmateai-{version}-arm64-images.tar"
    run(["docker", "save", "--platform", "linux/arm64", "-o", str(tar), tag, "postgres:16-alpine", "redis:7-alpine"])
    return [f"{tag} (arm64)"]


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dest")
    ap.add_argument("--version", default="1.0.0")
    ap.add_argument("--no-image", action="store_true", help="không build / xuất image (chỉ mã nguồn + trình cài)")
    ap.add_argument("--arm64", action="store_true", help="kèm image linux/arm64 cho Mac Apple Silicon (build giả lập, chậm)")
    ap.add_argument("--force", action="store_true", help="xoá nội dung thư mục đích nếu có")
    a = ap.parse_args()
    out = Path(a.dest).resolve()
    if out.exists() and any(out.iterdir()):
        if not a.force:
            print(f"Thư mục {out} không rỗng — dùng --force để ghi đè.")
            return 2
        for child in out.iterdir():
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    out.mkdir(parents=True, exist_ok=True)

    print("[1/4] Xuất mã nguồn (git HEAD)…")
    commit = export_source(out)
    print("[2/4] Tệp cài đặt…")
    copy_package_files(out)
    print("[3/4] Kiểm tra bí mật…")
    hits = scan_for_secrets(out)
    if hits:
        print("DỪNG: gói chứa bí mật của máy build trong:", *hits, sep="\n  ")
        return 1
    images = []
    if not a.no_image:
        print("[4/4] Build + xuất image (lần đầu 10–20 phút)…")
        images = build_images(out, a.version)
        if a.arm64:
            print("[4b] Build + xuất image arm64 (giả lập, 30–90 phút)…")
            images += build_images_arm64(out, a.version)
    (out / "VERSION.txt").write_text(
        f"VN-MateAI {a.version}\ncommit {commit}\nbuild {datetime.now():%Y-%m-%d %H:%M}\n"
        f"images: {', '.join(images) or '(không kèm — install sẽ build từ app/)'}\n"
        "Bản sạch: không kèm dữ liệu, tài khoản, token, khoá của máy build.\n", encoding="utf-8")
    sums = [f"{sha256(p)}  {p.relative_to(out).as_posix()}" for p in sorted(out.glob("*")) + sorted(out.glob("images/*"))
            if p.is_file()]
    (out / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n", encoding="utf-8", newline="\n")  # LF: sha256sum -c trên Linux
    print(f"XONG → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
