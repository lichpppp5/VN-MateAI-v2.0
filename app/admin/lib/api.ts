// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
// Phase 78: Admin được FastAPI phục vụ ở /admin — CÙNG origin với backend.
// Nên mặc định gọi API bằng đường dẫn TƯƠNG ĐỐI '/api/v1/...': không qua
// CORS, không cần cấu hình thêm. Đường dẫn tuyệt đối chỉ dùng khi
// NEXT_PUBLIC_API_BASE được đặt tường minh (tách Admin ra domain riêng).
const API_BASE = process.env.NEXT_PUBLIC_API_BASE || '/api/v1';

interface RequestOptions extends RequestInit {
  params?: Record<string, string>;
}

class ApiClient {
  private baseUrl: string;
  private token: string | null = null;

  constructor(baseUrl: string) {
    this.baseUrl = baseUrl;
    if (typeof window !== 'undefined') {
      // Đọc cả 2 khoá: portal (web/app.js) và admin dùng tên khác nhau.
      // Đọc chung token nên mở /admin không phải đăng nhập lại lần nữa.
      this.token =
        localStorage.getItem('vnmateai_token') ||
        localStorage.getItem('vnmate_token');
    }
  }

  setToken(token: string) {
    this.token = token;
    if (typeof window !== 'undefined') {
      localStorage.setItem('vnmateai_token', token);
      localStorage.setItem('vnmate_token', token);
    }
  }

  clearToken() {
    this.token = null;
    if (typeof window !== 'undefined') {
      localStorage.removeItem('vnmateai_token');
      localStorage.removeItem('vnmate_token');
    }
  }

  private async request<T>(endpoint: string, options: RequestOptions = {}): Promise<T> {
    const { params, headers, ...restOptions } = options;

    // Mặc định: đường dẫn TƯƠNG ĐỐI trên chính origin — FastAPI phục vụ cả
    // /admin lẫn /api/v1 nên không qua CORS, không cần cấu hình thêm.
    // Chỉ khi NEXT_PUBLIC_API_BASE được đặt tường minh (tách domain ở
    // production) thì mới ghép thành URL tuyệt đối.
    const query = params ? '?' + new URLSearchParams(params).toString() : '';
    const path = this.baseUrl
      ? `${this.baseUrl}${endpoint}${query}`
      : `${endpoint}${query}`;

    const defaultHeaders: HeadersInit = {
      'Content-Type': 'application/json',
      ...(this.token && { Authorization: `Bearer ${this.token}` }),
      ...headers,
    };

    const response = await fetch(path, {
      ...restOptions,
      headers: defaultHeaders,
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({ message: 'Unknown error' }));
      throw new Error(error.message || `HTTP ${response.status}`);
    }

    if (response.status === 204) {
      return {} as T;
    }

    return response.json();
  }

  // Auth
  async login(username: string, password: string) {
    return this.request<{ access_token: string; token_type: string }>('/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    });
  }

  /**
   * Danh mục connector + JSON Schema cấu hình.
   *
   * Đây là nguồn DUY NHẤT cho trang Plugin Vault. Trước đây trang này gọi
   * `/plugins` — một endpoint không hề tồn tại — nên lưới luôn trống. Không tạo
   * danh sách plugin giả ở frontend: connector nào không có trong registry thì
   * không được hiện, vì giao diện không được hứa thứ hệ thống chưa có.
   */
  async getConnectorCatalog() {
    const res = await this.request<{
      status: string;
      connectors: Record<string, ConnectorCatalogEntry>;
    }>('/enterprise/connectors/catalog');
    return Object.values(res.connectors ?? {});
  }

  // ── Routing Rules ─────────────────────────────────────────────────────
  // PHẦN CHƯA CÓ BACKEND — giữ lại để sẵn sàng, KHÔNG gọi được ở thời điểm này.
  //
  // Đã kiểm tra: không có bảng `routing_rules` trong CSDL và
  // GET /api/v1/routing/rules trả 404. Trang /admin/routing vì vậy hiện thông
  // báo "chưa có nơi lưu quy tắc" thay vì một bảng bấm Lưu xong rơi mất dữ
  // liệu. Các hàm dưới đây + `RoutingBuilder.tsx` + `useRoutingRules.ts` đã
  // dựng sẵn: có endpoint CRUD là nối vào chạy, không phải làm lại giao diện.
  async getRoutingRules() {
    return this.request<RoutingRule[]>('/routing/rules');
  }

  async createRoutingRule(rule: Omit<RoutingRule, 'id' | 'createdAt' | 'updatedAt'>) {
    return this.request<RoutingRule>('/routing/rules', {
      method: 'POST',
      body: JSON.stringify(rule),
    });
  }

  async updateRoutingRule(id: string, rule: Partial<RoutingRule>) {
    return this.request<RoutingRule>(`/routing/rules/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(rule),
    });
  }

  async deleteRoutingRule(id: string) {
    return this.request<void>(`/routing/rules/${id}`, {
      method: 'DELETE',
    });
  }

  // ── Dashboard ────────────────────────────────────────────────────────
  //
  // Phase 78: bản đầu dùng /dashboard/stats, /dashboard/audit-logs,
  // /dashboard/approvals, /workers — KHÔNG endpoint nào tồn tại (cả 4 trả
  // 404), nên trang dashboard trắng hoàn toàn. Nay dùng lại đúng các endpoint
  // portal đang gọi, tức là dữ liệu đã được kiểm chứng hoạt động.
  async getDashboardStats() {
    return this.request<SystemStats>('/system/stats');
  }

  /** Nhật ký hoạt động. Cùng nguồn với khung log của portal. */
  async getAuditLogs(limit = 50) {
    const res = await this.request<{
      status: string;
      count: number;
      logs: AuditLog[];
    }>('/logs/recent');
    return (res.logs ?? []).slice(0, limit);
  }

  /** Phê duyệt đang chờ (HITL). */
  async getPendingApprovals() {
    const res = await this.request<{
      status: string;
      total_pending: number;
      pending_approvals: ApprovalRequest[];
    }>('/enterprise/hitl/pending');
    return res.pending_approvals ?? [];
  }

  /**
   * Ping thật một connector qua skill `check_connector_health`.
   *
   * Đây là bản sao đúng cách portal đang làm: skill gọi thẳng API của
   * dịch vụ nên trả về độ trễ và lỗi thật. Trước đây nút "Kiểm tra" của
   * Admin chỉ đọc lại cấu hình trong bộ nhớ — đó không phải kiểm tra kết
   * nối, chỉ là đọc trạng thái file cấu hình.
   */
  async pingConnector(pluginId: string) {
    return this.request<{
      success?: boolean;
      data?: {
        connectors?: Record<
          string,
          { success?: boolean; latency_ms?: number; error?: string }
        >;
      };
    }>('/skills/execute', {
      method: 'POST',
      body: JSON.stringify({
        name: 'check_connector_health',
        arguments: { connectors: [pluginId] },
      }),
    });
  }
}

export const api = new ApiClient(API_BASE);

/**
 * fetch() kèm JWT của phiên đăng nhập (cùng khoá với ApiClient). Dùng cho các
 * component gọi thẳng `/api/v1/...` — các endpoint đó không còn public.
 */
export function sessionToken(): string | null {
  return typeof window !== 'undefined'
    ? localStorage.getItem('vnmateai_token') || localStorage.getItem('vnmate_token')
    : null;
}

export function authFetch(input: string, init: RequestInit = {}): Promise<Response> {
  const token = sessionToken();
  const headers = new Headers(init.headers);
  if (token) headers.set('Authorization', `Bearer ${token}`);
  return fetch(input, { ...init, headers });
}

// Types
/**
 * Một connector lấy từ `GET /enterprise/connectors/catalog`.
 *
 * `configured: false` KHÔNG có nghĩa connector hỏng — nghĩa là chưa điền đủ
 * khoá bắt buộc. Giao diện phải hiện "chờ kết nối"/"chưa cấu hình", tuyệt đối
 * không vẽ trạng thái "Đã kết nối" cho thứ chưa từng kiểm tra.
 */
export interface ConnectorCatalogEntry {
  id: string;
  display_name: string;
  description: string;
  configured: boolean;
  missing_fields: string[];
  actions: string[];
  max_risk_level: number | null;
  config_schema: JsonSchema;
}

/** Trạng thái hiển thị của một connector, suy ra từ dữ liệu thật. */
export type ConnectorStatus = 'connected' | 'waiting' | 'error';

export function connectorStatus(entry: ConnectorCatalogEntry): ConnectorStatus {
  if (entry.configured) return 'connected';
  return 'waiting';
}

export interface JsonSchema {
  type: 'object';
  properties: Record<string, JsonSchemaProperty>;
  required?: string[];
}

export interface JsonSchemaProperty {
  type: 'string' | 'number' | 'boolean' | 'array' | 'object';
  title: string;
  description?: string;
  format?: 'password' | 'email' | 'uri' | 'secret';
  enum?: string[];
  default?: unknown;
  items?: JsonSchemaProperty;
  properties?: Record<string, JsonSchemaProperty>;
}

export interface RoutingRule {
  id: string;
  name: string;
  description: string;
  condition: RoutingCondition;
  action: RoutingAction;
  priority: number;
  enabled: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface RoutingCondition {
  type: 'task_type' | 'risk_level' | 'user_role' | 'time_range' | 'custom';
  field: string;
  operator: 'equals' | 'contains' | 'greater_than' | 'less_than' | 'in' | 'regex';
  value: unknown;
}

export interface RoutingAction {
  type: 'm365_direct' | 'legacy_bot' | 'esp32_voice' | 'webhook' | 'email';
  target: string;
  config: Record<string, unknown>;
}

/**
 * Số liệu tổng quan — đúng cấu trúc `GET /api/v1/system/stats`.
 *
 * Đây là endpoint portal đang dùng, không phải endpoint mới. Ở đây KHÔNG
 * có "17/17 Mac Mini" hay "Ubuntu Gateway": hệ thống chưa đăng ký cụm máy
 * nào, nên những ô đó sẽ báo "chưa có nguồn" thay vì bịa số.
 */
export interface SystemStats {
  status: string;
  timestamp: string;
  hardware: {
    cpu_percent: number;
    ram_percent: number;
    ram_used_gb: number;
    ram_total_gb: number;
    disk_percent: number;
    uptime_seconds: number;
  };
  tasks: {
    total: number;
    completed: number;
    issues: number;
    pending: number;
  };
  users_count: number;
  online_clients_count: number;
  audio_nodes_count: number;
  skills_count: number;
}

/** Dòng nhật ký — đúng cấu trúc phần tử của `GET /api/v1/logs/recent`. */
export interface AuditLog {
  event?: string;
  level: 'INFO' | 'WARNING' | 'ERROR' | 'DEBUG' | string;
  color?: string;
  logger: string;
  message: string;
  timestamp: string;
}

/** Phê duyệt đang chờ — từ `GET /api/v1/enterprise/hitl/pending`. */
export interface ApprovalRequest {
  id?: string;
  tool?: string;
  risk_level?: number;
  reason?: string;
  created_at?: string;
  [k: string]: unknown;
}