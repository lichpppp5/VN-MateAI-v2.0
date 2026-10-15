# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
scripts/backup.py — sao lưu / kiểm chứng / khôi phục VN-MateAI (prompt Supervisor §116).

  python scripts/backup.py create                 # tạo backups/<thời điểm>/ + manifest.json
  python scripts/backup.py verify backups/<dir>   # kiểm toàn vẹn: sha256, integrity_check, số dòng
  python scripts/backup.py restore backups/<dir> --yes   # DỪNG máy chủ trước; tự lưu trạng thái hiện tại
  python scripts/backup.py create --push          # tạo + đẩy bản nén lên object storage (bản ngoài máy)
  python scripts/backup.py push backups/<dir>     # đẩy một bản đã có
  python scripts/backup.py list-remote            # bản sao lưu trên object storage
  python scripts/backup.py pull backups/<tên>.zip # tải về + giải nén vào backups/ rồi `verify`
  python scripts/backup.py create --push --ensure-infra   # (tác vụ đặt lịch) PostgreSQL chưa chạy thì tự bật Docker + hạ tầng trước

Sự cố 2026-10-09 → 12: tác vụ 02:00 chạy lúc Docker Desktop chưa bật → PostgreSQL không kết nối được → không có bản nào suốt
85 giờ, mỗi lần hỏng còn để lại một thư mục rỗng. Nay: `--ensure-infra` tự bật hạ tầng; bản đang làm nằm ở `backups/.partial-*`
và chỉ đổi tên thành bản thật khi ĐẠT kiểm chứng; hỏng thì xoá phần dở và gửi cảnh báo ngay (không đợi sentinel 26 giờ);
khoá `backups/.backup.lock` ngăn tác vụ đặt lịch và máy chủ (chạy bù) sao lưu cùng lúc.

"Có file backup" chưa đủ: `create` tự chạy `verify` ngay sau khi chép, và `restore`
chỉ ghi đè khi bản sao lưu kiểm chứng đạt.

Bản sao lưu chứa BÍ MẬT (config.json, khoá giải mã cấu hình, khoá ký JWT, khoá thiết
bị, khoá TLS) — thư mục `backups/` không được commit (.gitignore) và phải cất ở nơi
được bảo vệ như chính máy chủ.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]

#: (đường dẫn tương đối, loại). "sqlite" = sao lưu trực tuyến bằng API backup của SQLite.
ITEMS: List[tuple] = [
    ("vnmateai.db", "sqlite"),
    ("hr_kpi.db", "sqlite"),
    ("config.json", "file"),
    ("certs/config_secret.key", "file"),   # thiếu khoá này thì bí mật trong config.json không giải được
    ("certs/jwt_secret.key", "file"),
    ("certs/device_secret.key", "file"),
    ("certs/worker_secret.key", "file"),
    ("certs/server.key", "file"),
    ("certs/server.crt", "file"),
    ("certs/vendor_signing.key", "file"),  # khoá ký bản quyền / bản phát hành (chỉ máy nhà cung cấp) — mất là không cấp lại được
    ("storage/vector_db", "dir"),
    ("skills/registry.json", "file"),
    ("identity_core.md", "file"),
]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _row_counts(db: Path) -> Dict[str, int]:
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name;")]
        return {t: con.execute(f'SELECT COUNT(*) FROM "{t}";').fetchone()[0] for t in tables}
    finally:
        con.close()


def _integrity(db: Path) -> str:
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        return str(con.execute("PRAGMA integrity_check;").fetchone()[0])
    finally:
        con.close()


def _pg_target(root: Path, rel: str):
    """(dsn, schema) khi CSDL đang chạy trên PostgreSQL (DATABASE_URL), ngược lại None.
    Khi đó FILE .db cũ chỉ là bản lùi — sao lưu phải chụp từ PostgreSQL, không chép file.
    Chỉ áp cho bản cài này (ROOT); cây thư mục khác (bản sao, thử nghiệm) giữ cách chép file."""
    if Path(root).resolve() != ROOT.resolve():
        return None
    sys.path.insert(0, str(root / "src"))
    from mateai.infrastructure.database import pg_compat
    url = pg_compat.database_url()
    if not url:
        return None
    return url, pg_compat.schema_for(root / rel)


LOCK_STALE_S = 2 * 3600


class BackupLocked(RuntimeError):
    pass


def _lock(root: Path) -> Path:
    """Khoá loại trừ (tạo tệp độc quyền). Khoá cũ quá 2 giờ (tiến trình chết giữa chừng) bị gỡ."""
    lock = root / "backups" / ".backup.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        if lock.exists() and time.time() - lock.stat().st_mtime > LOCK_STALE_S:
            lock.unlink()
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise BackupLocked(f"Đang có một lần sao lưu khác chạy ({lock})") from None
    with os.fdopen(fd, "w") as f:
        f.write(f"{os.getpid()} {datetime.now().isoformat(timespec='seconds')}")
    return lock


def create(root: Path = ROOT, out_dir: Path | None = None) -> Path:
    """Tạo + kiểm chứng. Mặc định ghi vào `backups/.partial-<thời điểm>` rồi mới đổi tên — không bao giờ để lại bản dở."""
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=False)
        return _create_into(root, out_dir)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    final = root / "backups" / stamp
    work = root / "backups" / f".partial-{stamp}"
    lock = _lock(root)
    try:
        work.mkdir(parents=True, exist_ok=False)
        try:
            _create_into(root, work)
        except BaseException:
            shutil.rmtree(work, ignore_errors=True)
            raise
        work.rename(final)
        return final
    finally:
        lock.unlink(missing_ok=True)


def _create_into(root: Path, dest: Path) -> Path:
    t0 = time.perf_counter()
    manifest: Dict[str, Any] = {"created_at": datetime.now().isoformat(timespec="seconds"), "items": {}}
    for rel, kind in ITEMS:
        src = root / rel
        pg = _pg_target(root, rel) if kind == "sqlite" else None
        if pg is not None:
            # PostgreSQL là nguồn sự thật: bản chụp nhất quán (REPEATABLE READ) ra file SQLite —
            # cùng định dạng + cùng `verify`; khôi phục bằng pg_migration.migrate (đối chiếu checksum).
            from mateai.infrastructure.database.pg_migration import export_to_sqlite
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            export_to_sqlite(pg[0], pg[1], target)
            manifest["items"][rel] = {"kind": kind, "present": True, "source": "postgresql", "schema": pg[1],
                                      "sha256": _sha256(target), "rows": _row_counts(target)}
            continue
        if not src.exists():
            manifest["items"][rel] = {"kind": kind, "present": False}
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if kind == "sqlite":
            # API backup: bản chụp nhất quán kể cả khi máy chủ đang ghi (WAL).
            src_con = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True)
            dst_con = sqlite3.connect(target)
            try:
                src_con.backup(dst_con)
            finally:
                dst_con.close()
                src_con.close()
            manifest["items"][rel] = {"kind": kind, "present": True, "sha256": _sha256(target),
                                      "rows": _row_counts(target)}
        elif kind == "dir":
            shutil.copytree(src, target)
            files = {p.relative_to(target).as_posix(): _sha256(p) for p in sorted(target.rglob("*")) if p.is_file()}
            manifest["items"][rel] = {"kind": kind, "present": True, "files": files}
        else:
            shutil.copy2(src, target)
            manifest["items"][rel] = {"kind": kind, "present": True, "sha256": _sha256(target)}
    manifest["duration_s"] = round(time.perf_counter() - t0, 2)
    (dest / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    problems = verify(dest)
    if problems:
        raise RuntimeError("Sao lưu vừa tạo KHÔNG đạt kiểm chứng: " + "; ".join(problems))
    return dest


def verify(dest: Path) -> List[str]:
    """Danh sách lỗi (rỗng = đạt)."""
    mf = dest / "manifest.json"
    if not mf.is_file():
        return ["thiếu manifest.json"]
    manifest = json.loads(mf.read_text(encoding="utf-8"))
    problems: List[str] = []
    for rel, info in manifest["items"].items():
        if not info.get("present"):
            continue
        path = dest / rel
        if info["kind"] == "dir":
            for f, digest in info["files"].items():
                p = path / f
                if not p.is_file() or _sha256(p) != digest:
                    problems.append(f"{rel}/{f}: thiếu hoặc sai sha256")
            continue
        if not path.is_file():
            problems.append(f"{rel}: thiếu")
            continue
        if _sha256(path) != info["sha256"]:
            problems.append(f"{rel}: sai sha256")
        if info["kind"] == "sqlite":
            ok = _integrity(path)
            if ok != "ok":
                problems.append(f"{rel}: integrity_check = {ok}")
            if _row_counts(path) != info["rows"]:
                problems.append(f"{rel}: số dòng không khớp manifest")
    return problems


def restore(dest: Path, root: Path = ROOT) -> Path:
    """Ghi đè trạng thái hiện tại bằng bản sao lưu ĐÃ kiểm chứng. Trước đó lưu trạng thái
    hiện tại vào backups/pre-restore-<thời điểm>/ để quay lại được."""
    problems = verify(dest)
    if problems:
        raise RuntimeError("Không khôi phục: bản sao lưu không đạt kiểm chứng — " + "; ".join(problems))
    safety = create(root, out_dir=root / "backups" / f"pre-restore-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
    manifest = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
    for rel, info in manifest["items"].items():
        if not info.get("present"):
            continue
        src, target = dest / rel, root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        pg = _pg_target(root, rel) if info["kind"] == "sqlite" else None
        if pg is not None:
            # Đang chạy PostgreSQL: nạp bản chụp vào schema (thay toàn bộ, trong một transaction).
            from mateai.infrastructure.database.pg_migration import migrate
            rep = migrate(src, pg[0], schema=pg[1], replace=True)
            if not rep["ok"]:
                raise RuntimeError(f"Khôi phục {rel} vào PostgreSQL lệch: {rep['mismatches']}")
            continue
        if info["kind"] == "dir":
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(src, target)
        else:
            for suffix in ("-wal", "-shm"):   # nhật ký WAL cũ không được áp lên bản khôi phục
                side = Path(str(target) + suffix)
                if side.exists():
                    side.unlink()
            shutil.copy2(src, target)
    return safety


def _anchor_audit() -> None:
    """Neo đầu chuỗi băm audit ra ổ khác + object storage (mateai.application.security.audit_anchor). Lỗi chỉ báo."""
    try:
        sys.path.insert(0, str(ROOT / "src"))
        from mateai.application.security import audit_anchor
        res = audit_anchor.anchor()
        a = res.get("anchor") or {}
        print(f"Neo audit: dòng {a.get('id')} → {len(res.get('written') or [])} nơi" +
              (f"; lỗi: {'; '.join(res['errors'])}" if res.get("errors") else ""))
    except Exception as exc:  # noqa: BLE001
        print(f"NEO AUDIT LỖI: {type(exc).__name__}: {exc}")


def _store(store=None):
    if store is not None:
        return store
    sys.path.insert(0, str(ROOT / "src"))
    from mateai.infrastructure.files.object_storage import object_store
    return object_store()


def push(path: Path, store=None) -> str:
    """Đẩy bản sao lưu (thư mục -> nén zip; hoặc tệp có sẵn) lên object storage. Trả khoá."""
    path = Path(path)
    if path.is_dir():
        archive = _archive(path)
    else:
        archive = path
    key = f"backups/{archive.name}"
    _store(store).put(key, archive.read_bytes(), content_type="application/zip")
    return key


def _archive(path: Path) -> Path:
    path = Path(path)
    if path.is_file():
        return path
    zp = path.with_name(path.name + ".zip")
    return zp if zp.is_file() else Path(shutil.make_archive(str(path), "zip", root_dir=path))


_STAMP_ZIP = re.compile(r"^\d{8}-\d{6}\.zip$")


def offsite_settings(root: Path = ROOT) -> Dict[str, Any]:
    """`backup` trong config.json: offsite_dirs (thư mục trên Ổ VẬT LÝ KHÁC / ổ mạng / ổ ngoài),
    keep (số bản giữ ở mỗi thư mục đó, mặc định 30). Chỉ áp cho bản cài này (ROOT)."""
    if Path(root).resolve() != ROOT.resolve():
        return {"offsite_dirs": [], "keep": 30}
    sys.path.insert(0, str(root / "src"))
    from mateai.config.loader import read_raw_config
    cfg = read_raw_config().get("backup") or {}
    return {"offsite_dirs": [str(d) for d in cfg.get("offsite_dirs") or [] if str(d).strip()],
            "keep": max(1, int(cfg.get("keep", 30)))}


def replicate(path: Path, dirs: List[str], keep: int = 30) -> List[Dict[str, Any]]:
    """Chép bản sao lưu (nén zip) sang từng thư mục `dirs`, đối chiếu sha256 bản chép, rồi chỉ giữ
    `keep` bản mới nhất mang tên chuẩn `YYYYMMDD-HHMMSS.zip` ở thư mục đó (tệp khác không đụng tới).
    Lỗi một thư mục không chặn thư mục khác — kết quả từng nơi nằm trong danh sách trả về."""
    archive = _archive(path)
    digest = _sha256(archive)
    out: List[Dict[str, Any]] = []
    for d in dirs:
        target_dir = Path(d)
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / archive.name
            shutil.copy2(archive, target)
            if _sha256(target) != digest:
                raise OSError("bản chép sai sha256")
            olds = sorted(f for f in target_dir.iterdir() if f.is_file() and _STAMP_ZIP.match(f.name))
            removed = [f.name for f in olds[:-keep]] if len(olds) > keep else []
            for name in removed:
                (target_dir / name).unlink()
            out.append({"dir": str(target_dir), "ok": True, "file": target.name, "removed": removed})
        except OSError as exc:
            out.append({"dir": str(target_dir), "ok": False, "error": str(exc)})
    return out


def fetch(key: str, dest_dir: Path, store=None) -> Path:
    """Tải bản sao lưu từ object storage về `dest_dir` (thư mục backup: giải nén luôn)."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / Path(key).name
    out.write_bytes(_store(store).get(key))
    return out


def _report_replicas(path: Path) -> int:
    """Chép sang backup.offsite_dirs (nếu cấu hình). Mã thoát 3 khi một nơi lỗi — Task Scheduler
    ghi nhận lần chạy thất bại thay vì im lặng."""
    st = offsite_settings()
    if not st["offsite_dirs"]:
        return 0
    rc = 0
    for r in replicate(path, st["offsite_dirs"], st["keep"]):
        if r["ok"]:
            print(f"Đã chép sang {r['dir']}\{r['file']} (đối chiếu sha256 ĐẠT; xoá bản cũ: {len(r['removed'])})")
        else:
            print(f"LỖI chép sang {r['dir']}: {r['error']}")
            rc = 3
    return rc


# ── hạ tầng: PostgreSQL phải chạy trước khi sao lưu ─────────────────────────

def _pg_reachable(url: str, timeout: int = 3) -> bool:
    try:
        import psycopg
        with psycopg.connect(url, connect_timeout=timeout):
            return True
    except Exception:  # noqa: BLE001
        return False


def _run(cmd: List[str], timeout: float) -> bool:
    try:
        return subprocess.run(cmd, cwd=ROOT, capture_output=True, timeout=timeout).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _docker_desktop_exe() -> Path | None:
    cands = []
    docker = shutil.which("docker")
    if docker:
        cands.append(Path(docker).resolve().parents[2] / "Docker Desktop.exe")      # …/resources/bin/docker.exe
    for base in (os.environ.get("LOCALAPPDATA", ""), os.environ.get("ProgramFiles", "")):
        if base:
            cands += [Path(base) / "Programs" / "DockerDesktop" / "Docker Desktop.exe", Path(base) / "Docker" / "Docker" / "Docker Desktop.exe"]
    return next((c for c in cands if c.is_file()), None)


def ensure_infra(wait_engine_s: float = 240, wait_db_s: float = 180) -> str:
    """CSDL chạy trên PostgreSQL mà chưa kết nối được → bật Docker Desktop (Windows) + `docker compose up -d` hạ tầng, chờ CSDL.
    Trả mô tả việc đã làm; ném RuntimeError nói rõ bước hỏng."""
    target = _pg_target(ROOT, "vnmateai.db")
    if target is None:
        return "CSDL dùng SQLite — không cần hạ tầng"
    url = target[0]
    if _pg_reachable(url):
        return "PostgreSQL đã sẵn sàng"
    steps = []
    if not _run(["docker", "info"], 20):
        exe = _docker_desktop_exe() if sys.platform == "win32" else None
        if exe is None:
            raise RuntimeError("PostgreSQL không kết nối được và Docker không chạy (không tìm thấy Docker Desktop để tự bật)")
        subprocess.Popen([str(exe)], cwd=str(exe.parent), close_fds=True)
        steps.append("đã bật Docker Desktop")
        deadline = time.monotonic() + wait_engine_s
        while not _run(["docker", "info"], 20):
            if time.monotonic() > deadline:
                raise RuntimeError(f"Docker Desktop không sẵn sàng sau {wait_engine_s:.0f} giây")
            time.sleep(5)
    compose = ["docker", "compose", "-f", str(ROOT / "deploy" / "docker-compose.infra.yml")]
    if (ROOT / "deploy" / ".env").is_file():
        compose += ["--env-file", str(ROOT / "deploy" / ".env")]
    if not _run(compose + ["up", "-d"], 300):
        raise RuntimeError("`docker compose up -d` hạ tầng thất bại")
    steps.append("đã bật hạ tầng (docker compose up -d)")
    deadline = time.monotonic() + wait_db_s
    while not _pg_reachable(url):
        if time.monotonic() > deadline:
            raise RuntimeError(f"PostgreSQL vẫn không kết nối được sau {wait_db_s:.0f} giây ({'; '.join(steps)})")
        time.sleep(3)
    return "; ".join(steps) + "; PostgreSQL sẵn sàng"


def _notify_failure(reason: str) -> None:
    """Cảnh báo NGAY qua các kênh cảnh báo (Telegram…) — không đợi sentinel phát hiện sau 26 giờ. Lỗi gửi chỉ in ra."""
    try:
        sys.path.insert(0, str(ROOT / "src"))
        import asyncio
        from mateai.application.operations import alert_dispatcher
        asyncio.run(alert_dispatcher.dispatch("Sao lưu tự động THẤT BẠI", f"{reason[:500]} — xem logs/backup.log.",
                                              severity="critical", category="backup_failed", force=True))
    except Exception as exc:  # noqa: BLE001
        print(f"(không gửi được cảnh báo: {type(exc).__name__}: {exc})")


def main() -> int:
    ap = argparse.ArgumentParser(description="Sao lưu / kiểm chứng / khôi phục VN-MateAI")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("--push", action="store_true", help="đẩy bản nén lên object storage sau khi kiểm chứng")
    c.add_argument("--ensure-infra", action="store_true", help="PostgreSQL chưa chạy thì tự bật Docker + hạ tầng trước")
    c.add_argument("--no-alert", action="store_true", help="hỏng thì không gửi cảnh báo")
    rp = sub.add_parser("replicate", help="chép một bản sao lưu sang backup.offsite_dirs")
    rp.add_argument("path")
    p = sub.add_parser("push")
    p.add_argument("path")
    sub.add_parser("list-remote")
    pl = sub.add_parser("pull")
    pl.add_argument("key")
    v = sub.add_parser("verify")
    v.add_argument("path")
    r = sub.add_parser("restore")
    r.add_argument("path")
    r.add_argument("--yes", action="store_true", help="xác nhận đã DỪNG máy chủ và muốn ghi đè")
    args = ap.parse_args()
    if args.cmd == "create":
        print(f"── {datetime.now().isoformat(timespec='seconds')} sao lưu bắt đầu")
        try:
            if args.ensure_infra:
                print(f"Hạ tầng: {ensure_infra()}")
            dest = create()
        except BackupLocked as exc:
            print(f"BỎ QUA: {exc}")
            return 0
        except Exception as exc:  # noqa: BLE001
            print(f"THẤT BẠI: {type(exc).__name__}: {exc}")
            if not args.no_alert:
                _notify_failure(f"{type(exc).__name__}: {exc}")
            return 1
        m = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
        print(f"OK {dest} ({m['duration_s']} s) — đã kiểm chứng. Thư mục chứa BÍ MẬT, cất ở nơi an toàn.")
        if args.push:
            try:
                print(f"Đã đẩy lên object storage: {push(dest)} (bucket phải PRIVATE — bản sao lưu chứa bí mật).")
            except Exception as exc:  # noqa: BLE001 — bản trên máy + bản sao ổ khác vẫn có; báo nhưng không bỏ việc chép
                print(f"ĐẨY LÊN OBJECT STORAGE LỖI: {type(exc).__name__}: {exc}")
                if not args.no_alert:
                    _notify_failure(f"Đã tạo {dest.name} nhưng đẩy lên object storage lỗi: {exc}")
        rc = _report_replicas(dest)
        if rc and not args.no_alert:
            _notify_failure(f"Đã tạo {dest.name} nhưng chép sang ổ / thư mục dự phòng (backup.offsite_dirs) lỗi")
        _anchor_audit()
        return rc
    if args.cmd == "replicate":
        return _report_replicas(Path(args.path))
    if args.cmd == "push":
        print(f"Đã đẩy: {push(Path(args.path))}")
        return 0
    if args.cmd == "list-remote":
        for k in _store().list("backups/"):
            print(k)
        return 0
    if args.cmd == "pull":
        out = fetch(args.key, ROOT / "backups")
        if out.suffix == ".zip":
            target = out.with_suffix("")
            shutil.unpack_archive(str(out), str(target))
            problems = verify(target)
            print(f"{target}: " + ("ĐẠT kiểm chứng" if not problems else "KHÔNG ĐẠT: " + "; ".join(problems)))
            return 0 if not problems else 2
        print(f"Đã tải: {out}")
        return 0
    if args.cmd == "verify":
        problems = verify(Path(args.path))
        print("ĐẠT" if not problems else "KHÔNG ĐẠT:\n  " + "\n  ".join(problems))
        return 0 if not problems else 2
    if not args.yes:
        print("Khôi phục ghi đè dữ liệu hiện tại. Dừng máy chủ rồi chạy lại với --yes.")
        return 1
    safety = restore(Path(args.path))
    print(f"Đã khôi phục từ {args.path}. Trạng thái trước khi khôi phục: {safety}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")   # chạy từ Task Scheduler / console cp1252 vẫn in được tiếng Việt
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
