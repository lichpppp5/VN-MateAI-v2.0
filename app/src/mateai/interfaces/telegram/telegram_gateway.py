# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/telegram_gateway.py
========================
Telegram Bot Gateway for VN-MateAI — Phase 18: Native Domain Sync & Telegram Gateway.

Responsibilities:
  - Outbound Alerting: Push incident/system alerts to a configured Telegram group.
  - Inbound Chat: Listen for admin queries via Telegram, forward to LLM engine, reply with AI response.
  - Background Thread: Non-blocking operation that doesn't interfere with FastAPI/uvicorn.
  - Fault Tolerance: Network loss or bad tokens NEVER crash the web server process.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy import of telegram to avoid startup crash if library is unavailable
# ---------------------------------------------------------------------------
try:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
    from telegram.ext import (
        Application,
        ApplicationBuilder,
        CallbackQueryHandler,
        CommandHandler,
        ContextTypes,
        MessageHandler,
        filters,
    )
    _TELEGRAM_AVAILABLE = True
except ImportError:
    _TELEGRAM_AVAILABLE = False
    logger.warning("[TelegramGateway] python-telegram-bot not installed. Telegram features disabled.")

# ── Hằng số cho nút bấm HITL 1-Tap (Phase 58 BƯỚC 3) ───────────────────────────
# Callback data phải dưới 64 byte theo quy định của Telegram Bot API, nên chỉ
# mang ký hiệu ngắn + mã yêu cầu (dạng "HITL-A1B2C3D4" = 14 ký tự).
HITL_CB_APPROVE = "hitl:ok:"
HITL_CB_REJECT = "hitl:no:"
HITL_CB_DATA_MAX = 64

# Tin nhắn Telegram tối đa 4096 ký tự. Tin nhắng HITL chứa cả tham số tác vụ
# (danh sách đơn vị, họ tên, số tiền...) nên rất dễ vượt — cắt bớt phần tham
# số thay vì để Telegram từ chối cả tin nhắn.
TELEGRAM_MAX_TEXT = 4096
_HITL_TRUNCATE_AT = 3000


def escape_markdown(text: str, version: int = 2) -> str:
    """
    Escape special characters for Telegram Markdown (v1) or MarkdownV2.
    For MarkdownV2: _, *, [, ], (, ), ~, >, #, +, -, =, |, {, }, ., !
    For Markdown (v1): _, *, `, [
    """
    if not text:
        return ""
    import re
    if version == 2:
        escape_chars = r"_*[]()~>#+-=|{}.!"
        return re.sub(f"([{re.escape(escape_chars)}])", r"\\\1", text)
    else:
        escape_chars = r"_*`["
        return re.sub(f"([{re.escape(escape_chars)}])", r"\\\1", text)


class TelegramBotService:
    """
    Telegram Bot Service — dual-channel AI gateway.

    Start with .start() to launch in a background daemon thread.
    Stop with .stop() for graceful shutdown.
    Use .send_incident_alert(message) to push outbound alerts from anywhere in the codebase.
    """

    def __init__(self) -> None:
        self._app: Optional[Any] = None  # telegram.ext.Application
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._running: bool = False
        self._ready: bool = False
        self._chat_histories: Dict[str, List[Dict[str, str]]] = {}
        self._recent_chats: Dict[str, Dict[str, Any]] = {}

    # Token thật của Telegram: "<số>:<chuỗi>". Giá trị mẫu (YOUR_..._HERE) hay
    # chuỗi gõ nhầm không được đem đi gọi API — vừa vô ích, vừa ghi nó vào log.
    _BOT_TOKEN_RE = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{20,}$")

    def _outbound_config(self) -> Optional[Any]:
        """Cấu hình để GỬI tin chủ động — chỉ khi gateway đang BẬT và token đúng dạng.

        `VNMATEAI_TELEGRAM_OUTBOUND=off` tắt mọi tin chủ động (cảnh báo, yêu cầu
        duyệt) bất kể config.json — bộ test đặt biến này cho cả phiên, vì config
        thật có token bot thật và từng có tin thử lọt vào nhóm vận hành.
        """
        import os
        if os.environ.get("VNMATEAI_TELEGRAM_OUTBOUND", "").strip().lower() == "off":
            return None
        from mateai.config.loader import get_config_section
        if not get_config_section("telegram").get("enabled", False):
            return None
        cfg = self._get_config()
        if not cfg or not self._BOT_TOKEN_RE.match((cfg.bot_token or "").strip()):
            return None
        return cfg

    def _get_config(self) -> Optional[Any]:
        """Lazily load telegram config from settings, reloading if needed or falling back to config.json."""
        try:
            from mateai.config.loader import settings, reload_settings_if_changed
            try:
                reload_settings_if_changed()
            except Exception:
                pass
            cfg = getattr(settings, "telegram", None)
            if cfg and cfg.bot_token:
                return cfg
        except Exception:
            pass

        # Resilient fallback: read directly from config.json
        try:
            from mateai.config.loader import TelegramConfig, get_config_section
            tg_data = get_config_section("telegram")
            if tg_data.get("bot_token"):
                return TelegramConfig(**tg_data)
        except Exception as exc:
            logger.error("[TelegramGateway] Direct config.json read error: %s", exc)

        return None

    def _record_recent_chat(self, chat_id: str, name: str, username: str) -> None:
        """Cache incoming chat metadata for easy auto-detection in the UI."""
        if not chat_id:
            return
        from datetime import datetime as _dt
        self._recent_chats[str(chat_id)] = {
            "chat_id": str(chat_id),
            "name": name or "User",
            "username": f"@{username}" if username and not username.startswith("@") else username,
            "updated_at": _dt.now().strftime("%H:%M:%S %d/%m/%Y"),
        }

    # ------------------------------------------------------------------
    # Inbound handler: receives admin Telegram messages → AI engine
    # ------------------------------------------------------------------

    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Process incoming Telegram messages from authorized admins."""
        try:
            cfg = self._get_config()
            if not cfg:
                return

            chat_id = str(update.effective_chat.id)
            user = update.effective_user
            sender_name = (user.username or user.first_name or "Admin") if user else "Admin"
            self._record_recent_chat(chat_id, sender_name, getattr(user, "username", "") or "")

            admin_ids = [str(x) for x in (cfg.admin_chat_ids or [])]

            # Fail-closed: danh sách trống nghĩa là CHƯA ai được phép (trước đây
            # là ai cũng được — và lệnh chạy với quyền admin). Chat vẫn được ghi
            # lại ở trên để admin chọn chat_id trong giao diện cấu hình.
            if chat_id not in admin_ids:
                logger.info("[TelegramGateway] Bỏ qua tin từ chat chưa được phép: %s", chat_id)
                return

            text = (update.message.text or "").strip()
            if not text:
                return

            source_device = f"telegram:{chat_id}:{sender_name}"
            logger.info("[TelegramGateway] Inbound query from @%s (chat=%s): %s", sender_name, chat_id, text[:120])

            # Send typing action
            await context.bot.send_chat_action(chat_id=chat_id, action="typing")

            # Get conversation history for this chat
            history = list(self._chat_histories.get(chat_id, []))

            # Forward to LLM engine with conversation history
            from mateai.application.operations.topology_events import emit
            trace_id = f"tg-{int(time.time() * 1000) % 10**9}"
            t0 = time.perf_counter()
            emit("turn", stage="start", trace_id=trace_id, channel="telegram", source="telegram",
                 target="voice", status="running", detail=f"@{sender_name}: {text}")
            try:
                from mateai.application.agent.llm_engine import llm_engine
                result: Dict[str, Any] = await llm_engine.ask_async(
                    query=text,
                    source_device=source_device,
                    history=history,
                )
                reply_text = result.get("reply") or "Em đã xử lý lệnh."
                emit("turn", stage="end", trace_id=trace_id, channel="telegram", source="voice",
                     target="telegram", node="voice", ms=(time.perf_counter() - t0) * 1000,
                     detail=f"trả lời {len(reply_text)} ký tự" + (
                         f" · {(result.get('route_info') or {}).get('model')}" if (result.get("route_info") or {}).get("model") else ""))
                route_info = result.get("route_info", {})
                model_used = route_info.get("model", "")

                # Update conversation history
                if chat_id not in self._chat_histories:
                    self._chat_histories[chat_id] = []
                self._chat_histories[chat_id].append({"role": "user", "content": text})
                self._chat_histories[chat_id].append({"role": "assistant", "content": reply_text})
                if len(self._chat_histories[chat_id]) > 20:
                    self._chat_histories[chat_id] = self._chat_histories[chat_id][-20:]

                # Format reply with source attribution
                footer = f"\n\n─── via {model_used}" if model_used else ""
                final_reply = f"{reply_text}{footer}"

                # Telegram max message length: 4096 chars
                if len(final_reply) > 4000:
                    final_reply = final_reply[:3997] + "..."

                try:
                    await update.message.reply_text(final_reply, parse_mode="Markdown")
                except Exception as md_err:
                    logger.debug("[TelegramGateway] Markdown parse error (%s), fallback to plain text", md_err)
                    await update.message.reply_text(final_reply)

            except Exception as llm_exc:
                emit("turn", stage="end", trace_id=trace_id, channel="telegram", source="voice",
                     target="telegram", node="voice", status="error",
                     ms=(time.perf_counter() - t0) * 1000, detail=type(llm_exc).__name__)
                logger.error("[TelegramGateway] LLM processing error: %s", llm_exc)
                await update.message.reply_text(
                    f"⚠️ Hệ thống gặp lỗi khi xử lý lệnh: {llm_exc}"
                )

        except Exception as exc:
            logger.error("[TelegramGateway] Unexpected error in _handle_message: %s", exc)

    # ── HITL 1-Tap (Phase 58 BƯỚC 3) ───────────────────────────────────────
    @staticmethod
    def build_hitl_keyboard(approval_id: str) -> Optional[Any]:
        """
        Tạo bàn phím nút bấm cho một yêu cầu phê duyệt.

        Trả về `InlineKeyboardMarkup`, hoặc `None` nếu thư viện Telegram chưa
        cài — để phía gọi quyết định chuyển sang gửi bản chữ.

        Vì sao cần nút bấm
        ------------------
        Trước đây `request_approval()` bảo CEO gõ `/approve HITL-XXXXXXXX`. Ở
        điện thoại, mỗi lần duyệt là: mở app → gõ 9 ký tự lệnh → gõ 14 ký tự
        mã → gửi. Khoảng 20-30 giây mỗi lần, và gõ sai 1 ký tự là phải làm
        lại. Đây chính là lý do các hệ thống HITL "có nhưng không ai dùng".

        Nút bấm rút ngắn việc đó xuống một cú chạm, đồng thời loại bỏ nguyên
        nhân gõ sai (không có ký tự nào phải gõ).
        """
        if not _TELEGRAM_AVAILABLE:
            return None

        # Cắt mã cho vừa 64 byte để không bị Telegram từ chối cả tin nhắn.
        aid = (approval_id or "")[: HITL_CB_DATA_MAX - len(HITL_CB_APPROVE)]
        if not aid:
            return None

        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ DUYỆT",
                        callback_data=f"{HITL_CB_APPROVE}{aid}",
                    ),
                    InlineKeyboardButton(
                        text="⛔ TỪ CHỐI",
                        callback_data=f"{HITL_CB_REJECT}{aid}",
                    ),
                ],
            ]
        )

    def send_hitl_request(
        self,
        approval_id: str,
        action_name: str,
        description: str,
        risk_level: int,
        params: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Gửi yêu cầu phê duyệt kèm nút bấm 1-Tap.

        Dùng đúng pattern fire-and-forget của `send_incident_alert`: chạy nền
        bằng thread, không chặn người gọi. Lý do — hàm này được
        `request_approval()` gọi giữa lúc HTTP request đang xử lý; chặn chờ
        mạng ở đó sẽ làm API `/skills/execute` ngoằng ngoặt mỗi lần có tác vụ
        rủi ro cao.

        Trả `True` chỉ khi tin nhắn **thực sự có nút bấm** để người gọi biết có
        thể hướng dẫn "bấm nút" hay phải dùng lệnh chữ. Trả `False` khi gửi
        lỗi — tuyệt đối không coi là gửi thành công, vì báo CEO "đã gửi thông
        báo" khi thực ra không có gì tới là loại báo cáo thành công giả mà
        Phase 58 đang cấm.
        """
        import json

        if not _TELEGRAM_AVAILABLE:
            logger.warning(
                "[TelegramGateway] HITL %s: thư viện Telegram chưa cài, không gửi được nút bấm.", approval_id
            )
            return False

        cfg = self._outbound_config()
        if cfg is None:
            logger.debug("[TelegramGateway] HITL %s: Telegram đang tắt hoặc chưa có bot_token hợp lệ.", approval_id)
            return False

        chat_id = cfg.incident_group_id or (str(cfg.admin_chat_ids[0]) if cfg.admin_chat_ids else "")
        if not chat_id:
            logger.warning("[TelegramGateway] HITL %s: chưa cấu hình chat_id đích.", approval_id)
            return False

        keyboard = self.build_hitl_keyboard(approval_id)
        has_buttons = keyboard is not None

        try:
            detail = ""
            if params:
                detail = f"\n• Tham số: `{json.dumps(params, ensure_ascii=False)[:200]}`"

            # `description` và JSON tham số là dữ liệu do người dùng / tác vụ
            # sinh ra, KHÔNG phải mẫu do ta soạn. Chèn thô vào tin nhắn
            # Markdown là mời Telegram trả 400 cho CẢ tin nhắn:
            #
            #   Nội dung: Ghi chi cho khách record_income hôm nay
            #             _________________________ đây là lý do vỡ
            # Telegram coi `_` là mở italic rồi không thấy đóng, nên
            # "can't parse entities: Can't find end of the entity" và
            # YÊU CẦU DUYỆT KHÔNG BAO GIỜ TỚI ĐƯỢC CEO — trong khi
            # `request_approval()` vẫn trả `telegram_buttons=True` và
            # hệ thống vẫn log "đã tạo yêu cầu phê duyệt". Đây đúng là
            # báo cáo thành công giả mà Phase 58 cấm.
            #
            # Thực tế mọi tên tác vụ thật đều chứa `_` (record_income,
            # record_expense, check_aws_cost, delete_employee...), nên
            # lỗi này không phải hiếm gặp mà là trường hợp thường.
            safe_description = escape_markdown(str(description or ""), version=1)

            body = (
                "🛡️ *YÊU CẦU PHÊ DUYỆT C.E.O*\n"
                f"Mã: `{approval_id}`\n"
                f"Cấp độ rủi ro: 🔴 Level {risk_level}/5\n"
                f"Tác vụ: `{action_name}`\n"
                f"Nội dung: {safe_description}{detail}\n\n"
            )
            if has_buttons:
                body += "👉 *Bấm nút bên dưới* để duyệt hoặc từ chối."
            else:
                body += f"👉 Duyệt: `/approve {approval_id}` · Từ chối: `/reject {approval_id}`"

            if len(body) > _HITL_TRUNCATE_AT:
                body = body[:_HITL_TRUNCATE_AT]
                # Cắt ngay sau một dấu `\` của escape sẽ để lại dấu gạch
                # chéo trần cuối tin, Telegram hiểu là mở entity rồi hỏng.
                if body.endswith("\\"):
                    body = body[:-1]
                body += "\n… (chi tiết bị cắt, xin mở Cổng Web)"
        except Exception as exc:
            logger.error("[TelegramGateway] HITL %s: dựng nội dung lỗi: %s", approval_id, exc)
            return False

        def _send_async() -> None:
            try:
                limits = httpx.Limits(max_connections=4, max_keepalive_connections=2)
                with httpx.Client(limits=limits, timeout=15.0) as client:
                    payload: Dict[str, Any] = {
                        "chat_id": chat_id,
                        "text": body,
                        "parse_mode": "Markdown",
                    }
                    if keyboard is not None:
                        payload["reply_markup"] = keyboard.to_dict()
                    url = f"https://api.telegram.org/bot{cfg.bot_token}/sendMessage"
                    resp = client.post(url, json=payload)
                    if resp.status_code != 200:
                        logger.error(
                            "[TelegramGateway] HITL %s: Telegram từ chối (%s): %s",
                            approval_id, resp.status_code, resp.text[:200],
                        )
                    else:
                        logger.info("[TelegramGateway] HITL %s: đã gửi kèm nút bấm 1-Tap.", approval_id)
            except Exception as exc:
                logger.error("[TelegramGateway] HITL %s: lỗi mạng khi gửi: %s", approval_id, exc)

        threading.Thread(target=_send_async, daemon=True, name="hitl-telegram").start()
        return has_buttons

    async def _handle_hitl_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """
        Xử lý thao tác bấm nút Duyệt / Từ chối.

        Nguyên tắc bất biến: **luôn** trả lời callback (`answer`) kể cả khi
        có lỗi. Telegram giữ nút sáng đèn trong ~60 giây; callback không được
        trả lời nghĩa là CEO bấm xong thấy nút vẫn ấp nháy và tưởng hệ thống
        treo, rồi bấm lại — tạo ra thao tác trùng.
        """
        query = update.callback_query
        try:
            await query.answer()  # Dập tắt đồng hồ trên nút ngay lập tức

            data = query.data or ""
            cfg = self._get_config()
            admin_ids = [str(x) for x in ((cfg.admin_chat_ids if cfg else None) or [])]
            chat_id = str(query.message.chat.id) if query.message else ""
            # Fail-closed: thiếu cấu hình / danh sách trống → không ai duyệt được.
            if chat_id not in admin_ids:
                logger.warning(
                    "[TelegramGateway] Chặn callback HITL từ chat không được phép: %s", chat_id
                )
                await query.edit_message_text(
                    "⛔ Bạn không có quyền duyệt tác vụ này."
                )
                return

            if data.startswith(HITL_CB_APPROVE):
                approval_id, ok = data[len(HITL_CB_APPROVE):], True
                verb = "DUYỆT"
            elif data.startswith(HITL_CB_REJECT):
                approval_id, ok = data[len(HITL_CB_REJECT):], False
                verb = "TỪ CHỐI"
            else:
                await query.answer("Nút không hợp lệ", show_alert=True)
                return

            reviewer = "telegram"
            user = query.from_user
            if user:
                reviewer = f"telegram:{user.username or user.id}"

            from mateai.application.security.zero_trust import hitl_manager

            if ok:
                # `approve_async()` là bản duyệt bất đồng bộ — bắt buộc ở
                # đây. Bản đồng bộ `approve()` KHÔNG chạy được executor dạng
                # coroutine (nó sẽ trả về coroutine chưa await, tức tác vụ
                # không chạy) và chỉ báo lỗi; còn bản async thì `await` thật
                # nên skill rủi ro được thực thi ngay khi CEO bấm nút.
                result = await hitl_manager.approve_async(approval_id, approved_by=reviewer)
            else:
                result = hitl_manager.reject(approval_id, rejected_by=reviewer, reason="Từ chối trên Telegram")

            # `approve_async()` trả `executed` + `execution_error`. Báo cáo phải
            # phản ánh đúng: duyệt nhưng không thực thi được là trạng thái RIÊNG,
            # không phải thành công.
            if ok:
                if result.get("executed"):
                    head = "✅ *Đã duyệt và thực thi thành công*"
                    body = result.get("execution_message") or ""
                elif result.get("status") == "approved_not_executed":
                    head = "⚠️ *Đã duyệt nhưng CHƯA thực thi được*"
                    body = result.get("execution_error") or "Không có callback thực thi trong tiến trình này."
                else:
                    head = "⚠️ *Đã ghi nhận duyệt*"
                    body = result.get("message") or ""
            else:
                head = "⛔ *Đã từ chối*"
                body = result.get("message", "")

            approval_id_out = result.get("id", approval_id)
            text = f"{head}\nMã: `{approval_id_out}`"
            if body:
                text += f"\n{body}"

            try:
                # Xoá nút sau khi xử lý: một yêu cầu chỉ được quyết định 1 lần.
                await query.edit_message_text(
                    text=text,
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
                )
            except Exception as md_err:
                logger.debug("[TelegramGateway] Markdown lỗi khi sửa tin nhắn HITL: %s", md_err)
                await query.edit_message_text(text=text)

        except Exception as exc:
            logger.error("[TelegramGateway] Lỗi xử lý nút HITL: %s", exc)
            try:
                await query.answer(f"Lỗi: {str(exc)[:150]}", show_alert=True)
            except Exception:
                pass

    async def _handle_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /start command with user chat ID display for easy configuration."""
        user = update.effective_user
        chat = update.effective_chat
        chat_id = str(chat.id) if chat else ""
        name = (user.first_name if user else "") or (user.username if user else "Admin")
        self._record_recent_chat(chat_id, name, getattr(user, "username", "") or "")
        await update.message.reply_text(
            f"👋 <b>Xin chào {name}!</b>\n\n"
            f"🆔 <b>Chat ID của bạn:</b> <code>{chat_id}</code>\n"
            f"<i>(Hãy sao chép Chat ID này dán vào ô 'ADMIN CHAT IDS' trên Web VN-MateAI)</i>\n\n"
            "🤖 VN-MateAI đang sẵn sàng tiếp nhận câu hỏi & điều phối RPA doanh nghiệp.\n"
            "Ví dụ: 'Kiểm tra CPU máy chủ' hoặc 'Lấy danh sách nhân viên phòng Kế toán'.",
            parse_mode="HTML",
        )

    async def _handle_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /status command."""
        try:
            from mateai.infrastructure.directory.domain_sync import domain_manager
            stats = domain_manager.get_stats()
            await update.message.reply_text(
                f"📊 VN-MateAI System Status\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"👥 Nhân sự (AD): {stats.get('employees_count', 0)} người\n"
                f"💻 Máy tính (AD): {stats.get('computers_count', 0)} thiết bị\n"
                f"🔄 Gateway: Đang hoạt động"
            )
        except Exception as exc:
            await update.message.reply_text(f"❌ Lỗi truy vấn: {exc}")

    # ------------------------------------------------------------------
    # Background runner
    # ------------------------------------------------------------------

    def _run_bot(self, bot_token: str) -> None:
        """Main bot loop running in a background thread."""
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)

            self._app = (
                ApplicationBuilder()
                .token(bot_token)
                .connect_timeout(20.0)
                .read_timeout(30.0)
                .write_timeout(20.0)
                .connection_pool_size(4)   # Limit pool to avoid GIL contention with audio threads
                .build()
            )

            # Register handlers
            self._app.add_handler(CommandHandler("start", self._handle_start))
            self._app.add_handler(CommandHandler("status", self._handle_status))
            self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message))

            # Handler nút bấm HITL 1-Tap (Phase 58 BƯỚC 3).
            #
            # `group=0` để nó được thử TRƯỚC các handler kia: nếu đăng ký sau
            # `MessageHandler(filters.TEXT)`, một lần bấm nút vẫn có thể bị
            # coi là tin nhắn text và chuyển tới LLM. python-telegram-bot
            # dừng ở group đầu tiên có handler phù hợp, nên thứ tự group
            # quyết định hành vi, còn thứ tự `add_handler` trong cùng group thì không.
            self._app.add_handler(
                CallbackQueryHandler(self._handle_hitl_callback, pattern=r"^hitl:"),
                group=0,
            )

            # Custom error handler to log conflicts gracefully
            async def _bot_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
                err = getattr(context, "error", None)
                err_str = str(err)
                if "Conflict" in err_str or "terminated by other getUpdates" in err_str:
                    logger.warning("[TelegramGateway] Polling conflict detected (another session polling). Backing off gracefully...")
                else:
                    logger.error("[TelegramGateway] Bot background error: %s", err)

            self._app.add_error_handler(_bot_error_handler)

            self._ready = True
            logger.info("[TelegramGateway] Bot polling started successfully.")

            self._app.run_polling(
                drop_pending_updates=True,
                close_loop=False,
                stop_signals=[],
            )

        except Exception as exc:
            logger.error("[TelegramGateway] Bot thread crashed: %s", exc)
        finally:
            self._running = False
            self._ready = False

    def start(self) -> bool:
        """
        Start the Telegram bot in a background daemon thread.
        Returns True if started, False if config missing or already running.
        """
        if not _TELEGRAM_AVAILABLE:
            logger.warning("[TelegramGateway] Cannot start: python-telegram-bot not installed.")
            return False

        if self.is_running:
            logger.info("[TelegramGateway] Bot already running.")
            return True

        # Check if telegram is enabled in config
        try:
            from mateai.config.loader import get_config_section
            if not get_config_section("telegram").get("enabled", False):
                logger.info("[TelegramGateway] Skipping startup: Telegram Gateway is disabled in config.")
                return False
        except Exception:
            pass

        cfg = self._get_config()
        if not cfg or not cfg.bot_token:
            logger.info("[TelegramGateway] Skipping startup: bot_token not configured.")
            return False

        self._running = True
        self._thread = threading.Thread(
            target=self._run_bot,
            args=(cfg.bot_token,),
            name="telegram-gateway",
            daemon=True,  # Daemon thread: auto-killed when main process exits
        )
        self._thread.start()
        logger.info("[TelegramGateway] Background daemon thread launched.")
        return True

    def stop(self) -> None:
        """Gracefully stop the Telegram bot."""
        self._running = False
        self._ready = False
        if self._app and self._loop and self._loop.is_running():
            try:
                self._loop.call_soon_threadsafe(self._app.stop_running)
            except Exception as exc:
                logger.warning("[TelegramGateway] Error during stop_running: %s", exc)
        if self._thread and self._thread.is_alive():
            try:
                self._thread.join(timeout=2.5)
            except Exception:
                pass
        self._thread = None
        self._app = None
        logger.info("[TelegramGateway] Bot stopped.")

    @property
    def is_running(self) -> bool:
        """Return True if bot thread is alive and ready."""
        return self._running and (self._thread is not None) and self._thread.is_alive()

    # ------------------------------------------------------------------
    # Outbound alerting & Verification API
    # ------------------------------------------------------------------

    def get_recent_chats(self, bot_token: Optional[str] = None) -> List[Dict[str, Any]]:
        """Retrieve recent chats either from runtime memory or directly from Telegram getUpdates API."""
        cfg = self._get_config()
        if bot_token and bot_token.strip() == "••••••••":
            bot_token = None
        token = (bot_token or "").strip() or (cfg.bot_token if cfg else "")
        results = dict(self._recent_chats)

        # If bot is already polling, do NOT call getUpdates over HTTP to avoid 409 Conflict
        if token and not self.is_running:
            try:
                with httpx.Client(timeout=15.0) as client:
                    resp = client.get(f"https://api.telegram.org/bot{token}/getUpdates?limit=25")
                    if resp.status_code == 200:
                        updates = resp.json().get("result", [])
                        from datetime import datetime as _dt
                        for item in updates:
                            msg = (
                                item.get("message")
                                or item.get("edited_message")
                                or item.get("channel_post")
                                or item.get("my_chat_member")
                            )
                            if msg and "chat" in msg:
                                chat = msg["chat"]
                                cid = str(chat.get("id"))
                                c_type = chat.get("type", "private")
                                title = chat.get("title") or chat.get("first_name") or chat.get("username") or "Unknown"
                                uname = chat.get("username", "")
                                results[cid] = {
                                    "chat_id": cid,
                                    "name": title,
                                    "username": f"@{uname}" if uname and not uname.startswith("@") else uname,
                                    "type": c_type,
                                    "updated_at": _dt.now().strftime("%H:%M:%S %d/%m/%Y"),
                                }
                                self._recent_chats[cid] = results[cid]
            except Exception as exc:
                logger.debug("[TelegramGateway] get_recent_chats API call error: %s", exc)

        return list(results.values())

    def test_connection(
        self,
        bot_token: Optional[str] = None,
        admin_chat_ids: Optional[List[Any]] = None,
        incident_group_id: Optional[str] = None,
        target_chat_id: Optional[str] = None,
        custom_message: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Verify Telegram bot credentials and send a test message.
        Synchronous, returning detailed status and error messages.
        """
        cfg = self._get_config()
        if bot_token and bot_token.strip() == "••••••••":
            bot_token = None
        token = (bot_token or "").strip() or (cfg.bot_token if cfg else "")
        if not token:
            return {
                "status": "warning",
                "message": "Chưa có Bot Token. Vui lòng nhập Bot Token từ @BotFather vào ô cấu hình."
            }

        # 1. Verify Bot Token via getMe
        try:
            with httpx.Client(timeout=20.0) as client:
                me_resp = client.get(f"https://api.telegram.org/bot{token}/getMe")
                if me_resp.status_code == 401:
                    return {
                        "status": "error",
                        "message": "Bot Token không hợp lệ hoặc đã bị vô hiệu hóa bởi @BotFather. Vui lòng kiểm tra lại token."
                    }
                elif me_resp.status_code != 200:
                    return {
                        "status": "error",
                        "message": f"Máy chủ Telegram trả về mã lỗi HTTP {me_resp.status_code}: {me_resp.text[:120]}"
                    }
                bot_data = me_resp.json().get("result", {})
                bot_name = bot_data.get("first_name", "Bot")
                bot_username = bot_data.get("username", "")

                # 2. Determine target chat IDs
                targets = []
                if target_chat_id:
                    targets.append(str(target_chat_id).strip())
                elif incident_group_id and str(incident_group_id).strip():
                    targets.append(str(incident_group_id).strip())
                elif admin_chat_ids:
                    targets.extend([str(cid).strip() for cid in admin_chat_ids if str(cid).strip()])
                elif cfg:
                    if cfg.incident_group_id and str(cfg.incident_group_id).strip():
                        targets.append(str(cfg.incident_group_id).strip())
                    elif cfg.admin_chat_ids:
                        targets.extend([str(cid).strip() for cid in cfg.admin_chat_ids if str(cid).strip()])

                # If still no target, try auto-detecting from recent chats
                if not targets:
                    recent = self.get_recent_chats(bot_token=token)
                    if recent:
                        targets.append(recent[0]["chat_id"])

                if not targets:
                    return {
                        "status": "warning",
                        "bot_username": bot_username,
                        "bot_name": bot_name,
                        "message": (
                            f"✅ Bot Token hợp lệ (Bot: @{bot_username})!\n"
                            f"⚠️ Tuy nhiên chưa có Admin Chat ID hoặc Incident Group ID để nhận tin nhắn.\n"
                            f"👉 Hướng dẫn: Mở Telegram, tìm bot @{bot_username}, bấm 'Start' hoặc gửi 1 tin nhắn, "
                            f"sau đó bấm nút 'Dò Chat ID Gần Nhất' hoặc nhập Chat ID vào ô rồi gửi lại."
                        )
                    }

                # 3. Send test message to targets
                from datetime import datetime as _dt
                sent_targets = []
                errors = []
                for cid in targets:
                    text_to_send = custom_message or (
                        f"🔔 <b>VN-MateAI Test Alert</b>\n\n"
                        f"✅ Kết nối Telegram thành công!\n"
                        f"🤖 Bot: @{bot_username} ({bot_name})\n"
                        f"🎯 Nhận tại: <code>{cid}</code>\n"
                        f"🕐 Thời gian: {_dt.now().strftime('%d/%m/%Y %H:%M:%S')}"
                    )
                    send_resp = client.post(
                        f"https://api.telegram.org/bot{token}/sendMessage",
                        json={
                            "chat_id": cid,
                            "text": text_to_send,
                            "parse_mode": "HTML",
                        },
                        timeout=20.0
                    )
                    if send_resp.status_code == 200:
                        sent_targets.append(cid)
                    else:
                        err_json = send_resp.json() if send_resp.headers.get("content-type", "").startswith("application/json") else {}
                        desc = err_json.get("description", send_resp.text[:120])
                        errors.append(f"Chat ID {cid}: {desc}")

                if sent_targets:
                    msg = f"✅ Đã gửi tin nhắn test thành công tới: {', '.join(sent_targets)} qua bot @{bot_username}."
                    if errors:
                        msg += f" (Một số chat thất bại: {'; '.join(errors)})"
                    return {
                        "status": "success",
                        "bot_username": bot_username,
                        "bot_name": bot_name,
                        "sent_to": sent_targets,
                        "message": msg
                    }
                else:
                    return {
                        "status": "error",
                        "bot_username": bot_username,
                        "message": f"Gửi tin thất bại: {'; '.join(errors)}. Gợi ý: Hãy mở bot @{bot_username} trên Telegram và bấm 'Start' trước."
                    }

        except httpx.ConnectTimeout:
            return {
                "status": "error",
                "message": "Kết nối tới Telegram API bị quá hạn (Connect Timeout). Vui lòng kiểm tra lại kết nối mạng Internet."
            }
        except Exception as exc:
            return {
                "status": "error",
                "message": f"Lỗi kiểm thử kết nối Telegram: {exc}"
            }

    def send_incident_alert(self, message: str, target: str = "incident_group") -> bool:
        """
        Push an alert message to the Telegram incident group or a specific admin chat.

        Args:
            message: Alert text to send.
            target: "incident_group" (default) or a specific admin chat_id string.

        Returns:
            True if message was dispatched, False on failure.
        """
        from mateai.application.operations.topology_events import emit
        first_line = message.splitlines()[0] if message else ""

        def _event(status: str, why: str = "") -> None:
            # Trang giám sát: kết quả THẬT của lần gửi (đã tới Telegram / bỏ qua vì sao / lỗi).
            emit("alert", stage="alert_out", node="telegram", status=status,
                 detail=f"{why}: {first_line}" if why else first_line)

        if not _TELEGRAM_AVAILABLE:
            logger.warning("[TelegramGateway] Alert skipped: library not installed.")
            _event("cancelled", "bỏ qua — chưa cài thư viện Telegram")
            return False

        cfg = self._outbound_config()
        if cfg is None:
            logger.debug("[TelegramGateway] Alert skipped: Telegram disabled or bot_token invalid.")
            _event("cancelled", "bỏ qua — gửi ra Telegram đang tắt / chưa cấu hình")
            return False

        if target == "incident_group":
            chat_id = cfg.incident_group_id or (str(cfg.admin_chat_ids[0]) if cfg.admin_chat_ids else "")
        else:
            chat_id = str(target or "").strip()
            # Chỉ gửi tới chat NỘI BỘ đã cấu hình (§91, §148). Trước đây `target` là chat_id
            # tuỳ ý — tool `send_telegram_message` (rủi ro 2, tự chạy) có thể bị dữ liệu độc
            # dẫn dắt gửi thông tin nội bộ tới chat của người ngoài.
            internal = {str(c).strip() for c in (cfg.admin_chat_ids or [])} | {str(cfg.incident_group_id or "").strip()}
            if chat_id not in internal - {""}:
                logger.warning("[TelegramGateway] Từ chối gửi tới chat ngoài danh sách nội bộ: %s", chat_id)
                _event("cancelled", "từ chối — chat không thuộc danh sách nội bộ")
                return False

        if not chat_id:
            logger.warning("[TelegramGateway] Alert skipped: no target chat_id configured.")
            _event("cancelled", "bỏ qua — chưa có chat nhận cảnh báo")
            return False

        # Run in a fire-and-forget thread to avoid blocking callers
        def _send_async() -> None:
            try:
                limits = httpx.Limits(
                    max_connections=4,
                    max_keepalive_connections=2,
                )
                with httpx.Client(limits=limits, timeout=15.0) as client:
                    url = f"https://api.telegram.org/bot{cfg.bot_token}/sendMessage"
                    payload = {
                        "chat_id": chat_id,
                        "text": message[:4096],
                        "parse_mode": "HTML",
                    }
                    resp = client.post(url, json=payload)
                    if resp.status_code != 200:
                        _event("error", f"Telegram từ chối (HTTP {resp.status_code})")
                        logger.warning(
                            "[TelegramGateway] sendMessage failed: HTTP %s — %s",
                            resp.status_code,
                            resp.text[:200],
                        )
                    else:
                        # Ghi ở đây, SAU khi Telegram đã trả 200. Log "đã gửi"
                        # ở trước khi gửi là báo cáo thành công giả: thread
                        # này có thể hỏng mạng, token sai, hoặc bị Telegram từ
                        # chối vì HTML sai — vẫn in ra "dispatched" như thể đã
                        # tới nơi.
                        _event("ok", "đã gửi")
                        logger.info(
                            "[TelegramGateway] Incident alert confirmed by Telegram (chat_id=%s)",
                            chat_id,
                        )
            except Exception as exc:
                _event("error", f"lỗi mạng ({type(exc).__name__})")
                logger.error("[TelegramGateway] send_incident_alert error (network fault): %s", exc)

        t = threading.Thread(target=_send_async, daemon=True, name="telegram-alert")
        t.start()
        logger.debug(
            "[TelegramGateway] Incident alert queued (fire-and-forget) to chat_id=%s", chat_id
        )
        return True

    def send_to_all_admins(self, message: str) -> bool:
        """Broadcast a message to all configured admin chat IDs."""
        cfg = self._get_config()
        if not cfg or not cfg.admin_chat_ids:
            return False

        dispatched = False
        for admin_id in cfg.admin_chat_ids:
            ok = self.send_incident_alert(message, target=str(admin_id))
            dispatched = dispatched or ok

        return dispatched


# Module-level singleton — import from anywhere with:
#   from mateai.interfaces.telegram.telegram_gateway import telegram_gateway
telegram_gateway = TelegramBotService()
