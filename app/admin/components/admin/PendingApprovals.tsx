// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
'use client';

import React from 'react';
import { cn } from '@/lib/utils';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card';
import { useDashboard } from '@/hooks/useDashboard';
import { ShieldCheck, CircleAlert } from 'lucide-react';

/**
 * Phê duyệt đang chờ — cùng nguồn `/api/v1/enterprise/hitl/pending` với
 * khung "Phê duyệt chờ" của portal.
 *
 * Không có nút Duyệt/Hủy ở đây: backend chưa có endpoint ghi quyết định, và
 * cơ chế phê duyệt thật đang chạy qua HITL + Telegram. Hiện nút bấm được mà
 * không có việc gì xảy ra thì tệ hơn là không có nút — nên ở đây chỉ liệt
 * kê cho biết có gì đang chờ, kèm lý do nếu rỗng.
 */
export function PendingApprovals() {
  const { approvals, loading } = useDashboard();

  return (
    <Card className="h-full">
      <CardHeader className="border-b border-slate-800">
        <CardTitle className="text-cyan-300 flex items-center gap-2">
          <ShieldCheck className="h-5 w-5" />
          Phê duyệt đang chờ
          {approvals.length > 0 && (
            <span className="px-2 py-0.5 text-xs font-mono bg-amber-500/15 text-amber-300 rounded">
              {approvals.length}
            </span>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="p-0">
        {approvals.length === 0 ? (
          <div className="p-8 text-center">
            <ShieldCheck className="h-10 w-10 text-emerald-500/40 mx-auto mb-3" />
            <p className="text-cyan-300 font-medium">
              {loading ? 'Đang kiểm tra…' : 'Không có yêu cầu nào đang chờ'}
            </p>
            <p className="text-xs text-slate-400 mt-1.5">
              Nguồn: <code className="text-cyan-400">/api/v1/enterprise/hitl/pending</code>
            </p>
          </div>
        ) : (
          <div className="divide-y divide-slate-800 max-h-[280px] overflow-y-auto">
            {approvals.map((a, i) => (
              <div key={a.id ?? i} className="px-4 py-3">
                <div className="flex items-center gap-2 flex-wrap">
                  <CircleAlert className="w-4 h-4 text-amber-400 shrink-0" />
                  <span className="text-sm text-cyan-300 font-medium">
                    {String(a.tool ?? a.id ?? 'Yêu cầu')}
                  </span>
                  {a.risk_level != null && (
                    <span className="px-1.5 py-px text-[11px] rounded bg-rose-500/15 text-rose-300">
                      Rủi ro {String(a.risk_level)}
                    </span>
                  )}
                </div>
                {a.reason != null && (
                  <p className="text-sm text-slate-300 mt-1.5 break-words">
                    {String(a.reason)}
                  </p>
                )}
                {a.created_at != null && (
                  <p className="text-[11px] text-slate-400 mt-1 font-mono">
                    {fmt(a.created_at as string)}
                  </p>
                )}
              </div>
            ))}
            <div className="px-4 py-2.5 bg-slate-900/60">
              <p className="text-[11px] text-amber-300/80">
                Chưa có nút Duyệt/Hủy: backend chưa có endpoint ghi quyết định.
                Phê duyệt thật đang chạy qua kênh HITL/Telegram.
              </p>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

const fmt = (iso: string) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString('vi-VN');
};
