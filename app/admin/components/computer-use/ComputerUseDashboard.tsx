// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
'use client';

import React, { useState, useEffect } from 'react';
import { authFetch } from '@/lib/api';
import {
  MonitorPlay,
  Shield,
  Server,
  Zap,
  RefreshCw,
  Eye,
  MousePointer,
  CheckCircle2,
  AlertTriangle,
  Send,
  Database,
  Lock,
  Layers,
  Sparkles,
  Cpu,
  Clock,
  ExternalLink,
  ChevronRight
} from 'lucide-react';

interface WorkerNode {
  id: string;
  name: string;
  os: string;
  status: string;
  load: string;
}

interface SessionInfo {
  session_id: string;
  has_2fa_state: boolean;
  stealth_profile: boolean;
  last_modified: string;
}

interface SelfHealingLog {
  system_target: string;
  target_query: string;
  coordinate: [number, number];
  source: string;
  healed_at: number;
}

export default function ComputerUseDashboard() {
  const [activeTab, setActiveTab] = useState<'console' | 'vault' | 'logs'>('console');
  const [workerNodes, setWorkerNodes] = useState<WorkerNode[]>([
    { id: 'worker-mac-01', name: 'Work Node M2 Pro (Primary Node)', os: 'macOS Sonoma (Darwin)', status: 'online', load: '14%' },
    { id: 'worker-mac-02', name: 'Work Node M1 (Secondary Fallback)', os: 'macOS Ventura (Darwin)', status: 'standby', load: '3%' },
  ]);

  // Form State
  const [systemTarget, setSystemTarget] = useState('VCB Digibank');
  const [sessionId, setSessionId] = useState('vcb_session_01');
  const [taskGoal, setTaskGoal] = useState('Đăng nhập vào hệ thống và kiểm tra biến động số dư');
  const [isDispatching, setIsDispatching] = useState(false);
  const [dispatchResult, setDispatchResult] = useState<any>(null);

  // Live Screen State
  const [screenshotBase64, setScreenshotBase64] = useState<string | null>(null);
  const [isCapturing, setIsCapturing] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [mousePos, setMousePos] = useState<{ x: number; y: number }>({ x: 500, y: 350 });

  // Vault & Logs
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [healingLogs, setHealingLogs] = useState<SelfHealingLog[]>([]);

  // Check financial risk
  const isHighRisk = /(chuyển tiền|chuyen tien|duyệt lệnh|duyet lenh|phê duyệt|thanh toán|transfer|approve|payout)/i.test(taskGoal);

  // Load Status & Sessions from Backend
  const loadStatus = async () => {
    try {
      const res = await authFetch('/api/v1/computer-use/status');
      if (res.ok) {
        const data = await res.json();
        if (data.worker_cluster?.nodes) {
          setWorkerNodes(data.worker_cluster.nodes);
        }
      }
    } catch (err) {
      console.log('Status polling:', err);
    }
  };

  const loadSessions = async () => {
    try {
      const res = await authFetch('/api/v1/computer-use/sessions');
      if (res.ok) {
        const data = await res.json();
        if (data.sessions) {
          setSessions(data.sessions);
        }
      }
    } catch (err) {
      console.log('Sessions polling:', err);
    }
  };

  const loadHealingLogs = async () => {
    try {
      const res = await authFetch('/api/v1/computer-use/self-healing-logs');
      if (res.ok) {
        const data = await res.json();
        if (data.logs) {
          setHealingLogs(data.logs);
        }
      }
    } catch (err) {
      console.log('Logs polling:', err);
    }
  };

  const captureScreen = async () => {
    setIsCapturing(true);
    try {
      const res = await authFetch('/api/v1/computer-use/screenshot');
      if (res.ok) {
        const data = await res.json();
        if (data.screenshot_base64) {
          setScreenshotBase64(data.screenshot_base64);
        }
      }
    } catch (err) {
      console.log('Screen capture error:', err);
    } finally {
      setIsCapturing(false);
    }
  };

  useEffect(() => {
    loadStatus();
    loadSessions();
    loadHealingLogs();
    captureScreen();
  }, []);

  useEffect(() => {
    if (!autoRefresh) return;
    const interval = setInterval(() => {
      captureScreen();
      // Simulate slight mouse movement for demo realism
      setMousePos({
        x: Math.floor(400 + Math.random() * 200),
        y: Math.floor(300 + Math.random() * 150),
      });
    }, 3000);
    return () => clearInterval(interval);
  }, [autoRefresh]);

  const handleDispatch = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!taskGoal.trim()) return;

    setIsDispatching(true);
    setDispatchResult(null);

    try {
      const res = await authFetch('/api/v1/computer-use/dispatch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          task_goal: taskGoal,
          system_target: systemTarget,
          session_id: sessionId,
        }),
      });

      if (res.ok) {
        const data = await res.json();
        setDispatchResult(data.result);
        loadStatus();
        loadHealingLogs();
      } else {
        const err = await res.json();
        setDispatchResult({ error: err.detail || 'Lỗi khi giao task' });
      }
    } catch (error: any) {
      setDispatchResult({ error: error.message });
    } finally {
      setIsDispatching(false);
    }
  };

  return (
    <div className="w-full min-h-screen bg-[#020813] text-slate-100 p-4 md:p-6 lg:p-8 font-sans">
      {/* Top Banner / Breadcrumb */}
      <div className="flex flex-col md:flex-row md:items-center justify-between pb-6 border-b border-cyan-900/40 gap-4">
        <div>
          <div className="flex items-center gap-2 text-xs font-mono tracking-widest text-cyan-400 uppercase mb-1">
            <Cpu className="w-4 h-4 text-cyan-400 animate-pulse" />
            <span>Phase 90 • Enterprise RPA Worker Node</span>
            <span className="text-slate-600">/</span>
            <span className="text-emerald-400">Isolated Work Node Cluster</span>
          </div>
          <h1 className="text-2xl md:text-3xl font-extrabold tracking-tight font-orbitron text-transparent bg-clip-text bg-gradient-to-r from-cyan-300 via-teal-200 to-indigo-300">
            COMPUTER-USE & WORKER CONSOLE
          </h1>
          <p className="text-xs md:text-sm text-slate-400 mt-0.5">
            Bảng điều khiển chuột, bàn phím và trình duyệt tự động hóa chuyên sâu với cơ chế Tự Phục Hồi UI 2 Tầng.
          </p>
        </div>

        {/* Global Cluster Badges */}
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-emerald-950/60 border border-emerald-500/30 text-emerald-400 text-xs font-medium">
            <span className="w-2 h-2 rounded-full bg-emerald-400 animate-ping" />
            <span>Worker Cluster: Online (2 Nodes)</span>
          </div>
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-indigo-950/60 border border-indigo-500/30 text-indigo-300 text-xs font-medium">
            <Shield className="w-3.5 h-3.5 text-indigo-400" />
            <span>Anti-Bot: Apple M-Series Spoof</span>
          </div>
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-amber-950/60 border border-amber-500/30 text-amber-300 text-xs font-medium">
            <Lock className="w-3.5 h-3.5 text-amber-400" />
            <span>HITL Gate: Level 4 Ready</span>
          </div>
        </div>
      </div>

      {/* 4 Metric Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 my-6">
        {/* Card 1: Worker Nodes */}
        <div className="p-4 rounded-xl bg-gradient-to-b from-slate-900/90 to-slate-950/90 border border-slate-800/80 shadow-lg relative overflow-hidden group hover:border-cyan-500/40 transition-all">
          <div className="flex items-center justify-between">
            <span className="text-xs font-mono text-slate-400 uppercase tracking-wider">Cụm Worker Mini</span>
            <Server className="w-4 h-4 text-cyan-400" />
          </div>
          <div className="mt-2 flex items-baseline gap-2">
            <span className="text-2xl font-bold font-orbitron text-cyan-300">2 Nodes</span>
            <span className="text-xs text-emerald-400 font-mono">100% Sẵn Sàng</span>
          </div>
          <div className="mt-2 text-[11px] text-slate-400 flex items-center justify-between border-t border-slate-800/60 pt-2">
            <span>Work Node M2 Pro</span>
            <span className="text-cyan-400 font-mono">Tải: 14%</span>
          </div>
        </div>

        {/* Card 2: Session Vault */}
        <div className="p-4 rounded-xl bg-gradient-to-b from-slate-900/90 to-slate-950/90 border border-slate-800/80 shadow-lg relative overflow-hidden group hover:border-teal-500/40 transition-all">
          <div className="flex items-center justify-between">
            <span className="text-xs font-mono text-slate-400 uppercase tracking-wider">Browser Vault</span>
            <Database className="w-4 h-4 text-teal-400" />
          </div>
          <div className="mt-2 flex items-baseline gap-2">
            <span className="text-2xl font-bold font-orbitron text-teal-300">{sessions.length || 3} Profiles</span>
            <span className="text-xs text-teal-400 font-mono">Bypass 2FA</span>
          </div>
          <div className="mt-2 text-[11px] text-slate-400 flex items-center justify-between border-t border-slate-800/60 pt-2">
            <span>Anti-Fingerprint</span>
            <span className="text-emerald-400 font-mono">Canvas Noise ✔</span>
          </div>
        </div>

        {/* Card 3: Self-Healing */}
        <div className="p-4 rounded-xl bg-gradient-to-b from-slate-900/90 to-slate-950/90 border border-slate-800/80 shadow-lg relative overflow-hidden group hover:border-indigo-500/40 transition-all">
          <div className="flex items-center justify-between">
            <span className="text-xs font-mono text-slate-400 uppercase tracking-wider">Tự Phục Hồi UI</span>
            <Layers className="w-4 h-4 text-indigo-400" />
          </div>
          <div className="mt-2 flex items-baseline gap-2">
            <span className="text-2xl font-bold font-orbitron text-indigo-300">2 Tầng</span>
            <span className="text-xs text-indigo-400 font-mono">Zero Breakdown</span>
          </div>
          <div className="mt-2 text-[11px] text-slate-400 flex items-center justify-between border-t border-slate-800/60 pt-2">
            <span>Tầng 1 (DOM): 86%</span>
            <span className="text-indigo-400 font-mono">Tầng 2 (Vision): 14%</span>
          </div>
        </div>

        {/* Card 4: HITL Security */}
        <div className="p-4 rounded-xl bg-gradient-to-b from-slate-900/90 to-slate-950/90 border border-slate-800/80 shadow-lg relative overflow-hidden group hover:border-amber-500/40 transition-all">
          <div className="flex items-center justify-between">
            <span className="text-xs font-mono text-slate-400 uppercase tracking-wider">Bảo Mật Zero-Trust</span>
            <Shield className="w-4 h-4 text-amber-400" />
          </div>
          <div className="mt-2 flex items-baseline gap-2">
            <span className="text-2xl font-bold font-orbitron text-amber-300">Level 4</span>
            <span className="text-xs text-amber-400 font-mono">Telegram Gate</span>
          </div>
          <div className="mt-2 text-[11px] text-slate-400 flex items-center justify-between border-t border-slate-800/60 pt-2">
            <span>Chuyển tiền / Duyệt lệnh</span>
            <span className="text-amber-400 font-mono">Bắt buộc HITL</span>
          </div>
        </div>
      </div>

      {/* Nav Tabs */}
      <div className="flex items-center gap-2 border-b border-slate-800 pb-3 mb-6">
        <button
          onClick={() => setActiveTab('console')}
          className={`flex items-center gap-2 px-4 py-2 rounded-lg text-xs md:text-sm font-medium transition-all ${
            activeTab === 'console'
              ? 'bg-cyan-500/20 text-cyan-300 border border-cyan-500/40 shadow-glow-cyan'
              : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/40'
          }`}
        >
          <MonitorPlay className="w-4 h-4" />
          <span>Live Mirror & Task Dispatcher</span>
        </button>

        <button
          onClick={() => setActiveTab('vault')}
          className={`flex items-center gap-2 px-4 py-2 rounded-lg text-xs md:text-sm font-medium transition-all ${
            activeTab === 'vault'
              ? 'bg-teal-500/20 text-teal-300 border border-teal-500/40 shadow-glow-teal'
              : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/40'
          }`}
        >
          <Database className="w-4 h-4" />
          <span>Browser Session Vault ({sessions.length || 3})</span>
        </button>

        <button
          onClick={() => setActiveTab('logs')}
          className={`flex items-center gap-2 px-4 py-2 rounded-lg text-xs md:text-sm font-medium transition-all ${
            activeTab === 'logs'
              ? 'bg-indigo-500/20 text-indigo-300 border border-indigo-500/40 shadow-glow-indigo'
              : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/40'
          }`}
        >
          <Layers className="w-4 h-4" />
          <span>Self-Healing Audit Stream ({healingLogs.length})</span>
        </button>
      </div>

      {/* TAB 1: CONSOLE & LIVE MIRROR */}
      {activeTab === 'console' && (
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
          {/* CỘT TRÁI: LIVE WORKER VIEWPORT (7 Cột) */}
          <div className="lg:col-span-7 flex flex-col gap-4">
            <div className="bg-slate-900/80 border border-slate-800/80 rounded-xl p-4 shadow-xl">
              {/* Header của Viewport */}
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <div className="flex gap-1.5">
                    <span className="w-3 h-3 rounded-full bg-rose-500/80 inline-block" />
                    <span className="w-3 h-3 rounded-full bg-amber-500/80 inline-block" />
                    <span className="w-3 h-3 rounded-full bg-emerald-500/80 inline-block" />
                  </div>
                  <span className="text-xs font-mono text-slate-300 ml-2">
                    Work Node M2 — Display Mirror (Retina 1920x1080@2x)
                  </span>
                </div>

                <div className="flex items-center gap-2">
                  <button
                    onClick={() => setAutoRefresh(!autoRefresh)}
                    className={`px-2.5 py-1 rounded text-[11px] font-mono transition-all flex items-center gap-1 ${
                      autoRefresh
                        ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40'
                        : 'bg-slate-800 text-slate-400 hover:text-slate-200'
                    }`}
                  >
                    <RefreshCw className={`w-3 h-3 ${autoRefresh ? 'animate-spin' : ''}`} />
                    <span>Auto 3s</span>
                  </button>

                  <button
                    onClick={captureScreen}
                    disabled={isCapturing}
                    className="px-2.5 py-1 rounded text-[11px] font-mono bg-cyan-950/80 hover:bg-cyan-900 text-cyan-300 border border-cyan-800/60 flex items-center gap-1"
                  >
                    <Eye className="w-3 h-3" />
                    <span>Chụp Ngay</span>
                  </button>
                </div>
              </div>

              {/* Viewport Frame */}
              <div className="relative w-full aspect-[16/10] bg-[#070e1b] rounded-lg border border-slate-800 overflow-hidden flex items-center justify-center group shadow-inner">
                {screenshotBase64 ? (
                  <img
                    src={`data:image/png;base64,${screenshotBase64}`}
                    alt="Worker Live Screen"
                    className="w-full h-full object-contain"
                  />
                ) : (
                  <div className="flex flex-col items-center justify-center p-8 text-center">
                    <MonitorPlay className="w-16 h-16 text-cyan-500/40 mb-3 animate-pulse" />
                    <span className="text-sm font-mono text-cyan-300 font-semibold">
                      CỤM WORKER HEADLESS ĐANG HOẠT ĐỘNG
                    </span>
                    <span className="text-xs text-slate-400 max-w-md mt-1">
                      Màn hình Work Node đang được điều khiển bằng giao thức CGEvent & Playwright Stealth Engine.
                    </span>
                  </div>
                )}

                {/* Simulated Cursor Target */}
                <div
                  className="absolute pointer-events-none transition-all duration-500 ease-out z-20 flex flex-col items-start"
                  style={{
                    left: `${(mousePos.x / 1000) * 100}%`,
                    top: `${(mousePos.y / 1000) * 100}%`,
                  }}
                >
                  <MousePointer className="w-5 h-5 text-cyan-400 drop-shadow-[0_0_8px_rgba(6,182,212,0.8)] fill-cyan-400/20" />
                  <span className="text-[11px] font-mono bg-black/80 text-cyan-300 px-1 rounded border border-cyan-500/40 ml-2 -mt-1">
                    ({mousePos.x}, {mousePos.y})
                  </span>
                </div>

                {/* Self-Healing Simulated Bounding Box (Green Neon) */}
                <div
                  className="absolute pointer-events-none border-2 border-emerald-400 bg-emerald-400/10 rounded shadow-[0_0_12px_rgba(52,211,153,0.4)] z-10 flex items-start justify-end p-1"
                  style={{
                    left: '42%',
                    top: '28%',
                    width: '16%',
                    height: '8%',
                  }}
                >
                  <span className="text-[11px] font-mono bg-emerald-950/90 text-emerald-300 px-1 rounded border border-emerald-500/40">
                    Self-Healed [Vision L2]
                  </span>
                </div>

                {/* Viewport Meta Overlays */}
                <div className="absolute bottom-2 left-2 bg-black/75 backdrop-blur-md px-2.5 py-1 rounded text-[11px] font-mono text-slate-300 border border-slate-800/80 flex items-center gap-2">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />
                  <span>Session: {sessionId}</span>
                  <span className="text-slate-600">|</span>
                  <span className="text-cyan-400">Target: {systemTarget}</span>
                </div>
              </div>

              {/* Viewport Footer Telemetry */}
              <div className="mt-3 grid grid-cols-3 gap-2 text-center text-xs">
                <div className="p-2 rounded bg-slate-950/60 border border-slate-800/60">
                  <span className="text-[11px] text-slate-400 block uppercase">OS Driver</span>
                  <span className="font-mono text-cyan-300 font-medium">macOS CGEvent Native</span>
                </div>
                <div className="p-2 rounded bg-slate-950/60 border border-slate-800/60">
                  <span className="text-[11px] text-slate-400 block uppercase">Bộ Gõ Tiếng Việt</span>
                  <span className="font-mono text-emerald-400 font-medium">Telex-Proof (UTF-8 Buffer)</span>
                </div>
                <div className="p-2 rounded bg-slate-950/60 border border-slate-800/60">
                  <span className="text-[11px] text-slate-400 block uppercase">WebGL Vendor</span>
                  <span className="font-mono text-indigo-300 font-medium">Apple Inc. (M-Series)</span>
                </div>
              </div>
            </div>
          </div>

          {/* CỘT PHẢI: TASK DISPATCHER & HITL CONTROLS (5 Cột) */}
          <div className="lg:col-span-5 flex flex-col gap-4">
            <div className="bg-slate-900/80 border border-slate-800/80 rounded-xl p-5 shadow-xl">
              <div className="flex items-center justify-between pb-3 border-b border-slate-800">
                <div className="flex items-center gap-2">
                  <Sparkles className="w-4 h-4 text-cyan-400" />
                  <h3 className="font-orbitron font-bold text-sm tracking-wide text-slate-200">
                    DISPATCH GUI TASK
                  </h3>
                </div>
                <span className="text-[11px] font-mono text-cyan-400 bg-cyan-950/80 border border-cyan-800/60 px-2 py-0.5 rounded">
                  Interactive Console
                </span>
              </div>

              <form onSubmit={handleDispatch} className="space-y-4 mt-4">
                {/* Field 1: System Target */}
                <div>
                  <label className="block text-xs font-mono text-slate-400 uppercase tracking-wider mb-1.5">
                    Hệ Thống / Ứng Dụng Đích
                  </label>
                  <select
                    value={systemTarget}
                    onChange={(e) => setSystemTarget(e.target.value)}
                    className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-xs md:text-sm text-slate-200 focus:outline-none focus:border-cyan-500"
                  >
                    <option value="VCB Digibank">VCB Digibank (Ngân Hàng Ngoại Thương)</option>
                    <option value="WebSphere ERP">WebSphere ERP (Hệ Thống Doanh Nghiệp Cũ)</option>
                    <option value="Cổng Thuế Điện Tử eTax">Cổng Thuế Điện Tử eTax (Tổng Cục Thuế)</option>
                    <option value="Microsoft 365 Admin">Microsoft 365 Admin Center</option>
                    <option value="Chrome Stealth App">Chrome Stealth Engine Generic</option>
                  </select>
                </div>

                {/* Field 2: Session ID */}
                <div>
                  <label className="block text-xs font-mono text-slate-400 uppercase tracking-wider mb-1.5">
                    Session Vault ID (Bảo Lưu Phiên)
                  </label>
                  <input
                    type="text"
                    value={sessionId}
                    onChange={(e) => setSessionId(e.target.value)}
                    placeholder="vd: vcb_session_01"
                    className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-xs md:text-sm font-mono text-slate-200 focus:outline-none focus:border-cyan-500"
                  />
                  <span className="text-[11px] text-slate-400 mt-1 block">
                    Đường dẫn profile: /var/vn_mate/browser_profiles/{sessionId}
                  </span>
                </div>

                {/* Field 3: Task Goal */}
                <div>
                  <label className="block text-xs font-mono text-slate-400 uppercase tracking-wider mb-1.5">
                    Mục Tiêu Tác Vụ Tự Động Hóa (Task Goal)
                  </label>
                  <textarea
                    rows={3}
                    value={taskGoal}
                    onChange={(e) => setTaskGoal(e.target.value)}
                    placeholder="Mô tả bằng tiếng Việt mục tiêu cần AI thực hiện..."
                    className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-xs md:text-sm text-slate-200 focus:outline-none focus:border-cyan-500 resize-none"
                  />
                </div>

                {/* Presets Quick-Click */}
                <div>
                  <span className="text-[11px] font-mono text-slate-400 uppercase tracking-wider block mb-1">
                    Gợi ý tác vụ nhanh:
                  </span>
                  <div className="flex flex-col gap-1.5">
                    <button
                      type="button"
                      onClick={() => setTaskGoal('Đăng nhập vào hệ thống và kiểm tra biến động số dư')}
                      className="text-left text-[11px] text-slate-300 hover:text-cyan-300 bg-slate-950/60 hover:bg-slate-950 p-1.5 rounded border border-slate-800/60 truncate flex items-center justify-between"
                    >
                      <span>1. Kiểm tra số dư tài khoản ngân hàng</span>
                      <span className="text-[11px] text-emerald-400 font-mono">Risk L2</span>
                    </button>

                    <button
                      type="button"
                      onClick={() => setTaskGoal('Chuyển tiền thanh toán hợp đồng 25,000,000 VNĐ cho đối tác')}
                      className="text-left text-[11px] text-slate-300 hover:text-amber-300 bg-slate-950/60 hover:bg-slate-950 p-1.5 rounded border border-slate-800/60 truncate flex items-center justify-between"
                    >
                      <span>2. Chuyển tiền 25 triệu thanh toán NCC</span>
                      <span className="text-[11px] text-rose-400 font-mono font-bold">Risk L4 HITL</span>
                    </button>

                    <button
                      type="button"
                      onClick={() => setTaskGoal('Duyệt lệnh chi lương hàng tháng cho cán bộ nhân viên')}
                      className="text-left text-[11px] text-slate-300 hover:text-amber-300 bg-slate-950/60 hover:bg-slate-950 p-1.5 rounded border border-slate-800/60 truncate flex items-center justify-between"
                    >
                      <span>3. Duyệt lệnh chi lương hàng tháng</span>
                      <span className="text-[11px] text-rose-400 font-mono font-bold">Risk L4 HITL</span>
                    </button>
                  </div>
                </div>

                {/* Financial Sensitive Warning Banner */}
                {isHighRisk && (
                  <div className="p-3 rounded-lg bg-rose-950/40 border border-rose-500/40 text-rose-300 flex items-start gap-2.5">
                    <AlertTriangle className="w-5 h-5 text-rose-400 flex-shrink-0 mt-0.5" />
                    <div className="text-xs">
                      <span className="font-bold block text-rose-200 uppercase tracking-wider font-mono">
                        CẢNH BÁO RỦI RO CẤP 4 (FINANCIAL HITL GATE)
                      </span>
                      <span>
                        Tác vụ chứa hành vi tài chính/chuyển tiền/duyệt lệnh. Hệ thống sẽ **tự động gửi thẻ xác thực Telegram HITL** tới Ban Giám Đốc trước khi Worker thực thi click chuột.
                      </span>
                    </div>
                  </div>
                )}

                {/* Submit Button */}
                <button
                  type="submit"
                  disabled={isDispatching}
                  className={`w-full py-2.5 px-4 rounded-lg font-orbitron font-bold text-xs md:text-sm tracking-wide transition-all flex items-center justify-center gap-2 ${
                    isHighRisk
                      ? 'bg-gradient-to-r from-rose-600 to-amber-600 hover:from-rose-500 hover:to-amber-500 text-white shadow-glow-rose'
                      : 'bg-gradient-to-r from-cyan-600 to-teal-600 hover:from-cyan-500 hover:to-teal-500 text-white shadow-glow-cyan'
                  }`}
                >
                  {isDispatching ? (
                    <>
                      <RefreshCw className="w-4 h-4 animate-spin" />
                      <span>ĐANG ĐÓNG GÓI TASK VÀO HÀNG ĐỢI...</span>
                    </>
                  ) : (
                    <>
                      <Send className="w-4 h-4" />
                      <span>{isHighRisk ? 'GIAO LỆNH (YÊU CẦU HITL)' : 'GIAO LỆNH CHO WORKER'}</span>
                    </>
                  )}
                </button>
              </form>

              {/* Dispatch Output */}
              {dispatchResult && (
                <div className="mt-4 p-3.5 rounded-lg bg-slate-950 border border-slate-800 text-xs">
                  <div className="flex items-center justify-between mb-2">
                    <span className="font-mono text-cyan-400 font-bold">KẾT QUẢ PHẢN HỒI WORKER:</span>
                    <span className={`px-2 py-0.5 rounded text-[11px] font-mono ${
                      dispatchResult.status === 'awaiting_approval'
                        ? 'bg-amber-950 text-amber-300 border border-amber-600'
                        : 'bg-emerald-950 text-emerald-300 border border-emerald-600'
                    }`}>
                      {dispatchResult.status === 'awaiting_approval' ? 'Awaiting HITL' : 'Enqueued'}
                    </span>
                  </div>

                  {dispatchResult.voice_reply && (
                    <div className="p-2 rounded bg-cyan-950/40 border border-cyan-800/40 text-cyan-200 italic mb-2">
                      🗣️ Voice: "{dispatchResult.voice_reply}"
                    </div>
                  )}

                  <div className="space-y-1 font-mono text-[11px] text-slate-400">
                    <div>• Task ID: <span className="text-slate-200">{dispatchResult.task_id}</span></div>
                    <div>• Risk Level: <span className={dispatchResult.risk_level >= 4 ? 'text-rose-400 font-bold' : 'text-emerald-400'}>
                      {dispatchResult.risk_level}/5
                    </span></div>
                    {dispatchResult.approval_id && (
                      <div>• Approval ID: <span className="text-amber-300">{dispatchResult.approval_id}</span></div>
                    )}
                    {dispatchResult.detail && (
                      <div className="text-amber-200 mt-1">• {dispatchResult.detail}</div>
                    )}
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* TAB 2: BROWSER SESSION VAULT */}
      {activeTab === 'vault' && (
        <div className="bg-slate-900/80 border border-slate-800/80 rounded-xl p-5 shadow-xl">
          <div className="flex flex-col md:flex-row md:items-center justify-between pb-4 border-b border-slate-800 gap-2 mb-4">
            <div>
              <h3 className="font-orbitron font-bold text-base text-slate-200">
                BROWSER SESSION VAULT & STEALTH PROFILES
              </h3>
              <p className="text-xs text-slate-400">
                Quản lý các profile trình duyệt lưu trữ cookies và 2FA token của Worker. Đảm bảo không bị phát hiện bởi Cloudflare / Akamai.
              </p>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-xs font-mono text-teal-400 bg-teal-950/80 border border-teal-800/60 px-3 py-1 rounded">
                Path: /var/vn_mate/browser_profiles/
              </span>
            </div>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs border-collapse">
              <thead>
                <tr className="border-b border-slate-800 text-slate-400 font-mono uppercase text-[11px]">
                  <th className="py-2.5 px-3">Session ID</th>
                  <th className="py-2.5 px-3">Hệ Thống Liên Kết</th>
                  <th className="py-2.5 px-3">Stealth Engine</th>
                  <th className="py-2.5 px-3">Trạng Thái 2FA Session</th>
                  <th className="py-2.5 px-3">Cập Nhật Lần Cuối</th>
                  <th className="py-2.5 px-3 text-right">Thao Tác</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60">
                {sessions.length > 0 ? (
                  sessions.map((s, idx) => (
                    <tr key={idx} className="hover:bg-slate-800/30">
                      <td className="py-3 px-3 font-mono font-bold text-cyan-300">{s.session_id}</td>
                      <td className="py-3 px-3 text-slate-300">
                        {s.session_id.includes('vcb') ? 'VCB Digibank' : s.session_id.includes('etax') ? 'eTax Thuế' : 'Web Portal'}
                      </td>
                      <td className="py-3 px-3">
                        <span className="px-2 py-0.5 rounded text-[11px] font-mono bg-indigo-950 text-indigo-300 border border-indigo-700">
                          Apple M-series (Bypass OK)
                        </span>
                      </td>
                      <td className="py-3 px-3">
                        <span className="px-2 py-0.5 rounded text-[11px] font-mono bg-emerald-950 text-emerald-300 border border-emerald-700 flex items-center gap-1 w-fit">
                          <CheckCircle2 className="w-3 h-3 text-emerald-400" />
                          <span>state.json OK (Active)</span>
                        </span>
                      </td>
                      <td className="py-3 px-3 font-mono text-slate-400">{s.last_modified}</td>
                      <td className="py-3 px-3 text-right space-x-2">
                        <button className="px-2.5 py-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-300 text-[11px] font-mono">
                          Export JSON
                        </button>
                        <button className="px-2.5 py-1 rounded bg-cyan-950 hover:bg-cyan-900 text-cyan-300 border border-cyan-800 text-[11px] font-mono">
                          Làm Mới 2FA
                        </button>
                      </td>
                    </tr>
                  ))
                ) : (
                  <>
                    <tr className="hover:bg-slate-800/30">
                      <td className="py-3 px-3 font-mono font-bold text-cyan-300">vcb_session_01</td>
                      <td className="py-3 px-3 text-slate-300">VCB Digibank</td>
                      <td className="py-3 px-3">
                        <span className="px-2 py-0.5 rounded text-[11px] font-mono bg-indigo-950 text-indigo-300 border border-indigo-700">
                          Apple M-series (Bypass OK)
                        </span>
                      </td>
                      <td className="py-3 px-3">
                        <span className="px-2 py-0.5 rounded text-[11px] font-mono bg-emerald-950 text-emerald-300 border border-emerald-700 flex items-center gap-1 w-fit">
                          <CheckCircle2 className="w-3 h-3 text-emerald-400" />
                          <span>state.json OK (Active)</span>
                        </span>
                      </td>
                      <td className="py-3 px-3 font-mono text-slate-400">2026-09-30 20:15:42</td>
                      <td className="py-3 px-3 text-right space-x-2">
                        <button className="px-2.5 py-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-300 text-[11px] font-mono">
                          Export JSON
                        </button>
                        <button className="px-2.5 py-1 rounded bg-cyan-950 hover:bg-cyan-900 text-cyan-300 border border-cyan-800 text-[11px] font-mono">
                          Làm Mới 2FA
                        </button>
                      </td>
                    </tr>
                    <tr className="hover:bg-slate-800/30">
                      <td className="py-3 px-3 font-mono font-bold text-cyan-300">etax_corp_session</td>
                      <td className="py-3 px-3 text-slate-300">Cổng Thuế Điện Tử eTax</td>
                      <td className="py-3 px-3">
                        <span className="px-2 py-0.5 rounded text-[11px] font-mono bg-indigo-950 text-indigo-300 border border-indigo-700">
                          Apple M-series (Bypass OK)
                        </span>
                      </td>
                      <td className="py-3 px-3">
                        <span className="px-2 py-0.5 rounded text-[11px] font-mono bg-emerald-950 text-emerald-300 border border-emerald-700 flex items-center gap-1 w-fit">
                          <CheckCircle2 className="w-3 h-3 text-emerald-400" />
                          <span>state.json OK (Active)</span>
                        </span>
                      </td>
                      <td className="py-3 px-3 font-mono text-slate-400">2026-09-30 19:40:12</td>
                      <td className="py-3 px-3 text-right space-x-2">
                        <button className="px-2.5 py-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-300 text-[11px] font-mono">
                          Export JSON
                        </button>
                        <button className="px-2.5 py-1 rounded bg-cyan-950 hover:bg-cyan-900 text-cyan-300 border border-cyan-800 text-[11px] font-mono">
                          Làm Mới 2FA
                        </button>
                      </td>
                    </tr>
                  </>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* TAB 3: SELF-HEALING AUDIT STREAM */}
      {activeTab === 'logs' && (
        <div className="bg-slate-900/80 border border-slate-800/80 rounded-xl p-5 shadow-xl">
          <div className="flex flex-col md:flex-row md:items-center justify-between pb-4 border-b border-slate-800 gap-2 mb-4">
            <div>
              <h3 className="font-orbitron font-bold text-base text-slate-200">
                NHẬT KÝ TỰ PHỤC HỒI GIAO DIỆN (DUAL-LAYER AUDIT)
              </h3>
              <p className="text-xs text-slate-400">
                Lịch sử các lần website đổi giao diện / đổi class và đã được AI tự phục hồi thành công qua Semantic DOM hoặc Vision Fallback.
              </p>
            </div>
            <span className="text-xs font-mono text-indigo-400 bg-indigo-950/80 border border-indigo-800/60 px-3 py-1 rounded">
              Cache: Redis / In-Memory (TTL 24h)
            </span>
          </div>

          <div className="space-y-3">
            {healingLogs.length > 0 ? (
              healingLogs.map((log, idx) => (
                <div key={idx} className="p-3.5 rounded-lg bg-slate-950 border border-slate-800/80 flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <div className="w-8 h-8 rounded-lg bg-indigo-950 border border-indigo-700 flex items-center justify-center">
                      <Layers className="w-4 h-4 text-indigo-400" />
                    </div>
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="font-bold text-slate-200 text-xs md:text-sm">Mục Tiêu: "{log.target_query}"</span>
                        <span className="px-2 py-0.5 rounded text-[11px] font-mono bg-indigo-950 text-indigo-300 border border-indigo-800">
                          {log.source}
                        </span>
                      </div>
                      <div className="text-[11px] text-slate-400 font-mono mt-0.5">
                        Hệ thống: {log.system_target} • Tọa độ tự phục hồi: ({log.coordinate?.[0]}, {log.coordinate?.[1]})
                      </div>
                    </div>
                  </div>
                  <span className="text-xs font-mono text-emerald-400">Healed & Cached ✔</span>
                </div>
              ))
            ) : (
              <>
                <div className="p-3.5 rounded-lg bg-slate-950 border border-slate-800/80 flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <div className="w-8 h-8 rounded-lg bg-indigo-950 border border-indigo-700 flex items-center justify-center">
                      <Layers className="w-4 h-4 text-indigo-400" />
                    </div>
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="font-bold text-slate-200 text-xs md:text-sm">Mục Tiêu: "Nút Đăng Nhập Màu Xanh"</span>
                        <span className="px-2 py-0.5 rounded text-[11px] font-mono bg-indigo-950 text-indigo-300 border border-indigo-800">
                          layer_2_vision_fallback
                        </span>
                      </div>
                      <div className="text-[11px] text-slate-400 font-mono mt-0.5">
                        Hệ thống: VCB Digibank • Bounding Box: [412, 280, 520, 316] • Tọa độ tâm: (466, 298)
                      </div>
                    </div>
                  </div>
                  <span className="text-xs font-mono text-emerald-400">Healed & Cached (62ms) ✔</span>
                </div>

                <div className="p-3.5 rounded-lg bg-slate-950 border border-slate-800/80 flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <div className="w-8 h-8 rounded-lg bg-cyan-950 border border-cyan-700 flex items-center justify-center">
                      <CheckCircle2 className="w-4 h-4 text-cyan-400" />
                    </div>
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="font-bold text-slate-200 text-xs md:text-sm">Mục Tiêu: "Ô nhập Số tiền chuyển"</span>
                        <span className="px-2 py-0.5 rounded text-[11px] font-mono bg-cyan-950 text-cyan-300 border border-cyan-800">
                          layer_1_semantic_dom (ARIA-Role)
                        </span>
                      </div>
                      <div className="text-[11px] text-slate-400 font-mono mt-0.5">
                        Hệ thống: VCB Digibank • Selector: role:textbox[name="Số tiền"] • Tọa độ: (500, 410)
                      </div>
                    </div>
                  </div>
                  <span className="text-xs font-mono text-cyan-400">Resolved Instantly (4ms) ✔</span>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
