// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
/*
 * Hàm nhỏ thay cho các onclick / oninput nội tuyến có điều kiện, phép cộng chuỗi, arrow function — thứ mà
 * web/inline-handlers.js (CSP chặt) cố ý không diễn giải. Mỗi hàm gọi thẳng đúng hàm đích, không nhận tên hàm từ HTML.
 */

/** Chọn model từ danh sách gợi ý → điền vào ô model của Quản lý trợ lý AI. */
function onAiModelPicked(value) {
  const el = document.getElementById('ai-llm-model');
  if (el) el.value = value;
  if (typeof onModelChanged === 'function') onModelChanged();
}

/** Chọn model từ danh sách gợi ý → điền vào ô model của Cấu hình hệ thống. */
function onCfgModelPicked(value) {
  const el = document.getElementById('cfg-llm-model');
  if (el) el.value = value;
  if (typeof onConfigModelChanged === 'function') onConfigModelChanged();
}

/** Thanh trượt: hiện giá trị kèm %, `signed` thì thêm dấu + cho số dương (tốc độ đọc). */
function showRangeValue(targetId, value, signed) {
  const el = document.getElementById(targetId);
  if (el) el.textContent = `${signed && Number(value) >= 0 ? '+' : ''}${value}%`;
}

/** Ô mẫu báo cáo đổi → vẽ lại bản xem trước (nếu module mẫu đã nạp). */
function previewTemplateIfReady() {
  if (typeof renderTemplatePreview === 'function') renderTemplatePreview();
}

/** Enter trong ô từ khoá cấm → thêm vào chính sách. */
function onEnterAddPolicy(event) {
  if (event && event.key === 'Enter') addSecurityPolicyItem();
}

/** Enter trong ô hỏi nhanh của Trung tâm chỉ huy → gửi câu hỏi. */
function onEnterCommandCenterAsk(event) {
  if (event && event.key === 'Enter') runCommandCenterAsk();
}

/** Sang Bảng điều khiển và cuộn tới danh sách việc chờ duyệt (`listId`: id của danh sách, thường là dash-pending-list). */
function goToPendingApprovals(listId) {
  switchTab('dashboard');
  setTimeout(() => document.getElementById(listId || 'dash-pending-list')?.scrollIntoView({ behavior: 'smooth', block: 'center' }), 120);
}
