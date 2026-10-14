// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
import React from 'react';
import { EdgeProps, getSmoothStepPath } from 'reactflow';
import { X, AlertTriangle } from 'lucide-react';

export interface GlowingEdgeData {
  isActive?: boolean;
  activeColor?: string;
  isError?: boolean;
  errorMessage?: string;
  sourceLabel?: string;
  targetLabel?: string;
  label?: string;
  protocol?: string;
  direction?: string;
}

export function GlowingEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  style = {},
  data,
  markerEnd,
  markerStart,
}: EdgeProps<GlowingEdgeData>) {
  const [edgePath, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
    borderRadius: 16,
  });

  const isActive = data?.isActive;
  const isError = data?.isError;
  const activeColor = data?.activeColor || '#f97316'; // Neon Orange/Red
  const errorColor = '#f43f5e'; // Rose / Neon Red for Disconnected State
  const defaultColor = '#00f2fe'; // Cyber Neon Cyan

  const currentStrokeColor = isError ? errorColor : isActive ? activeColor : defaultColor;

  return (
    <>
      <defs>
        {/* Glow Filter for Active and Error Edges */}
        <filter id={`glow-${id}`} x="-20%" y="-20%" width="140%" height="140%">
          <feGaussianBlur stdDeviation={isError ? "5" : isActive ? "4" : "2"} result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>

        {/* Linear Gradient for Edge Flow */}
        <linearGradient id={`grad-${id}`} x1="0%" y1="0%" x2="100%" y2="0%">
          <stop
            offset="0%"
            stopColor={isError ? '#f43f5e' : isActive ? activeColor : defaultColor}
            stopOpacity={0.9}
          />
          <stop
            offset="100%"
            stopColor={isError ? '#e11d48' : isActive ? '#ef4444' : '#0284c7'}
            stopOpacity={0.9}
          />
        </linearGradient>
      </defs>

      {/* Background wider glow line */}
      <path
        id={`${id}-glow`}
        className="react-flow__edge-path"
        d={edgePath}
        fill="none"
        stroke={currentStrokeColor}
        strokeWidth={isError ? 8 : isActive ? 8 : 4}
        strokeOpacity={isError ? 0.75 : isActive ? 0.6 : 0.2}
        style={{
          filter: `url(#glow-${id})`,
          transition: 'all 0.3s ease',
        }}
      />

      {/* Main Sharp Core Line */}
      <path
        id={id}
        className={`react-flow__edge-path ${isError ? 'animate-pulse' : isActive ? 'animate-pulse' : ''}`}
        d={edgePath}
        fill="none"
        stroke={`url(#grad-${id})`}
        strokeWidth={isError ? 3.5 : isActive ? 3.5 : 2}
        strokeDasharray={isError ? '8 6' : isActive ? '8 4' : '6 4'}
        style={{
          ...style,
          strokeDashoffset: 0,
          animation: isError
            ? 'edgeFlowError 1.2s linear infinite'
            : isActive
            ? 'edgeFlowFast 0.6s linear infinite'
            : 'edgeFlow 2.5s linear infinite',
          transition: 'all 0.3s ease',
        }}
        markerEnd={markerEnd}
        markerStart={markerStart}
      />

      {/* Visual Error Badge on Disconnected Edge */}
      {isError && (
        <foreignObject
          width={180}
          height={48}
          x={labelX - 90}
          y={labelY - 24}
          className="overflow-visible pointer-events-none"
        >
          <div className="flex flex-col items-center justify-center">
            <span className="flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[11px] font-mono font-bold bg-rose-600/95 text-white shadow-[0_0_18px_#f43f5e] border border-rose-400 animate-bounce">
              <X className="w-3.5 h-3.5 stroke-[3]" />
              <span>MẤT KẾT NỐI</span>
            </span>
            {data?.label && (
              <span className="mt-1 px-2 py-0.5 text-[11px] font-mono font-semibold bg-rose-950/95 text-rose-300 border border-rose-500/60 rounded max-w-[170px] truncate shadow">
                {data.label}
              </span>
            )}
          </div>
        </foreignObject>
      )}

      {/* Real-time Flow Badge Indicator if Active (and not in error) */}
      {!isError && isActive && (
        <foreignObject
          width={200}
          height={48}
          x={labelX - 100}
          y={labelY - 24}
          className="overflow-visible pointer-events-none"
        >
          <div className="flex flex-col items-center justify-center">
            <span className="flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[11px] font-mono font-bold bg-gradient-to-r from-amber-500 to-orange-500 text-black shadow-[0_0_16px_#f97316] animate-bounce">
              <span className="w-2 h-2 rounded-full bg-black animate-ping"></span>
              <span>⚡ LIVE FLOW</span>
            </span>
            {data?.label && (
              <span className="mt-1 px-2 py-0.5 text-[11px] font-mono font-bold bg-slate-950/95 text-amber-300 border border-amber-500/60 rounded max-w-[190px] truncate shadow">
                {data.label}
              </span>
            )}
          </div>
        </foreignObject>
      )}

      {/* Normal State: Directional Send/Receive Cyber Pill Badge */}
      {!isError && !isActive && data?.label && (
        <foreignObject
          width={190}
          height={32}
          x={labelX - 95}
          y={labelY - 16}
          className="overflow-visible pointer-events-auto cursor-pointer"
        >
          <div className="flex items-center justify-center group" title={`Đường truyền: ${data.label} (${data.protocol || 'Real-time'})`}>
            <span className="flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[11px] font-mono font-semibold bg-slate-950/95 text-cyan-300 border border-cyan-500/40 shadow-[0_2px_12px_rgba(0,0,0,0.85)] group-hover:border-cyan-300 group-hover:text-cyan-200 group-hover:shadow-[0_0_12px_rgba(0,242,254,0.4)] transition-all max-w-[185px] truncate">
              <span className="text-[11px] font-bold text-cyan-400">➔</span>
              <span className="truncate">{data.label}</span>
            </span>
          </div>
        </foreignObject>
      )}

      <style jsx global>{`
        @keyframes edgeFlow {
          from {
            stroke-dashoffset: 24;
          }
          to {
            stroke-dashoffset: 0;
          }
        }
        @keyframes edgeFlowFast {
          from {
            stroke-dashoffset: 24;
          }
          to {
            stroke-dashoffset: 0;
          }
        }
        @keyframes edgeFlowError {
          from {
            stroke-dashoffset: 0;
          }
          to {
            stroke-dashoffset: 24;
          }
        }
      `}</style>
    </>
  );
}
