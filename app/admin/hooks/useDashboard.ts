// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
'use client';

import { useState, useEffect, useCallback } from 'react';
import {
  api,
  type SystemStats,
  type AuditLog,
  type ApprovalRequest,
} from '@/lib/api';

/**
 * Dữ liệu cho trang Command Center của Admin.
 *
 * Phase 78: bản đầu gọi /dashboard/stats, /dashboard/audit-logs,
 * /dashboard/approvals, /workers — không endpoint nào tồn tại, cả 4 trả 404,
 * nên trang trắng hoàn toàn. Nay dùng lại đúng các endpoint portal đang gọi.
 *
 * Không có WebSocket ở đây: portal đã có luồng log thật qua
 * /api/v1/logs/recent và đẩy qua ws://…/ws/logs. Admin poll cùng nguồn đó
 * thay vì mở thêm một kênh real-time trùng chức năng.
 */
export function useDashboard() {
  const [stats, setStats] = useState<SystemStats | null>(null);
  const [auditLogs, setAuditLogs] = useState<AuditLog[]>([]);
  const [approvals, setApprovals] = useState<ApprovalRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchAll = useCallback(async () => {
    try {
      setLoading(true);
      // Promise.allSplash: một nguồn lỗi không được làm mất hai nguồn còn lại.
      const [s, l, a] = await Promise.allSettled([
        api.getDashboardStats(),
        api.getAuditLogs(60),
        api.getPendingApprovals(),
      ]);
      if (s.status === 'fulfilled') setStats(s.value);
      if (l.status === 'fulfilled') setAuditLogs(l.value);
      if (a.status === 'fulfilled') setApprovals(a.value);

      const failed = [s, l, a].filter((r) => r.status === 'rejected').length;
      setError(failed ? `${failed}/3 nguồn dữ liệu không tải được` : null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
    // Số liệu tổng quan chậm; log và phê duyệt cần cập nhật nhanh hơn.
    const t1 = setInterval(fetchAll, 15000);
    return () => clearInterval(t1);
  }, [fetchAll]);

  return {
    stats,
    auditLogs,
    approvals,
    loading,
    error,
    refetch: fetchAll,
  };
}

/**
 * Danh sách "worker ngoại vi" (Work Node, Ubuntu Gateway, Robot ESP32...).
 *
 * KHÔNG có endpoint nào trả danh sách này — hệ thống chưa đăng ký cụm máy
 * nào. Briefing có vẽ "🟢 17/17 Work Nodes Online" nhưng con số đó không có
 * nguồn, nên ở đây trả về mảng rỗng kèm lý do, để giao diện hiện "chưa có
 * nguồn dữ liệu" thay vì bịa 17/17.
 */
export function useWorkers() {
  const [workers] = useState<never[]>([]);
  const [reason] = useState<string>(
    'Hệ thống chưa đăng ký cụm máy ngoại vi nào — chưa có nguồn dữ liệu để hiển thị.',
  );
  const [loading] = useState(false);
  const refetch = useCallback(() => {}, []);

  return { workers, loading, error: null, reason, refetch };
}

/** Thời gian hoạt động (giây) → chuỗi dễ đọc. */
export function formatUptime(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return '—';
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d} ngày ${h} giờ`;
  if (h > 0) return `${h} giờ ${m} phút`;
  return `${m} phút`;
}

/** Màu theo mức độ sử dụng: <60% xanh, <80% hổ phách, >=80% hồng. */
export function loadColor(pct: number | null | undefined): string {
  if (pct == null) return 'bg-slate-600';
  if (pct >= 80) return 'bg-rose-500';
  if (pct >= 60) return 'bg-amber-500';
  return 'bg-emerald-500';
}
