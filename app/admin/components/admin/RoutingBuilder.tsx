// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
'use client';

import React, { useState } from 'react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/Button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card';
import { Input } from '@/components/ui/Input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { Switch } from '@/components/ui/Switch';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from '@/components/ui/Dialog';
import { DynamicForm, JsonSchema, JsonSchemaProperty } from '@/components/admin/DynamicForm';
import { useToast } from '@/hooks/useToast';
import { useRoutingRules } from '@/hooks/useRoutingRules';
import { Plus, Trash2, Edit, GripVertical, ChevronUp, ChevronDown, AlertCircle, CheckCircle, Loader2 } from 'lucide-react';
import { RoutingRule, RoutingCondition, RoutingAction } from '@/lib/api';

const CONDITION_TYPES = [
  { value: 'task_type', label: 'Task Type' },
  { value: 'risk_level', label: 'Risk Level' },
  { value: 'user_role', label: 'User Role' },
  { value: 'time_range', label: 'Time Range' },
  { value: 'custom', label: 'Custom Expression' },
];

const CONDITION_OPERATORS = [
  { value: 'equals', label: 'Equals' },
  { value: 'contains', label: 'Contains' },
  { value: 'greater_than', label: 'Greater Than' },
  { value: 'less_than', label: 'Less Than' },
  { value: 'in', label: 'In List' },
  { value: 'regex', label: 'Regex Match' },
];

const ACTION_TYPES = [
  { value: 'm365_direct', label: 'Direct Microsoft 365', description: 'Send directly via Teams/Outlook' },
  { value: 'legacy_bot', label: 'Legacy AI Bot', description: 'Route to legacy bot for processing' },
  { value: 'esp32_voice', label: 'Robot ESP32', description: 'Voice output via ESP32 robot' },
  { value: 'webhook', label: 'Webhook', description: 'Call external webhook URL' },
  { value: 'email', label: 'Email', description: 'Send email notification' },
];

const TASK_TYPES = [
  { value: 'urgent', label: 'Urgent Task' },
  { value: 'daily_report', label: 'Daily Report' },
  { value: 'reminder', label: 'General Reminder' },
  { value: 'meeting', label: 'Meeting Notification' },
  { value: 'approval', label: 'Approval Request' },
  { value: 'system_alert', label: 'System Alert' },
];

const RISK_LEVELS = [
  { value: 1, label: 'Level 1 - Low' },
  { value: 2, label: 'Level 2 - Low-Medium' },
  { value: 3, label: 'Level 3 - Medium' },
  { value: 4, label: 'Level 4 - High' },
  { value: 5, label: 'Level 5 - Critical' },
];

const USER_ROLES = [
  { value: 'admin', label: 'Administrator' },
  { value: 'manager', label: 'Manager' },
  { value: 'user', label: 'Standard User' },
  { value: 'guest', label: 'Guest' },
];

interface RoutingBuilderProps {
  onRulesChange?: () => void;
}

export function RoutingBuilder({ onRulesChange }: RoutingBuilderProps) {
  const { rules, loading, createRule, updateRule, deleteRule, toggleRule, refetch } = useRoutingRules();
  const { toast } = useToast();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingRule, setEditingRule] = useState<RoutingRule | null>(null);
  const [draggedIndex, setDraggedIndex] = useState<number | null>(null);

  const isEditing = !!editingRule;

  const initialCondition: RoutingCondition = {
    type: 'task_type',
    field: 'taskType',
    operator: 'equals',
    value: '',
  };

  const initialAction: RoutingAction = {
    type: 'm365_direct',
    target: '',
    config: {},
  };

  const defaultValues = React.useMemo(() => ({
    name: '',
    description: '',
    condition: initialCondition,
    action: initialAction,
    priority: rules.length + 1,
    enabled: true,
  }), [rules.length]);

  const handleSubmit = async (data: Record<string, unknown>) => {
    const ruleData = {
      ...data,
      condition: data.condition as RoutingCondition,
      action: data.action as RoutingAction,
    };

    if (isEditing) {
      await updateRule(editingRule.id, ruleData);
    } else {
      await createRule(ruleData as Omit<RoutingRule, 'id' | 'createdAt' | 'updatedAt'>);
    }
    setDialogOpen(false);
    setEditingRule(null);
    onRulesChange?.();
  };

  const handleEdit = (rule: RoutingRule) => {
    setEditingRule(rule);
    setDialogOpen(true);
  };

  const handleDelete = async (id: string) => {
    if (confirm('Are you sure you want to delete this routing rule?')) {
      await deleteRule(id);
      onRulesChange?.();
    }
  };

  const handleToggle = async (rule: RoutingRule) => {
    await toggleRule(rule.id, !rule.enabled);
    onRulesChange?.();
  };

  const handleDragStart = (e: React.DragEvent, index: number) => {
    setDraggedIndex(index);
    e.dataTransfer.effectAllowed = 'move';
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
  };

  const handleDrop = (e: React.DragEvent, index: number) => {
    e.preventDefault();
    if (draggedIndex !== null && draggedIndex !== index) {
      const newRules = [...rules];
      const [removed] = newRules.splice(draggedIndex, 1);
      newRules.splice(index, 0, removed);
      
      // Update priorities
      newRules.forEach((rule, i) => {
        if (rule.priority !== i + 1) {
          updateRule(rule.id, { priority: i + 1 });
        }
      });
      onRulesChange?.();
    }
    setDraggedIndex(null);
  };

  const conditionSchema: JsonSchemaProperty = {
    type: 'object',
    title: 'Condition',
    properties: {
      type: {
        type: 'string',
        title: 'Condition Type',
        enum: CONDITION_TYPES.map(c => c.value),
        ui: { widget: 'select', options: CONDITION_TYPES.map(c => ({ value: c.value, label: c.label })) },
      },
      field: {
        type: 'string',
        title: 'Field',
        ui: { widget: 'text', placeholder: 'e.g., taskType, riskLevel, userRole' },
      },
      operator: {
        type: 'string',
        title: 'Operator',
        enum: CONDITION_OPERATORS.map(o => o.value),
        ui: { widget: 'select', options: CONDITION_OPERATORS.map(o => ({ value: o.value, label: o.label })) },
      },
      value: {
        type: 'string',
        title: 'Value',
        ui: { widget: 'text', placeholder: 'Enter value to match' },
      },
    },
    required: ['type', 'field', 'operator', 'value'],
  };

  const actionSchema: JsonSchemaProperty = {
    type: 'object',
    title: 'Action',
    properties: {
      type: {
        type: 'string',
        title: 'Action Type',
        enum: ACTION_TYPES.map(a => a.value),
        ui: { widget: 'select', options: ACTION_TYPES.map(a => ({ value: a.value, label: a.label })) },
      },
      target: {
        type: 'string',
        title: 'Target',
        ui: { widget: 'text', placeholder: 'e.g., channel name, webhook URL, email address' },
      },
      config: {
        type: 'object',
        title: 'Additional Config',
        properties: {},
        ui: { widget: 'hidden' },
      },
    },
    required: ['type', 'target'],
  };

  const ruleSchema: JsonSchema = {
    type: 'object',
    properties: {
      name: {
        type: 'string',
        title: 'Rule Name',
        minLength: 1,
        maxLength: 100,
        ui: { widget: 'text', placeholder: 'e.g., Urgent tasks to M365' },
      },
      description: {
        type: 'string',
        title: 'Description',
        ui: { widget: 'textarea', placeholder: 'Describe what this rule does' },
      },
      condition: conditionSchema,
      action: actionSchema,
      priority: {
        type: 'number',
        title: 'Priority',
        minimum: 1,
        ui: { widget: 'text', placeholder: '1 = highest priority' },
      },
      enabled: {
        type: 'boolean',
        title: 'Enabled',
        ui: { widget: 'switch' },
      },
    },
    required: ['name', 'condition', 'action', 'priority'],
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-orbitron font-bold text-vnmate-cyan">Smart Routing Builder</h2>
          <p className="text-vnmate-slate-400 mt-1">Define how tasks are routed to different channels based on conditions</p>
        </div>
        <Button variant="cyber" onClick={() => { setEditingRule(null); setDialogOpen(true); }}>
          <Plus className="mr-2 h-4 w-4" />
          Add Routing Rule
        </Button>
      </div>

      {loading ? (
        <Card>
          <CardContent className="flex items-center justify-center py-12">
            <Loader2 className="h-8 w-8 animate-spin text-vnmate-cyan" />
            <span className="ml-3 text-vnmate-slate-400">Loading routing rules...</span>
          </CardContent>
        </Card>
      ) : rules.length === 0 ? (
        <Card className="text-center py-12">
          <CardContent>
            <AlertCircle className="h-12 w-12 text-vnmate-slate-600 mx-auto mb-4" />
            <h3 className="text-lg font-semibold text-vnmate-neon mb-2">No routing rules configured</h3>
            <p className="text-vnmate-slate-400 mb-6">Create your first routing rule to define how tasks are distributed</p>
            <Button variant="cyber" onClick={() => { setEditingRule(null); setDialogOpen(true); }}>
              <Plus className="mr-2 h-4 w-4" />
              Create First Rule
            </Button>
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardContent className="p-0">
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-vnmate-slate-800 bg-vnmate-slate-900/50">
                    <th className="p-4 text-left text-xs font-semibold text-vnmate-slate-400 uppercase tracking-wider w-10">#</th>
                    <th className="p-4 text-left text-xs font-semibold text-vnmate-slate-400 uppercase tracking-wider">Rule Name</th>
                    <th className="p-4 text-left text-xs font-semibold text-vnmate-slate-400 uppercase tracking-wider">Condition</th>
                    <th className="p-4 text-left text-xs font-semibold text-vnmate-slate-400 uppercase tracking-wider">Action</th>
                    <th className="p-4 text-left text-xs font-semibold text-vnmate-slate-400 uppercase tracking-wider">Priority</th>
                    <th className="p-4 text-left text-xs font-semibold text-vnmate-slate-400 uppercase tracking-wider">Status</th>
                    <th className="p-4 text-left text-xs font-semibold text-vnmate-slate-400 uppercase tracking-wider w-32">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-vnmate-slate-800">
                  {rules.map((rule, index) => (
                    <tr
                      key={rule.id}
                      draggable
                      onDragStart={(e) => handleDragStart(e, index)}
                      onDragOver={handleDragOver}
                      onDrop={(e) => handleDrop(e, index)}
                      className={cn(
                        'transition-colors',
                        draggedIndex === index && 'opacity-50 bg-vnmate-cyan/5',
                        !rule.enabled && 'opacity-50'
                      )}
                    >
                      <td className="p-4">
                        <GripVertical className="h-5 w-5 text-vnmate-slate-600 cursor-grab active:cursor-grabbing" />
                      </td>
                      <td className="p-4">
                        <div className="font-medium text-vnmate-neon">{rule.name}</div>
                        <div className="text-sm text-vnmate-slate-500 truncate max-w-xs">{rule.description}</div>
                      </td>
                      <td className="p-4">
                        <div className="flex items-center gap-2 text-sm">
                          <span className="px-2 py-0.5 bg-vnmate-cyan/10 text-vnmate-cyan rounded text-xs font-mono">
                            {CONDITION_TYPES.find(c => c.value === rule.condition.type)?.label || rule.condition.type}
                          </span>
                          <span className="text-vnmate-slate-500">{rule.condition.operator}</span>
                          <span className="font-mono text-vnmate-neon text-xs bg-vnmate-slate-800 px-1.5 py-0.5 rounded">
                            {String(rule.condition.value)}
                          </span>
                        </div>
                      </td>
                      <td className="p-4">
                        <div className="flex items-center gap-2">
                          <span className={cn(
                            'px-2 py-0.5 rounded text-xs font-mono',
                            rule.action.type === 'm365_direct' && 'bg-vnmate-emerald/10 text-vnmate-emerald',
                            rule.action.type === 'legacy_bot' && 'bg-vnmate-amber/10 text-vnmate-amber',
                            rule.action.type === 'esp32_voice' && 'bg-vnmate-purple/10 text-vnmate-purple',
                            rule.action.type === 'webhook' && 'bg-vnmate-blue/10 text-vnmate-blue',
                            rule.action.type === 'email' && 'bg-vnmate-pink/10 text-vnmate-pink',
                          )}>
                            {ACTION_TYPES.find(a => a.value === rule.action.type)?.label || rule.action.type}
                          </span>
                          <span className="text-vnmate-slate-500 text-xs truncate max-w-[150px]">
                            {rule.action.target}
                          </span>
                        </div>
                      </td>
                      <td className="p-4">
                        <span className="font-mono text-vnmate-cyan">#{rule.priority}</span>
                      </td>
                      <td className="p-4">
                        <div className="flex items-center gap-2">
                          <Switch
                            checked={rule.enabled}
                            onCheckedChange={() => handleToggle(rule)}
                            aria-label={`Toggle rule ${rule.name}`}
                          />
                          <span className={cn('text-sm', rule.enabled ? 'text-vnmate-emerald' : 'text-vnmate-slate-500')}>
                            {rule.enabled ? 'Active' : 'Inactive'}
                          </span>
                        </div>
                      </td>
                      <td className="p-4">
                        <div className="flex items-center gap-2">
                          <Button variant="ghost" size="icon" onClick={() => handleEdit(rule)} disabled={loading}>
                            <Edit className="h-4 w-4" />
                          </Button>
                          <Button variant="ghost" size="icon" onClick={() => handleDelete(rule.id)} disabled={loading}>
                            <Trash2 className="h-4 w-4" />
                          </Button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      )}

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-3xl max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{isEditing ? 'Edit' : 'Create'} Routing Rule</DialogTitle>
            <DialogDescription>
              Define a condition and action for routing tasks. Rules are evaluated in priority order (1 = highest).
            </DialogDescription>
          </DialogHeader>
          <DynamicForm
            schema={ruleSchema}
            onSubmit={handleSubmit}
            defaultValues={isEditing ? {
              ...editingRule!,
              condition: editingRule!.condition,
              action: editingRule!.action,
            } : defaultValues}
            submitText={isEditing ? 'Update Rule' : 'Create Rule'}
            cancelText="Cancel"
            onCancel={() => { setDialogOpen(false); setEditingRule(null); }}
            loading={false}
            layout="grid"
            cols={1}
          />
        </DialogContent>
      </Dialog>
    </div>
  );
}