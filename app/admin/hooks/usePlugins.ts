// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
'use client';

import { useState, useEffect, useCallback } from 'react';
import {
  api,
  type ConnectorCatalogEntry,
  type ConnectorStatus,
  connectorStatus,
} from '@/lib/api';
import { useToast } from '@/hooks/useToast';

/**
 * Nguồn duy nhất cho trang Plugin Vault: danh mục connector thật từ backend.
 *
 * Trước đây hook này gọi `/plugins` — endpoint không tồn tại — nên mọi thứ trả
 * về rỗng và trang hiện lưới trống. Không được thay bằng danh sách viết tay
 * trong frontend: connector nào không có trong registry thì hệ thống không có,
 * và hiện nó ra là hứa một thứ không tồn tại.
 *
 * "Chưa cấu hình" KHÔNG phải "lỗi": connector thiếu khoá thì hiện "chờ kết nối",
 * đúng quy ước đã dùng ở Phase 73-76.
 */
export function usePlugins() {
  const [plugins, setPlugins] = useState<ConnectorCatalogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // connectorId đang được ping — để nút tương ứng hiện spinner, các nút
  // khác vẫn bấm được (4 connector, mỗi cái một lệnh mất vài giây).
  const [testing, setTesting] = useState<string | null>(null);
  const { success: toastSuccess, error: toastError } = useToast();

  const fetchPlugins = useCallback(async () => {
    try {
      setLoading(true);
      const data = await api.getConnectorCatalog();
      setPlugins(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch connectors');
      setPlugins([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchPlugins();
  }, [fetchPlugins]);

  /**
   * Ping thật connector qua skill `check_connector_health`.
   *
   * Phase 78 (bản 2): trước đây hàm này chỉ đọc lại cấu hình trong bộ nhớ và
   * tự nói "chưa có lời gọi thật" — đúng là thành thật, nhưng vô dụng: người
   * vận hành bấm "Kiểm tra" là muốn biết dịch vụ còn sống không. Nay gọi đúng
   * skill mà portal đang dùng, nên hai nơi cho cùng một kết quả thật.
   */
  const testConnection = async (pluginId: string): Promise<void> => {
    setTesting(pluginId);
    try {
      const res = await api.pingConnector(pluginId);
      const c = res.data?.connectors?.[pluginId];
      if (!c) {
        toastError('Không có kết quả', `Máy chủ không trả kết quả cho ${pluginId}`);
      } else if (c.success) {
        toastSuccess(`${pluginId} phản hồi OK`, `Độ trễ ${(c.latency_ms ?? 0).toFixed(0)} ms`);
      } else {
        // Lỗi thật từ dịch vụ: "Authentication failed", "Not authenticated"...
        toastError(`${pluginId} không kết nối được`, c.error || 'Không rõ lý do');
      }
      await fetchPlugins();
    } catch (err) {
      toastError('Không kiểm tra được', err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setTesting(null);
    }
  };

  return {
    plugins,
    loading,
    error,
    testing,
    refetch: fetchPlugins,
    testConnection,
  };
}

export type { ConnectorCatalogEntry, ConnectorStatus };
export { connectorStatus };
