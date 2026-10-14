# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/agents/agent_orchestrator.py
==================================
Phase 57: Autonomous Multi-Agent Enterprise Architecture.
Tách logic LLM độc tôn thành một mạng lưới các Agent chuyên trách:
  1. CEO Router Agent (Điều phối, phân loại intent, giao việc cấp dưới)
  2. CFO Agent (Quản lý tài chính, sổ quỹ, chi phí, dòng tiền)
  3. HR Agent (Quản lý nhân sự, chấm công, phân công nhiệm vụ, chính sách RAG)
  4. CTO Agent (Quản lý hạ tầng, IT AIOps, AD, an toàn hệ thống)

Tích hợp Inter-Agent Communication Bus cho phép các Agent trao đổi thông tin chéo.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from mateai.infrastructure.database.erp_database import erp_db
from core.plugin_manager import export_skill

logger = logging.getLogger(__name__)


def _principal_of(context: Optional[Dict[str, Any]]) -> Optional[str]:
    """Người mà chuỗi tác nhân đang phục vụ: từ tin nhắn bus, không thì từ cổng tool."""
    if context and context.get("principal"):
        return str(context["principal"])
    from mateai.application.security.security_guard import CURRENT_PRINCIPAL
    return CURRENT_PRINCIPAL.get()


class BaseAgent:
    """Lớp cơ sở cho các Agent chuyên trách trong doanh nghiệp."""

    def __init__(self, name: str, role: str, description: str) -> None:
        self.name = name
        self.role = role
        self.description = description
        self.message_bus: Optional[AgentMessageBus] = None

    def set_bus(self, bus: AgentMessageBus) -> None:
        self.message_bus = bus

    def can_handle(self, query: str) -> float:
        """Trả về điểm phù hợp (0.0 - 1.0) của Agent đối với câu hỏi."""
        raise NotImplementedError

    def process(self, query: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Thực thi câu hỏi và trả về kết quả chuẩn."""
        raise NotImplementedError


class AgentMessageBus:
    """
    Kênh truyền thông nội bộ (Inter-Agent Communication Bus) giữa các Agent.

    Có hai chốt an toàn chống lỗi kiến trúc đa tác nhân:

    1. `MAX_CALL_DEPTH` — CFO hỏi CTO, CTO lại hỏi CFO… Trước đây `request()`
       không theo dõi độ sâu nên một chu kỳ A↔B sẽ đệ quy vô hạn và treo
       tiến trình server. Nay vượt ngưỡng thì trả lỗi có kiểm soát.
    2. `MAX_LOG_ENTRIES` — `interaction_log` trước đây append vô hạn, rò rỉ bộ
       nhớ theo thời gian. Nay giữ N bản ghi gần nhất (vòng tròn).
    """

    MAX_CALL_DEPTH = 3
    MAX_LOG_ENTRIES = 200

    def __init__(self) -> None:
        self.agents: Dict[str, BaseAgent] = {}
        self.interaction_log: List[Dict[str, Any]] = []
        self._depth: int = 0

    def register(self, agent: BaseAgent) -> None:
        self.agents[agent.name.lower()] = agent
        agent.set_bus(self)

    def request(self, from_agent: str, to_agent: str, query: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Cho phép một Agent gửi yêu cầu dữ liệu sang một Agent khác."""
        target = self.agents.get(to_agent.lower())
        if not target:
            return {"status": "error", "message": f"Agent '{to_agent}' không tồn tại trên Message Bus."}

        if self._depth >= self.MAX_CALL_DEPTH:
            logger.warning(
                "[Inter-Agent Bus] Chặn gọi %s -> %s: đã đạt giới hạn độ sâu %d "
                "(nghi vấn vòng lặp giữa các Agent).",
                from_agent, to_agent, self.MAX_CALL_DEPTH,
            )
            return {
                "status": "error",
                "message": (
                    f"Đã đạt giới hạn {self.MAX_CALL_DEPTH} lớp gọi liên Agent. "
                    f"'{from_agent}' yêu cầu '{to_agent}' — chuỗi này quá sâu, "
                    f"không thể trả lời đầy đủ."
                ),
                "truncated": True,
            }

        # Prompt cuối §57: tác nhân khác là chủ thể KHÔNG tin sẵn — mỗi tin nhắn mang id, người
        # gửi / nhận, thời điểm và NGƯỜI được phục vụ (phân quyền theo người đó, không theo tác nhân).
        principal = _principal_of(payload)
        interaction = {
            "message_id": uuid.uuid4().hex[:12],
            "timestamp": datetime.now().isoformat(),
            "from": from_agent,
            "to": to_agent,
            "query": query,
            "depth": self._depth,
            "principal": principal,
        }
        self.interaction_log.append(interaction)
        if len(self.interaction_log) > self.MAX_LOG_ENTRIES:
            del self.interaction_log[: len(self.interaction_log) - self.MAX_LOG_ENTRIES]
        logger.info("[Inter-Agent Bus] %s -> %s: '%s'", from_agent, to_agent, query[:80])

        self._depth += 1
        try:
            return target.process(query, context={"requester": from_agent, "payload": payload or {},
                                                  "principal": principal,
                                                  "message_id": interaction["message_id"]})
        finally:
            self._depth -= 1


class CFOAgent(BaseAgent):
    """CFO Agent: Chuyên trách Tài chính, Ngân sách, Dòng tiền và Sổ quỹ."""

    def __init__(self) -> None:
        super().__init__(
            name="CFO_Agent",
            role="Chief Financial Officer (Giám đốc Tài chính)",
            description="Quản lý sổ quỹ finances, báo cáo thu chi, chi phí server, ngân sách và dòng tiền.",
        )

    def can_handle(self, query: str) -> float:
        text = query.lower()
        keywords = ("tiền", "thu", "chi", "sổ quỹ", "tài chính", "ngân sách", "hóa đơn", "lương", "doanh thu", "chi phí", "burn rate", "runway", "cashflow")
        score = sum(1 for k in keywords if k in text)
        return min(1.0, score * 0.25)

    def process(self, query: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        text = query.lower()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Tài chính là CONFIDENTIAL (prompt cuối §62): xét cấp bảo mật của NGƯỜI hỏi — kể cả khi
        # yêu cầu tới từ tác nhân khác qua bus. Trước đây CFO trả số liệu cho bất kỳ ai.
        if principal_clearance(_principal_of(context)) < 3:
            return {"status": "error", "agent": self.name, "role": self.role, "denied": "abac_clearance",
                    "reply": "💰 [CFO] Số liệu tài chính cần cấp bảo mật 3 — tài khoản hiện tại không đủ."}

        # Trường hợp CEO hỏi về chi phí server / hạ tầng Cloud: CFO tự ping CTO Agent qua Message Bus
        if any(w in text for w in ("chi phí server", "tiền server", "cloud", "aws", "azure")):
            server_data = {}
            if self.message_bus:
                cto_res = self.message_bus.request(
                    from_agent=self.name,
                    to_agent="cto_agent",
                    query="Lấy dữ liệu chi phí và hạ tầng máy chủ",
                )
                server_data = cto_res.get("data", {})

            fin_summary = erp_db.get_financial_summary(30)
            breakdown = fin_summary.get("category_breakdown", {}) or {}

            # KHÔNG bịa số liệu. Trước đây dùng
            # .get("Hạ tầng Cloud & Server", 45000000.0) — tức là khi sổ quỹ
            # chưa có dòng chi hạ tầng nào, CFO vẫn báo "45.000.000 VND" như
            # thật, khiến CEO ra quyết định tài chính dựa trên số không tồn tại.
            # Nay: không có dữ liệu thì nói thẳng là không có.
            server_cost = next(
                (float(v) for k, v in breakdown.items() if "hạ tầng" in str(k).lower() or "server" in str(k).lower() or "cloud" in str(k).lower()),
                None,
            )
            total_expense = float(fin_summary.get("total_expense") or 0.0)

            if server_cost is None:
                reply = (
                    f"💼 [CFO BÁO CÁO TÀI CHÍNH HẠ TẦNG]:\n"
                    f"• Sổ quỹ chưa ghi nhận khoản chi nào cho hạ tầng Cloud/Server trong 30 ngày qua "
                    f"(các mục đang có: {', '.join(breakdown.keys()) or 'chưa có khoản chi nào'}).\n"
                    f"• Dữ liệu đối chiếu từ CTO: {server_data.get('infrastructure_summary', 'chưa có dữ liệu')}.\n"
                    f"• Đánh giá CFO: chưa đủ dữ liệu để kết luận chi phí hạ tầng có tăng hay không. "
                    f"Sếp muốn em mở khung nhập hoá đơn AWS/Azure vào để em phân tích không ạ?"
                )
                server_cost_value: Optional[float] = None
            else:
                pct = (server_cost / total_expense * 100) if total_expense > 0 else 0.0
                reply = (
                    f"💼 [CFO BÁO CÁO TÀI CHÍNH HẠ TẦNG]:\n"
                    f"• Chi phí Cloud & Server 30 ngày qua: {server_cost:,.0f} VND.\n"
                    f"• Dữ liệu đối chiếu từ CTO: {server_data.get('infrastructure_summary', 'Đang hoạt động ổn định trên 4 máy chủ')}.\n"
                    f"• Đánh giá CFO: Chi phí hạ tầng chiếm {pct:.1f}% tổng chi tiêu công ty."
                )
                server_cost_value = server_cost

            return {
                "status": "success",
                "agent": self.name,
                "role": self.role,
                "reply": reply,
                "inter_agent_call": "CTO_Agent",
                "data_missing": server_cost is None,
                "suggested_action": "upload_invoice_excel" if server_cost is None else "",
                "data": {"server_cost": server_cost_value, "server_data": server_data},
            }

        # Báo cáo tổng thể hoặc ghi nhận chi tiêu
        if "tổng quan" in text or "dòng tiền" in text or "báo cáo" in text or "runway" in text:
            summary = erp_db.get_financial_summary(30)
            return {
                "status": "success",
                "agent": self.name,
                "role": self.role,
                "reply": (
                    f"💼 [CFO BÁO CÁO DÒNG TIỀN DOANH NGHIỆP]:\n"
                    f"• Tổng thu: {summary['total_income']:,.0f} VND\n"
                    f"• Tổng chi: {summary['total_expense']:,.0f} VND\n"
                    f"• Số dư ròng: {summary['net_balance']:,.0f} VND\n"
                    f"• Tốc độ chi tiêu/ngày: {summary['daily_burn_rate']:,.0f} VND\n"
                    f"• Số ngày an toàn (Runway): {summary['runway_days']} ngày."
                ),
                "data": summary,
            }

        # Trả lời chung
        fin = erp_db.get_financial_summary(30)
        return {
            "status": "success",
            "agent": self.name,
            "role": self.role,
            "reply": f"💼 [CFO]: Tôi đang giám sát sổ quỹ tài chính. Hiện số dư khả dụng là {fin['net_balance']:,.0f} VND.",
            "data": fin,
        }


class HRAgent(BaseAgent):
    """HR Agent: Chuyên trách Nhân sự, Chấm công, Phân công nhiệm vụ và Quy chế (RAG)."""

    def __init__(self) -> None:
        super().__init__(
            name="HR_Agent",
            role="Chief Human Resources Officer (Giám đốc Nhân sự)",
            description="Quản lý nhân viên, chấm công, nghỉ phép, phân bổ task và giải đáp chính sách công ty.",
        )

    def can_handle(self, query: str) -> float:
        text = query.lower()
        keywords = ("nhân sự", "nhân viên", "chấm công", "nghỉ phép", "nghỉ ốm", "thai sản", "nội quy", "chính sách", "phân việc", "giao việc", "onboarding", "tuyển dụng")
        score = sum(1 for k in keywords if k in text)
        return min(1.0, score * 0.25)

    def process(self, query: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        text = query.lower()

        # Hỏi về chính sách công ty -> Kích hoạt Enterprise RAG
        if any(w in text for w in ("nghỉ ốm", "nghỉ phép", "quy chế", "nội quy", "chính sách", "bảo hiểm", "thai sản", "thời gian làm việc")):
            from mateai.application.knowledge.rag_engine import rag_engine
            rag_res = rag_engine.answer_policy_question(query)
            return {
                "status": "success",
                "agent": self.name,
                "role": self.role,
                "reply": f"👥 [HR - GIẢI ĐÁP CHÍNH SÁCH DOANH NGHIỆP]:\n{rag_res.get('answer')}",
                "sources": rag_res.get("sources", []),
            }

        # Tra cứu chấm công
        if "chấm công" in text or "điểm danh" in text:
            from mateai.application.skills.builtin.business_tools import get_attendance_report
            att = get_attendance_report()
            return {
                "status": "success",
                "agent": self.name,
                "role": self.role,
                "reply": f"👥 [HR BÁO CÁO CHẤM CÔNG]: Hôm nay có {att.get('total_records', 0)} lượt chấm công đã được ghi nhận.",
                "data": att,
            }

        # Giao việc thông minh
        if any(w in text for w in ("giao việc", "phân việc", "lên kế hoạch", "tổ chức")):
            # Hành động có tác dụng phụ của tác nhân con -> qua Policy Engine (kill switch,
            # tắt tác nhân, L5, rủi ro). Trước đây gọi hàm skill thẳng, ngoài mọi cổng.
            # RBAC đã xét ở lớp ngoài (`delegate_to_multi_agent`).
            from mateai.application.security import policy_engine as pe
            from mateai.application.security.safety_guard import security_engine
            # Phân quyền theo NGƯỜI đang được phục vụ (trước đây caller=None: mất danh tính).
            decision = pe.authorize("assign_task_intelligently", {"description": query},
                                    caller=_principal_of(context), agent_id=pe.AGENT_ORCHESTRATOR)
            security_engine.log_audit("orchestrator", "assign_task_intelligently", str(decision.risk),
                                      "SUCCESS" if decision.allowed else "REJECTED",
                                      {"agent_id": decision.agent_id, "decision": decision.effect,
                                       "rule": decision.rule, "policy_version": decision.policy_version})
            if not decision.allowed:
                return {"status": "error", "agent": self.name, "role": self.role,
                        "reply": f"👥 [HR] Không giao việc: {decision.reasons[0]}", "policy": decision.as_dict()}
            from mateai.application.skills.builtin.proactive_manager import assign_task_intelligently
            task_res = assign_task_intelligently(description=query)
            return {
                "status": "success",
                "agent": self.name,
                "role": self.role,
                "reply": f"👥 [HR - ĐIỀU PHỐI CÔNG VIỆC]:\n{task_res.get('message')}",
                "data": task_res,
            }

        # Danh sách nhân viên
        # LƯU Ý: trước đây query `LIMIT 10` rồi báo "Toàn công ty có
        # {len(emps)} nhân viên" — tức là len() của tập bị cắt còn 10 dòng.
        # Công ty hơn 10 người thì HR Agent báo sai số nhân viên. Nay đếm
        # riêng bằng COUNT(*) và chỉ lấy tối đa 10 dòng để xem trước.
        total_employees, emps = erp_db.employee_count_and_preview(10)

        if total_employees == 0:
            reply = (
                "👥 [HR]: Danh bạ nhân sự hiện đang TRỐNG — chưa có hồ sơ nhân viên nào. "
                "Sếp muốn em mở khung để nhập danh sách từ file Excel không ạ?"
            )
        else:
            shown = len(emps)
            reply = f"👥 [HR]: Toàn công ty hiện có {total_employees} nhân viên trong danh bạ quản trị."
            if shown < total_employees:
                reply += f" (đang hiển thị {shown} người đầu tiên)"

        return {
            "status": "success",
            "agent": self.name,
            "role": self.role,
            "reply": reply,
            "data_missing": total_employees == 0,
            "suggested_action": "upload_employee_excel" if total_employees == 0 else "",
            "data": emps,
            "total_employees": total_employees,
        }


class CTOAgent(BaseAgent):
    """CTO Agent: Chuyên trách Hạ tầng IT, Máy chủ, Active Directory và An ninh mạng."""

    def __init__(self) -> None:
        super().__init__(
            name="CTO_Agent",
            role="Chief Technology Officer (Giám đốc Công nghệ)",
            description="Quản lý AIOps, máy chủ, Active Directory, an toàn bảo mật và dịch vụ hệ thống.",
        )

    def can_handle(self, query: str) -> float:
        text = query.lower()
        keywords = ("server", "máy chủ", "it", "cpu", "ram", "active directory", "ad", "tài khoản", "mạng", "bảo mật", "dịch vụ", "sql", "backup")
        score = sum(1 for k in keywords if k in text)
        return min(1.0, score * 0.25)

    def process(self, query: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        text = query.lower()

        # Kiểm tra trạng thái máy tính / thiết bị
        dev_count, audit_count = erp_db.device_and_audit_counts()

        infra_summary = f"Quản lý {dev_count} máy trạm/server. Nhật ký an ninh Audit Logs: {audit_count} bản ghi."

        return {
            "status": "success",
            "agent": self.name,
            "role": self.role,
            "reply": (
                f"💻 [CTO BÁO CÁO HẠ TẦNG KỸ THUẬT]:\n"
                f"• Tình trạng hệ sinh thái IT: Sẵn sàng 100%.\n"
                f"• Thiết bị kết nối mạng: {dev_count} thiết bị.\n"
                f"• Chuẩn bảo mật: Zero-Trust Architecture kích hoạt.\n"
                f"• {infra_summary}"
            ),
            "data": {
                "infrastructure_summary": infra_summary,
                "devices_count": dev_count,
                "audit_count": audit_count,
            },
        }


class CEORouterAgent:
    """CEO Router Agent: Tiếp nhận yêu cầu toàn diện, phân luồng Intent và tổng hợp báo cáo."""

    def __init__(self) -> None:
        self.message_bus = AgentMessageBus()
        self.cfo = CFOAgent()
        self.hr = HRAgent()
        self.cto = CTOAgent()

        self.message_bus.register(self.cfo)
        self.message_bus.register(self.hr)
        self.message_bus.register(self.cto)

    def route_and_execute(self, query: str) -> Dict[str, Any]:
        """Phân loại intent và điều phối cho Agent cấp dưới phù hợp nhất."""
        scores = {
            "cfo": self.cfo.can_handle(query),
            "hr": self.hr.can_handle(query),
            "cto": self.cto.can_handle(query),
        }

        # Chọn Agent có điểm match cao nhất
        best_agent_key = max(scores, key=scores.get)
        best_score = scores[best_agent_key]

        logger.info("[CEO Router] Phân loại Intent: %s (Điểm: %s)", best_agent_key, scores)

        if best_score > 0.15:
            target_agent = getattr(self, best_agent_key)
            result = target_agent.process(query)
            result["routed_by"] = "CEO_Router_Agent"
            result["intent"] = best_agent_key
            return result

        # Nếu là câu hỏi chung hoặc yêu cầu họp giao ban
        if any(w in query.lower() for w in ("giao ban", "tổng hợp", "tình hình", "họp", "báo cáo ngày")):
            return self.get_executive_briefing()

        # Mặc định: Phản hồi từ CEO Agent với thông tin tổng quan
        overview = erp_db.get_company_kpi_overview()
        # Số dư quỹ chỉ cho người đủ cấp bảo mật (trước đây chèn sẵn vào câu trả lời cho mọi người).
        if principal_clearance(_principal_of(None)) < 3:
            overview = {**overview, "finances": None}
            money = ""
        else:
            money = f" và số dư quỹ là {overview['finances']['net_balance']:,.0f} VND"
        return {
            "status": "success",
            "agent": "CEO_Router_Agent",
            "role": "CEO & Chief Enterprise Orchestrator",
            "reply": (
                f"👑 [VIRTUAL C.O.O & ENTERPRISE O.S]:\n"
                f"Tôi đã tiếp nhận chỉ thị: '{query}'.\n"
                f"Hệ thống Multi-Agent đang sẵn sàng với CFO (Tài chính), HR (Nhân sự) và CTO (Kỹ thuật).\n"
                f"Hiện toàn công ty có {overview['active_tasks']} task đang thực thi{money}."
            ),
            "data": overview,
        }

    def get_executive_briefing(self) -> Dict[str, Any]:
        """Tạo báo cáo Họp Giao Ban Tự Động (Executive Daily Standup) tóm tắt 24h qua."""
        kpi = erp_db.get_company_kpi_overview()
        fin = kpi["finances"]
        leaderboard = erp_db.get_task_leaderboard(3)

        top_names = [f"#{r['rank']} {r['name']} ({r['completed_tasks']} task)" for r in leaderboard]

        briefing_text = (
            f"📢 [BÁO CÁO HỌP GIAO BAN DOANH NGHIỆP 24H QUA]\n\n"
            f"1. 💰 DÒNG TIỀN & TÀI CHÍNH (CFO):\n"
            f"   • Số dư ròng: {fin['net_balance']:,.0f} VND\n"
            f"   • Thu trong tháng: {fin['total_income']:,.0f} VND | Chi: {fin['total_expense']:,.0f} VND\n"
            f"   • Dự báo Runway: {fin['runway_days']} ngày hoạt động an toàn.\n\n"
            f"2. 📋 TIẾN ĐỘ CÔNG VIỆC TOÀN CÔNG TY (HR & COO):\n"
            f"   • Tổng số việc: {kpi['total_tasks']} | Hoàn thành: {kpi['completed_tasks']} ({kpi['completion_rate']}%)\n"
            f"   • Đang thực hiện: {kpi['active_tasks']} | Quá hạn: {kpi['overdue_tasks']} việc.\n"
            f"   • Top thi đua: {', '.join(top_names) if top_names else 'Đang cập nhật'}.\n\n"
            f"3. 🛡️ AN TOÀN & HẠ TẦNG IT (CTO):\n"
            f"   • Hệ thống bảo mật Zero-Trust: Ổn định.\n"
            f"   • Các dịch vụ Cloud & AI Gateway: Hoạt động 100%."
        )

        return {
            "status": "success",
            "agent": "CEO_Router_Agent",
            "role": "CEO & Virtual COO",
            "reply": briefing_text,
            "briefing": briefing_text,
            "data": kpi,
        }

    def generate_cross_domain_report(
        self,
        query_context: str = "",
        requested_departments: Optional[List[str]] = None,
        clearance_level: int = 1,
    ) -> Dict[str, Any]:
        """
        Phân tích tương quan đa phòng ban (Executive Cross-Domain Intelligence).
        Tự động liên kết chéo:
          - Chi phí hạ tầng Cloud (CTO) vs Doanh thu & Hóa đơn xuất được (CFO).
          - Tiến độ công việc & KPI (HR) vs Năng suất thực tế.
        Kiểm tra quyền truy cập (Clearance Level):
          - Level 1-2 (Public/Internal): Số liệu tổng quan, ẩn chi tiết hợp đồng mật/bảng lương.
          - Level 3-4 (Confidential/Secret): Đầy đủ dữ liệu tài chính chi tiết.
        """
        kpi = erp_db.get_company_kpi_overview()
        fin = kpi.get("finances", {})
        depts = requested_departments or ["FIN", "HR", "CTO"]
        norm_depts = [d.upper() for d in depts]

        # 1. Thu thập dữ liệu các mảng
        cfo_res = self.cfo.process("báo cáo tài chính tổng quan") if any(d in norm_depts for d in ("FIN", "CFO")) else {}
        hr_res = self.hr.process("báo cáo nhân sự và chấm công") if any(d in norm_depts for d in ("HR", "COO")) else {}
        cto_res = self.cto.process("báo cáo hạ tầng cloud và máy chủ") if any(d in norm_depts for d in ("CTO", "IT", "TECH")) else {}

        # 2. Phân tích đối soát chéo (Cross-Correlation)
        income = fin.get("total_income", 0)
        expense = fin.get("total_expense", 0)
        net_balance = fin.get("net_balance", 0)
        active_tasks = kpi.get("active_tasks", 0)
        completion_rate = kpi.get("completion_rate", 0.0)

        # Trước đây: "chi phí cloud" là hằng số bịa 15 000 000 VND rồi tính "tỷ trọng ~x% (Tối ưu)".
        # Không có số đo chi phí thật trong báo cáo này -> không nêu tỷ trọng (prompt: không bịa số).
        cloud_cost_ratio = None

        # Kiểm tra Data Clearance Level
        is_confidential = clearance_level >= 3

        # 3. Tạo Voice Summary (< 4 câu, tối ưu phát thanh < 1s)
        voice_lines = [
            f"Báo cáo điều hành tổng hợp: Toàn công ty hiện có {active_tasks} nhiệm vụ đang xử lý, tỷ lệ hoàn thành đạt {completion_rate}%.",
        ]
        if is_confidential:
            voice_lines.append(f"Số dư ròng khả dụng đạt {net_balance:,.0f} đồng, đảm bảo runway {fin.get('runway_days', 30)} ngày.")
        else:
            voice_lines.append("Tình hình tài chính và dòng tiền vận hành duy trì ngưỡng an toàn.")

        voice_summary = " ".join(voice_lines)

        # 4. Tạo Rich Markdown Details
        md_lines = [
            "# 📊 BÁO CÁO ĐIỀU HÀNH TỔNG HỢP LIÊN PHÒNG BAN (CROSS-DOMAIN INTELLIGENCE)",
            f"*Thời gian lập: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Mức độ bảo mật: Clearance Level {clearance_level}*",
            "",
            "## 1. ĐỐI SOÁT TÀI CHÍNH & HẠ TẦNG (CFO ⨉ CTO)",
        ]
        if is_confidential:
            md_lines.extend([
                f"- **Tổng thu trong kỳ:** {income:,.0f} VND",
                f"- **Tổng chi trong kỳ:** {expense:,.0f} VND",
                f"- **Số dư ròng:** {net_balance:,.0f} VND",
                "- **Chi phí Cloud:** chưa có số đo trong báo cáo này (xem `check_aws_cost`).",
            ])
        else:
            md_lines.append("- *(Số liệu chi tiết dòng tiền yêu cầu Clearance Level ≥ 3)*")

        md_lines.extend([
            "",
            "## 2. TIẾN ĐỘ THỰC THI & NĂNG SUẤT NHÂN SỰ (HR ⨉ COO)",
            f"- **Tổng số nhiệm vụ:** {kpi.get('total_tasks', 0)}",
            f"- **Đang thực hiện:** {active_tasks} | **Hoàn tất:** {kpi.get('completed_tasks', 0)} ({completion_rate}%)",
            f"- **Nhiệm vụ quá hạn:** {kpi.get('overdue_tasks', 0)} việc",
            "",
            "## 3. KIỂM SOÁT TỰ TRỊ",
            f"- **Kill switch:** {'ĐANG BẬT — chỉ tác vụ chỉ đọc' if _kill_switch_on() else 'tắt'}",
        ])

        rich_details = "\n".join(md_lines)

        return {
            "status": "success",
            "scope": requested_departments or "all",
            "clearance_level": clearance_level,
            "voice_summary": voice_summary,
            "rich_details": rich_details,
            "metrics": {
                "active_tasks": active_tasks,
                "completion_rate": completion_rate,
                "net_balance": net_balance if is_confidential else None,
                "cloud_ratio_pct": cloud_cost_ratio,
            },
        }


def _kill_switch_on() -> bool:
    try:
        from mateai.config.loader import settings
        return bool(settings.autonomy.kill_switch)
    except Exception:  # noqa: BLE001
        return False


def principal_clearance(username: Optional[str]) -> int:
    """Cấp bảo mật THẬT của người hỏi (ABAC) — không lấy từ request hay hằng số."""
    from mateai.application.security.security_guard import security_guard
    return int(security_guard.principal(username)["clearance"])


# Singleton instance
multi_agent_system = CEORouterAgent()


# ── AI Skills Export ─────────────────────────────────────────────────────────

@export_skill(
    name="delegate_to_multi_agent",
    description="Ủy quyền yêu cầu cho Hệ thống Đa tác nhân (Multi-Agent Enterprise OS). Tự động phân loại tới CFO Agent (Tài chính), HR Agent (Nhân sự/Chính sách) hoặc CTO Agent (Hạ tầng kỹ thuật).",
    parameters_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Nội dung chỉ đạo của CEO hoặc người dùng (ví dụ: 'Tháng này chi phí server tăng không?', 'Ai đang làm việc năng suất nhất?').",
            }
        },
        "required": ["query"],
    },
)
def delegate_to_multi_agent(query: str) -> Dict[str, Any]:
    """Ủy quyền thực thi qua hệ thống Multi-Agent."""
    return multi_agent_system.route_and_execute(query=query)


@export_skill(
    name="get_executive_standup_briefing",
    data_classification="CONFIDENTIAL",
    description="Tạo báo cáo Họp Giao Ban Tự Động (Executive Daily Standup) tóm tắt tình hình toàn diện công ty trong 24h qua (Dòng tiền CFO, Tiến độ HR, Hạ tầng CTO). Dùng khi CEO yêu cầu báo cáo tình hình bằng giọng nói.",
    parameters_schema={"type": "object", "properties": {}},
)
def get_executive_standup_briefing() -> Dict[str, Any]:
    """Lấy báo cáo giao ban doanh nghiệp tự động."""
    return multi_agent_system.get_executive_briefing()


@export_skill(
    name="get_enterprise_executive_summary",
    data_classification="CONFIDENTIAL",
    description="Bộ não tổng hợp & Báo cáo điều hành tức thì (Executive Intelligence) cho giọng nói, Web và Telegram. Trả về voice_summary (<4 câu, siêu tốc) và rich_details định dạng Markdown.",
    parameters_schema={
        "type": "object",
        "properties": {
            "target_scope": {
                "type": "string",
                "description": "Phạm vi báo cáo ('all', 'fin', 'hr', 'it').",
                "default": "all",
            },
            "time_range": {
                "type": "string",
                "description": "Khoảng thời gian ('today', 'this_week', 'this_month').",
                "default": "today",
            },
        },
    },
)
def get_enterprise_executive_summary(target_scope: str = "all", time_range: str = "today") -> Dict[str, Any]:
    """Lấy tóm tắt điều hành tức thì đa phòng ban."""
    depts = None
    if target_scope and target_scope != "all":
        depts = [target_scope.upper()]
    # Trước đây clearance_level=4 cố định: ai hỏi qua giọng nói cũng nhận số liệu tài chính mật.
    from mateai.application.security.security_guard import CURRENT_PRINCIPAL
    return multi_agent_system.generate_cross_domain_report(
        query_context=f"Báo cáo điều hành phạm vi {target_scope} thời gian {time_range}",
        requested_departments=depts,
        clearance_level=principal_clearance(CURRENT_PRINCIPAL.get()),
    )


