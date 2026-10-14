// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
'use client';

import React from 'react';
import { cn } from '@/lib/utils';
import { Card, CardContent } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { useDashboard, formatUptime, loadColor } from '@/hooks/useDashboard';
import { RefreshCw, Cpu, MemoryStick, HardDrive, Activity, Server } from 'lucide-react';

/** Thanh đo có nhãn. Nhận null → "chờ kết nối" chứ không vẽ 0%. */
function Meter({
  label, value, unit, icon: Icon,
}: {
  label: string; value: number | null; unit: string; icon: React.ElementType;
}) {
  const known = value != null && Number.isFinite(value);
  return (
    <div>
      <div className="flex items-center justify-between mb-1.5">
        <span className="flex items-center gap-1.5 text-sm text-slate-400">
          <Icon className="w-3.5 h-3.5" />
          {label}
        </span>
        <span className={cn('font-mono text-sm', known ? 'text-cyan-300' : 'text-slate-400 italic')}>
          {known ? `${value}${unit}` : 'chờ kết nối'}
        </span>
      </div>
      <div className="h-2 bg-slate-800 rounded-full overflow-hidden">
        {known ? (
          <div
            className={cn('h-full rounded-full transition-all duration-500', loadColor(value))}
            style={{ width: `${Math.min(100, Math.max(0, value))}%` }}
          />
        ) : (
          <div className="h-full w-full rounded-full bg-slate-800" />
        )}
      </div>
    </div>
  );
}

export function SystemHealth() {
  const { stats } = useDashboard();
  if (!stats) return null;
  const hw = stats.hardware ?? ({} as SystemStatsHardware);

  return (
    <Card>
      <CardContent className="p-6">
        <h3 className="text-lg font-semibold text-cyan-300 mb-4 flex items-center gap-2">
          <Activity className="h-5 w-5" />
          Sức khoẻ hệ thống
        </h3>
        <div className="space-y-4">
          <Meter label="CPU" value={hw.cpu_percent ?? null} unit="%" icon={Cpu} />
          <Meter label="Bộ nhớ" value={hw.ram_percent ?? null} unit="%" icon={MemoryStick} />
          <Meter label="Ổ đĩa" value={hw.disk_percent ?? null} unit="%" icon={HardDrive} />
          <div className="flex items-center justify-between pt-1">
            <span className="flex items-center gap-1.5 text-sm text-slate-400">
              <Server className="h-3.5 w-3.5" />
              Thời gian chạy
            </span>
            <span className="font-mono text-sm text-cyan-300">
              {formatUptime(hw.uptime_seconds)}
            </span>
          </div>
          <p className="text-xs text-slate-400 pt-2 border-t border-slate-800">
            Dữ liệu từ <code className="text-cyan-400">/api/v1/system/stats</code> — cùng nguồn
            với bảng điều khiển của portal.
          </p>
        </div>
      </CardContent>
    </Card>
  );
}

type SystemStatsHardware = {
  cpu_percent?: number; ram_percent?: number; disk_percent?: number;
  ram_used_gb?: number; ram_total_gb?: number; uptime_seconds?: number;
};

export function DashboardStats() {
  const { stats, loading, error, refetch } = useDashboard();

  if (loading && !stats) {
    return (
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {[1, 2, 3, 4].map((i) => (
          <div key={i} className="h-28 rounded-xl bg-slate-800/50 border border-slate-800 animate-pulse" />
        ))}
      </div>
    );
  }

  if (!stats) {
    return (
      <div className="p-4 rounded-lg bg-rose-500/10 border border-rose-500/30 text-sm text-rose-300">
        Không tải được số liệu hệ thống{error ? ` — ${error}` : ''}
      </div>
    );
  }

  const cards = [
    { title: 'Kỹ năng đã nạp', value: stats.skills_count, unit: '', icon: Activity,
      color: 'text-cyan-400', bg: 'bg-cyan-500/10', bd: 'border-cyan-500/30' },
    { title: 'Tác vụ hôm nay', value: stats.tasks?.total ?? null, unit: '',
      sub: `${stats.tasks?.completed ?? 0} xong · ${stats.tasks?.pending ?? 0} chờ · ${stats.tasks?.issues ?? 0} lỗi`,
      icon: Activity, color: 'text-amber-400', bg: 'bg-amber-500/10', bd: 'border-amber-500/30' },
    { title: 'Client đang kết nối', value: stats.online_clients_count, unit: '',
      sub: `${stats.users_count} tài khoản · ${stats.audio_nodes_count} mạch âm thanh`,
      icon: Server, color: 'text-emerald-400', bg: 'bg-emerald-500/10', bd: 'border-emerald-500/30' },
  ];

  return (
    <div className="space-y-3">
      {error && (
        <div className="p-3 rounded-lg bg-amber-500/10 border border-amber-500/30 text-sm text-amber-300">
          {error} — phần còn lại vẫn hiển thị bình thường.
        </div>
      )}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {cards.map((c, i) => {
          const known = c.value != null;
          return (
            <Card key={i} className={cn('border-l-4', c.bd)}>
              <CardContent className="p-5">
                <div className="flex items-start justify-between">
                  <div className="min-w-0">
                    <p className="text-sm text-slate-400 font-medium">{c.title}</p>
                    <p className={cn('text-3xl font-bold mt-1', known ? 'text-cyan-300' : 'text-slate-400 italic text-2xl')}>
                      {known ? c.value : '—'}
                    </p>
                    {c.sub && <p className="text-xs text-slate-400 mt-1">{c.sub}</p>}
                  </div>
                  <div className={cn('p-2.5 rounded-lg shrink-0', c.bg)}>
                    <c.icon className={cn('w-5 h-5', c.color)} />
                  </div>
                </div>
              </CardContent>
            </Card>
          );
        })}
        <Card className="border-l-4 border-slate-700">
          <CardContent className="p-5 flex flex-col justify-between">
            <div>
              <p className="text-sm text-slate-400 font-medium">Làm mới</p>
              <p className="text-xs text-slate-400 mt-1">
                Số liệu cập nhật mỗi 15 giây.
              </p>
            </div>
            <Button variant="outline" size="sm" onClick={refetch} className="mt-3 self-start">
              <RefreshCw className={cn('h-3.5 w-3.5', loading && 'animate-spin')} />
              Tải lại
            </Button>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
