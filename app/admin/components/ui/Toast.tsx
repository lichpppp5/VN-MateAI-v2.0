// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
'use client';

import * as React from 'react';
import { X, CheckCircle, AlertCircle, AlertTriangle, Info } from 'lucide-react';
import { useToast } from '@/hooks/useToast';
import { cn } from '@/lib/utils';

const toastIcons = {
  success: CheckCircle,
  error: AlertCircle,
  warning: AlertTriangle,
  info: Info,
};

const toastColors = {
  success: 'border-vnmate-emerald/50 bg-vnmate-emerald/10 text-vnmate-emerald',
  error: 'border-vnmate-pink/50 bg-vnmate-pink/10 text-vnmate-pink',
  warning: 'border-vnmate-amber/50 bg-vnmate-amber/10 text-vnmate-amber',
  info: 'border-vnmate-cyan/50 bg-vnmate-cyan/10 text-vnmate-cyan',
};

export function Toaster() {
  const { toasts, remove } = useToast();

  return (
    <div className="fixed bottom-6 right-6 z-[100] flex flex-col gap-3 w-96">
      {toasts.map((toast) => (
        <Toast key={toast.id} toast={toast} onRemove={remove} />
      ))}
    </div>
  );
}

interface ToastProps {
  toast: {
    id: string;
    type: 'success' | 'error' | 'warning' | 'info';
    title: string;
    message?: string;
  };
  onRemove: (id: string) => void;
}

function Toast({ toast, onRemove }: ToastProps) {
  const Icon = toastIcons[toast.type];
  const baseClass = toastColors[toast.type];

  return (
    <div
      className={cn(
        'flex items-start gap-3 rounded-lg p-4 shadow-xl backdrop-blur-xl border animate-in slide-in-from-right-full fade-in',
        baseClass
      )}
    >
      <div className="flex-shrink-0 mt-0.5">
        <Icon className="h-5 w-5" />
      </div>
      <div className="flex-1 min-w-0">
        <p className="font-semibold text-vnmate-neon">{toast.title}</p>
        {toast.message && (
          <p className="mt-1 text-sm text-vnmate-slate-300">{toast.message}</p>
        )}
      </div>
      <button
        onClick={() => onRemove(toast.id)}
        className="flex-shrink-0 text-vnmate-slate-400 hover:text-vnmate-neon transition-colors"
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  );
}