# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/email_gateway.py
=====================
Phase 56: Omnichannel Email Gateway for VN-MateAI Enterprise OS.
Tự động tiếp nhận email khách hàng qua IMAP, phân loại mức độ khẩn cấp (P1-P4),
tạo Ticket tự động vào bảng tasks của hệ thống ERP, và tự động phản hồi (Auto-reply) qua SMTP.
"""

from __future__ import annotations

import email
import imaplib
import logging
import smtplib
import threading
import time
from datetime import datetime
from email.header import decode_header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional

from mateai.infrastructure.database.erp_database import erp_db
from core.plugin_manager import export_skill

logger = logging.getLogger(__name__)


def _domain_of(address: str) -> str:
    addr = str(address or "").strip().lower()
    if "<" in addr and ">" in addr:
        addr = addr[addr.rfind("<") + 1:addr.rfind(">")]
    return addr.rsplit("@", 1)[-1].strip() if "@" in addr else ""


def auto_reply_allowed(sender: str) -> "tuple[bool, str]":
    """Chính sách giao tiếp ra ngoài của email gateway (prompt §148): mặc định KHÔNG gửi."""
    try:
        from mateai.config.loader import settings
        auto = settings.autonomy
        if auto.kill_switch:
            return False, "kill_switch"
        if not auto.email_auto_reply:
            return False, "auto_reply_off"
        domain = _domain_of(sender)
        allowed = {str(d).strip().lower().lstrip("@") for d in (auto.email_auto_reply_domains or []) if str(d).strip()}
        if not domain or domain not in allowed:
            return False, "domain_not_allowed"
        return True, "allowed"
    except Exception:  # noqa: BLE001 — lỗi đọc chính sách: không gửi
        return False, "policy_error"


class EmailGateway:
    """Quản lý cổng tiếp nhận và phản hồi email tự động cho doanh nghiệp."""

    def __init__(self) -> None:
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._history: List[Dict[str, Any]] = []
        self._lock = threading.Lock()

        # Cấu hình mặc định (có thể override từ config.json)
        self.imap_host = "imap.gmail.com"
        self.imap_port = 993
        self.smtp_host = "smtp.gmail.com"
        self.smtp_port = 587
        self.username = ""
        self.password = ""
        self.enabled = False
        self.poll_interval = 60.0

    def configure(
        self,
        username: str,
        password: str,
        imap_host: str = "imap.gmail.com",
        smtp_host: str = "smtp.gmail.com",
        enabled: bool = True,
    ) -> None:
        """Cập nhật thông tin cấu hình hòm thư doanh nghiệp."""
        self.username = username
        self.password = password
        self.imap_host = imap_host
        self.smtp_host = smtp_host
        self.enabled = enabled

    def start(self) -> None:
        """Khởi động luồng kiểm tra hòm thư ngầm."""
        if self._running or not self.enabled or not self.username:
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, name="email-gateway-loop", daemon=True)
        self._thread.start()
        logger.info("[EmailGateway] Đã khởi động luồng kiểm tra hộp thư tự động.")

    def stop(self) -> None:
        """Dừng kiểm tra hộp thư."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
        logger.info("[EmailGateway] Đã dừng Email Gateway.")

    def _poll_loop(self) -> None:
        """Vòng lặp đọc email qua IMAP."""
        while self._running:
            try:
                self.check_unread_emails()
            except Exception as exc:
                logger.error("[EmailGateway] Lỗi kiểm tra email: %s", exc)
            time.sleep(self.poll_interval)

    def check_unread_emails(self) -> List[Dict[str, Any]]:
        """Kết nối IMAP, đọc các thư chưa đọc và tự động xử lý."""
        if not self.username or not self.password:
            return []

        processed_tickets: List[Dict[str, Any]] = []
        try:
            mail = imaplib.IMAP4_SSL(self.imap_host, self.imap_port)
            mail.login(self.username, self.password)
            mail.select("inbox")

            status, messages = mail.search(None, "UNSEEN")
            if status != "OK" or not messages[0]:
                mail.logout()
                return []

            for num in messages[0].split():
                status, data = mail.fetch(num, "(RFC822)")
                if status != "OK" or not data:
                    continue

                raw_email = data[0][1]
                msg = email.message_from_bytes(raw_email)

                # Decode subject
                subject, encoding = decode_header(msg.get("Subject", "Yêu cầu từ khách hàng"))[0]
                if isinstance(subject, bytes):
                    subject = subject.decode(encoding or "utf-8", errors="ignore")

                sender = msg.get("From", "khachhang@doanhnghiep.com")

                # Extract body
                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            body = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                            break
                else:
                    body = msg.get_payload(decode=True).decode("utf-8", errors="ignore")

                ticket = self.process_incoming_email(
                    sender=sender,
                    subject=subject,
                    content=body,
                )
                processed_tickets.append(ticket)

            mail.logout()
        except Exception as exc:
            logger.error("[EmailGateway] Lỗi IMAP: %s", exc)

        return processed_tickets

    def process_incoming_email(
        self,
        sender: str,
        subject: str,
        content: str,
    ) -> Dict[str, Any]:
        """Phân tích nội dung email, tạo Ticket vào bảng tasks, và gửi auto-reply."""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        text_lower = f"{subject} {content}".lower()

        # 1. Phân loại mức độ khẩn cấp (P1 - P4)
        priority = "P3-Normal"
        if any(w in text_lower for w in ("khẩn cấp", "sập", "cháy", "down", "mất kết nối toàn bộ", "nguy hiểm", "critical", "khẩn")):
            priority = "P1-Critical"
        elif any(w in text_lower for w in ("lỗi", "chậm", "không đăng nhập được", "lỗi thanh toán", "bug", "gấp")):
            priority = "P2-High"
        elif any(w in text_lower for w in ("hỏi", "tư vấn", "thông tin", "báo giá", "tài liệu")):
            priority = "P4-Low"

        # 2. Xác định phòng ban phụ trách
        target_dept = 1  # Mặc định phòng 1 (IT / Kỹ thuật)
        assignee_id = 1
        # Truy vấn nằm ở tầng dữ liệu (ERPDatabase), không chạy SQL ở tầng giao diện.
        if any(w in text_lower for w in ("thanh toán", "hóa đơn", "tiền", "hợp đồng", "chi phí")):
            found = erp_db.find_department_id_by_name(["tài chính", "kế toán"])
        else:
            found = erp_db.find_department_id_by_name(["kỹ thuật", "it"])
        if found is not None:
            target_dept = found
        emp = erp_db.first_employee_id_in_department(target_dept)
        if emp is not None:
            assignee_id = emp

        # 3. Tạo Task Ticket vào ERP
        clean_subj = subject.strip() or "Yêu cầu hỗ trợ khách hàng"
        ticket_title = f"[Email Ticket - {priority}] {clean_subj}"
        resolution_notes = (
            f"📩 Người gửi: {sender}\n"
            f"⏱️ Tiếp nhận: {now_str}\n"
            f"⚡ Độ ưu tiên: {priority}\n"
            f"📝 Nội dung tóm tắt: {content[:300]}"
        )

        task_res = erp_db.create_erp_task(
            dept_id=target_dept,
            assignee_id=assignee_id,
            title=ticket_title,
            due_date=datetime.now().strftime("%Y-%m-%d 17:30:00"),
            created_by_ai=1,
            resolution_notes=resolution_notes,
        )

        ticket_id = task_res.get("id", "TCK" + str(int(time.time()) % 100000))

        # 4. Soạn thảo phản hồi tự động
        reply_body = (
            f"Kính gửi Quý khách ({sender}),\n\n"
            f"Hệ thống VN-MateAI Enterprise OS đã tiếp nhận yêu cầu của Quý khách với thông tin sau:\n"
            f"• Mã yêu cầu (Ticket ID): #{ticket_id}\n"
            f"• Tiêu đề: {clean_subj}\n"
            f"• Mức độ ưu tiên: {priority}\n"
            f"• Trạng thái: Đã chuyển đến bộ phận chuyên trách xử lý.\n\n"
            f"Chuyên viên của chúng tôi sẽ liên hệ lại trong thời gian sớm nhất.\n"
            f"Trân trọng cảm ơn Quý khách!\n\n"
            f"--\n"
            f"Hệ điều hành Tự chủ Doanh nghiệp VN-MateAI\n"
            f"Hotline / Portal: https://mateai.enterprise.local"
        )

        # 5. Tự trả lời RA NGOÀI chỉ khi chính sách cho phép (prompt §148): bật
        # `autonomy.email_auto_reply` VÀ miền người gửi nằm trong danh sách cho phép.
        # Trước đây trả lời mọi người gửi — xác nhận hộp thư đang hoạt động cho cả spam.
        allowed, why = auto_reply_allowed(sender)
        smtp_sent = False
        if allowed:
            smtp_sent = self._send_smtp_reply(to_email=sender, subject=f"Re: [Ticket #{ticket_id}] {clean_subj}", body=reply_body)
        try:
            from mateai.application.security.safety_guard import security_engine
            security_engine.log_audit("VN-MATEAI-EMAIL", "email_auto_reply", "EXTERNAL",
                                      "SUCCESS" if smtp_sent else "REJECTED",
                                      {"agent_id": "VN-MATEAI-EMAIL", "ticket_id": ticket_id,
                                       "recipient_domain": _domain_of(sender), "sent": smtp_sent, "policy": why})
        except Exception:  # noqa: BLE001
            pass

        ticket_data = {
            "ticket_id": ticket_id,
            "sender": sender,
            "subject": clean_subj,
            "priority": priority,
            "received_at": now_str,
            "assigned_dept_id": target_dept,
            "task_id": task_res.get("id"),
            "auto_reply_sent": smtp_sent,
            "reply_content": reply_body,
        }

        from mateai.application.operations.topology_events import emit
        emit("email", stage="ticket", source="email", target="db",
             status="error" if priority == "P1-Critical" else "ok",
             detail=f"#{ticket_id} {priority} · {clean_subj}")

        with self._lock:
            self._history.insert(0, ticket_data)
            if len(self._history) > 100:
                self._history.pop()

        # P1 / P2: báo qua khâu cảnh báo chung (mọi kênh đã kết nối, không chỉ Telegram)
        if priority in ("P1-Critical", "P2-High"):
            from mateai.application.operations import alert_dispatcher
            alert_dispatcher.notify(
                f"Email khẩn từ khách hàng ({priority}): {clean_subj}",
                f"Người gửi: {sender}\nTicket: #{ticket_id}\nĐã tự động tạo task và điều phối nhân sự xử lý.",
                severity="critical" if priority == "P1-Critical" else "warning",
                category=f"email:{ticket_id}", source="Email Gateway")

        logger.info("[EmailGateway] Đã tiếp nhận và tạo Ticket #%s từ %s (Ưu tiên: %s).", ticket_id, sender, priority)
        return ticket_data

    def _send_smtp_reply(self, to_email: str, subject: str, body: str) -> bool:
        """Gửi email phản hồi qua máy chủ SMTP."""
        if not self.username or not self.password:
            logger.debug("[EmailGateway] Bỏ qua SMTP thực: Chưa cấu hình username/password.")
            return False

        try:
            msg = MIMEMultipart()
            msg["From"] = self.username
            msg["To"] = to_email
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "plain", "utf-8"))

            server = smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=10.0)
            server.starttls()
            server.login(self.username, self.password)
            server.sendmail(self.username, [to_email], msg.as_string())
            server.quit()
            logger.info("[EmailGateway] Đã gửi auto-reply thành công tới %s.", to_email)
            return True
        except Exception as exc:
            logger.warning("[EmailGateway] Lỗi gửi SMTP auto-reply: %s", exc)
            return False

    def get_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Lấy danh sách các ticket đã tiếp nhận từ email."""
        with self._lock:
            return list(self._history[:limit])


# Singleton instance
email_gateway = EmailGateway()


# ── AI Skills Export ─────────────────────────────────────────────────────────

@export_skill(
    name="simulate_incoming_customer_email",
    description="Mô phỏng hoặc tiếp nhận email từ khách hàng/đối tác gửi tới hòm thư doanh nghiệp. Tự động phân tích ngữ nghĩa, xếp loại khẩn cấp (P1-P4), tạo Ticket vào bảng tasks và tạo email phản hồi lịch sự.",
    parameters_schema={
        "type": "object",
        "properties": {
            "sender_email": {
                "type": "string",
                "description": "Địa chỉ email của khách hàng gửi tới (ví dụ: 'nguyenvana@techcorp.vn').",
            },
            "subject": {
                "type": "string",
                "description": "Tiêu đề email (ví dụ: 'Máy chủ API không phản hồi từ 10h sáng').",
            },
            "content": {
                "type": "string",
                "description": "Nội dung chi tiết của email báo lỗi hoặc yêu cầu dịch vụ.",
            },
        },
        "required": ["sender_email", "subject", "content"],
    },
)
def simulate_incoming_customer_email(
    sender_email: str,
    subject: str,
    content: str,
) -> Dict[str, Any]:
    """Xử lý email khách hàng đến và tạo Ticket tự động."""
    res = email_gateway.process_incoming_email(
        sender=sender_email,
        subject=subject,
        content=content,
    )
    return {
        "status": "success",
        "message": f"Đã tiếp nhận email từ {sender_email}. Đã tạo Ticket #{res['ticket_id']} (Độ ưu tiên: {res['priority']}).",
        "ticket": res,
    }
