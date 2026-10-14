// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
'use client';

import React from 'react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/Button';
import { Card, CardContent } from '@/components/ui/Card';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/Dialog';
import { DynamicForm, JsonSchema } from '@/components/admin/DynamicForm';
import { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider } from '@/components/ui/Tooltip';
import { useToast } from '@/hooks/useToast';
import { Settings, Shield, Loader2, KeyRound, CircleAlert, CircleCheck } from 'lucide-react';
import type { ConnectorCatalogEntry } from '@/lib/api';

/** Chữ viết tắt làm logo — không tải ảnh ngoài, không phụ thuộc mạng. */
const MONOGRAM: Record<string, string> = {
  aws: 'AWS',
  oci: 'OCI',
  paperless: 'PX',
  einvoice: 'HD',
};

interface PluginCardProps {
  plugin: ConnectorCatalogEntry;
  onTestConnection: (pluginId: string) => Promise<void>;
  testing?: string | null;
}

export function PluginCard({ plugin, onTestConnection, testing }: PluginCardProps) {
  const { error: toastError, info: toastInfo } = useToast();
  const [dialogOpen, setDialogOpen] = React.useState(false);
  const [saving, setSaving] = React.useState(false);
  const [testingNow, setTestingNow] = React.useState(false);

  // Chưa đủ khoá = "chờ kết nối", KHÔNG phải "lỗi". Đây là khác biệt quan trọng:
  // hiện "lỗi" khiến người vận hành tưởng có sự cố cần sửa code.
  const isWaiting = !plugin.configured;
  const missingCount = plugin.missing_fields?.length ?? 0;
  const isTesting = testing === plugin.id || testingNow;

  const handleSave = async (_data: Record<string, unknown>) => {
    setSaving(true);
    try {
      // Chưa có endpoint ghi cấu hình cho admin. Tuyệt đối KHÔNG báo "đã lưu"
      // khi chưa gửi đi đâu — báo sai ở đây là người dùng tin là đã lưu rồi
      // đóng hộp thoại, rồi mất thông tin đăng nhập.
      toastError(
        'Chưa lưu được',
        'Backend chưa có endpoint ghi cấu hình connector. Xem README mục "Backend API Endpoints Expected".',
      );
      throw new Error('not-implemented');
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async () => {
    setTestingNow(true);
    try {
      await onTestConnection(plugin.id);
    } finally {
      setTestingNow(false);
    }
  };

  return (
    <>
      <Card
        className={cn(
          'card-hover',
          plugin.configured
            ? 'border-emerald-500/40'
            : 'border-amber-500/30',
        )}
      >
        <CardContent className="p-5">
          <div className="flex items-start justify-between gap-4">
            <div className="flex items-center gap-3">
              <div
                className={cn(
                  'w-14 h-14 rounded-xl flex items-center justify-center border font-orbitron font-bold text-sm shrink-0',
                  plugin.configured
                    ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400'
                    : 'bg-slate-800/50 border-slate-700 text-slate-400',
                )}
              >
                {MONOGRAM[plugin.id] ?? plugin.id.slice(0, 2).toUpperCase()}
              </div>
              <div className="min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <h3 className="font-semibold text-cyan-300 truncate">{plugin.display_name}</h3>
                  {plugin.configured ? (
                    <span className="inline-flex items-center gap-1 text-xs text-emerald-400">
                      <CircleCheck className="w-3.5 h-3.5" />
                      Đã cấu hình
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1 text-xs text-amber-400">
                      <Loader2 className="w-3.5 h-3.5" />
                      chờ kết nối
                    </span>
                  )}
                </div>
                <p className="text-sm text-slate-400 mt-1">{plugin.description}</p>
              </div>
            </div>
            {plugin.max_risk_level != null && (
              <TooltipProvider>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <span className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded bg-slate-800 text-slate-300 shrink-0 cursor-help">
                      <Shield className="w-3 h-3" />
                      Risk {plugin.max_risk_level}
                    </span>
                  </TooltipTrigger>
                  <TooltipContent>
                    Mức rủi ro cao nhất mà connector chấp nhận được. Từ mức này
                    trở lên cần phê duyệt của người dùng.
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
            )}
          </div>

          {/* Năng lực thật — lấy từ CONNECTOR_RISK_LEVELS, không bịa. */}
          <div className="flex flex-wrap gap-1.5 mt-4">
            {plugin.actions.length > 0 ? (
              plugin.actions.map((a) => (
                <span
                  key={a}
                  className="text-xs px-2 py-0.5 rounded bg-slate-800/70 border border-slate-700 font-mono text-slate-300"
                >
                  {a}
                </span>
              ))
            ) : (
              <span className="text-xs text-slate-400">chưa có tác vụ nào được khai báo</span>
            )}
          </div>

          {isWaiting && missingCount > 0 && (
            <div className="mt-4 p-3 rounded-lg bg-amber-500/10 border border-amber-500/30">
              <div className="flex items-start gap-2">
                <CircleAlert className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
                <div className="min-w-0">
                  <p className="text-sm text-amber-300">
                    Còn thiếu {missingCount} khoá bắt buộc — mọi lời gọi sẽ thất bại.
                  </p>
                  <p className="text-xs font-mono text-amber-200/70 mt-1 break-all">
                    {plugin.missing_fields.join(', ')}
                  </p>
                </div>
              </div>
            </div>
          )}

          <div className="flex items-center gap-2 mt-4 pt-4 border-t border-slate-800">
            <Button
              variant="outline"
              size="sm"
              className="flex-1"
              onClick={() => setDialogOpen(true)}
            >
              <Settings className="mr-1.5 h-3.5 w-3.5" />
              Cấu hình
            </Button>
            <Button
              variant={plugin.configured ? 'cyber-emerald' : 'cyber'}
              size="sm"
              className="flex-1"
              onClick={handleTest}
              disabled={isTesting}
            >
              {isTesting ? (
                <>
                  <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                  Đang kiểm tra
                </>
              ) : (
                <>
                  <KeyRound className="mr-1.5 h-3.5 w-3.5" />
                  Kiểm tra
                </>
              )}
            </Button>
          </div>
        </CardContent>
      </Card>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Cấu hình {plugin.display_name}</DialogTitle>
            <DialogDescription>
              Form này được sinh tự động từ schema do backend khai báo — thêm
              connector mới không cần sửa mã giao diện.
            </DialogDescription>
          </DialogHeader>
          <DynamicForm
            schema={plugin.config_schema as JsonSchema}
            onSubmit={handleSave}
            submitText="Lưu cấu hình"
            cancelText="Đóng"
            onCancel={() => setDialogOpen(false)}
            loading={saving}
            layout="grid"
            cols={2}
          />
        </DialogContent>
      </Dialog>
    </>
  );
}
