// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
'use client';

/**
 * Ô thành phần theo phong cách giao diện topology ban đầu (thẻ bát giác neon,
 * huy hiệu "XXX // YYY", viên trạng thái nhấp nháy, lưới số đo, dấu X đỏ khi lỗi)
 * — nhưng mọi chữ / số đều lấy từ trạng thái THẬT do LiveTopology truyền vào.
 */

import React, { memo } from 'react';
import { Handle, NodeProps, Position } from 'reactflow';
import { Loader2, X } from 'lucide-react';

export type CyberStatus = 'ok' | 'degraded' | 'down' | 'off' | 'unknown';

export interface CyberNodeData {
  id: string;
  kind: string;
  label: string;
  status: CyberStatus;
  statusText: string;
  detail: string;
  since: string;
  badge: string;
  accent: string;               // màu nhấn theo loại thành phần (hex)
  icon: React.ElementType;
  metrics: [string, string][];  // tối đa 4 ô số đo
  glowColor?: string;           // đang có sự kiện đi qua
  running?: boolean;
  runs?: number;
  wide?: boolean;
}

const STATUS_DOT: Record<CyberStatus, string> = {
  ok: '#34d399', degraded: '#f59e0b', down: '#f43f5e', off: '#8b9ab0', unknown: '#a3b1c6',
};

const OCTAGON =
  'polygon(0% 14px, 14px 0%, calc(100% - 14px) 0%, 100% 14px, 100% calc(100% - 14px), calc(100% - 14px) 100%, 14px 100%, 0% calc(100% - 14px))';

export const CyberNode = memo(({ data, selected }: NodeProps<CyberNodeData>) => {
  const down = data.status === 'down';
  const warn = data.status === 'degraded';
  const off = data.status === 'off' || data.status === 'unknown';
  const glow = data.glowColor;
  const color = down ? '#f43f5e' : warn ? '#f59e0b' : off ? '#8b9ab0' : data.accent;
  const border = glow ?? (selected ? '#e2e8f0' : color);
  const Icon = data.icon;

  return (
    <div
      className="relative"
      style={{
        width: data.wide ? 330 : 290,
        transform: glow ? 'scale(1.04)' : undefined,
        transition: 'transform 0.3s',
        opacity: data.status === 'off' ? 0.6 : 1,
      }}
    >
      {down && (
        <div
          className="absolute -right-3 -top-3 z-30 flex h-8 w-8 items-center justify-center rounded-full border-2 border-white bg-rose-600 shadow-[0_0_20px_#f43f5e] animate-bounce"
          title={data.detail || 'Mất kết nối'}
        >
          <X className="h-5 w-5 stroke-[3.5] text-white" />
        </div>
      )}
      {!!data.runs && !down && (
        <div
          className="absolute -right-2.5 -top-2.5 z-30 flex h-6 min-w-[24px] items-center justify-center rounded-full border border-slate-950 px-1 text-[11px] font-mono font-bold text-slate-950"
          style={{ background: data.accent, boxShadow: `0 0 12px ${data.accent}` }}
          title="Số sự kiện trong 10 phút qua"
        >
          {data.runs}
        </div>
      )}
      <div
        className="relative rounded-2xl border-2 p-4 backdrop-blur-2xl transition-all duration-300"
        style={{
          clipPath: OCTAGON,
          borderColor: border,
          background: down
            ? 'linear-gradient(to bottom, rgba(76,5,25,0.9), rgba(2,6,23,0.95))'
            : 'rgba(2,9,23,0.95)',
          boxShadow: glow
            ? `0 0 45px ${glow}cc, inset 0 0 18px ${glow}55`
            : `0 0 28px ${color}40, inset 0 0 14px ${color}22`,
        }}
      >
        {/* Huy hiệu + trạng thái */}
        <div className="mb-3 flex items-center justify-between gap-2">
          <span
            className="truncate rounded border px-2 py-0.5 font-mono text-[11px] font-extrabold uppercase tracking-widest"
            style={{ color, borderColor: `${color}66`, background: `${color}1a` }}
          >
            {data.badge}
          </span>
          <span
            className="flex shrink-0 items-center gap-1.5 rounded-full border px-2 py-0.5"
            style={{ borderColor: down ? '#f43f5e' : '#1e293b', background: down ? 'rgba(76,5,25,0.9)' : '#0f172a' }}
          >
            {data.running ? (
              <Loader2 className="h-3 w-3 animate-spin text-sky-300" />
            ) : (
              <span className="relative flex h-2.5 w-2.5">
                {!off && <span className="absolute inline-flex h-full w-full animate-ping rounded-full opacity-75" style={{ background: STATUS_DOT[data.status] }} />}
                <span className="relative inline-flex h-2.5 w-2.5 rounded-full" style={{ background: STATUS_DOT[data.status] }} />
              </span>
            )}
            <span className="font-mono text-[11px] font-bold uppercase tracking-wider" style={{ color: data.running ? '#7dd3fc' : STATUS_DOT[data.status] }}>
              {data.running ? 'Đang xử lý' : data.statusText}
            </span>
          </span>
        </div>

        {/* Biểu tượng + tên */}
        <div className="flex items-center gap-3.5">
          <div
            className="relative flex shrink-0 items-center justify-center rounded-xl border-2 p-3 shadow-inner"
            style={{
              borderColor: `${color}80`,
              background: `linear-gradient(135deg, ${color}40, ${color}10)`,
              color,
            }}
          >
            <Icon className={`h-7 w-7 ${off ? '' : 'animate-pulse'}`} />
          </div>
          <div className="min-w-0">
            <h4 className="truncate font-orbitron text-[15px] font-extrabold tracking-wide" style={{ color: down ? '#fda4af' : '#e2e8f0' }} title={data.label}>
              {data.label}
            </h4>
            <p className={`mt-0.5 truncate font-mono text-[11px] ${down ? 'font-bold text-rose-400' : warn ? 'text-amber-300' : 'text-slate-400'}`} title={data.detail}>
              {data.detail || data.since}
            </p>
          </div>
        </div>

        {/* Số đo thật */}
        {data.metrics.length > 0 && (
          <div className="mt-3.5 grid grid-cols-2 gap-2 border-t pt-3 font-mono text-xs" style={{ borderColor: down ? '#9f123980' : '#1e293b' }}>
            {data.metrics.slice(0, 4).map(([k, v]) => (
              <div key={k} className="min-w-0 rounded-lg border p-2" style={{ borderColor: '#1e293bcc', background: 'rgba(15,23,42,0.8)' }}>
                <div className="truncate text-[11px] text-slate-400">{k}</div>
                <div className="truncate font-bold" style={{ color: down ? '#fda4af' : color }} title={v}>{v}</div>
              </div>
            ))}
          </div>
        )}
        {data.detail && data.since && (
          <div className="mt-2 truncate font-mono text-[11px] text-slate-400">{data.since}</div>
        )}
      </div>

      <Handle
        type="target"
        position={Position.Left}
        className="!h-3.5 !w-3.5 !border-2 !border-slate-950"
        style={{ background: color }}
      />
      <Handle
        type="source"
        position={Position.Right}
        className="!h-3.5 !w-3.5 !border-2 !border-slate-950"
        style={{ background: color }}
      />
    </div>
  );
});

CyberNode.displayName = 'CyberNode';
