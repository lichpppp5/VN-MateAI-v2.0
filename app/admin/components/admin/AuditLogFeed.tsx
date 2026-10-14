// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
'use client';

import React from 'react';
import { cn } from '@/lib/utils';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { useDashboard } from '@/hooks/useDashboard';
import { RefreshCw, Activity, Inbox } from 'lucide-react';

const LEVEL_STYLE: Record<string, { icon: React.ElementType; cls: string }> = {
  ERROR: { icon: Activity, cls: 'border-rose-500/30 bg-rose-500/10 text-rose-300' },
  WARNING: { icon: Activity, cls: 'border-amber-500/30 bg-amber-500/10 text-amber-300' },
  INFO: { icon: Inbox, cls: 'border-cyan-500/30 bg-cyan-500/10 text-cyan-300' },
  DEBUG: { icon: Inbox, cls: 'border-slate-700 bg-slate-800/60 text-slate-400' },
};

const fmtTime = (iso?: string) => {
  if (!iso) return '--:--:--';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '--:--:--' : d.toTimeString().slice(0, 8);
};

/**
 * Nhật ký hoạt động — cùng nguồn `/api/v1/logs/recent` mà portal dùng cho
 * khung log, nên hai nơi không thể lệch nhau.
 */
export function AuditLogFeed({ maxLogs = 40 }: { maxLogs?: number }) {
  const { auditLogs, loading, refetch } = useDashboard();
  const rows = auditLogs.slice(0, maxLogs);

  return (
    <Card className="h-full">
      <CardHeader className="border-b border-slate-800 flex-row items-center justify-between space-y-0">
        <CardTitle className="text-cyan-300 flex items-center gap-2">
          <Activity className="h-5 w-5" />
          Nhật ký hoạt động
          <span className="text-xs font-normal text-slate-400 font-mono">({rows.length})</span>
        </CardTitle>
        <Button variant="ghost" size="sm" onClick={refetch} aria-label="Tải lại nhật ký">
          <RefreshCw className={cn('h-4 w-4', loading && 'animate-spin')} />
        </Button>
      </CardHeader>
      <CardContent className="p-0">
        {rows.length === 0 ? (
          <div className="p-10 text-center text-slate-400">
            <Inbox className="h-10 w-10 text-slate-700 mx-auto mb-3" />
            <p className="text-sm">
              {loading ? 'Đang tải nhật ký…' : 'Chưa có dòng nhật ký nào.'}
            </p>
          </div>
        ) : (
          <div className="max-h-[440px] overflow-y-auto divide-y divide-slate-800">
            {rows.map((log, i) => {
              const s = LEVEL_STYLE[log.level] ?? LEVEL_STYLE.DEBUG;
              const Icon = s.icon;
              return (
                <div key={`${log.timestamp}-${i}`} className="px-4 py-2.5 hover:bg-slate-900/50">
                  <div className="flex items-start gap-2.5">
                    <span className={cn('shrink-0 mt-0.5 p-1 rounded', s.cls)}>
                      <Icon className="w-3 h-3" />
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 flex-wrap text-[11px]">
                        <span className="font-mono text-cyan-400">{fmtTime(log.timestamp)}</span>
                        <span className="text-slate-400 font-mono">{log.logger}</span>
                        <span className={cn('px-1.5 py-px rounded font-medium', s.cls)}>
                          {log.level}
                        </span>
                      </div>
                      <p className="text-sm text-slate-200 mt-0.5 break-words">{log.message}</p>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
