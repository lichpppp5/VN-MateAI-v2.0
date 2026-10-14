// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
import { type ClassValue, clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatDate(date: Date | string): string {
  const d = new Date(date);
  return d.toLocaleTimeString('vi-VN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });
}

export function formatDateFull(date: Date | string): string {
  const d = new Date(date);
  return d.toLocaleString('vi-VN', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });
}

export function truncate(str: string, length: number): string {
  if (str.length <= length) return str;
  return str.slice(0, length) + '...';
}

export function generateId(): string {
  return Math.random().toString(36).substring(2, 15);
}

export function debounce<T extends (...args: unknown[]) => unknown>(
  fn: T,
  delay: number
): (...args: Parameters<T>) => void {
  let timeoutId: NodeJS.Timeout;
  return (...args: Parameters<T>) => {
    clearTimeout(timeoutId);
    timeoutId = setTimeout(() => fn(...args), delay);
  };
}

export function getStatusColor(status: 'online' | 'offline' | 'warning'): string {
  switch (status) {
    case 'online':
      return 'text-vnmate-emerald';
    case 'offline':
      return 'text-vnmate-slate-500';
    case 'warning':
      return 'text-vnmate-amber';
    default:
      return 'text-vnmate-slate-500';
  }
}

export function getStatusDotClass(status: 'online' | 'offline' | 'warning'): string {
  switch (status) {
    case 'online':
      return 'status-dot status-online';
    case 'offline':
      return 'status-dot status-offline';
    case 'warning':
      return 'status-dot status-warning';
    default:
      return 'status-dot status-offline';
  }
}