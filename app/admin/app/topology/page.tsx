// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
'use client';

import React from 'react';
import dynamic from 'next/dynamic';
import { Loader2 } from 'lucide-react';

// Next.js Lazy Loading with ssr: false
const LiveTopology = dynamic(() => import('@/components/topology/LiveTopology'), {
  ssr: false,
  loading: () => (
    <div className="w-full h-screen bg-[#01060e] flex flex-col items-center justify-center border-0 text-cyan-400">
      <Loader2 className="w-10 h-10 animate-spin text-cyan-400 mb-4" />
      <span className="font-orbitron font-bold text-sm tracking-wider text-slate-300">
        ĐANG TẢI SƠ ĐỒ HỆ THỐNG...
      </span>
      <span className="text-xs font-mono text-slate-400 mt-1">
        Trạng thái thật + sự kiện thời gian thực
      </span>
    </div>
  ),
});

export default function TopologyRootPage() {
  return (
    <div className="w-full h-screen p-0 m-0 overflow-hidden bg-[#01060e]">
      {/* Sơ đồ giám sát thời gian thực */}
      <LiveTopology />
    </div>
  );
}
