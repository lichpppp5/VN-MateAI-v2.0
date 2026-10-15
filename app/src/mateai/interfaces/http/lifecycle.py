# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/lifecycle.py
===================================
Khởi động / tắt dịch vụ nền của máy chủ. Chuyển từ `server.py` (Supervisor Phase 10,
§198: `server.py` chỉ dựng app).

Mỗi dịch vụ là MỘT bước có tên (`default_steps()`, giữ nguyên thứ tự cũ). Kết quả từng
bước — ok / skipped / error, kèm thời gian — nằm ở `STATE.report` và ở
`GET /api/v1/health/startup` (admin). Trước đây bước lỗi chỉ để lại một dòng log
cảnh báo: dịch vụ tắt mà không ai biết.

  - Bước thường lỗi → ghi lại, chạy tiếp các bước sau (như cũ).
  - Bước lõi (`critical`) lỗi → dừng khởi động (như cũ: hai đoạn này vốn nằm ngoài try).
  - `main.py` chạy HAI uvicorn trên cùng `app` (HTTPS + IoT) nên sự kiện startup /
    shutdown tới HAI LẦN; chỉ lần đầu chạy (skill nạp 2 lần, Sentinel 2 task… là lỗi cũ).
  - Task nền tạo qua `spawn()`: asyncio chỉ giữ tham chiếu YẾU tới task, task không ai
    giữ có thể bị thu gom giữa chừng.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Union

logger = logging.getLogger(__name__)


class StepSkipped(Exception):
    """Bước không chạy vì cấu hình (tắt / chưa cấu hình) — không phải lỗi."""


@dataclass
class StepContext:
    app: Any
    loop: asyncio.AbstractEventLoop


StepFn = Callable[[StepContext], Union[None, str, Awaitable[Optional[str]]]]


@dataclass
class Step:
    name: str
    run: StepFn
    critical: bool = False


@dataclass
class LifecycleState:
    started: bool = False
    complete: bool = False
    stopped: bool = False
    report: Dict[str, Dict[str, Any]] = field(default_factory=dict)


STATE = LifecycleState()
_BACKGROUND: Set["asyncio.Task[Any]"] = set()


def spawn(coro: Awaitable[Any], name: str) -> "asyncio.Task[Any]":
    """Tạo task nền và GIỮ tham chiếu tới khi xong; lỗi của task được ghi log."""
    task = asyncio.ensure_future(coro)
    try:
        task.set_name(name)
    except AttributeError:  # pragma: no cover
        pass
    _BACKGROUND.add(task)

    def _done(t: "asyncio.Task[Any]") -> None:
        _BACKGROUND.discard(t)
        if not t.cancelled() and t.exception() is not None:
            logger.error("Task nền '%s' dừng vì lỗi: %r", name, t.exception())

    task.add_done_callback(_done)
    return task


# ── Các bước khởi động (thứ tự giữ nguyên như server.py cũ) ──────────────────

def _core(ctx: StepContext) -> Awaitable[str]:
    from core.plugin_manager import plugin_manager
    from mateai.application.devices.task_manager import task_manager
    from mateai.interfaces.http import log_stream
    from mateai.interfaces.websocket import audio_announce
    from mateai.interfaces.websocket.client_orchestrator import orchestrator

    log_stream.install(ctx.loop)                 # Phase 21: log thời gian thực qua WS
    orchestrator.set_event_loop(ctx.loop)
    audio_announce.bind_loop(ctx.loop)
    task_manager.set_tts_notifier(audio_announce.broadcast_tts_notification)
    # Hàng đợi duyệt duy nhất: khôi phục yêu cầu còn hạn (tác vụ từ cổng tool) sau khi
    # khởi động lại. Executor "tool" đăng ký khi import tool_gate.
    import mateai.application.agent.tool_gate  # noqa: F401
    from mateai.application.security.zero_trust import hitl_manager
    hitl_manager.restore_pending_from_audit()

    async def _load() -> str:
        count = await ctx.loop.run_in_executor(None, plugin_manager.load_plugins)
        logger.info("FastAPI startup: loaded %d skill(s). Orchestrator & TaskManager ready.", count)
        return f"{count} skill"

    return _load()


def _tracing(ctx: StepContext) -> str:
    from mateai.infrastructure.observability.tracing import setup_tracing
    from mateai.config.loader import settings
    setup_tracing()
    return f"exporter {settings.OTEL_EXPORTER}"


def _ephemeral_sweeper(ctx: StepContext) -> None:
    from mateai.infrastructure.cache.ephemeral_cache import ephemeral_cache
    spawn(ephemeral_cache.start_sweeper_loop(), "ephemeral-sweeper")   # tự huỷ dữ liệu RAM mỗi 60 s


def _telegram(ctx: StepContext) -> str:
    from mateai.config.loader import get_config_section
    from mateai.interfaces.telegram.telegram_gateway import telegram_gateway
    if not get_config_section("telegram").get("enabled", False):
        raise StepSkipped("tắt trong cấu hình")
    if not telegram_gateway.start():
        raise StepSkipped("chưa cấu hình bot_token")
    return "đã chạy"


def _observability(ctx: StepContext) -> None:
    from mateai.application.operations.health_monitor import start_observability_workers
    spawn(start_observability_workers(), "observability-workers")


def _hud_telemetry(ctx: StepContext) -> None:
    from mateai.interfaces.http import hud_voice
    spawn(hud_voice.telemetry_loop(), "hud-telemetry")


def _encrypt_secrets(ctx: StepContext) -> None:
    from mateai.config.loader import encrypt_existing_secrets
    encrypt_existing_secrets()                   # khoá còn dạng chữ thường -> mã hoá (một lần)
    try:
        from mateai.infrastructure.connectors import custom_registry
        custom_registry.encrypt_existing()       # khoá nguồn dữ liệu doanh nghiệp (config/data_sources.json)
    except Exception:  # noqa: BLE001 — không để lỗi tệp khai báo chặn khởi động
        logger.exception("Không mã hoá được khoá nguồn dữ liệu")


def _alert_dispatcher(ctx: StepContext) -> None:
    from mateai.application.operations import alert_dispatcher
    alert_dispatcher.bind_loop(ctx.loop)         # notify() gọi từ thread đẩy về loop này


def _topology(ctx: StepContext) -> None:
    from mateai.interfaces.http.topology import topology_loop
    spawn(topology_loop(), "topology-loop")      # /admin/topology: trạng thái thật 2 s/lần


def _sentinel(ctx: StepContext) -> None:
    from mateai.application.operations.autonomous_sentinel import autonomous_sentinel
    autonomous_sentinel.start()


def _udp_beacon(ctx: StepContext) -> str:
    from mateai.interfaces.iot import discovery_beacon
    return discovery_beacon.start()


def _proactive_manager(ctx: StepContext) -> None:
    from mateai.application.skills.builtin.proactive_manager import proactive_manager
    proactive_manager.start()                    # đôn đốc 08:00 & 16:00


def _rag_warmup(ctx: StepContext) -> str:
    from mateai.application.knowledge.rag_engine import rag_engine
    return f"{rag_engine.collection.count()} đoạn tài liệu"


def _multi_agent_warmup(ctx: StepContext) -> str:
    from mateai.application.agent.agent_orchestrator import multi_agent_system  # noqa: F401
    from mateai.application.analytics.analytics_engine import analytics_engine  # noqa: F401
    from mateai.application.knowledge.graph_rag import graph_rag
    return f"GraphRAG {len(graph_rag.nodes)} thực thể, {len(graph_rag.edges)} quan hệ"


def _webhook_routes(ctx: StepContext) -> None:
    from mateai.interfaces.http.webhook_gateway import register_webhook_routes
    register_webhook_routes(ctx.app)             # /api/webhooks/{source}


def _connectors(ctx: StepContext) -> None:
    from mateai.infrastructure.connectors import (  # noqa: F401
        aws_connector,
        einvoice_connector,
        oci_connector,
        paperless_connector,
    )


def _dev_fleet(ctx: StepContext) -> str:
    """Dev Fleet (ngoại vi): chỉ khi module bật + có endpoint mới chạy vòng đồng bộ nền. Tắt = không làm gì."""
    from mateai.application.devfleet.service import dev_fleet
    if dev_fleet.mode == "disabled" or not dev_fleet.cfg.endpoint:
        return "tắt"
    dev_fleet.start_poller()
    return f"chế độ {dev_fleet.mode}"


def _infra_monitor(ctx: StepContext) -> str:
    """Giám sát hạ tầng: chỉ chạy vòng nền khi đã có nguồn Prometheus / Grafana. Không có = không làm gì."""
    from mateai.application.monitoring import infra_monitor
    if not infra_monitor.monitor_sources(include_secrets=False):
        return "chưa có nguồn giám sát"
    infra_monitor.start_poller()
    return "đang chạy"


def _playbooks(ctx: StepContext) -> str:
    """Lượt chạy kịch bản đang dở khi máy chủ chết -> INTERRUPTED (không tự chạy lại)."""
    from mateai.application.playbooks import engine
    n = engine.recover_interrupted()
    from mateai.application.llmops import evaluation
    n += evaluation.recover_interrupted()
    from mateai.application.playbooks import triggers
    triggers.start_scheduler()                     # kịch bản tự kích hoạt theo lịch (cron)
    return f"{n} lượt chạy bị gián đoạn được đánh dấu" if n else "ok"


def _orchestration(ctx: StepContext) -> str:
    """Lõi điều phối: phiếu đang giao dở khi máy chủ chết -> INTERRUPTED; vòng nền đối soát duyệt + giao hàng đợi."""
    from mateai.application.orchestration import service
    n = service.recover_interrupted()
    spawn(service.background_loop(), "orchestration-pump")
    return f"{n} phiếu việc bị gián đoạn được đánh dấu" if n else "ok"


def _acoustic_ack(ctx: StepContext) -> None:
    from mateai.application.commands.fast_command_router import STATIC_REPLIES
    from mateai.infrastructure.tts.acoustic_ack import warmup_acoustic_ack_cache
    from mateai.interfaces.http import hud_voice
    # Realtime P4: thêm câu cố định của lệnh nhanh và vòng hội thoại HUD.
    spawn(warmup_acoustic_ack_cache(
        extra_phrases=(*STATIC_REPLIES, hud_voice.FOLLOW_UP_QUESTION, hud_voice.FAREWELL, hud_voice.GOODBYE)),
        "acoustic-ack-warmup")


def _connector_tools(ctx: StepContext) -> str:
    # TRƯỚC request đầu tiên: `ask_async()` đọc `get_all_tools_schema()` mỗi lượt, đăng ký
    # muộn làm tool vô hình với LLM tới lượt sau.
    from mateai.infrastructure.connectors.tool_bridge import register_connector_tools
    st = register_connector_tools()
    return f"{st.get('registered', 0)} tool, bỏ qua {st.get('skipped', 0)}"


def _computer_use(ctx: StepContext) -> str:
    from mateai.application.skills.computer_use_plugin import register_computer_use_tool
    return f"{register_computer_use_tool().get('registered', 0)} tool"


def _mcp_gateway(ctx: StepContext) -> str:
    """Công cụ MCP đã lưu đăng ký ngay (AI thấy từ lượt đầu); kết nối lại + đồng bộ danh sách chạy nền."""
    from mateai.application.agent_gateway import directory, mcp_gateway
    n = mcp_gateway.register_all_from_cache()
    a2a = directory.register_all()                 # agent A2A: công cụ từ thẻ agent đã lưu
    servers = len(mcp_gateway.list_servers())
    if servers:
        spawn(mcp_gateway.refresh_all(), "mcp-refresh")
    return f"{servers} MCP server, {n} công cụ MCP; {a2a} agent A2A"


def _backup_catchup(ctx: StepContext) -> str:
    """Sao lưu chạy bù khi bản mới nhất cũ hơn ngưỡng (tác vụ đặt lịch 02:00 có thể lỡ khi Docker / máy tắt)."""
    from mateai.application.operations import backup_catchup
    return backup_catchup.start()


def _desktop_session(ctx: StepContext) -> str:
    """Báo sớm khi máy chủ chạy ở phiên nền: tool cần màn hình (mở trình duyệt, ứng dụng…) sẽ bị từ chối rõ ràng."""
    from mateai.infrastructure.desktop_session import status
    st = status(refresh=True)
    if not st.get("interactive"):
        logger.warning("[Desktop] %s — tool cần màn hình trên máy chủ sẽ bị từ chối; chạy bằng Start.bat trong phiên đăng nhập.", st.get("reason"))
        return f"KHÔNG có màn hình: {st.get('reason')}"
    return f"có màn hình ({st.get('reason')})"


def _default_passwords(ctx: StepContext) -> str:
    """Tài khoản mặc định còn dùng mật khẩu dev công khai (admin123…) → bắt đổi ở lần đăng nhập tới."""
    from mateai.infrastructure.database.db_manager import db_manager
    flagged = db_manager.flag_known_dev_passwords()
    return f"bắt đổi mật khẩu: {', '.join(flagged)}" if flagged else "không còn mật khẩu dev"


def _schema_migrations(ctx: StepContext) -> str:
    """Bước nâng cấp CSDL có số thứ tự (migrations.py) — sao lưu trước khi chạy bước thật trên CSDL có dữ liệu."""
    from mateai.infrastructure.database import migrations
    res = migrations.migrate(migrations.main_db_connect)
    if res.get("unknown"):
        return f"CSDL mới hơn ứng dụng ({res['unknown'][-1]}) — /readyz báo lỗi"
    ran = res.get("ran") or []
    return f"phiên bản {res.get('current')}" + (f", vừa chạy {', '.join(ran)}" if ran else "") +         (f", sao lưu {res['backup']}" if res.get("backup") else "")


def _background_workers(ctx: StepContext) -> None:
    from mateai.application.operations.background_workers import background_worker_manager
    spawn(background_worker_manager.start(), "background-workers")


def _email_gateway(ctx: StepContext) -> str:
    from mateai.config.loader import get_config_section
    from mateai.interfaces.email.email_gateway import email_gateway
    cfg = get_config_section("email_gateway")
    if not (cfg.get("enabled") and cfg.get("username")):
        raise StepSkipped("tắt hoặc chưa cấu hình")
    email_gateway.configure(
        username=cfg["username"],
        password=cfg.get("password", ""),
        imap_host=cfg.get("imap_host", "imap.gmail.com"),
        smtp_host=cfg.get("smtp_host", "smtp.gmail.com"),
        enabled=True,
    )
    email_gateway.start()
    return "đang theo dõi hộp thư"


def default_steps() -> List[Step]:
    return [
        Step("core", _core, critical=True),
        Step("schema_migrations", _schema_migrations),
        Step("default_passwords", _default_passwords),
        Step("tracing", _tracing),
        Step("ephemeral_sweeper", _ephemeral_sweeper),
        Step("telegram", _telegram),
        Step("observability_workers", _observability),
        Step("hud_telemetry", _hud_telemetry),
        Step("encrypt_secrets", _encrypt_secrets),
        Step("alert_dispatcher", _alert_dispatcher, critical=True),
        Step("topology_loop", _topology),
        Step("autonomous_sentinel", _sentinel),
        Step("udp_beacon", _udp_beacon),
        Step("proactive_manager", _proactive_manager),
        Step("rag_warmup", _rag_warmup),
        Step("multi_agent_warmup", _multi_agent_warmup),
        Step("webhook_routes", _webhook_routes),
        Step("connectors", _connectors),
        Step("acoustic_ack_warmup", _acoustic_ack),
        Step("connector_tools", _connector_tools),
        Step("dev_fleet", _dev_fleet),
        Step("infra_monitor", _infra_monitor),
        Step("playbooks", _playbooks),
        Step("orchestration", _orchestration),
        Step("computer_use_tool", _computer_use),
        Step("desktop_session", _desktop_session),
        Step("mcp_gateway", _mcp_gateway),
        Step("backup_catchup", _backup_catchup),
        Step("background_workers", _background_workers),
        Step("email_gateway", _email_gateway),
    ]


async def run_startup(app: Any, steps: Optional[List[Step]] = None) -> None:
    if STATE.started:
        logger.info("Startup lifecycle already initialised (dual uvicorn listener) — skipping second run.")
        return
    STATE.started = True
    logger.info("VN-MateAI © 2026 Dương Thanh Lịch (https://github.com/lichpppp/VNMateAI-v1.0)")
    ctx = StepContext(app=app, loop=asyncio.get_running_loop())
    for step in steps if steps is not None else default_steps():
        t0 = time.perf_counter()
        status, detail = "ok", ""
        try:
            out = step.run(ctx)
            if inspect.isawaitable(out):
                out = await out
            detail = str(out) if out else ""
        except StepSkipped as skip:
            status, detail = "skipped", str(skip)
        except Exception as exc:  # noqa: BLE001 — một dịch vụ hỏng không chặn các dịch vụ khác
            status, detail = "error", f"{type(exc).__name__}: {exc}"
            if step.critical:
                STATE.report[step.name] = {"status": status, "detail": detail,
                                           "ms": round((time.perf_counter() - t0) * 1000, 1)}
                logger.critical("Khởi động: bước lõi '%s' lỗi — dừng khởi động: %s", step.name, detail)
                raise
        STATE.report[step.name] = {"status": status, "detail": detail,
                                   "ms": round((time.perf_counter() - t0) * 1000, 1)}
        log = logger.warning if status == "error" else logger.info
        log("Khởi động [%s]: %s%s", step.name, status, f" — {detail}" if detail else "")
    STATE.complete = True
    logger.info("Startup hoàn tất — /startupz và /readyz sẵn sàng.")


async def run_shutdown() -> None:
    """Dừng dịch vụ nền (một lần, dù hai listener cùng báo tắt)."""
    if STATE.stopped:
        return
    STATE.stopped = True
    logger.info("Shutdown initiated — stopping background services...")
    try:
        from mateai.application.operations.background_workers import background_worker_manager
        await background_worker_manager.stop(timeout=10.0)
        logger.info("Background Worker Manager stopped.")
    except Exception as e:  # noqa: BLE001
        logger.warning("Background Worker Manager shutdown error: %s", e)

    # Supervisor P9 (§101): luồng đôn đốc, luồng đọc email trước đây không được dừng.
    for name, mod, attr in (
        ("Autonomous Sentinel", "mateai.application.operations.autonomous_sentinel", "autonomous_sentinel"),
        ("Telegram Gateway", "mateai.interfaces.telegram.telegram_gateway", "telegram_gateway"),
        ("Proactive Manager", "mateai.application.skills.builtin.proactive_manager", "proactive_manager"),
        ("Email Gateway", "mateai.interfaces.email.email_gateway", "email_gateway"),
        ("Dev Fleet", "mateai.application.devfleet.service", "dev_fleet"),
        ("Infra Monitor", "mateai.application.monitoring.infra_monitor", "infra_monitor_stopper"),
    ):
        try:
            getattr(__import__(mod, fromlist=[attr]), attr).stop()
            logger.info("%s stopped.", name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s stop error: %s", name, exc)

    loop = asyncio.get_running_loop()
    pending = [t for t in _BACKGROUND if not t.done() and t.get_loop() is loop]
    for t in pending:
        t.cancel()
    if pending:
        await asyncio.wait(pending, timeout=5.0)
    try:
        from mateai.infrastructure.http.connection_pool import connection_pool_manager
        await connection_pool_manager.close_all()      # pool HTTP (LLM / TTS / STT)
        logger.info("HTTP connection pools closed.")
    except Exception as exc:  # noqa: BLE001
        logger.warning("HTTP pool close error: %s", exc)
    logger.info("VN-MateAI shutdown complete.")
