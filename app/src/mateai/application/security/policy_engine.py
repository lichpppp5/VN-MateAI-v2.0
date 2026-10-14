# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/application/security/policy_engine.py
============================================
Policy Engine chuẩn — MỘT hàm quyết định cho mọi lần thực thi tool
(prompt Supervisor §13–§17, `docs/autonomy/policy-model.md`).

Hình thành bằng cách gộp các luật đang rải ở 4 nơi (không thêm bản song song):
  - mức rủi ro: `risk_engine.assess_risk` (chuyển từ `zero_trust`);
  - RBAC: `security_guard.check_permission`;
  - cấu hình Portal `security.forbidden_keywords` / `require_confirmation_actions`
    (trước đây cổng tool KHÔNG đọc hai danh sách này);
  - uỷ quyền "duyệt rồi nhớ" (chuyển từ `tool_gate`), nay có hạn dùng.

Thứ tự (DENY luôn thắng — kể cả admin, kể cả khi đã được duyệt):
  1. kill switch toàn cục / tác nhân / tool        -> DENY
  2. L5 (never_autonomous) + tool cấm + từ khoá cấm  -> DENY
  3. RBAC theo danh tính người/thiết bị gọi          -> DENY
     ABAC: cấp bảo mật người gọi < mức phân loại dữ liệu của tool (tool contract) -> DENY
  4. rủi ro -> mức tự trị:  1 = L0, 2 = L2 (tự chạy)
                            >= 3 = L3: chỉ chạy khi người có quyền đã duyệt lượt này,
                                       hoặc có uỷ quyền còn hạn cho đúng danh tính + tool (L4)
  5. chế độ khẩn cấp (§54): AI tự chạy (L2 / L4, không người duyệt) quá
     `emergency_max_actions_per_minute` hành động có tác dụng phụ trong 60 s
     -> tự BẬT kill switch (bền trong cấu hình, có audit + cảnh báo), từ chối hành động
     vượt ngưỡng. Chỉ người tắt lại được. Tác vụ chỉ đọc vẫn chạy.

LLM không có đường nào ghi đè quyết định: đầu vào là tên tool, tham số, danh tính
do máy chủ xác thực, cờ `approved` do hàng đợi duyệt đặt, và cấu hình.
Không có ngoại lệ theo vai trò: tài khoản admin cũng phải duyệt từ L3 (thay quyết
định cũ "admin bỏ qua duyệt" — prompt mới thay thế hoàn toàn, 2026-10-05).
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

ALLOW = "allow"
DENY = "deny"
REQUIRE_APPROVAL = "require_approval"

# ── Danh tính tác nhân AI (§11) ─────────────────────────────────────────────
AGENT_VOICE = "VN-MATEAI-VOICE"            # portal, HUD, robot, mic máy chủ, REST thoại
AGENT_TELEGRAM = "VN-MATEAI-TELEGRAM"
AGENT_PORTAL_OPS = "VN-MATEAI-PORTAL-OPS"   # thao tác tệp / máy trạm từ Portal
AGENT_ORCHESTRATOR = "VN-MATEAI-ORCHESTRATOR"
AGENT_CONNECTOR = "VN-MATEAI-CONNECTOR"     # tool đăng ký động (connector, computer-use)
HUMAN_DIRECT = "HUMAN-DIRECT"               # người bấm trực tiếp trên Portal, không qua AI
KNOWN_AGENTS = (AGENT_VOICE, AGENT_TELEGRAM, AGENT_PORTAL_OPS, AGENT_ORCHESTRATOR, AGENT_CONNECTOR, HUMAN_DIRECT)


def agent_id_for(source_device: Optional[str]) -> str:
    """Tác nhân thực hiện theo kênh do MÁY CHỦ gán (không lấy từ nội dung LLM)."""
    sd = str(source_device or "").strip().lower()
    if sd.startswith("telegram"):
        return AGENT_TELEGRAM
    if sd.startswith("http:"):
        return AGENT_PORTAL_OPS
    if sd.startswith("orchestrator"):
        return AGENT_ORCHESTRATOR
    return AGENT_VOICE


@dataclass
class Decision:
    effect: str
    level: str
    risk: int
    agent_id: str
    reasons: List[str] = field(default_factory=list)
    rule: str = ""
    policy_version: str = ""

    @property
    def allowed(self) -> bool:
        return self.effect == ALLOW

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _autonomy():
    from mateai.config.loader import settings
    return settings.autonomy


def policy_version() -> str:
    """Băm nội dung luật đang hiệu lực — ghi kèm mọi quyết định để truy lại (§128)."""
    try:
        from mateai.config.loader import settings
        blob = json.dumps({
            "autonomy": settings.autonomy.model_dump(),
            "forbidden_keywords": list(settings.security.forbidden_keywords or []),
            "require_confirmation_actions": list(settings.security.require_confirmation_actions or []),
        }, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]
    except Exception:  # noqa: BLE001
        return "unknown"


def _level_for(risk: int) -> str:
    return "L0" if risk <= 1 else "L2" if risk == 2 else "L3"


def _forbidden_keyword(tool: str, args: Dict[str, Any]) -> Optional[str]:
    from mateai.config.loader import settings
    payload = (tool + " " + json.dumps(args or {}, ensure_ascii=False, default=str)).lower()
    for kw in settings.security.forbidden_keywords or []:
        k = str(kw).strip().lower()
        if k and k in payload:
            return str(kw)
    return None


# ── Uỷ quyền "duyệt rồi nhớ" (L4) — chuyển từ tool_gate ───────────────────────

def grant_principal(caller: Optional[str]) -> Optional[str]:
    """Danh tính được nhớ uỷ quyền: robot "device:<id>" / agent ngoài "agent:<id>" (token riêng) hoặc kênh
    Telegram "telegram:<chat_id>" (bỏ tên người gửi — tên đổi được)."""
    from mateai.application.security.security_guard import AGENT_PRINCIPAL_PREFIX, DEVICE_PRINCIPAL_PREFIX
    c = str(caller or "")
    if c.lower().startswith((DEVICE_PRINCIPAL_PREFIX, AGENT_PRINCIPAL_PREFIX)):
        return c
    parts = c.split(":")
    if len(parts) >= 2 and parts[0].lower() == "telegram" and parts[1].strip():
        return f"telegram:{parts[1].strip()}"
    return None


def _people_decision(tool: str) -> bool:
    from mateai.application.security.hr_guard import is_people_decision
    return is_people_decision(tool)


def has_delegation(caller: Optional[str], tool: str) -> bool:
    principal = grant_principal(caller)
    if not principal:
        return False
    try:
        from mateai.infrastructure.database.db_manager import db_manager
        return db_manager.has_approval_grant(principal, tool, max_age_days=_autonomy().approval_grant_ttl_days)
    except Exception as exc:  # noqa: BLE001 — lỗi tra cứu: hỏi duyệt như thường
        logger.warning("Không đọc được uỷ quyền cho '%s': %s", principal, exc)
        return False


def remember_delegation(requested_by: Optional[str], tool: str, granted_by: str) -> None:
    principal = grant_principal(requested_by)
    if not (principal and tool):
        return
    try:
        from mateai.infrastructure.database.db_manager import db_manager
        db_manager.add_approval_grant(principal, tool, granted_by)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Không lưu được uỷ quyền '%s' / '%s': %s", principal, tool, exc)


# ── Chế độ khẩn cấp (§54) ────────────────────────────────────────────────────

EMERGENCY_ACTOR = "system:emergency-guard"


def _autonomous_burst(limit: int) -> bool:
    """Ghi một hành động AI TỰ chạy có tác dụng phụ; True nếu vượt ngưỡng trong 60 s.
    Đếm ở kho dùng chung (Redis khi có): nhiều tiến trình cùng một ngưỡng toàn hệ thống."""
    from mateai.infrastructure.cache import shared_state
    return shared_state.store().hit("emergency:autonomous", limit, 60.0) > 0


def _engage_emergency(agent_id: str, tool: str, limit: int) -> None:
    """Bật kill switch qua đường đổi giới hạn tự trị chuẩn (lịch sử + audit), rồi cảnh báo."""
    reason = (f"Chế độ khẩn cấp: AI tự chạy quá {limit} hành động có tác dụng phụ trong 60 s "
              f"(tác nhân {agent_id}, tool '{tool}'). Chỉ còn tác vụ chỉ đọc — cần người kiểm tra rồi tắt.")
    try:
        from mateai.application.administration import autonomy_settings
        autonomy_settings.update(EMERGENCY_ACTOR, {"kill_switch": True}, reason)
    except Exception as exc:  # noqa: BLE001 — không ghi được cấu hình: vẫn khoá trong RAM
        logger.error("Không lưu được kill switch khẩn cấp: %s", exc)
        _autonomy().kill_switch = True
    logger.critical(reason)
    try:
        from mateai.application.operations import alert_dispatcher
        alert_dispatcher.notify("Chế độ khẩn cấp AI đã bật", reason, severity="critical", category="security",
                                source="policy_engine")
    except Exception as exc:  # noqa: BLE001
        logger.error("Không gửi được cảnh báo chế độ khẩn cấp: %s", exc)


# ── Quyết định ───────────────────────────────────────────────────────────────

def authorize(
    tool: str,
    args: Optional[Dict[str, Any]] = None,
    *,
    caller: Optional[str],
    agent_id: str,
    approved: bool = False,
    declared_risk: Optional[int] = None,
    session_id: Optional[str] = None,
    check_rbac: bool = True,
) -> Decision:
    """`agent_id = HUMAN_DIRECT`: người bấm trực tiếp trên Portal (không qua AI) —
    kill switch và L5 là giới hạn của AI nên không áp; L5 khi đó cần duyệt (§149).
    `check_rbac=False`: chỉ cho cổng lồng bên trong một tool đã qua RBAC ở lớp ngoài."""
    from mateai.application.security.risk_engine import assess_risk
    from mateai.application.security.security_guard import GLOBALLY_FORBIDDEN_TOOLS, security_guard

    args = dict(args or {})
    name = str(tool or "").strip()
    key = name.lower()
    version = policy_version()
    risk = assess_risk(name, args, declared_risk)
    auto = _autonomy()

    def deny(rule: str, reason: str, level: Optional[str] = None) -> Decision:
        return Decision(DENY, level or _level_for(risk), risk, agent_id, [reason], rule, version)

    is_ai = agent_id != HUMAN_DIRECT

    # 1. Kill switch — ngoài LLM (§95–§96): toàn cục chỉ còn tác vụ chỉ đọc.
    if is_ai and auto.kill_switch and risk > 1:
        return deny("kill_switch", "Công tắc dừng khẩn cấp đang bật: chỉ còn tác vụ chỉ đọc.")
    if agent_id in set(auto.disabled_agents or []):
        return deny("agent_disabled", f"Tác nhân {agent_id} đang bị tắt.")
    if key in {str(t).strip().lower() for t in (auto.disabled_tools or [])}:
        return deny("tool_disabled", f"Tool '{name}' đang bị tắt.")

    # 2. Cấm tuyệt đối / từ khoá cấm (mọi trường hợp) · L5 (với AI).
    if name in GLOBALLY_FORBIDDEN_TOOLS:
        return deny("forbidden_tool", f"Tool '{name}' bị cấm vĩnh viễn trên toàn hệ thống.", "L5")
    kw = _forbidden_keyword(name, args)
    if kw:
        return deny("forbidden_keyword", f"Tác vụ '{name}' chứa từ khoá bị cấm theo chính sách: '{kw}'.")
    never = key in {str(t).strip().lower() for t in (auto.never_autonomous_tools or [])}
    if never and is_ai:
        return deny("never_autonomous", f"Tác vụ '{name}' thuộc nhóm L5 — chỉ người thực hiện, AI không bao giờ chạy.", "L5")
    if never:
        risk = 5

    # 3. RBAC — trước khi sinh yêu cầu duyệt (người không có quyền thì không hỏi ai cả).
    if check_rbac:
        ok, reason = security_guard.check_permission(tool_name=name, employee_id=caller,
                                                     session_id=session_id, payload=args)
        if not ok:
            return deny("rbac", reason)

    # 3b. ABAC (prompt cuối §35, §62): dữ liệu CONFIDENTIAL / RESTRICTED cần đủ cấp bảo mật —
    # kể cả khi AI gọi thay người. Luôn xét (kể cả cổng lồng), lấy thuộc tính từ máy chủ.
    from mateai.application.security.security_guard import required_clearance
    need = required_clearance(name)
    if need > 1:
        who = security_guard.principal(caller)
        if who["clearance"] < need:
            return deny("abac_clearance", f"Tác vụ '{name}' dùng dữ liệu cần cấp bảo mật {need}; "
                                          f"tài khoản hiện có cấp {who['clearance']}.")

    # 4. Rủi ro -> mức tự trị.
    level = _level_for(risk)
    if approved and risk >= 3:
        return Decision(ALLOW, "L3", risk, agent_id, ["người có quyền đã duyệt lượt này"], "approved", version)
    if risk < 3:
        decision = Decision(ALLOW, level, risk, agent_id, [f"rủi ro {risk}: tự chạy"], "auto", version)
    elif has_delegation(caller, name) and not _people_decision(name):
        # Quyết định về con người không dùng "duyệt một lần, nhớ 30 ngày": mỗi quyết định về một người được duyệt riêng.
        decision = Decision(ALLOW, "L4", risk, agent_id, ["uỷ quyền còn hạn cho danh tính + tool này"], "delegated", version)
    else:
        decision = None
    if decision is not None:
        # 5. Chế độ khẩn cấp: chỉ đếm hành động AI TỰ chạy có tác dụng phụ.
        limit = int(getattr(auto, "emergency_max_actions_per_minute", 0) or 0)
        if is_ai and risk > 1 and not approved and limit > 0 and _autonomous_burst(limit):
            _engage_emergency(agent_id, name, limit)
            return deny("emergency_mode", f"Chế độ khẩn cấp: AI tự chạy quá {limit} hành động trong 60 s — "
                                          "chuyển sang chỉ đọc, cần người kiểm tra.")
        return decision
    return Decision(REQUIRE_APPROVAL, "L3", risk, agent_id,
                    [f"rủi ro {risk}/5: cần người có quyền duyệt"], "supervised", version)
