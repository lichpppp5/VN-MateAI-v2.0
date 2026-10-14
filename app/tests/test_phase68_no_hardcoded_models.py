# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_phase68_no_hardcoded_models.py
=========================================
Kiểm thử Phase 68 — không còn tên model của provider ghi cứng trong mã.

Bối cảnh
--------
Lúc Phase 68 viết, toàn bộ cấu hình model trỏ vào provider đã chết:
  - `ag/*` (antigravity) — 5 tài khoản, KHÔNG có khoá
  - `oc/*` (opencode-zen) — 402 "Insufficient account funds"
  - `mimo/*` (xiaomi-mimo) — 402 "Insufficient account balance"
  - `mmf/*` — không tồn tại ở đâu cả

Nhưng danh sách dự phòng lại GHI CỨNG tên model của các provider đó. Hệ quả:
mỗi lần lưu cấu hình, 3-4 model chết được ghi lại vào config.json, và vòng
lặp auto-fallback thử chết trước khi tới model thật — mỗi lần một lần gọi
ra ngoài rồi báo lỗi. Người dùng thấy "chậm" trong khi thực ra thời gian đang
bị đốt vào những model không tồn tại.

Cách sửa: hỏi router nó đang phục vụ model nào, rồi tin vào đó.
"""

from __future__ import annotations

import ast
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASSED = 0
FAILED = 0
FAILURES: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
    else:
        FAILED += 1
        FAILURES.append(f"  ✗ {name}" + (f" — {extra}" if extra else ""))


def section(title: str) -> None:
    print(f"\n▸ {title}")


# ── Danh sách provider/tên model ĐÃ CHẾT ──────────────────────────────────
#
# Phase 81: cả hai danh sách dưới đây từng SAI, và việc test báo động suốt
# mấy chục lượt chạy là bằng chứng. Gốc rễ giống nhau: chúng ghi lại trạng
# thái của router tại một thời điểm, rồi được coi như sự thật vĩnh viễn.
#
# Lúc Phase 68 viết, `ag/*` (antigravity) trả lỗi vì tài khoản chưa có khoá,
# nên bị xếp vào nhóm chết. Sau đó khoá có rồi: router phục vụ 20 model `ag/*`
# và gọi thật trả về nội dung đúng (`ag/gemini-3-flash` -> "OK",
# `ag/claude-sonnet-4-6` -> "OK"). Danh sách cũ không biết nên cứ báo động.
#
# Nay mỗi thay đổi đều phải KÈM BẰNG CHỨNG, và phần cuối tệp có bước tự gọi
# thật từng nhóm để lần sau provider nào chết thì test tự bắt, không cần ai
# sửa danh sách.
#
# `oc/`, `mmf/`, `mimo/` vẫn giữ: chúng đã biến mất khỏi router hoàn toàn, nên
# nếu xuất hiện lại thì đó đúng là hồi quy cần cảnh báo.
DEAD_PREFIXES = ("oc/", "mmf/", "mimo/")
#
# Phase 81: 6 tên dưới đây từng nằm trong danh sách này
# (gemini-3.8-flash, gemini-3.7-flash-medium, gemini-3-flash,
#  gemini-3.6-flash-medium, gemini-pro-agent, gemini-3.1-pro-low). Chúng được
# gỡ sau khi đối chiếu với router: cả 6 đều đang tồn tại và gọi được qua
# `ag/*`. Giữ lại sẽ bắt đầu báo động sai khi có ai đó nhắc tên model đang
# chạy trong mã.
DEAD_NAMES = (
    "gemini-2.5-flash", "claude-3-7-sonnet", "mimo-auto",
)


def dead_hits(text: str) -> list[str]:
    out = []
    for name in DEAD_NAMES:
        if name in text:
            out.append(name)
    return out


# ══ 1. Không còn model chết trong mã nguồn ══════════════════════════════
section("Mã nguồn không còn tên model chết")

TARGETS = [
    "src/mateai/application/agent/llm_engine.py", "src/mateai/infrastructure/llm/llm_provider.py",
    "src/mateai/config/loader.py", "src/mateai/interfaces/http/server.py",
    "src/mateai/application/operations/health_monitor.py", "src/mateai/application/skills/meta_architect.py",
    "web/app.js", "web/index.html",
    # Phase 82: template theo dõi bởi git — nếu nó chứa tên model chết thì
    # người dùng copy template làm config.json mới là khởi đầu với model hỏng.
    "config.example.json",
]
for rel in TARGETS:
    p = ROOT / rel
    if not p.exists():
        check(f"{rel} tồn tại", False, "không thấy file")
        continue
    hits = dead_hits(p.read_text(encoding="utf-8"))
    check(f"{rel} không nhắc model chết", not hits, f"còn {hits}")

# ══ 2. Danh sách dự phòng lấy từ router ═════════════════════════════════
section("Danh sách dự phòng lấy từ router")

srv = (ROOT / "src" / "mateai" / "interfaces" / "http" / "server.py").read_text(encoding="utf-8")
check("có hàm _router_model_pool", "async def _router_model_pool()" in srv)
check("hàm gọi /v1/models của router", "/v1/models" in srv)
check("hàm lọc bỏ combo (tên không có '/')",
      '"/" in mid' in srv,
      "combo không có dấu '/' — đưa vào dự phòng thì gọi lại chính nó")
check("endpoint trả danh sách cho UI", '"/api/v1/config/models"' in srv)
check("endpoint yêu cầu đăng nhập", "require_roles" in srv.split('"/api/v1/config/models"')[1][:400])
check("lưu cấu hình KHÔNG dùng danh sách ghi cứng",
      "DEFAULT_ROUTER_FALLBACKS = [" not in srv,
      "vẫn gán danh sách ghi cứng")
check("lưu cấu hình dùng pool từ router",
      "DEFAULT_ROUTER_FALLBACKS = list(pool)" in srv)

# ══ 3. Router có thực sự phục vụ model không ════════════════════════════
section("Router thực sự phục vụ model")
cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
base = (cfg.get("llm") or {}).get("base_url", "")
key = (cfg.get("llm") or {}).get("api_key", "")
if not base or not key:
    check("đọc được base_url + api_key từ config.json", False, "thiếu cấu hình")
else:
    root = base.rsplit("/v1", 1)[0]
    try:
        req = urllib.request.Request(
            f"{root}/v1/models", headers={"Authorization": f"Bearer {key}"}
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            payload = json.loads(r.read().decode("utf-8"))
        ids = [e.get("id") for e in payload.get("data") or [] if isinstance(e, dict)]
        real = [m for m in ids if m and "/" in m]
        check("router trả về danh sách model", len(ids) > 0, f"{len(ids)} model")
        check("có model thật (không phải chỉ combo)", len(real) > 0, str(ids))
        check("không còn model chết trong danh sách router",
              not any(m.startswith(DEAD_PREFIXES) for m in real),
              str([m for m in real if m.startswith(DEAD_PREFIXES)]))

        # ── TỰ CHỨNG MINH: nhóm model nào thật sự gọi được ──────────────
        #
        # Danh sách `DEAD_PREFIXES` ở trên là điều GHI NHỚ, và nó đã một lần
        # sai: `ag/*` bị xếp vào nhóm chết từ lúc chưa có khoá, rồi sau đó sống
        # lại mà danh sách không biết. Test báo động nhầm suốt từ đó.
        #
        # Nên bước này không tin danh sách: với MỖI nhóm có trong danh sách
        # router, gọi thật một model và tự kết luận. Sau này provider nào chết
        # thì test tự bắt được, không cần ai sửa danh sách.
        #
        # Cố ý gọi `stream: false`: mấy model `ag/*` trả `text/event-stream` dù
        # không yêu cầu, và nếu đọc như JSON thì tưởng provider chết trong khi
        # nó sống — đúng cái bẫy đã làm kiểm tra thủ công báo sai lúc đầu.
        def _goi(model: str):
            body = json.dumps({
                "model": model,
                "messages": [{"role": "user", "content": "Nói đúng một từ: OK"}],
                "max_tokens": 30,
                "stream": False,
            }).encode()
            req = urllib.request.Request(
                f"{root}/v1/chat/completions", data=body,
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {key}"},
            )
            with urllib.request.urlopen(req, timeout=45) as r:
                return r.status, r.read().decode("utf-8", "replace")

        nhom = {}
        for m in real:
            nhom.setdefault(m.split("/", 1)[0], []).append(m)

        for ten, models in sorted(nhom.items()):
            # Thử nhiều model, không chỉ model đầu tiên.
            #
            # Lý do: danh sách router trộn lẫn model gọi được với model không
            # dùng được cho endpoint này. Đo thật: `openrouter/typesafe/jev-1.13`
            # trả 400 "is a decisions model and cannot be used with the
            # chat/completions endpoint" — vĩnh viễn không dùng được; còn các
            # model `:free` thì chỉ hết hạn mức tạm thời (429). Nếu lấy đúng
            # model đầu tiên thì sẽ kết luận cả nhóm là chết và báo động giả.
            sống, chưa_kết_luận, ghi = False, False, ""
            for mau in models[:3]:
                try:
                    status, body = _goi(mau)
                    if status == 200 and body.lstrip().startswith("{"):
                        sống = True
                        ghi = f"{mau} -> HTTP 200"
                        break
                    ghi = f"{mau} -> HTTP {status}"
                except urllib.error.HTTPError as e:
                    code = e.code
                    if code in (429, 503):
                        # Hết hạn mức / quá tải: KHÔNG phải bằng chứng provider
                        # chết. Đánh dấu để cuối vòng bỏ qua thay vì báo động.
                        chưa_kết_luận = True
                        ghi = f"{mau} -> HTTP {code} (hết hạn mức)"
                        continue
                    ghi = f"{mau} -> HTTP {code}: {e.read()[:90]}"
                except Exception:
                    chưa_kết_luận = True
                    ghi = f"{mau} -> lỗi mạng/timeout"

            if sống:
                check(f"nhóm '{ten}' có model gọi thật được", True)
            elif chưa_kết_luận:
                check(f"nhóm '{ten}' có model gọi thật được", True,
                      f"bỏ qua — chưa đủ bằng chứng ({ghi})")
            else:
                check(f"nhóm '{ten}' có model gọi thật được", False, ghi)
    except Exception as exc:  # pylint: disable=broad-except
        check("gọi được /v1/models của router", False, f"{type(exc).__name__}: {exc}")

# ══ 4. config.json trỏ model đang chạy ══════════════════════════════════
section("config.json trỏ model đang chạy")
llm = cfg.get("llm") or {}
r_models = llm.get("router_models") or []
s_models = llm.get("specialist_models") or []
check("có router_models", len(r_models) > 0, str(r_models))
check("không model chết trong router_models",
      not any(m.startswith(DEAD_PREFIXES) for m in r_models),
      str([m for m in r_models if m.startswith(DEAD_PREFIXES)]))
check("có specialist_models", len(s_models) > 0, str(s_models))
check("không model chết trong specialist_models",
      not any(m.startswith(DEAD_PREFIXES) for m in s_models),
      str([m for m in s_models if m.startswith(DEAD_PREFIXES)]))
check("model chính không phải model chết",
      not str(llm.get("model_name", "")).startswith(DEAD_PREFIXES),
      str(llm.get("model_name")))

# ══ 5. Chuyển giao chuyên gia không còn trỏ chết ════════════════════════
section("Chuyển giao chuyên gia")
deleg = (ROOT / "src" / "mateai" / "application" / "skills" / "builtin" / "ai_delegation.py").read_text(encoding="utf-8")
check("ai_delegation dùng specialist_models", "specialist_models" in deleg)
check("không ghi cứng model chết trong ai_delegation", not dead_hits(deleg), str(dead_hits(deleg)))

# ══ 6. Báo lỗi rõ ràng khi chưa cấu hình model ══════════════════════════
section("Không cấu hình model thì báo lỗi rõ ràng")
lle = (ROOT / "src" / "mateai" / "application" / "agent" / "llm_engine.py").read_text(encoding="utf-8")
check("ask_async chặn danh sách model rỗng", "if not models:" in lle)
check("stream_voice_response chặn danh sách model rỗng",
      lle.count("if not models:") >= 2,
      f"chỉ {lle.count('if not models:')} chỗ")
check("thông báo lỗi chỉ tới tab cấu hình",
      "Quản Lý Trợ Lý AI" in lle,
      "lỗi phải chỉ người dùng tới đúng chỗ sửa")
check("không còn gọi model rỗng", "[active_m] if active_m else []" in lle)

# ══ 7. Giao diện lấy danh sách sống ════════════════════════════════════
section("Giao diện lấy danh sách model sống")
appjs = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
idx = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
check("app.js có routerModelList", "let routerModelList = []" in appjs)
check("app.js gọi endpoint model sống", "/api/v1/config/models" in appjs)
check("app.js nạp danh sách khi mở cấu hình", "loadRouterModels();" in appjs)
check("app.js có hàm fallbackModels", "function fallbackModels(" in appjs)
check("datalist ai-models-list rỗng chờ nạp", '<datalist id="ai-models-list"></datalist>' in idx)
check("datalist models-list rỗng chờ nạp", '<datalist id="models-list"></datalist>' in idx)
check("có container nút bấm nhanh tab AI", 'id="ai-quick-models"' in idx)
check("có container nút bấm nhanh tab Cấu hình", 'id="cfg-quick-models"' in idx)
check("có chỗ hiện chuỗi auto-fallback", 'id="cfg-fallback-chain"' in idx)
check("chuỗi fallback được nạp động", "cfg-fallback-chain" in appjs)
check("không còn datalist model ghi cứng",
      not re.search(r'<datalist id="(?:ai-models-list|models-list)">\s*<option', idx),
      "vẫn còn <option> ghi cứng trong datalist")
check("router rỗng thì báo rõ, không im lặng",
      "Router chưa phục vụ model nào" in appjs)

# ══ 8. Hàm _router_model_pool xử lý đúng các tình huống ════════════════
section("_router_model_pool xử lý đúng tình huống")
tree = ast.parse(srv)
fn = next((n for n in ast.walk(tree)
           if isinstance(n, ast.AsyncFunctionDef) and n.name == "_router_model_pool"), None)
check("tồn tại hàm", fn is not None)
if fn:
    src = ast.get_source_segment(srv, fn) or ""
    check("có timeout", "timeout=" in src)
    check("bắt lỗi, không làm sập trang cấu hình", "except Exception" in src)
    check("ghi log khi hỏi router thất bại", "logger.warning" in src)
    check("bỏ model rỗng", "if mid and" in src)
    check("loại trùng lặp", "not in out" in src)
    check("timeout ngắn (không treo giao diện)", "timeout=5" in src or "timeout=6" in src,
          "timeout phải ngắn để không treo trang cấu hình")

# ══ 9. Danh sách model SẮP XẾP và nằm trong ô sổ ra, không in tràn màn hình ══
# Người dùng phản ánh: "Tên Mô Hình kéo từ 9router cần sắp xếp ẩn vào thanh
# sổ ra, hiện tại nó đang in hết ra màn hình, chiếm không gian". Trước đây
# `loadRouterModels()` in TOÀN BỘ model thành chip nút ở cả hai tab; nay gom
# vào một <select> đã sắp xếp theo tên.
section("Danh sách model được sắp xếp và gom vào ô sổ ra")
appjs3 = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
idx3 = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
check("có hàm sắp xếp dùng chung", "function _sortModels" in appjs3)
sort_src = appjs3.split("function _sortModels", 1)[-1].split("\nfunction ", 1)[0]
check("sắp xếp theo tên (localeCompare)", "localeCompare" in sort_src)
check("bỏ giá trị rỗng trước khi sort", ".filter" in sort_src)
check("loadRouterModels dùng hàm sắp xếp", "_sortModels(" in appjs3.split(
    "async function loadRouterModels", 1)[-1].split(
    "async function loadConfig", 1)[0])
# Hai ô chọn cùng nguồn router — trước đây tab Trợ lý AI tự gọi loadAIProxyModels
# (query proxy thẳng bằng ô khoá trống, trả 31 model) trong khi tab Cấu Hình
# dùng router pool (30 model): cùng một ô chọn mà tuỳ tab mở trước mà khác nhau.
check("tab AI không còn tự gọi query proxy riêng",
      "loadRouterModels();" in appjs3.split("function loadAIManagerConfig", 1)[
          -1].split("async function saveAIConfig", 1)[0] or
      "loadRouterModels();" in appjs3.split("updatePromptStats();", 1)[-1].split(
          "\n}", 1)[0],
      "phải nạp từ router pool, không query proxy với ô khoá trống")
check("loadRouterModels đổ model vào cả hai select",
      "'ai-proxy-model-select'" in appjs3 and "'proxy-model-select'" in appjs3)
loadrm_src = appjs3.split("async function loadRouterModels", 1)[-1].split(
    "async function loadConfig", 1)[0]
check("cả hai select có model thì hiện, không có thì ẩn",
      "classList.toggle('hidden'" in loadrm_src)
check("không còn in chip từng model trong loadRouterModels",
      "selectQuickModel(" not in loadrm_src and "selectConfigQuickModel(" not in loadrm_src,
      "vẫn vẽ chip? 'selectQuickModel(' còn trong phần thân loadRouterModels")
check("có nơi báo gọn số model thay vì dãy nút",
      "chọn trong ô sổ ra bên trên" in appjs3)
# Hàm onModelChanged từng được ô select tham chiếu nhưng CHƯA từng được định
# nghĩa — chọn model không gây ra gì ngoài việc điền ô nhập.
check("hàm onModelChanged được định nghĩa (trước chỉ tham chiếu)",
      "function onModelChanged" in appjs3,
      "‘onModelChanged’ bị select tham chiếu mà không bao giờ tồn tại")
check("onModelChanged cập nhật fallback chain",
      "renderFallbackChain();" in appjs3.split("function onModelChanged", 1)[-1].split(
          "\n\n", 1)[0])

# ══ 10. Template config.example.json không gợi ý model chết/cứng ═════════════
# Người dùng yêu cầu: "API key, Model không được cố định trong core — đổi key
# chỉ cần điền key mới". Template theo dõi bởi git là thứ người mới copy ra làm
# config.json; danh sách model ghi cứng trong đó sẽ chết theo thời gian như các
# danh sách đã từng chết. Template phải để trống danh sách dự phòng (nạp từ
# router lúc người dùng bấm Lưu — Phase 68) và model chính là placeholder.
section("Template config.example.json không gợi ý model chết/cứng")
EXAMPLE = (ROOT / "config.example.json").read_text(encoding="utf-8")
ex_cfg = json.loads(EXAMPLE)
ex_llm = ex_cfg.get("llm") or {}
check("config.example.json có cấu trúc llm", isinstance(ex_llm, dict), str(type(ex_llm)))
ex_all_models = (ex_llm.get("router_models") or []) + (ex_llm.get("specialist_models") or [])
check("không model chết trong router_models/specialist_models của template",
      not any(str(m).startswith(DEAD_PREFIXES) for m in ex_all_models),
      str([m for m in ex_all_models if str(m).startswith(DEAD_PREFIXES)]))
check("router_models của template trống (để router cấp lúc lưu)",
      len(ex_llm.get("router_models") or []) == 0, str(ex_llm.get("router_models")))
check("specialist_models của template trống (để router cấp lúc lưu)",
      len(ex_llm.get("specialist_models") or []) == 0, str(ex_llm.get("specialist_models")))
mn = str(ex_llm.get("model_name") or ex_cfg.get("MODEL_NAME") or "")
check("model_name của template là placeholder, không phải tên hãng",
      mn.startswith("YOUR_") or mn == "", mn)
# Mọi chỗ nhắc provider_model trong template phải placeholder — cấu hình tương
# thích ngược nhân bản cùng giá trị ra nhiều đường dẫn, sót một chỗ là lệch.
for _path, _val in (("routing.primary", (ex_cfg.get("routing") or {}).get("primary", {}).get("provider_model", "")),
                    ("router.primary", (ex_cfg.get("router") or {}).get("primary", {}).get("provider_model", ""))):
    check(f"provider_model của {_path} là placeholder",
          not _val or str(_val).startswith("YOUR_"), str(_val))

print("\n" + "─" * 66)
if FAILURES:
    print("Các assertion FAIL:")
    for f in FAILURES:
        print(f)
print(f"\nTổng: {PASSED + FAILED} | Pass: {PASSED} | Fail: {FAILED}")
sys.exit(1 if FAILED else 0)
