// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
/**
 * fmtServerTime — hiển thị mốc thời gian do máy chủ trả về theo GIỜ ĐỊA PHƯƠNG của người xem.
 *
 * Máy chủ có hai quy ước lưu (mỗi cột chỉ một kiểu — docs/architecture/current-system-map.md):
 *   - ISO có chữ T, KHÔNG kèm múi giờ ("2026-10-06T12:35:19.38")  -> giờ UTC
 *     (audit_logs, users, thiết bị, lịch sử cấu hình, uỷ quyền);
 *   - "YYYY-MM-DD HH:MM:SS" (dấu cách)                           -> giờ địa phương của máy chủ
 *     (sổ tác vụ, trace thoại);
 *   - chuỗi có hậu tố múi giờ (Z, +07:00) -> theo đúng hậu tố.
 * Trước đây nhiều chỗ cắt chuỗi UTC rồi hiện như giờ địa phương: lệch 7 giờ ở Việt Nam.
 *
 * fmtServerTime(value, { seconds = true, date = true }) -> "YYYY-MM-DD HH:MM[:SS]" | "—"
 * parseServerTime(value) -> Date | null
 */
(function (root) {
  'use strict';

  const TZ_SUFFIX = /(Z|[+-]\d\d:?\d\d)$/i;

  function parseServerTime(value) {
    if (value == null || value === '') return null;
    let s = String(value).trim();
    let d;
    if (/^\d{4}-\d\d-\d\dT/.test(s)) {
      d = new Date(TZ_SUFFIX.test(s) ? s : `${s}Z`);           // ISO không múi giờ = UTC
    } else if (/^\d{4}-\d\d-\d\d \d\d:\d\d/.test(s)) {
      const [datePart, timePart] = s.split(' ');
      const [y, mo, da] = datePart.split('-').map(Number);
      const [h, mi, se] = timePart.split(':').map((x) => Number(String(x).slice(0, 2)));
      d = new Date(y, mo - 1, da, h, mi, se || 0);              // giờ địa phương
    } else {
      return null;
    }
    return Number.isNaN(d.getTime()) ? null : d;
  }

  function fmtServerTime(value, opts) {
    const o = opts || {};
    const d = parseServerTime(value);
    if (!d) return value == null || value === '' ? '—' : String(value);
    const p = (n) => String(n).padStart(2, '0');
    const datePart = `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
    const timePart = `${p(d.getHours())}:${p(d.getMinutes())}` + (o.seconds === false ? '' : `:${p(d.getSeconds())}`);
    return o.date === false ? timePart : `${datePart} ${timePart}`;
  }

  root.parseServerTime = parseServerTime;
  root.fmtServerTime = fmtServerTime;
})(typeof window !== 'undefined' ? window : globalThis);
