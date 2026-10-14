// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
'use client';

/**
 * Sơ đồ giám sát thời gian thực (docs/realtime/topology-plan.md).
 *
 * - Ô = thành phần thật từ `GET /api/v1/system/topology`, trạng thái
 *   ok / degraded / down / off / unknown kèm số đo và lý do; không có số đo
 *   thì hiện "chưa có số đo", không điền số giả.
 * - `/ws/topology`: "snapshot" (2 s/lần) cập nhật trạng thái, "step" là từng
 *   bước xử lý thật (lượt thoại, tool, phê duyệt, robot) -> sáng cạnh tương ứng
 *   và thêm vào "Luồng trực tiếp".
 * - Mất kết nối WS: tự nối lại, trong lúc đó hỏi REST mỗi 5 s (không đứng hình).
 * - Mô phỏng chỉ chạy CỤC BỘ trên trình duyệt này, gắn nhãn MÔ PHỎNG.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ReactFlow, {
  Background,
  BackgroundVariant,
  Controls,
  Edge,
  Handle,
  MarkerType,
  MiniMap,
  Node,
  NodeProps,
  Position,
  ReactFlowInstance,
  applyNodeChanges,
  NodeChange,
} from 'reactflow';
import 'reactflow/dist/style.css';
import {
  Activity, AlertTriangle, Bot, Cpu, Database, Gauge, Headphones, MessageSquare, Monitor,
  Plug, RotateCcw, Save, Server, ShieldCheck, Volume2, Wrench, Wifi, WifiOff, Play,
  Cloud, Layers, FileText, Receipt, Send, Network,
  Radar, Mail, CalendarClock, ListChecks, BookOpen, Brain, Users, Webhook, RadioTower, MemoryStick,
  BellRing, Hash, Inbox, Send as SendIcon,
} from 'lucide-react';
import { authFetch, sessionToken } from '@/lib/api';
import { CyberNode, CyberNodeData } from './CyberNode';
import { GlowingEdge } from './GlowingEdge';

// ── Kiểu dữ liệu từ máy chủ ─────────────────────────────────────────────────
type Status = 'ok' | 'degraded' | 'down' | 'off' | 'unknown';

interface TopoNode {
  id: string;
  kind: string;
  label: string;
  status: Status;
  detail: string;
  metrics: Record<string, unknown>;
  group: string;
  since: number;
}

interface TopoEdge { id: string; source: string; target: string }

interface TopoEvent {
  seq: number;
  kind: string;
  status: string;
  ts: string;
  t: number;
  node?: string;
  source?: string;
  target?: string;
  stage?: string;
  trace_id?: string;
  channel?: string;
  ms?: number;
  detail?: string;
  simulated?: boolean;
}

interface Snapshot {
  nodes: TopoNode[];
  edges: TopoEdge[];
  counts: Record<string, number>;
  timestamp: string;
  layout?: Record<string, { x: number; y: number }>;
}

// ── Hiển thị ────────────────────────────────────────────────────────────────
const STATUS_TEXT: Record<Status, string> = {
  ok: 'Hoạt động', degraded: 'Suy giảm', down: 'Lỗi', off: 'Tắt', unknown: 'Chưa rõ',
};
const STATUS_COLOR: Record<Status, string> = {
  ok: '#22c55e', degraded: '#f59e0b', down: '#ef4444', off: '#64748b', unknown: '#94a3b8',
};
const EVENT_COLOR: Record<string, string> = {
  running: '#38bdf8', ok: '#22c55e', waiting: '#f59e0b', error: '#ef4444',
  down: '#ef4444', degraded: '#f59e0b', cancelled: '#94a3b8',
};
const STAGE_TEXT: Record<string, string> = {
  start: 'bắt đầu', router: 'định tuyến', ack_audio: 'câu xác nhận', first_text: 'LLM trả câu đầu',
  first_answer_audio: 'tiếng trả lời đầu', end: 'kết thúc', request: 'chờ phê duyệt',
  approved: 'đã duyệt', wake: 'nghe thấy tên gọi', stt: 'nhận dạng giọng nói',
  listen: 'đang lắng nghe', farewell: 'tạm biệt',
  alert_out: 'gửi cảnh báo', ticket: 'tạo ticket', received: 'nhận webhook', duplicate: 'trùng, bỏ qua',
  alert: 'phát hiện sự cố', resolved: 'đã khôi phục', audit: 'rà soát đôn đốc',
  dispatch: 'bắt đầu gửi cảnh báo', notify: 'gửi tới kênh', dispatched: 'kết quả gửi',
};
const KIND_TEXT: Record<string, string> = {
  turn: 'Lượt hội thoại', tool: 'Tool', approval: 'Phê duyệt', robot: 'Robot',
  status: 'Đổi trạng thái', simulation: 'Mô phỏng', alert: 'Cảnh báo Telegram', email: 'Email',
  webhook: 'Webhook', incident: 'Sự cố (Sentinel)', job: 'Tác vụ nền', schedule: 'Lịch tự động',
};
const METRIC_TEXT: Record<string, string> = {
  cpu_percent: 'CPU %', ram_percent: 'RAM %', disk_percent: 'Đĩa %', uptime: 'Thời gian chạy',
  latency_ms: 'Độ trễ (ms)', model: 'Model', models_cooling: 'Model tạm bỏ qua', turns: 'Số lượt',
  last_outcome: 'Kết quả gần nhất', last_ttfa_ms: 'Tiếng đầu (ms)', last_ttl_ms: 'Tổng lượt (ms)',
  backend: 'Bộ nhận dạng', last_ms: 'Lần gần nhất (ms)', connections: 'Kết nối',
  voice_sessions: 'Phiên thoại', ip: 'IP', state: 'Trạng thái', emotion: 'Biểu cảm', busy: 'Đang xử lý',
  follow_up: 'Nghe tiếp (mức)', firmware: 'Firmware', last_active: 'Hoạt động cuối', skills: 'Số kỹ năng',
  pending: 'Đang chờ', items: 'Danh sách', platform: 'Hệ điều hành',
  interval_s: 'Chu kỳ quét (s)', active_incidents: 'Sự cố đang mở', last_alert: 'Cảnh báo gần nhất',
  schedule: 'Lịch chạy', runs: 'Số lần chạy', last_run: 'Lần chạy gần nhất', last_result: 'Kết quả gần nhất',
  running: 'Đang chạy', completed: 'Đã xong', failed: 'Lỗi', max_concurrent: 'Song song tối đa',
  inbox: 'Hộp thư', tickets: 'Ticket đã tạo', last_ticket: 'Ticket gần nhất',
  received: 'Đã nhận', duplicates: 'Trùng (bỏ qua)', last: 'Gần nhất', last_source: 'Nguồn gần nhất',
  port: 'Cổng UDP', employees: 'Nhân viên', computers: 'Máy tính', last_sync: 'Đồng bộ gần nhất',
  chunks: 'Đoạn tài liệu', graph_entities: 'Thực thể (Graph)', graph_relations: 'Quan hệ (Graph)',
  records: 'Bản ghi', agents: 'Tác tử', interactions: 'Lượt trao đổi', active_items: 'Mục trong RAM',
  channels_ready: 'Kênh đã kết nối', min_severity: 'Mức tối thiểu', watch_topology: 'Theo dõi sơ đồ',
  sent: 'Đã gửi',
  sessions: 'Phiên', agent_version: 'Phiên bản Agent', heartbeat_age_s: 'Nhịp tim (s trước)',
};

const KIND_ICON: Record<string, React.ElementType> = {
  server: Server, llm: Cpu, database: Database, channel: MessageSquare, pipeline: Activity,
  stt: Headphones, tts: Volume2, robot: Bot, tools: Wrench, approval: ShieldCheck,
  worker: Monitor, connector: Plug,
  sentinel: Radar, email: Mail, scheduler: CalendarClock, jobs: ListChecks, knowledge: BookOpen,
  memory: Brain, directory: Users, webhook: Webhook, beacon: RadioTower, agents: Network, cache: MemoryStick,
  alerts: BellRing, notify: SendIcon,
};

/**
 * Cột mặc định theo hướng luồng xử lý (trái -> phải, kiểu n8n):
 * kênh vào -> nhận dạng giọng -> lõi hội thoại -> LLM / TTS / cổng tool -> nơi thực thi -> CSDL.
 */
function defaultColumn(n: TopoNode): number {
  if (n.group === 'channel') return 0;                                   // kênh vào + email + webhook
  if (['stt', 'beacon', 'sentinel', 'scheduler'].includes(n.id)) return 1; // nghe + dịch vụ tự chạy
  if (n.id === 'voice' || n.id === 'cache') return 2;
  if (n.id === 'llm' || n.id === 'tts' || n.id === 'tools') return 3;
  if (n.id === 'alerts') return 4;                                        // khâu cảnh báo chung
  if (n.kind === 'notify') return 5;                                       // kênh gửi cảnh báo ra
  if (n.kind === 'connector') return 6;
  if (['db', 'ad', 'rag', 'memory'].includes(n.id)) return 7;              // dữ liệu & tri thức
  return 4;   // core, hitl, máy trạm, đa tác tử, tác vụ nền
}

const COL_W = 420;
const ROW_H = 290;

function defaultPositions(nodes: TopoNode[]): Record<string, { x: number; y: number }> {
  const cols: Record<number, string[]> = {};
  for (const n of nodes) (cols[defaultColumn(n)] ??= []).push(n.id);
  const out: Record<string, { x: number; y: number }> = {};
  for (const [col, ids] of Object.entries(cols)) {
    ids.forEach((id, row) => {
      out[id] = { x: Number(col) * COL_W, y: (row - (ids.length - 1) / 2) * ROW_H };
    });
  }
  return out;
}

function fmtValue(v: unknown): string {
  if (v === null || v === undefined || v === '') return '—';
  if (typeof v === 'boolean') return v ? 'có' : 'không';
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : v.toFixed(1);
  if (Array.isArray(v)) return v.length ? v.map((x) => (typeof x === 'object' ? JSON.stringify(x) : String(x))).join(', ') : '—';
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

function fmtSince(t: number): string {
  if (!t) return '';
  const d = new Date(t * 1000);
  const ago = Math.max(0, Math.round(Date.now() / 1000 - t));
  const rel = ago < 60 ? `${ago}s` : ago < 3600 ? `${Math.floor(ago / 60)} phút` : `${Math.floor(ago / 3600)} giờ`;
  return `từ ${d.toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' })} (${rel})`;
}

function eventText(e: TopoEvent): string {
  const stage = e.stage ? STAGE_TEXT[e.stage] ?? e.stage : '';
  const path = e.source && e.target ? `${e.source} → ${e.target}` : e.node ?? '';
  return [KIND_TEXT[e.kind] ?? e.kind, stage, path].filter(Boolean).join(' · ');
}

// ── Ô thành phần ────────────────────────────────────────────────────────────
type NodeData = CyberNodeData;

/** Huy hiệu + màu nhấn theo loại thành phần (giữ phong cách giao diện ban đầu). */
const KIND_STYLE: Record<string, [string, string]> = {
  server: ['CORE BRAIN // MÁY CHỦ', '#00f2fe'],
  llm: ['AI DISPATCH GATEWAY', '#818cf8'],
  pipeline: ['VOICE PIPELINE // LÕI', '#a855f7'],
  stt: ['SPEECH → TEXT', '#e879f9'],
  tts: ['TEXT → SPEECH', '#f472b6'],
  channel: ['KÊNH VÀO', '#38bdf8'],
  robot: ['ROBOT // ESP32-S3', '#10b981'],
  tools: ['TOOL GATEWAY // ZERO-TRUST', '#f59e0b'],
  approval: ['HITL // PHÊ DUYỆT', '#fb923c'],
  worker: ['WORKER NODE // LAN', '#10b981'],
  database: ['DATABASE // SQLITE', '#3b82f6'],
  connector: ['EXTERNAL CONNECTOR', '#2dd4bf'],
  sentinel: ['AUTONOMOUS SENTINEL', '#f43f5e'],
  scheduler: ['VIRTUAL C.O.O // LỊCH', '#eab308'],
  jobs: ['BACKGROUND WORKERS', '#a3e635'],
  email: ['EMAIL GATEWAY // IMAP', '#60a5fa'],
  webhook: ['WEBHOOK GATEWAY', '#c084fc'],
  beacon: ['UDP DISCOVERY // 8888', '#34d399'],
  directory: ['ACTIVE DIRECTORY / LDAP', '#6366f1'],
  knowledge: ['ENTERPRISE RAG // GRAPH', '#22d3ee'],
  memory: ['COGNITIVE MEMORY', '#d946ef'],
  agents: ['MULTI-AGENT // C-SUITE', '#a855f7'],
  cache: ['EPHEMERAL CACHE // RAM', '#94a3b8'],
  alerts: ['ALERT HUB // ĐA KÊNH', '#f97316'],
  notify: ['KÊNH CẢNH BÁO', '#fb7185'],
};
const ID_STYLE: Record<string, [string, string, React.ElementType]> = {
  hud: ['KÊNH // HUD', '#38bdf8', Monitor],
  portal: ['KÊNH // WEB PORTAL', '#22d3ee', MessageSquare],
  telegram: ['TELEGRAM BOT GATEWAY', '#0ea5e9', Send],
  'connector:aws': ['AWS CLOUD', '#f59e0b', Cloud],
  'connector:oci': ['ORACLE OCI', '#ef4444', Layers],
  'connector:paperless': ['PAPERLESS-NGX', '#14b8a6', FileText],
  'connector:einvoice': ['E-INVOICE VN', '#10b981', Receipt],
  'notify:teams': ['MICROSOFT TEAMS', '#6264a7', MessageSquare],
  'notify:email': ['EMAIL // SMTP', '#38bdf8', Mail],
  'notify:outlook': ['OUTLOOK // M365', '#0078d4', Inbox],
  'notify:slack': ['SLACK', '#e01e5a', Hash],
  'notify:webhook': ['WEBHOOK RA', '#c084fc', Webhook],
};
const METRIC_PICK: Record<string, string[]> = {
  server: ['cpu_percent', 'ram_percent', 'disk_percent', 'uptime'],
  llm: ['latency_ms', 'model', 'models_cooling'],
  pipeline: ['turns', 'last_outcome', 'last_ttfa_ms', 'last_ttl_ms'],
  stt: ['backend', 'last_ms'], tts: ['last_ms'],
  channel: ['connections', 'voice_sessions'],
  robot: ['state', 'ip', 'emotion', 'follow_up'],
  tools: ['skills'], approval: ['pending'],
  worker: ['cpu_percent', 'ram_percent', 'disk_percent', 'agent_version'],
  sentinel: ['active_incidents', 'interval_s', 'last_alert'],
  scheduler: ['schedule', 'runs', 'last_run', 'last_result'],
  jobs: ['running', 'pending', 'completed', 'failed'],
  email: ['inbox', 'tickets', 'last_ticket'],
  webhook: ['received', 'duplicates', 'last', 'last_source'],
  beacon: ['port'],
  directory: ['employees', 'computers', 'last_sync'],
  knowledge: ['chunks', 'graph_entities', 'graph_relations'],
  memory: ['records'],
  agents: ['agents', 'interactions'],
  cache: ['active_items', 'sessions'],
  alerts: ['channels_ready', 'min_severity', 'last_alert'],
  notify: ['sent', 'failed', 'last'],
};

function toCyber(n: TopoNode, glowColor: string | undefined, runs: number | undefined): CyberNodeData {
  const [badge, accent] = ID_STYLE[n.id] ?? KIND_STYLE[n.kind] ?? ['MODULE', '#00f2fe'];
  const icon = ID_STYLE[n.id]?.[2] ?? KIND_ICON[n.kind] ?? Gauge;
  const m = n.metrics || {};
  const metrics = (METRIC_PICK[n.kind] ?? [])
    .filter((k) => m[k] !== undefined && m[k] !== null && !(Array.isArray(m[k]) && !(m[k] as unknown[]).length))
    .map((k) => [METRIC_TEXT[k] ?? k, fmtValue(m[k])] as [string, string]);
  return {
    id: n.id, kind: n.kind, label: n.label, status: n.status, statusText: STATUS_TEXT[n.status] ?? n.status,
    detail: n.detail, since: fmtSince(n.since), badge, accent, icon, metrics,
    glowColor, running: glowColor === EVENT_COLOR.running, runs, wide: n.id === 'core' || n.id === 'voice',
  };
}

const nodeTypes = { cyber: CyberNode };

/** Ô trên sơ đồ -> id kênh cho API gửi thử ('' = mọi kênh đã kết nối). */
const ALERT_CHANNEL_OF: Record<string, string> = {
  alerts: '', telegram: 'telegram', 'notify:teams': 'alert_teams', 'notify:email': 'alert_email',
  'notify:outlook': 'alert_outlook', 'notify:slack': 'alert_slack', 'notify:webhook': 'alert_webhook',
};
const edgeTypes = { glowing: GlowingEdge };

// ── Trang ───────────────────────────────────────────────────────────────────
const EDGE_GLOW_MS = 2500;
const MAX_EVENTS = 300;

type Tab = 'flow' | 'incidents' | 'detail';

export default function LiveTopology() {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [events, setEvents] = useState<TopoEvent[]>([]);
  const [wsState, setWsState] = useState<'connecting' | 'live' | 'down'>('connecting');
  const [lastUpdate, setLastUpdate] = useState<number>(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>('flow');
  const [glow, setGlow] = useState<Record<string, { color: string; until: number; label?: string }>>({});
  const [nodes, setNodes] = useState<Node<NodeData>[]>([]);
  const [rf, setRf] = useState<ReactFlowInstance | null>(null);
  const fitted = useRef(false);
  const [error, setError] = useState<string>('');
  const [notice, setNotice] = useState<string>('');
  const [, setTick] = useState(0);
  const lastSeq = useRef(0);
  const savedLayout = useRef<Record<string, { x: number; y: number }>>({});

  // Sự kiện mới -> danh sách + làm sáng cạnh/ô liên quan.
  const addEvents = useCallback((evs: TopoEvent[]) => {
    const fresh = evs.filter((e) => e.seq > lastSeq.current || e.simulated);
    if (!fresh.length) return;
    for (const e of fresh) if (!e.simulated) lastSeq.current = Math.max(lastSeq.current, e.seq);
    setEvents((prev) => [...prev, ...fresh].slice(-MAX_EVENTS));
    const now = Date.now();
    setGlow((prev) => {
      const next = { ...prev };
      for (const e of fresh) {
        if (now - e.t * 1000 > EDGE_GLOW_MS && !e.simulated) continue;   // lịch sử cũ: không nhấp nháy
        const color = EVENT_COLOR[e.status] ?? '#38bdf8';
        if (e.source && e.target) {
          const label = e.ms !== undefined && e.stage !== 'start' ? `${Math.round(e.ms)} ms` : (e.stage ? STAGE_TEXT[e.stage] ?? e.stage : undefined);
          next[`${e.source}->${e.target}`] = { color, until: now + EDGE_GLOW_MS, label };
          next[`node:${e.target}`] = { color, until: now + EDGE_GLOW_MS };
        }
        if (e.node) next[`node:${e.node}`] = { color, until: now + EDGE_GLOW_MS };
      }
      return next;
    });
  }, []);

  const applySnapshot = useCallback((s: Snapshot) => {
    setSnap(s);
    setLastUpdate(Date.now());
    if (s.layout) savedLayout.current = s.layout;
  }, []);

  // Tải lần đầu + dự phòng REST khi WS rớt.
  const loadRest = useCallback(async () => {
    try {
      const res = await authFetch('/api/v1/system/topology');
      if (res.status === 401 || res.status === 403) {
        setError(res.status === 403 ? 'Tài khoản không có quyền xem sơ đồ (cần quản lý hoặc admin).' : 'Phiên đăng nhập hết hạn.');
        return;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      applySnapshot(await res.json());
      const er = await authFetch(`/api/v1/system/topology/events?since=${lastSeq.current}&limit=300`);
      if (er.ok) addEvents((await er.json()).events ?? []);
      setError('');
    } catch (e) {
      setError(`Không tải được trạng thái: ${(e as Error).message}`);
    }
  }, [addEvents, applySnapshot]);

  useEffect(() => { loadRest(); }, [loadRest]);

  // WebSocket + tự nối lại.
  useEffect(() => {
    let socket: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;
    let poll: ReturnType<typeof setInterval> | null = null;
    let ping: ReturnType<typeof setInterval> | null = null;
    let closed = false;
    let attempt = 0;

    const connect = () => {
      setWsState('connecting');
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const token = sessionToken();
      socket = new WebSocket(`${protocol}//${window.location.host}/ws/topology${token ? `?token=${encodeURIComponent(token)}` : ''}`);
      socket.onopen = () => {
        attempt = 0;
        setWsState('live');
        if (poll) { clearInterval(poll); poll = null; }
        ping = setInterval(() => socket?.readyState === WebSocket.OPEN && socket.send(JSON.stringify({ action: 'ping' })), 25000);
      };
      socket.onmessage = (msg) => {
        let data: any;
        try { data = JSON.parse(msg.data); } catch { return; }
        if (data.event === 'snapshot') applySnapshot(data);
        else if (data.event === 'history') addEvents(data.events ?? []);
        else if (data.event === 'step') addEvents([data]);
      };
      socket.onclose = () => {
        if (ping) { clearInterval(ping); ping = null; }
        if (closed) return;
        setWsState('down');
        if (!poll) poll = setInterval(loadRest, 5000);
        attempt += 1;
        retry = setTimeout(connect, Math.min(15000, 1000 * 2 ** Math.min(attempt, 4)));
      };
      socket.onerror = () => socket?.close();
    };
    connect();
    return () => {
      closed = true;
      if (retry) clearTimeout(retry);
      if (poll) clearInterval(poll);
      if (ping) clearInterval(ping);
      socket?.close();
    };
  }, [addEvents, applySnapshot, loadRest]);

  // Đồng hồ 1 s: tắt dần hiệu ứng sáng, cập nhật "x giây trước".
  useEffect(() => {
    const id = setInterval(() => {
      const now = Date.now();
      setGlow((prev) => {
        const keys = Object.keys(prev).filter((k) => prev[k].until > now);
        return keys.length === Object.keys(prev).length ? prev : Object.fromEntries(keys.map((k) => [k, prev[k]]));
      });
      setTick((t) => t + 1);
    }, 500);
    return () => clearInterval(id);
  }, []);

  // Số sự kiện 10 phút qua của từng ô (huy hiệu dưới ô).
  const runs = useMemo(() => {
    const out: Record<string, number> = {};
    const since = Date.now() / 1000 - 600;
    for (const e of events) {
      if (e.t < since || e.kind === 'status' || e.simulated) continue;
      [e.node, e.source, e.target].filter((id, i, a) => id && a.indexOf(id) === i)
        .forEach((id) => { out[id!] = (out[id!] ?? 0) + 1; });
    }
    return out;
  }, [events]);

  // Ô do state giữ: React Flow ghi kích thước đo được vào chính object ô, ô chỉ
  // hiện khi đã có kích thước. Mỗi snapshot chỉ cập nhật data + thêm/bớt ô,
  // giữ nguyên vị trí và kích thước.
  useEffect(() => {
    if (!snap) return;
    const defaults = defaultPositions(snap.nodes);
    setNodes((prev) => {
      const old = Object.fromEntries(prev.map((n) => [n.id, n]));
      return snap.nodes.map((n) => {
        const g = glow[`node:${n.id}`];
        return {
          ...(old[n.id] ?? {}),
          id: n.id,
          type: 'cyber',
          position: old[n.id]?.position ?? savedLayout.current[n.id] ?? defaults[n.id],
          data: toCyber(n, g?.color, runs[n.id]),
          selected: n.id === selected,
        };
      });
    });
  }, [snap, glow, selected, runs]);

  // Lần đầu có ô (và mỗi khi số ô đổi): căn sơ đồ vừa khung nhìn.
  useEffect(() => {
    if (!rf || !nodes.length) return;
    const key = nodes.length;
    if (fitted.current && (rf as unknown as { _n?: number })._n === key) return;
    (rf as unknown as { _n?: number })._n = key;
    fitted.current = true;
    const t = setTimeout(() => rf.fitView({ padding: 0.08, duration: 300 }), 80);
    return () => clearTimeout(t);
  }, [rf, nodes.length]);

  /**
   * Cạnh luôn chạy trái -> phải (kiểu n8n). Bước trả về (llm -> voice, tts -> hud,
   * core -> tools…) làm sáng cạnh xuôi giữa hai ô đó thay vì vẽ vòng cong ngược.
   */
  const rfEdges: Edge[] = useMemo(() => {
    if (!snap) return [];
    const byId = Object.fromEntries(snap.nodes.map((n) => [n.id, n]));
    const forward = (a: string, b: string): [string, string] =>
      byId[a] && byId[b] && defaultColumn(byId[a]) > defaultColumn(byId[b]) ? [b, a] : [a, b];
    const pairs: Record<string, { source: string; target: string; temp: boolean; fwd: boolean; back: boolean }> = {};
    const addPair = (s0: string, t0: string, temp: boolean) => {
      const [a, b] = forward(s0, t0);
      const key = `${a}->${b}`;
      const p = (pairs[key] ??= { source: a, target: b, temp, fwd: false, back: false });
      if (!temp) p.temp = false;
      if (a === s0) p.fwd = true; else p.back = true;
      return key;
    };
    for (const e of snap.edges) addPair(e.source, e.target, false);
    const lit: Record<string, { color: string; until: number; label?: string }> = {};
    for (const [k, g] of Object.entries(glow)) {
      if (k.startsWith('node:')) continue;
      const [s0, t0] = k.split('->');
      if (!byId[s0] || !byId[t0] || s0 === t0) continue;
      const key = addPair(s0, t0, true);     // cạnh chưa có: vẽ tạm khi đang sáng
      if (!lit[key] || g.until > lit[key].until) lit[key] = g;
    }
    return Object.entries(pairs).map(([id, p]) => {
      const g = lit[id];
      const target = byId[p.target];
      const targetDown = target?.status === 'down';
      return {
        id, source: p.source, target: p.target, type: 'glowing',
        hidden: p.temp && !g,
        data: {
          isActive: !!g,
          activeColor: g?.color,
          isError: targetDown && !g,
          label: g?.label ?? (targetDown ? `${target.label}: ${target.detail || 'lỗi'}` : undefined),
        },
        markerEnd: p.fwd ? { type: MarkerType.ArrowClosed, color: g?.color ?? (targetDown ? '#f43f5e' : '#00f2fe'), width: 14, height: 14 } : undefined,
        markerStart: p.back ? { type: MarkerType.ArrowClosed, color: g?.color ?? '#00f2fe', width: 14, height: 14, orient: 'auto-start-reverse' } : undefined,
      };
    });
  }, [snap, glow]);

  const onNodesChange = useCallback((changes: NodeChange[]) => {
    setNodes((nds) => applyNodeChanges(changes, nds) as Node<NodeData>[]);
  }, []);

  const saveLayout = async () => {
    const res = await authFetch('/api/v1/system/topology/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ nodes: nodes.map((n) => ({ id: n.id, position: n.position })), edges: [] }),
    });
    setNotice(res.ok ? 'Đã lưu bố cục.' : res.status === 403 ? 'Chỉ admin được lưu bố cục.' : `Lưu lỗi (HTTP ${res.status}).`);
    if (res.ok) savedLayout.current = Object.fromEntries(nodes.map((n) => [n.id, n.position]));
  };

  const resetLayout = async () => {
    const res = await authFetch('/api/v1/system/topology/reset', { method: 'POST' });
    if (res.ok) {
      savedLayout.current = {};
      if (snap) {
        const d = defaultPositions(snap.nodes);
        setNodes((nds) => nds.map((n) => ({ ...n, position: d[n.id] ?? n.position })));
        setTimeout(() => rf?.fitView({ padding: 0.08, duration: 300 }), 80);
      }
      setNotice('Đã đặt lại bố cục mặc định.');
    }
    else setNotice(res.status === 403 ? 'Chỉ admin được đặt lại bố cục.' : `Đặt lại lỗi (HTTP ${res.status}).`);
  };

  const [testing, setTesting] = useState(false);
  /** Gửi cảnh báo THỬ thật (chỉ admin) — kết quả từng kênh hiện ở thông báo + "Luồng trực tiếp". */
  const testAlert = async (channel: string) => {
    setTesting(true);
    try {
      const res = await authFetch('/api/v1/system/notifications/test', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ channel: channel || null }),
      });
      if (res.status === 403) { setNotice('Chỉ admin được gửi thử cảnh báo.'); return; }
      const d = await res.json();
      const rs: { channel: string; status: string; detail: string }[] = d.results ?? [];
      setNotice(rs.length
        ? rs.map((r) => `${r.channel}: ${r.status === 'ok' ? 'đã gửi' : r.status === 'cancelled' ? 'chờ kết nối' : 'lỗi'} — ${r.detail}`).join(' · ')
        : 'Chưa kênh nào kết nối — cấu hình ở Portal → Cấu hình kết nối ngoại vi.');
    } catch (e) {
      setNotice(`Gửi thử lỗi: ${(e as Error).message}`);
    } finally {
      setTesting(false);
    }
  };

  /** Mô phỏng CỤC BỘ một lượt thoại để thử giao diện — không gửi lên máy chủ. */
  const simulate = () => {
    const steps: [string, string, string, string][] = [
      ['start', 'hud', 'voice', 'running'], ['router', 'hud', 'voice', 'ok'], ['first_text', 'llm', 'voice', 'ok'],
      ['first_answer_audio', 'tts', 'hud', 'ok'], ['end', 'voice', 'hud', 'ok'],
    ];
    const trace = `sim-${Date.now()}`;
    steps.forEach(([stage, source, target, status], i) => setTimeout(() => addEvents([{
      seq: -Date.now() - i, kind: 'simulation', stage, source, target, status, trace_id: trace,
      ts: new Date().toISOString(), t: Date.now() / 1000, detail: 'MÔ PHỎNG cục bộ — không phải dữ liệu thật', simulated: true,
    }]), i * 700));
  };

  // ── Bảng bên ─────────────────────────────────────────────────────────────
  const flows = useMemo(() => {
    const groups: { key: string; events: TopoEvent[] }[] = [];
    const byKey: Record<string, { key: string; events: TopoEvent[] }> = {};
    for (const e of events) {
      if (e.kind === 'status') continue;
      const key = e.trace_id ?? `seq-${e.seq}`;
      if (!byKey[key]) { byKey[key] = { key, events: [] }; groups.push(byKey[key]); }
      byKey[key].events.push(e);
    }
    return groups.reverse().slice(0, 40);
  }, [events]);

  const incidents = useMemo(() => {
    const bad = (snap?.nodes ?? []).filter((n) => n.status === 'down' || n.status === 'degraded')
      .sort((a, b) => (a.status === 'down' ? -1 : 1) - (b.status === 'down' ? -1 : 1));
    const errs = events.filter((e) => !e.simulated && (e.status === 'error' || e.status === 'down' || e.status === 'degraded' || e.status === 'waiting')).slice(-40).reverse();
    return { bad, errs };
  }, [snap, events]);

  const selNode = snap?.nodes.find((n) => n.id === selected) ?? null;
  const selEvents = useMemo(() => selected
    ? events.filter((e) => e.node === selected || e.source === selected || e.target === selected).slice(-30).reverse()
    : [], [events, selected]);

  const counts = snap?.counts ?? {};
  const stale = lastUpdate && Date.now() - lastUpdate > 10000;

  return (
    <div className="flex h-full w-full flex-col bg-[#01060e] text-slate-100">
      {/* Thanh trên */}
      <div className="z-20 flex flex-wrap items-center gap-3 border-b border-cyan-500/30 bg-slate-950/95 px-4 py-2 shadow-xl backdrop-blur-2xl">
        <div className="flex items-center gap-2">
          <div className="rounded-lg border border-cyan-400/40 bg-cyan-500/20 p-1.5 text-cyan-300">
            <Network className="h-4 w-4 animate-pulse" />
          </div>
          <div className="flex flex-col">
            <span className="font-orbitron text-xs font-extrabold tracking-wider text-cyan-300">VN-MATEAI</span>
            <span className="font-mono text-[11px] tracking-tight text-slate-400">TOPOLOGY · GIÁM SÁT THỜI GIAN THỰC</span>
          </div>
        </div>
        <span className={`flex items-center gap-1 rounded-lg border border-slate-800 px-2 py-0.5 font-mono text-xs ${wsState === 'live' ? 'bg-green-900/50 text-green-300' : wsState === 'connecting' ? 'bg-sky-900/50 text-sky-300' : 'bg-red-900/50 text-red-300'}`}>
          {wsState === 'live' ? <Wifi className="h-3 w-3" /> : <WifiOff className="h-3 w-3" />}
          {wsState === 'live' ? 'Trực tiếp' : wsState === 'connecting' ? 'Đang kết nối…' : 'Mất kết nối — hỏi lại mỗi 5 s'}
        </span>
        {lastUpdate > 0 && (
          <span className={`text-xs ${stale ? 'text-amber-400' : 'text-slate-400'}`}>
            cập nhật {Math.round((Date.now() - lastUpdate) / 1000)}s trước{stale ? ' — dữ liệu có thể cũ' : ''}
          </span>
        )}
        <div className="flex flex-wrap gap-2 text-xs">
          {(['down', 'degraded', 'ok', 'off', 'unknown'] as Status[]).map((s) => (
            <span key={s} className="flex items-center gap-1 rounded-lg border border-slate-800 bg-slate-900/90 px-2 py-0.5 font-mono">
              <span className="inline-block h-2 w-2 rounded-full" style={{ background: STATUS_COLOR[s] }} />
              {STATUS_TEXT[s]}: <b>{counts[s] ?? 0}</b>
            </span>
          ))}
        </div>
        <div className="ml-auto flex flex-wrap gap-2">
          <button onClick={simulate} className="flex items-center gap-1 rounded-lg bg-gradient-to-r from-orange-600 to-amber-600 px-3 py-1 font-mono text-xs font-bold text-white shadow-[0_0_15px_rgba(249,115,22,0.4)] hover:from-orange-500 hover:to-amber-500" title="Chạy thử hiệu ứng trên trình duyệt này, không gửi lên máy chủ">
            <Play className="h-3 w-3" /> Mô phỏng
          </button>
          <button onClick={saveLayout} className="flex items-center gap-1 rounded-lg border border-emerald-500/40 bg-emerald-950/80 px-2 py-1 font-mono text-xs font-semibold text-emerald-300 hover:bg-emerald-900">
            <Save className="h-3 w-3" /> Lưu bố cục
          </button>
          <button onClick={resetLayout} className="flex items-center gap-1 rounded-lg border border-slate-700 bg-slate-900 px-2 py-1 font-mono text-xs text-slate-300 hover:text-rose-400">
            <RotateCcw className="h-3 w-3" /> Đặt lại
          </button>
        </div>
      </div>
      {(error || notice) && (
        <div className={`px-4 py-1 text-xs ${error ? 'bg-red-950 text-red-300' : 'bg-slate-900 text-slate-300'}`} onClick={() => setNotice('')}>
          {error || notice}
        </div>
      )}

      <div className="flex min-h-0 flex-1">
        {/* Sơ đồ */}
        <div className="relative min-w-0 flex-1">
          {!snap && !error && <div className="absolute inset-0 flex items-center justify-center text-sm text-slate-400">Đang tải trạng thái thật…</div>}
          <ReactFlow
            nodes={nodes}
            edges={rfEdges}
            nodeTypes={nodeTypes}
            edgeTypes={edgeTypes}
            onNodesChange={onNodesChange}
            onInit={setRf}
            onNodeClick={(_, n) => { setSelected(n.id); setTab('detail'); }}
            onPaneClick={() => setSelected(null)}
            nodesConnectable={false}
            minZoom={0.15}
            maxZoom={2.2}
            proOptions={{ hideAttribution: true }}
          >
            <Background variant={BackgroundVariant.Dots} gap={24} size={1.5} color="#00f2fe25" className="bg-[#01060e]" />
            <Controls showInteractive={false} className="!overflow-hidden !rounded-xl !border-slate-800 !bg-slate-950/90 !shadow-2xl [&>button]:!border-b [&>button]:!border-slate-800 [&>button]:!bg-transparent [&>button]:!fill-cyan-400 hover:[&>button]:!bg-slate-900" />
            <MiniMap pannable zoomable className="!rounded-xl !border !border-cyan-500/30 !bg-slate-950/90 !shadow-2xl" nodeStrokeColor="#00f2fe"
              nodeColor={(n) => { const d = n.data as NodeData; return d.status === 'down' ? '#f43f5e' : d.status === 'ok' ? d.accent : '#334155'; }} maskColor="#01060ecc" />
          </ReactFlow>
        </div>

        {/* Bảng bên */}
        <div className="flex w-[380px] shrink-0 flex-col border-l border-cyan-500/30 bg-slate-950/95 font-mono">
          <div className="flex border-b border-slate-800 text-xs">
            {([['flow', `Luồng trực tiếp`], ['incidents', `Sự cố (${incidents.bad.length})`], ['detail', 'Chi tiết']] as [Tab, string][]).map(([k, label]) => (
              <button key={k} onClick={() => setTab(k)} className={`flex-1 px-2 py-2 ${tab === k ? 'border-b-2 border-cyan-400 text-cyan-300' : 'text-slate-400 hover:text-slate-200'}`}>
                {k === 'incidents' && incidents.bad.length > 0 && <AlertTriangle className="mr-1 inline h-3 w-3 text-red-400" />}
                {label}
              </button>
            ))}
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-3 text-xs">
            {tab === 'flow' && (
              flows.length === 0
                ? <p className="text-slate-400">Chưa có hoạt động nào. Mỗi lượt thoại, tool, phê duyệt hay robot sẽ hiện ở đây ngay khi xảy ra.</p>
                : flows.map((g) => <FlowCard key={g.key} events={g.events} onPick={(id) => { setSelected(id); setTab('detail'); }} />)
            )}

            {tab === 'incidents' && (
              <>
                <h3 className="mb-2 font-semibold text-slate-300">Thành phần đang có vấn đề</h3>
                {incidents.bad.length === 0 && <p className="mb-3 text-green-400">Không có thành phần nào lỗi hoặc suy giảm.</p>}
                {incidents.bad.map((n) => (
                  <button key={n.id} onClick={() => { setSelected(n.id); setTab('detail'); }} className="mb-2 block w-full rounded border p-2 text-left hover:bg-slate-900" style={{ borderColor: STATUS_COLOR[n.status] }}>
                    <div className="flex justify-between font-semibold"><span>{n.label}</span><span style={{ color: STATUS_COLOR[n.status] }}>{STATUS_TEXT[n.status]}</span></div>
                    <div className="text-slate-400">{n.detail || 'không có mô tả'}</div>
                    <div className="text-slate-400">{fmtSince(n.since)}</div>
                  </button>
                ))}
                <h3 className="mb-2 mt-4 font-semibold text-slate-300">Sự kiện lỗi / chờ gần đây</h3>
                {incidents.errs.length === 0 && <p className="text-slate-400">Không có.</p>}
                {incidents.errs.map((e) => <EventRow key={e.seq} e={e} />)}
              </>
            )}

            {tab === 'detail' && (
              selNode ? (
                <>
                  <div className="mb-2 flex items-center justify-between">
                    <h3 className="text-sm font-semibold">{selNode.label}</h3>
                    <span className="font-semibold" style={{ color: STATUS_COLOR[selNode.status] }}>{STATUS_TEXT[selNode.status]}</span>
                  </div>
                  <div className="mb-1 font-mono text-slate-400">{selNode.id}</div>
                  {selNode.detail && <p className="mb-2 text-slate-300">{selNode.detail}</p>}
                  <p className="mb-3 text-slate-400">{fmtSince(selNode.since)}</p>
                  {selNode.id in ALERT_CHANNEL_OF && (
                    <button
                      onClick={() => testAlert(ALERT_CHANNEL_OF[selNode.id])}
                      disabled={testing}
                      className="mb-3 w-full rounded-lg border border-orange-500/50 bg-orange-950/40 px-2 py-1.5 text-xs font-bold text-orange-300 hover:bg-orange-900/50 disabled:opacity-50"
                    >
                      {testing ? 'Đang gửi…' : selNode.id === 'alerts' ? 'Gửi thử tới mọi kênh đã kết nối' : 'Gửi thử cảnh báo qua kênh này'}
                    </button>
                  )}
                  <table className="mb-4 w-full">
                    <tbody>
                      {Object.entries(selNode.metrics).length === 0 && <tr><td className="text-slate-400">Chưa có số đo.</td></tr>}
                      {Object.entries(selNode.metrics).map(([k, v]) => (
                        <tr key={k} className="border-b border-slate-800">
                          <td className="py-1 pr-2 text-slate-400">{METRIC_TEXT[k] ?? k}</td>
                          <td className="py-1 text-right font-mono">{k === 'last_active' && typeof v === 'number' ? fmtSince(v) : fmtValue(v)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <h4 className="mb-2 font-semibold text-slate-300">Sự kiện liên quan</h4>
                  {selEvents.length === 0 && <p className="text-slate-400">Chưa có.</p>}
                  {selEvents.map((e) => <EventRow key={`${e.seq}`} e={e} />)}
                </>
              ) : <p className="text-slate-400">Bấm vào một ô trên sơ đồ để xem số đo và sự kiện của nó.</p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function EventRow({ e }: { e: TopoEvent }) {
  const color = EVENT_COLOR[e.status] ?? '#94a3b8';
  return (
    <div className="mb-1 flex gap-2 border-l-2 pl-2" style={{ borderColor: color }}>
      <span className="shrink-0 font-mono text-slate-400">{e.ts.slice(11, 19)}</span>
      <div className="min-w-0">
        <div>
          {e.simulated && <span className="mr-1 rounded bg-fuchsia-900 px-1 text-[11px] text-fuchsia-200">MÔ PHỎNG</span>}
          <span style={{ color }}>{eventText(e)}</span>
          {e.ms !== undefined && <span className="ml-1 font-mono text-slate-400">{Math.round(e.ms)} ms</span>}
        </div>
        {e.detail && <div className="truncate text-slate-400" title={e.detail}>{e.detail}</div>}
      </div>
    </div>
  );
}

/** Một lượt (cùng trace_id) hoặc một sự kiện lẻ: các bước theo thứ tự + tổng thời gian. */
function FlowCard({ events, onPick }: { events: TopoEvent[]; onPick: (id: string) => void }) {
  const first = events[0];
  const last = events[events.length - 1];
  const done = last.stage === 'end' || (first.kind !== 'turn' && first.kind !== 'simulation');
  const failed = events.some((e) => e.status === 'error');
  const color = failed ? '#ef4444' : done ? '#22c55e' : '#38bdf8';
  const title = first.kind === 'turn' || first.kind === 'simulation'
    ? `${KIND_TEXT[first.kind]} · ${first.channel ?? first.source ?? ''}`
    : KIND_TEXT[first.kind] ?? first.kind;
  return (
    <div className="mb-2 rounded border border-slate-800 bg-slate-900/60 p-2">
      <div className="mb-1 flex items-center justify-between">
        <span className="font-semibold" style={{ color }}>
          {first.simulated && <span className="mr-1 rounded bg-fuchsia-900 px-1 text-[11px] text-fuchsia-200">MÔ PHỎNG</span>}
          {title}
        </span>
        <span className="font-mono text-slate-400">
          {first.ts.slice(11, 19)}{events.length > 1 && last.t > first.t ? ` · ${Math.round((last.t - first.t) * 1000)} ms` : ''}
          {!done && !failed && <span className="ml-1 animate-pulse text-sky-300">●</span>}
        </span>
      </div>
      {events.map((e) => (
        <div key={e.seq} className="flex items-center gap-2">
          <span className="inline-block h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: EVENT_COLOR[e.status] ?? '#94a3b8' }} />
          <span className="text-slate-300">{e.stage ? STAGE_TEXT[e.stage] ?? e.stage : e.kind}</span>
          {(e.source || e.node) && (
            <button className="truncate text-slate-400 hover:text-sky-300" onClick={() => onPick((e.target ?? e.node ?? e.source)!)}>
              {e.source && e.target ? `${e.source} → ${e.target}` : e.node}
            </button>
          )}
          {e.ms !== undefined && <span className="ml-auto shrink-0 font-mono text-slate-400">{Math.round(e.ms)} ms</span>}
        </div>
      ))}
      {last.detail && !last.simulated && <div className="mt-1 truncate text-slate-400" title={last.detail}>{last.detail}</div>}
    </div>
  );
}
