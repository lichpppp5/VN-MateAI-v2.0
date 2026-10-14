# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
core/analytics_engine.py
========================
Phase 57: Text-to-SQL Analytics & Predictive Time-Series Forecasting.
Biến các chỉ đạo bằng giọng nói hoặc chat của CEO thành các biểu đồ Chart.js động,
thực thi SQL an toàn ở chế độ Read-Only và cảnh báo dự báo suy giảm dòng tiền.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from mateai.infrastructure.database.erp_database import erp_db
from core.plugin_manager import export_skill

logger = logging.getLogger(__name__)

# Schema định nghĩa cơ sở dữ liệu để LLM sinh câu truy vấn SQL chuẩn xác
DB_SCHEMA_PROMPT = """
Các bảng trong cơ sở dữ liệu nghiệp vụ (PostgreSQL hoặc SQLite — chỉ dùng SQL chuẩn chạy được trên cả hai):
1. finances (id, type: 'income'/'expense', amount: REAL, category: TEXT, description: TEXT, created_by: TEXT, date: TEXT 'YYYY-MM-DD HH:MM:SS')
2. tasks (id, dept_id, assignee_id, title: TEXT, status: 'pending'/'in_progress'/'completed'/'cancelled', due_date: TEXT, created_by_ai: INTEGER)
3. employees (id, dept_id, name: TEXT, position: TEXT, email: TEXT, phone: TEXT, role: TEXT)
4. departments (id, name: TEXT, description: TEXT)
5. attendance (id, employee_id, check_in_time: TEXT, check_out_time: TEXT, status: 'present'/'late'/'early_leave'/'absent')
6. devices (id, dept_id, hostname: TEXT, ip_address: TEXT, type: TEXT)
7. audit_logs (id, timestamp: TEXT, employee_id: TEXT, action_type: TEXT, payload: TEXT, status: TEXT)
"""

# Số dòng tối đa một truy vấn BI được trả về. Trước đây dùng `fetchall()`
# không giới hạn — một câu như "SELECT * FROM audit_logs" kéo toàn bộ nhật ký
# về RAM rồi đẩy vào Chart.js, gây treo tab. Giờ lấy có giới hạn.
MAX_SQL_ROWS = 500

# Chỉ thị hệ thống cho LLM sinh SQL. Ràng buộc được nhấn mạnh vì đây là điểm
# mà LLM hay "tiện tay" sinh câu lệnh ghi — mọi câu sinh ra đều đi qua
# `_is_safe_read_only_query` + SQLite authorizer, nhưng sinh đúng ngay từ đầu
# rẻ hơn là sinh rồi bị chặn.
SQL_SYSTEM_PROMPT = f"""Bạn là chuyên gia SQL cho doanh nghiệp Việt Nam. Nhiệm vụ: dịch câu hỏi của CEO thành MỘT câu lệnh SQL duy nhất.

{DB_SCHEMA_PROMPT}

QUY TẮC BẮT BUỘC:
- CHỉ được trả về đúng một câu lệnh `SELECT`, không giải thích, không bọc trong code fence.
- TUYỆT ĐỐI KHÔNG dùng: INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, REPLACE, PRAGMA, ATTACH.
- Bắt buộc đặt bí danh cột đầu tiên là `label` và cột số thứ hai là `value` (hệ thống vẽ biểu đồ từ đúng hai cột này).
- Ngày giờ lưu dạng chuỗi 'YYYY-MM-DD HH:MM:SS': gom theo ngày dùng `substr(cot, 1, 10)`, theo tháng dùng `substr(cot, 1, 7)`. KHÔNG dùng hàm riêng của một CSDL (date(), strftime, julianday, to_char).
- Chỉ dùng các bảng được liệt kê trên. Không bịa tên bảng/cột.
- Giới hạn tối đa {MAX_SQL_ROWS} dòng (thêm LIMIT khi cần)."""


def _ask_llm_for_sql(question: str) -> Tuple[str, str, str]:
    """
    Gọi LLM để sinh SQL. Trả về (sql, model_đã_dùng, lỗi).

    `sql` rỗng nghĩa là không sinh được — khi đó người gọi dùng bộ sinh từ
    khoá `if/elif` làm phương án dự phòng. Phương án dự phòng này là **có chủ
    ý**: hệ thống phải vẽ được biểu đồ ngay cả khi router LLM hỏng, thay vì
    trả lỗi cho CEO.

    Nhưng phải dự phòng **có thật** chứ không phải dự phòng giả: vì vậy hàm
    trả kèm `lỗi` và `text_to_sql_and_chart` đưa nó ra response. Nếu không,
    biểu đồ rơi về nhánh dự phòng trông vẫn rất thuyết phục mà người dùng
    không hề biết mình đang xem số liệu của câu hỏi khác — đúng kiểu "báo cáo
    thành công" mà Phase 57 cấm.
    """
    try:
        from mateai.application.skills.builtin.ai_delegation import _get_llm_config
        from mateai.infrastructure.llm.llm_provider import complete_text_blocking
    except Exception as exc:
        return "", "", f"không import được LLM provider: {exc}"

    cfg = _get_llm_config()
    if not cfg.get("api_key") or cfg["api_key"] == "sk-dummy":
        return "", "", "chưa cấu hình LLM API key"

    models = cfg.get("specialist_models") or [cfg["specialist_model"]]
    try:
        raw, model_name = complete_text_blocking(
            cfg["base_url"], cfg["api_key"], models,
            [
                {"role": "system", "content": SQL_SYSTEM_PROMPT},
                {"role": "user", "content": f"Câu hỏi của CEO: {question}"},
            ],
            temperature=0.0,  # SQL cần tất định, không sáng tạo
            max_tokens=1000,  # model có bước suy luận có thể tốn >400 token trước khi viết SQL
            # Câu SQL ≤ 400 token: 20 s/model là dư. Model chuyên gia "thinking" hay
            # chậm; nếu hết model, có bộ sinh SQL dự phòng (và báo rõ lỗi cho CEO) —
            # tốt hơn bắt CEO chờ tới N × 60 s.
            timeout=20.0,
        )
    except Exception as exc:
        # Provider đã thử lần lượt mọi model (bỏ qua model hỏng/đã ngừng); lỗi nói
        # rõ nguyên nhân để CEO không phải đoán.
        logger.warning("[AnalyticsEngine] LLM sinh SQL thất bại: %s", exc)
        return "", "", f"thử {len(models)} model đều thất bại — {str(exc)[:300]}"

    sql = _extract_sql((raw or "").strip())
    if sql:
        return sql, model_name, ""
    return "", "", f"{model_name}: trả lời không chứa câu SELECT nào"


# ---------------------------------------------------------------------------


def _infer_chart_type(prompt_lower: str, sql: str) -> str:
    """
    Đoán loại biểu đồ khi SQL do LLM sinh ra.

    Heuristic theo trục thời gian trong SQL: có `date`/`substr`/`strftime` và
    `GROUP BY` thì đó là chuỗi thời gian → `line`; người hỏi muốn "tỷ lệ",
    "cơ cấu", "phân bổ" mà tổng số nhóm nhỏ → `doughnut`; còn lại `bar`.

    Cũng chỉ là heuristic: Chart.js vẽ được mọi loại trên mọi tập dữ liệu, nên
    chọn sai chỉ làm biểu đồ kém trực quan chứ không làm sai dữ liệu.
    """
    sql_l = sql.lower()
    if any(fn in sql_l for fn in ("date(", "strftime", "substr(", "julianday")):
        return "line"
    if any(w in prompt_lower for w in ("tỷ lệ", "ty le", "cơ cấu", "co cau", "phân bổ", "ty trong")):
        return "doughnut"
    if any(w in prompt_lower for w in ("xu hướng", "theo tháng", "theo ngày", "diễn biến", "over time")):
        return "line"
    return "bar"


def _extract_sql(raw: str) -> str:
    """Bóc câu SQL ra khỏi phần trả lời của LLM (chịu được code fence và lời dẫn)."""
    if not raw:
        return ""
    text = raw.strip()

    fence = re.search(r"```(?:sql)?\s*(.+?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()

    match = re.search(r"\bSELECT\b[\s\S]+", text, flags=re.IGNORECASE)
    if not match:
        return ""

    sql = match.group(0).strip()
    # Cắt phần thừa sau dấu chấm câu cuối cùng.
    sql = sql.split(";")[0].strip()
    # Câu trả lời bị cắt (hết max_tokens giữa chừng) thường thiếu dấu đóng ngoặc:
    # gắn thêm ";" sẽ ra SQL hỏng. Coi là không sinh được → thử model khác / dự phòng.
    if sql.count("(") != sql.count(")"):
        return ""
    return sql + ";"


class AnalyticsEngine:
    """Công cụ chuyển đổi Text-to-SQL và Phân tích Dự báo Chuỗi thời gian."""

    def __init__(self) -> None:
        pass

    def _is_safe_read_only_query(self, sql: str) -> Tuple[bool, str]:
        """Kiểm tra tính an toàn nghiêm ngặt: Chỉ cho phép câu lệnh SELECT, nghiêm cấm các lệnh ghi/sửa/xóa."""
        clean = re.sub(r"--.*?$", "", sql, flags=re.MULTILINE)
        clean = re.sub(r"/\*.*?\*/", "", clean, flags=re.DOTALL)
        clean = clean.strip()

        if not clean.upper().startswith("SELECT"):
            return False, "Chỉ cho phép câu lệnh truy vấn đọc dữ liệu (SELECT)."

        forbidden_words = (
            "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE",
            "REPLACE", "TRUNCATE", "EXEC", "EXECUTE", "ATTACH", "DETACH",
            "PRAGMA", "VACUUM", "REINDEX",
        )
        tokens = set(re.findall(r"\b[A-Za-z_]+\b", clean.upper()))
        for fw in forbidden_words:
            if fw in tokens:
                return False, f"Phát hiện từ khóa nguy hiểm bị chặn bởi Zero-Trust: '{fw}'."

        return True, "Hợp lệ"

    def execute_safe_sql(self, sql: str) -> List[Dict[str, Any]]:
        """
        Thực thi câu lệnh SQL ở chế độ chỉ-đọc.

        Ba lớp phòng vệ, từ ngoài vào trong:

        1. `_is_safe_read_only_query()` — lọc theo token: phải bắt đầu bằng
           SELECT và không chứa từ khóa ghi/xóa.
        2. Authorizer của SQLite (ERPDatabase.execute_readonly_select) — chặn ở mức engine,
           kể cả lệnh lách được qua lớp 1 (ví dụ `SELECT load_extension(...)`).
        3. `PRAGMA query_only = ON` — chặn mọi ghi vào DB ở mức transaction.

        Lý do cần cả ba: lớp 1 là regex trên text do LLM sinh ra, mà LLM sinh
        text — không phải lớp bảo mật. Authorizer của SQLite chỉ có ở trong
        cảnh bảo mật; đây mới là lớp thực sự giữ dữ liệu.
        """
        is_safe, msg = self._is_safe_read_only_query(sql)
        if not is_safe:
            raise PermissionError(f"Cảnh báo bảo mật: {msg}")

        # Lớp 2 + 3 (authorizer của SQLite + query_only) nằm ở tầng dữ liệu.
        return erp_db.execute_readonly_select(sql, max_rows=MAX_SQL_ROWS)

    def text_to_sql_and_chart(self, prompt: str) -> Dict[str, Any]:
        """
        Dịch chỉ đạo của CEO thành câu lệnh SQL và sinh cấu hình Chart.js.

        Đường đi theo thứ tự ưu tiên:

        1. **LLM sinh SQL** — `DB_SCHEMA_PROMPT` vốn được viết ra để dùng với
           LLM nhưng trước đây là **biến chết**: không hàm nào đọc tới nó. Mọi
           câu hỏi đều rơi xuống chuỗi `if/elif` bên dưới chỉ có 6 nhánh, nên
           "top 3 phòng ban chi tiêu nhiều nhất quý 2" trả về đúng biểu đồ
           "cơ cấu chi tiêu" — sai câu hỏi nhưng trông rất thuyết phục.
        2. **Dự phòng `if/elif`** khi không có LLM, LLM lỗi, hoặc SQL sinh ra
           không an toàn. Có chủ ý giữ lại: hệ thống phải vẽ được biểu đồ ngay
           cả khi router LLM hỏng.

        Kết quả trả về có `sql_source` cho biết SQL đến từ đâu, để khi biểu đồ
        sai người dùng biết ngay là do dự phòng hay do LLM. Kèm `llm_error` để
        khi rơi về dự phòng, nguyên nhân cụ thể hiện ra chứ không im lặng.
        """
        prompt_lower = prompt.lower()

        # 1. Ưu tiên LLM sinh SQL
        sql_query, llm_model, llm_error = _ask_llm_for_sql(prompt)
        sql_source = "llm"
        chart_type = "bar"
        chart_title = f"Biểu đồ: {prompt[:80]}"

        if sql_query:
            is_safe, reason = self._is_safe_read_only_query(sql_query)
            if not is_safe:
                logger.warning(
                    "[AnalyticsEngine] LLM sinh SQL không an toàn, chuyển sang dự phòng: %s", reason
                )
                sql_query, sql_source = "", "keyword_fallback"
            else:
                chart_type = _infer_chart_type(prompt_lower, sql_query)
        else:
            sql_source = "keyword_fallback"

        # 2. Dự phòng: ánh xạ từ khoá -> truy vấn SQL định sẵn
        if not sql_query:
            sql_query, chart_type, chart_title = self._fallback_sql_for(prompt_lower)
            chart_title = f"{chart_title} (chế độ dự phòng)"

        # 3. Thực thi SQL ở chế độ Read-only
        try:
            records = self.execute_safe_sql(sql_query)
        except PermissionError as exc:
            logger.error("[AnalyticsEngine] SQL bị chặn bởi Zero-Trust: %s", exc)
            return {"status": "error", "message": str(exc)}
        except Exception as exc:
            logger.error("[AnalyticsEngine] Lỗi thực thi SQL: %s", exc)
            return {"status": "error", "message": f"Lỗi truy vấn SQL: {str(exc)}"}

        labels = []
        values = []
        for r in records:
            label_val = str(r.get("label") or "Khác")
            labels.append(label_val)
            values.append(float(r.get("value") or 0))

        # Màu sắc hiện đại cho biểu đồ
        palette = [
            "rgba(59, 130, 246, 0.8)",   # Blue
            "rgba(16, 185, 129, 0.8)",  # Green
            "rgba(245, 158, 11, 0.8)",  # Amber
            "rgba(239, 68, 68, 0.8)",   # Red
            "rgba(139, 92, 246, 0.8)",  # Purple
            "rgba(236, 72, 153, 0.8)",  # Pink
            "rgba(20, 184, 166, 0.8)",  # Teal
        ]

        # 3. Tạo cấu hình Chart.js hoàn chỉnh
        chart_config = {
            "type": chart_type,
            "data": {
                "labels": labels,
                "datasets": [
                    {
                        "label": chart_title,
                        "data": values,
                        "backgroundColor": palette[:len(values)] if len(values) <= len(palette) else palette,
                        "borderColor": "#38bdf8",
                        "borderWidth": 1.5,
                        "fill": (chart_type == "line"),
                    }
                ],
            },
            "options": {
                "responsive": True,
                "maintainAspectRatio": False,
                "plugins": {
                    "title": {
                        "display": True,
                        "text": f"📊 {chart_title}",
                        "color": "#f8fafc",
                        "font": {"size": 16, "weight": "bold"},
                    },
                    "legend": {
                        "display": (chart_type in ("pie", "doughnut")),
                        "labels": {"color": "#94a3b8"},
                    },
                },
                "scales": {
                    "x": {"ticks": {"color": "#94a3b8"}, "grid": {"color": "rgba(255,255,255,0.05)"}} if chart_type not in ("pie", "doughnut") else {},
                    "y": {"ticks": {"color": "#94a3b8"}, "grid": {"color": "rgba(255,255,255,0.05)"}} if chart_type not in ("pie", "doughnut") else {},
                } if chart_type not in ("pie", "doughnut") else {},
            },
        }

        return {
            "status": "success",
            "prompt": prompt,
            "sql": sql_query.strip(),
            "sql_source": sql_source,
            "llm_model": llm_model or None,
            "llm_error": llm_error or None,
            "chart_type": chart_type,
            "title": chart_title,
            "records_count": len(records),
            "chart_config": chart_config,
        }

    @staticmethod
    def _fallback_sql_for(prompt_lower: str) -> Tuple[str, str, str]:
        """
        Sinh SQL dự phòng bằng so khớp từ khoá khi LLM không khả dụng.

        Trả về (sql, chart_type, chart_title). Đây là đường dự phòng, không
        phải đường chính — nó chỉ có 6 nhánh nên câu hỏi ngoài danh mục sẽ rơi
        về nhánh `else` (cơ cấu chi tiêu) và trả về **biểu đồ đúng hình thức
        nhưng sai nội dung**. Vì vậy kết quả trả về có gắn `(chế độ dự phòng)`
        vào tiêu đề để người dùng không tin nhầm.
        """
        if any(w in prompt_lower for w in ("chi phí", "khoản chi", "tiêu tiền")):
            return (
                """
                SELECT category AS label, SUM(amount) AS value
                FROM finances
                WHERE type = 'expense'
                GROUP BY category
                ORDER BY value DESC;
                """,
                "bar",
                "Phân bổ chi phí theo danh mục (VND)",
            )
        if any(w in prompt_lower for w in ("doanh thu", "thu nhập", "khoản thu", "nguồn thu")):
            return (
                """
                SELECT category AS label, SUM(amount) AS value
                FROM finances
                WHERE type = 'income'
                GROUP BY category
                ORDER BY value DESC;
                """,
                "bar",
                "Phân bổ nguồn doanh thu (VND)",
            )
        if any(w in prompt_lower for w in ("dòng tiền", "thu chi", "cashflow")):
            return (
                """
                SELECT date AS label,
                       CASE WHEN type = 'income' THEN amount ELSE -amount END AS value
                FROM finances
                ORDER BY date ASC
                LIMIT 15;
                """,
                "line",
                "Dòng tiền Thu/Chi theo giao dịch gần nhất",
            )
        if any(w in prompt_lower for w in ("trạng thái task", "tiến độ", "công việc")):
            return (
                """
                SELECT status AS label, COUNT(*) AS value
                FROM tasks
                GROUP BY status;
                """,
                "doughnut",
                "Tỷ lệ trạng thái công việc toàn công ty",
            )
        if any(w in prompt_lower for w in ("phòng ban", "bộ phận")):
            return (
                """
                SELECT d.name AS label, COUNT(e.id) AS value
                FROM departments d
                LEFT JOIN employees e ON d.id = e.dept_id
                GROUP BY d.id, d.name
                ORDER BY value DESC;
                """,
                "bar",
                "Số lượng nhân sự theo phòng ban",
            )
        if any(w in prompt_lower for w in ("năng suất", "top", "leaderboard", "hoàn thành")):
            return (
                """
                SELECT e.name AS label, COUNT(t.id) AS value
                FROM employees e
                JOIN tasks t ON e.id = t.assignee_id
                WHERE t.status = 'completed'
                GROUP BY e.id, e.name
                ORDER BY value DESC
                LIMIT 5;
                """,
                "bar",
                "Top nhân viên hoàn thành nhiều việc nhất",
            )
        return (
            """
            SELECT category AS label, SUM(amount) AS value
            FROM finances
            WHERE type = 'expense'
            GROUP BY category;
            """,
            "doughnut",
            "Cơ cấu chi tiêu doanh nghiệp",
        )

    def evaluate_predictive_cashflow(self) -> Dict[str, Any]:
        """Phân tích chuỗi thời gian dòng tiền và cảnh báo sớm nguy cơ cạn quỹ."""
        fin = erp_db.get_financial_summary(30)
        net_balance = fin["net_balance"]
        daily_burn = fin["daily_burn_rate"]
        runway = fin["runway_days"]

        # ── Chưa có dữ liệu thì KHÔNG kết luận gì cả ─────────────────────
        # Lỗi đã xảy ra: bảng `finances` trống nên mọi số về 0, rồi
        # `net_balance <= 0` bắt thành CẢNH BÁO ĐỎ và bắn Telegram, với nội
        # dung vô lý: "Tốc độ chi tiêu (0 VND/ngày) đang vượt ngưỡng an toàn.
        # Quỹ dự trữ (0 VND) sẽ cạn trong 0.0 ngày" — 0 không thể vượt ngưỡng
        # nào, và 0 VND ở đây là KHÔNG CÓ giao dịch chứ không phải quỹ cạn.
        #
        # Không có số đo thì phải nói không có số đo. Không dùng 0 để đại diện
        # cho "chưa biết", và không gửi cảnh báo cho một sự kiện không có.
        if not fin.get("has_data", True):
            return {
                "status": "success",
                "alert_level": "NO_DATA",
                "is_critical": False,
                "is_warning": False,
                "has_data": False,
                "runway_days": None,
                "daily_burn_rate": None,
                "net_balance": None,
                "message": (
                    "Chưa có giao dịch tài chính nào được ghi nhận — chưa đủ dữ liệu "
                    "để dự báo dòng tiền. Chưa phải lúc kết luận quỹ an toàn hay "
                    "cạn."
                ),
            }

        # Đánh giá mức độ rủi ro dòng tiền
        # Nguy cấp: Runway < 15 ngày hoặc net_balance âm
        #
        # Điều kiện cũ là `net_balance <= 0`, coi số dư bằng 0 là nguy cấp.
        # Nhưng doanh nghiệp vừa lập, chi bằng 0, thu bằng 0 là chuyện bình
        # thường, không phải "cạn quỹ". Chỉ coi là nguy cấp khi ÂM thật, hoặc
        # khi thực sự đang đốt tiền mà runway ngắn.
        is_critical = (net_balance < 0) or (daily_burn > 0 and runway < 15.0)
        is_warning = (daily_burn > 0 and runway < 30.0)

        alert_level = "NORMAL"
        if is_critical:
            # Nói đúng LÝ DO. Trước đây mọi ca nguy cấp đều dùng một câu "tốc độ
            # chi tiêu đang vượt ngưỡng an toàn" — nhưng khi số dư âm mà chi phí
            # rất nhỏ thì câu đó sai: 266.000 VND/ngày không phải nguyên nhân,
            # nguyên nhân là đã âm. Người đọc tìm nhầm chỗ cần sửa.
            alert_message = (
                f"🚨 CẢNH BÁO ĐỎ TÀI CHÍNH (PREDICTIVE WARNING):\n"
                + (
                    f"Số dư đang ÂM: {net_balance:,.0f} VND. "
                    f"Chi tiêu {daily_burn:,.0f} VND/ngày. "
                    f"Quỹ cạn ngay từ hiện tại — cần bơm tiền hoặc cắt chi.\n"
                    if net_balance < 0 else
                    f"Tốc độ chi tiêu ({daily_burn:,.0f} VND/ngày) khiến quỹ "
                    f"chỉ còn đủ {runway:,.0f} ngày.\n"
                )
                + f"Quỹ dự trữ hiện còn {net_balance:,.0f} VND."
            )
        elif is_warning:
            alert_message = f"⚠️ Cảnh báo dòng tiền: Số ngày an toàn (Runway) còn dưới 30 ngày ({runway} ngày)."
        elif daily_burn == 0:
            # Có dữ liệu nhưng không có chi phí nào trong 30 ngày. Runway kiểu
            # cũ trả 0.0 ở trường hợp này (mẫu số bằng 0) — nói "còn 0 ngày"
            # là sai, vì không có gì để cạn.
            alert_message = (
                f"Không có khoản chi nào trong 30 ngày gần nhất. Số dư "
                f"{net_balance:,.0f} VND. Không tính được số ngày còn hoạt động "
                f"vì không có chi tiêu để chia."
            )
        else:
            alert_message = f"Dòng tiền an toàn. Số dư quỹ {net_balance:,.0f} VND dự kiến đủ vận hành trong {runway:,.0f} ngày."

        if is_critical:
            alert_level = "CRITICAL_RED"
            # Phát cảnh báo khẩn cấp sang Telegram. Chỉ khi có số đo thật —
            # nhánh NO_DATA đã trả về từ trên nên không bao giờ tới đây mà
            # chưa có giao dịch.
            from mateai.application.operations import alert_dispatcher
            alert_dispatcher.notify("Dòng tiền ở mức nguy hiểm", alert_message, severity="critical",
                                    category="finance:runway", source="Phân tích tài chính")

        elif is_warning:
            alert_level = "WARNING_YELLOW"

        return {
            "status": "success",
            "alert_level": alert_level,
            "is_critical": is_critical,
            "is_warning": is_warning,
            "has_data": True,
            "runway_days": runway if daily_burn > 0 else None,
            "daily_burn_rate": daily_burn,
            "net_balance": net_balance,
            "message": alert_message,
        }


# Singleton instance
analytics_engine = AnalyticsEngine()


# ── AI Skills Export ─────────────────────────────────────────────────────────

@export_skill(
    name="generate_dynamic_sql_chart",
    data_classification="CONFIDENTIAL",
    description="Chuyển đổi yêu cầu phân tích dữ liệu kinh doanh của CEO (ví dụ: 'Vẽ biểu đồ chi phí', 'Thống kê task theo phòng ban', 'Biểu đồ dòng tiền') thành câu lệnh SQL an toàn và sinh cấu hình Chart.js hiển thị ngay trên Web.",
    parameters_schema={
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "Câu lệnh hoặc yêu cầu phân tích (ví dụ: 'Vẽ biểu đồ chi phí theo danh mục', 'Tỷ lệ trạng thái task').",
            }
        },
        "required": ["prompt"],
    },
)
def generate_dynamic_sql_chart(prompt: str) -> Dict[str, Any]:
    """Sinh biểu đồ phân tích dữ liệu kinh doanh bằng Text-to-SQL."""
    return analytics_engine.text_to_sql_and_chart(prompt=prompt)


@export_skill(
    name="check_cashflow_predictive_health",
    data_classification="CONFIDENTIAL",
    description="Chạy thuật toán dự báo dòng tiền và tốc độ đốt tiền (Burn Rate & Runway). Nếu phát hiện quỹ công ty cạn kiệt trong vòng dưới 15 ngày, tự động kích hoạt CẢNH BÁO ĐỎ.",
    parameters_schema={"type": "object", "properties": {}},
)
def check_cashflow_predictive_health() -> Dict[str, Any]:
    """Kiểm tra sức khỏe dòng tiền dự báo."""
    return analytics_engine.evaluate_predictive_cashflow()
