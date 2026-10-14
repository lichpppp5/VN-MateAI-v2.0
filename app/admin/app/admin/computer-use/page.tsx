// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
'use client';

import React from 'react';
import dynamic from 'next/dynamic';
import { Loader2 } from 'lucide-react';

const ComputerUseDashboard = dynamic(
  () => import('@/components/computer-use/ComputerUseDashboard'),
  {
    ssr: false,
    loading: () => (
      <div className="w-full h-screen bg-[#020813] flex flex-col items-center justify-center text-cyan-400">
        <Loader2 className="w-10 h-10 animate-spin text-cyan-400 mb-4" />
        <span className="font-orbitron font-bold text-sm tracking-wider text-slate-300">
          ĐANG KẾT NỐI WORKER CLUSTER...
        </span>
        <span className="text-xs font-mono text-slate-400 mt-1">
          Khởi tạo môi trường điều khiển GUI & Self-Healing Engine
        </span>
      </div>
    ),
  }
);

export default function ComputerUsePage() {
  return (
    <div className="w-full -m-6 lg:-m-8 p-0 bg-[#020813]">
      <ComputerUseDashboard />
    </div>
  );
}
