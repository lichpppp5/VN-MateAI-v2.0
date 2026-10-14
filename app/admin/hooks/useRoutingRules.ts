// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
'use client';

import { useState, useEffect, useCallback } from 'react';
import { api, type RoutingRule } from '@/lib/api';
import { useToast } from '@/hooks/useToast';

export function useRoutingRules() {
  const [rules, setRules] = useState<RoutingRule[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { success: toastSuccess, error: toastError } = useToast();

  const fetchRules = useCallback(async () => {
    try {
      setLoading(true);
      const data = await api.getRoutingRules();
      setRules(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch routing rules');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchRules();
  }, [fetchRules]);

  const createRule = async (rule: Omit<RoutingRule, 'id' | 'createdAt' | 'updatedAt'>): Promise<boolean> => {
    try {
      await api.createRoutingRule(rule);
      toastSuccess('Rule created', 'Routing rule has been added');
      await fetchRules();
      return true;
    } catch (err) {
      toastError('Failed to create rule', err instanceof Error ? err.message : 'Unknown error');
      return false;
    }
  };

  const updateRule = async (id: string, rule: Partial<RoutingRule>): Promise<boolean> => {
    try {
      await api.updateRoutingRule(id, rule);
      toastSuccess('Rule updated', 'Routing rule has been updated');
      await fetchRules();
      return true;
    } catch (err) {
      toastError('Failed to update rule', err instanceof Error ? err.message : 'Unknown error');
      return false;
    }
  };

  const deleteRule = async (id: string): Promise<boolean> => {
    try {
      await api.deleteRoutingRule(id);
      toastSuccess('Rule deleted', 'Routing rule has been removed');
      await fetchRules();
      return true;
    } catch (err) {
      toastError('Failed to delete rule', err instanceof Error ? err.message : 'Unknown error');
      return false;
    }
  };

  const toggleRule = async (id: string, enabled: boolean): Promise<boolean> => {
    return updateRule(id, { enabled });
  };

  return {
    rules,
    loading,
    error,
    refetch: fetchRules,
    createRule,
    updateRule,
    deleteRule,
    toggleRule,
  };
}