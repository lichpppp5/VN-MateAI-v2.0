# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/dynamic_skill_router.py
============================
Phase 8: Dynamic Skill Loading & Taxonomy-Based Domain Indexing.

Mục tiêu cốt lõi:
  - Phân loại toàn bộ kỹ năng (79+ skills và các native tools) vào các Miền Nghiệp Vụ (Domains).
  - Lập chỉ mục bộ nhớ đệm (In-Memory Pre-indexed Catalog) để truy xuất schema công cụ < 0.1ms.
  - Phân giải ý định người dùng (Query-to-Domain Matching) tức thì:
      + Đàm thoại thông thường: Trả về 0 tools (Triệt tiêu 100% overhead token, TTFT < 200ms).
      + Tác vụ kỹ thuật: Chỉ nạp đúng 3-5 tools thuộc domain liên quan nhất (thay vì nhồi nhét 100 tools ~30k tokens).
  - Hỗ trợ Lazy Loading & Auto-Reindexing khi có plugin mới được nạp hoặc bật/tắt.
  - Bảo toàn 100% Zero-Trust, RBAC và tương thích ngược với API Admin.
"""

from __future__ import annotations

import logging
import math
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Domain Taxonomy Definition
# ---------------------------------------------------------------------------

class SkillDomain(str, Enum):
    """Các miền nghiệp vụ chuẩn hóa cho hệ sinh thái công cụ của VN-MateAI."""
    SYSTEM_OPS = "system_ops"              # CPU, RAM, Disk, Services, Process, Kill, Windows SysAdmin, Monitoring
    NETWORK_SECURITY = "network_security"  # Ping, IP, Port, Firewall, Vulnerabilities, SSL, Network interfaces
    FILE_STORAGE = "file_storage"          # Read/Write file, Search directory, Delete files, File system
    DATABASE_ERP = "database_erp"          # SQL queries, Excel records, ERP metrics, Finance/KPI, Data sources
    PC_AUTOMATION = "pc_automation"        # Computer Use GUI, Mouse, Keyboard, Screenshots, Windows control
    KNOWLEDGE_RAG = "knowledge_rag"        # Enterprise RAG, GraphRAG, Tra cứu tri thức, Q&A tài liệu
    ITSM_WORKFLOW = "itsm_workflow"        # Tickets, Incident management, Onboarding workflows, IT service
    MULTI_AGENT = "multi_agent"            # Executive standup, Delegation to CFO/HR/CTO/Specialist
    VISUAL_MEDIA = "visual_media"          # Visual boards, Charts, Overlays, Screen inspection
    GENERAL_TOOLS = "general_tools"        # Các công cụ tiện ích tổng quát khác


# Danh sách từ khóa nhận diện cho từng miền (Đã chuẩn hóa không dấu)
DOMAIN_SIGNATURES: Dict[SkillDomain, List[str]] = {
    SkillDomain.SYSTEM_OPS: [
        "cpu", "ram", "bo nho", "tien trinh", "process", "kill", "dung tien trinh",
        "he thong", "disk", "o dia", "dung luong", "restart", "khoi dong lai",
        "service", "dich vu", "sysadmin", "task manager", "uptime", "hardware",
        "nhiet do", "load", "tai", "hieu nang",
    ],
    SkillDomain.NETWORK_SECURITY: [
        "mang", "network", "ip", "ping", "port", "cong", "wifi", "router", "gateway",
        "ket noi", "an ninh", "security", "threat", "lo hong", "quet", "scan",
        "firewall", "tuong lua", "audit", "ssl", "cert", "dns", "bang thong",
    ],
    SkillDomain.FILE_STORAGE: [
        "file", "tap tin", "thu muc", "folder", "doc file", "ghi file", "xoa file",
        "tao file", "copy", "move", "rename", "doi ten", "sao chep", "duong dan",
        "path", "luu tru", "danh sach file", "tim file",
    ],
    SkillDomain.DATABASE_ERP: [
        "csdl", "database", "sql", "query", "truy van", "bang", "table", "mysql",
        "postgres", "excel", "bang tinh", "kpi", "doanh thu", "tai chinh", "chi phi",
        "nhan su", "luong", "erp", "don hang", "doanh so", "so lieu", "bao cao tai chinh",
    ],
    SkillDomain.PC_AUTOMATION: [
        "chup man hinh", "screenshot", "ung dung", "app", "mo app", "dong app",
        "chuot", "click", "ban phim", "go phim", "clipboard", "cua so", "window",
        "computer use", "dieu khien may", "nhan chuot", "keo tha",
    ],
    SkillDomain.KNOWLEDGE_RAG: [
        "tai lieu", "quy trinh", "huong dan", "chinh sach", "tri thuc", "knowledge",
        "rag", "graphrag", "tra cuu", "hoi dap", "tim kiem noi bo", "so tay", "wiki",
    ],
    SkillDomain.ITSM_WORKFLOW: [
        "ticket", "su co", "incident", "itsm", "sla", "ho tro", "yeu cau ho tro",
        "onboarding", "nhan vien moi", "workflow", "quy trinh duyet",
    ],
    SkillDomain.MULTI_AGENT: [
        "giao ban", "standup", "executive", "ceo", "cfo", "cto", "hr agent",
        "uy quyen", "delegate", "chuyen gia", "specialist", "phan tich sau", "da tac nhan",
    ],
    SkillDomain.VISUAL_MEDIA: [
        "bieu do", "chart", "do thi", "visual", "hien thi man hinh", "text board",
        "overlay", "hinh anh", "minh hoa",
    ],
}


def _strip_accents(text: str) -> str:
    """Loại bỏ dấu tiếng Việt và chuẩn hóa chữ thường."""
    if not text:
        return ""
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return text.replace("đ", "d").replace("Đ", "D").lower().strip()


#: Từ chức năng (đã bỏ dấu) — không mang nghĩa chọn công cụ.
_STOPWORDS = frozenset("""
cho toi ban em anh chi nghe cua va la co khong nao nay kia do voi mot nhung cac nhe nha giup hom
hay di the gi roi duoc can muon vao ra len xuong tu den trong tren duoi ay a oi minh chung
the a an to of in on for and by with is are be it this that from or as at
""".split())

#: Điểm từ đó coi là câu hỏi KHỚP RÕ một skill (đủ để chọn tool dù không có
#: từ khoá miền nào): vd một từ hiếm trong TÊN skill, hoặc một cụm hai từ +
#: một từ trong mô tả. Đo trên danh mục thật (82 skill).
STRONG_MATCH_SCORE = 4.5
#: Khớp YẾU: câu trò chuyện vẫn được đưa vài skill gần nhất để model tự quyết
#: ("hát cho tôi nghe bài…" khớp skill phát nhạc nhưng ít từ trùng).
WEAK_MATCH_SCORE = 2.0


#: Điểm từ đó coi là ĐÃ CÓ kỹ năng làm đúng việc này (không tạo kỹ năng mới).
#: Cao hơn STRONG_MATCH_SCORE (đủ để ĐƯA tool cho model chọn): động từ chung
#: ("tra cứu", "lấy dữ liệu", "chuyển đổi") đã vượt 4,5 — "tra cứu ngày âm lịch"
#: từng bị coi là trùng `fetch_data_source` nên kỹ năng không được tạo và model
#: đọc dữ liệu không liên quan. Đo 2026-10-04 trên danh mục thật (82 tool): 12
#: việc CHƯA có kỹ năng cao nhất 8,94; 8 việc ĐÃ có thấp nhất 10,15.
DUPLICATE_SKILL_SCORE = 10.0


def find_existing_skill(intent: str) -> Optional[str]:
    """Tên skill đã có làm đúng việc của yêu cầu (bỏ qua các công cụ quản lý kỹ
    năng) — dùng để không tạo trùng kỹ năng."""
    for score, tool in dynamic_skill_router.rank_tools(intent):
        name = (tool.get("function") or {}).get("name")
        if name in ("create_new_skill", "reload_all_skills", "list_available_skills"):
            continue
        return name if score >= DUPLICATE_SKILL_SCORE else None
    return None


#: Cụm chỉ CÁCH trả lời, không phải VIỆC cần làm (đã bỏ dấu). Không bỏ thì
#: "Giải thích ngắn gọn RAID 1 trong hai câu" khớp mô tả một skill xuất dữ
#: liệu ("… ngắn gọn …") trên ngưỡng khớp rõ -> câu hỏi kiến thức bị coi là
#: lệnh vận hành (bench Phase 1, docs/realtime/performance-baseline.md).
_ANSWER_STYLE = re.compile(
    r"\b(?:giai thich|ngan gon|chi tiet|de hieu|cu the|vi du|mot cach"
    r"|(?:trong|bang) (?:\d+|mot|hai|ba|bon|nam|vai) (?:cau|dong|y))\b"
)


def _task_text(query: str) -> str:
    """Câu hỏi đã bỏ dấu và bỏ các cụm chỉ cách trả lời — phần dùng để chấm skill."""
    return _ANSWER_STYLE.sub(" ", _strip_accents(query or ""))


def _tokens(text: str) -> List[str]:
    return [t for t in re.split(r"[^a-z0-9]+", _strip_accents(text)) if len(t) >= 2]


def _content_tokens(text: str) -> List[str]:
    return [t for t in _tokens(text) if t not in _STOPWORDS]


def _bigrams(tokens: List[str]) -> Set[Tuple[str, str]]:
    """Cụm hai từ liền nhau, bỏ cặp toàn từ chức năng. Dùng CÙNG cách cho câu
    hỏi và mô tả (trước đây câu hỏi bỏ từ chức năng trước khi ghép cặp nên
    "nghe nhac" không bao giờ khớp)."""
    return {(a, b) for a, b in zip(tokens, tokens[1:]) if not (a in _STOPWORDS and b in _STOPWORDS)}


# ---------------------------------------------------------------------------
# 2. Dynamic Skill Index & Router
# ---------------------------------------------------------------------------

class DynamicSkillRouter:
    """
    Bộ định tuyến kỹ năng động:
      - Quản lý bộ nhớ đệm phân loại (Pre-indexed Catalog) của tất cả kỹ năng.
      - Phân giải cực nhanh (sub-millisecond) các tools liên quan nhất theo ý định.
      - Giảm payload token gửi lên LLM từ ~30.000 tokens xuống còn ~500 tokens.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # domain -> List[Tool Schema Dict]
        self._domain_schemas: Dict[SkillDomain, List[Dict[str, Any]]] = {
            d: [] for d in SkillDomain
        }
        # tool_name -> Tool Schema Dict
        self._tool_cache: Dict[str, Dict[str, Any]] = {}
        # tool_name -> SkillDomain
        self._tool_domain_map: Dict[str, SkillDomain] = {}
        self._is_indexed: bool = False
        self._last_index_time: float = 0.0
        # tool_name -> (từ trong tên, từ trong mô tả, cụm hai từ trong mô tả)
        self._tool_terms: Dict[str, Tuple[Set[str], Set[str], Set[Tuple[str, str]]]] = {}
        # Trọng số độ hiếm của từ trong danh mục (0..1): "kiem", "tra", "may"
        # xuất hiện ở hàng chục công cụ nên gần như không phân biệt được gì.
        self._idf: Dict[str, float] = {}

    def rebuild_index(self) -> int:
        """
        Quét toàn bộ công cụ từ PluginManager, PluginRegistry và Native Tools,
        phân loại theo Domain và lập chỉ mục trong RAM.
        """
        with self._lock:
            t0 = time.perf_counter()
            self._domain_schemas = {d: [] for d in SkillDomain}
            self._tool_cache.clear()
            self._tool_domain_map.clear()
            self._tool_terms.clear()
            self._idf = {}

            all_tools: List[Dict[str, Any]] = []

            # 1. Nạp từ PluginManager
            try:
                from core.plugin_manager import plugin_manager
                for tool in plugin_manager.get_all_tools():
                    all_tools.append(tool)
            except Exception as exc:
                logger.warning("[DynamicSkillRouter] Không thể nạp tools từ PluginManager: %s", exc)

            # Danh mục duy nhất là plugin_manager (Phase 6): đã gỡ danh sách tool
            # viết tay trong llm_engine và việc nạp schema từ plugin_registry
            # (registry chỉ là chính sách thực thi).

            # Khử trùng lặp tên tool (Ưu tiên tool từ registry hoặc native)
            unique_tools: Dict[str, Dict[str, Any]] = {}
            for t in all_tools:
                fn = t.get("function") or {}
                name = fn.get("name")
                if name:
                    unique_tools[name] = t

            # 4. Phân loại từng tool vào Domain tương ứng
            for name, tool in unique_tools.items():
                self._tool_cache[name] = tool
                domain = self._classify_tool_domain(name, tool)
                self._tool_domain_map[name] = domain
                self._domain_schemas[domain].append(tool)
                _desc_tokens = _tokens((tool.get("function") or {}).get("description", ""))
                self._tool_terms[name] = (set(_tokens(name.replace("_", " "))), set(_desc_tokens), _bigrams(_desc_tokens))

            n_tools = max(1, len(self._tool_terms))
            df: Dict[str, int] = {}
            for name_t, desc_t, _pairs in self._tool_terms.values():
                for tok in name_t | desc_t:
                    df[tok] = df.get(tok, 0) + 1
            top = math.log((n_tools + 1) / 2)
            self._idf = {tok: max(0.0, math.log((n_tools + 1) / (c + 1))) / top if top > 0 else 1.0
                         for tok, c in df.items()}
            self._is_indexed = True
            self._last_index_time = time.monotonic()
            elapsed_ms = (time.perf_counter() - t0) * 1000

            logger.info(
                "[DynamicSkillRouter] Đã lập chỉ mục %d công cụ trên %d miền nghiệp vụ trong %.2fms.",
                len(unique_tools),
                len(self._domain_schemas),
                elapsed_ms,
            )
            return len(unique_tools)

    def _ensure_indexed(self) -> None:
        """Đảm bảo chỉ mục đã sẵn sàng."""
        if not self._is_indexed or not self._tool_cache:
            self.rebuild_index()

    def _classify_tool_domain(self, name: str, tool_schema: Dict[str, Any]) -> SkillDomain:
        """
        Phân loại tự động một công cụ vào một Domain cụ thể
        dựa trên tên hàm và nội dung mô tả.
        """
        fn = tool_schema.get("function") or {}
        desc = _strip_accents(fn.get("description", ""))
        name_clean = _strip_accents(name)
        combined = f"{name_clean} {desc}"

        # Quy tắc đặc thù cho các nhóm công cụ chính thức
        if any(k in name_clean for k in ["process", "service", "sysadmin", "cpu", "ram", "disk", "hardware", "system"]):
            return SkillDomain.SYSTEM_OPS
        if any(k in name_clean for k in ["ping", "port", "network", "firewall", "security", "threat", "scan"]):
            return SkillDomain.NETWORK_SECURITY
        if any(k in name_clean for k in ["file", "dir", "directory", "folder", "read_file", "write_file", "search_files"]):
            return SkillDomain.FILE_STORAGE
        if any(k in name_clean for k in ["excel", "sql", "database", "kpi", "finance", "hr", "erp", "data_source"]):
            return SkillDomain.DATABASE_ERP
        if any(k in name_clean for k in ["pc_control", "computer_use", "screenshot", "mouse", "keyboard", "window"]):
            return SkillDomain.PC_AUTOMATION
        if any(k in name_clean for k in ["rag", "knowledge", "wiki", "document"]):
            return SkillDomain.KNOWLEDGE_RAG
        if any(k in name_clean for k in ["ticket", "itsm", "onboarding", "sla", "incident"]):
            return SkillDomain.ITSM_WORKFLOW
        if any(k in name_clean for k in ["delegate", "standup", "executive", "agent_orchestrator"]):
            return SkillDomain.MULTI_AGENT
        if any(k in name_clean for k in ["visual", "chart", "overlay", "display_visual_data"]):
            return SkillDomain.VISUAL_MEDIA

        # Chấm điểm theo từ khóa miền
        best_domain = SkillDomain.GENERAL_TOOLS
        max_score = 0
        for domain, keywords in DOMAIN_SIGNATURES.items():
            score = sum(2 for kw in keywords if kw in name_clean) + sum(1 for kw in keywords if kw in desc)
            if score > max_score:
                max_score = score
                best_domain = domain

        return best_domain

    def get_domain_stats(self) -> Dict[str, int]:
        """Thống kê số lượng công cụ trong từng miền nghiệp vụ."""
        self._ensure_indexed()
        with self._lock:
            return {domain.value: len(tools) for domain, tools in self._domain_schemas.items()}

    def get_tools_by_domain(self, domain: SkillDomain | str) -> List[Dict[str, Any]]:
        """Lấy tất cả schema công cụ thuộc một miền cụ thể."""
        self._ensure_indexed()
        if isinstance(domain, str):
            try:
                domain = SkillDomain(domain.lower())
            except ValueError:
                return []
        with self._lock:
            return list(self._domain_schemas.get(domain, []))

    def rank_tools(self, query: str) -> List[Tuple[float, Dict[str, Any]]]:
        """
        Chấm điểm MỌI công cụ theo câu hỏi, cao trước (chỉ công cụ có điểm > 0).

        Khớp theo TỪ (đã bỏ dấu), không theo chuỗi con — trước đây "hat" (hát)
        khớp cả "that", "chat"…, còn skill nằm ở nhóm chung (vd skill do AI tự
        tạo) bị loại trước khi chấm vì không thuộc miền nào khớp câu hỏi, nên
        trợ lý chỉ dùng được khi người dùng nói đúng tên skill.
          +4  từ của câu hỏi có trong TÊN công cụ
          +1.5 từ có trong mô tả
          +3  cụm hai từ liền nhau của câu hỏi có trong mô tả ("bai hat", "xo so")
          +2  công cụ thuộc miền có từ khoá xuất hiện trong câu hỏi
        Mỗi từ nhân với độ hiếm của nó trong danh mục (IDF, 0..1).
        """
        self._ensure_indexed()
        q_clean = _task_text(query)
        q_tokens = _content_tokens(q_clean)
        if not q_tokens:
            return []
        q_set = set(q_tokens)
        q_pairs = _bigrams(_tokens(q_clean))
        idf = self._idf

        def w(tok: str) -> float:
            return idf.get(tok, 1.0)
        hit_domains = {d for d, kws in DOMAIN_SIGNATURES.items() if any(kw in q_clean for kw in kws)}
        scored: List[Tuple[float, Dict[str, Any]]] = []
        with self._lock:
            for name, tool in self._tool_cache.items():
                name_t, desc_t, desc_pairs = self._tool_terms.get(name, (set(), set(), set()))
                score = (
                    4.0 * sum(w(t) for t in q_set & name_t)
                    + 1.5 * sum(w(t) for t in q_set & desc_t)
                    + 3.0 * sum(max(w(a), w(b)) for a, b in q_pairs & desc_pairs)
                )
                if score and self._tool_domain_map.get(name) in hit_domains:
                    score += 2.0
                if score > 0:
                    scored.append((score, tool))
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored

    def get_tool(self, name: str) -> Optional[Dict[str, Any]]:
        """Schema của một công cụ theo tên (None nếu không có)."""
        self._ensure_indexed()
        with self._lock:
            return self._tool_cache.get(name)

    def best_match_score(self, query: str) -> float:
        """Điểm của công cụ khớp nhất (0 nếu không có)."""
        ranked = self.rank_tools(query)
        return ranked[0][0] if ranked else 0.0

    def get_tools_for_query(
        self,
        query: str,
        max_tools: int = 5,
        domain_hint: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Hàm cốt lõi của Phase 8:
        Phân giải ngữ cảnh truy vấn và nạp động đúng danh sách công cụ cần thiết.

        Quy tắc:
          1. Nếu là câu đàm thoại xã giao thông thường ("xin chào", "cảm ơn", "bạn là ai"...):
             -> Trả về [] (0 tools) -> TTFT siêu tốc (<200ms).
          2. Nếu có domain_hint:
             -> Lấy công cụ từ domain gợi ý.
          3. Nếu là lệnh tác vụ kỹ thuật:
             -> Khớp các miền nghiệp vụ phù hợp, chấm điểm liên quan từng tool,
                và lấy ra top max_tools công cụ điểm cao nhất.
        """
        self._ensure_indexed()
        query_clean = _strip_accents(query or "")

        # 1. Phát hiện đàm thoại xã giao thông thường -> Short-circuit 0 tools
        if domain_hint in ("voice", "conversation"):
            return []

        CASUAL_STARTS = [
            "xin chao", "chao", "hello", "hi", "cam on", "thanks", "tam biet",
            "bye", "ban la ai", "em la ai", "khoe khong", "the nao", "thoi tiet",
            "ke chuyen", "hat", "ngay mai", "bay gio la", "may gio", "chuc",
        ]
        CASUAL_CONTAINS = [
            "khoe khong", "the nao", "thoi tiet", "ban la ai", "em la ai",
            "tam su", "tro chuyen", "khoe k", "ngay tot lanh", "chuc mung",
        ]
        words = query_clean.split()
        is_casual = (
            (any(query_clean.startswith(cs) for cs in CASUAL_STARTS) and len(words) <= 8)
            or (any(cc in query_clean for cc in CASUAL_CONTAINS) and len(words) <= 10)
        )
        if is_casual and self.best_match_score(query) < STRONG_MATCH_SCORE:
            logger.debug("[DynamicSkillRouter] Câu đàm thoại xã giao -> 0 tools (TTFT tối ưu).")
            return []

        ranked = self.rank_tools(query)
        selected = [t for _, t in ranked[:max_tools]]
        if not selected:
            # Lệnh kỹ thuật nhưng không từ nào khớp: vài công cụ của miền gợi ý
            # (hoặc vận hành hệ thống) để model còn có lựa chọn.
            fallback_domains: List[SkillDomain] = []
            if domain_hint:
                try:
                    fallback_domains.append(SkillDomain(domain_hint.lower()))
                except ValueError:
                    pass
            fallback_domains.append(SkillDomain.SYSTEM_OPS)
            with self._lock:
                for d in fallback_domains:
                    for t in self._domain_schemas.get(d, []):
                        if len(selected) >= max_tools:
                            break
                        if t not in selected:
                            selected.append(t)
        logger.debug(
            "[DynamicSkillRouter] Query: '%s' -> %d tools %s (điểm cao nhất %.1f, tổng %d)",
            query[:40], len(selected), [(t.get("function") or {}).get("name") for t in selected],
            ranked[0][0] if ranked else 0.0, len(self._tool_cache),
        )
        return selected


# ---------------------------------------------------------------------------
# Global Singleton Instance
# ---------------------------------------------------------------------------
dynamic_skill_router = DynamicSkillRouter()
