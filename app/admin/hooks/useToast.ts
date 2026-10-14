// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
'use client';

import { create } from 'zustand';

interface Toast {
  id: string;
  type: 'success' | 'error' | 'warning' | 'info';
  title: string;
  message?: string;
  duration?: number;
}

interface ToastState {
  toasts: Toast[];
  addToast: (toast: Omit<Toast, 'id'>) => void;
  removeToast: (id: string) => void;
  clearToasts: () => void;
}

// KHÔNG persist: một thông báo là sự kiện của phiên hiện tại. Trước đây toasts
// được lưu vào localStorage, nên tải lại trang là các toast cũ hiện lại y
// nguyên — người dùng thấy "Chưa lưu được" từ 5 phút trước như thể vừa xảy ra.
// Điều này cũng trái quy ước chung: chỉ báo cáo điều vừa thực sự xảy ra.
export const useToastStore = create<ToastState>()((set) => ({
  toasts: [],
  addToast: (toast) => {
    const id = Math.random().toString(36).substring(7);
    set((state) => ({
      toasts: [...state.toasts, { ...toast, id }],
    }));
    if (toast.duration !== 0) {
      setTimeout(() => {
        set((state) => ({
          toasts: state.toasts.filter((t) => t.id !== id),
        }));
      }, toast.duration || 5000);
    }
  },
  removeToast: (id) =>
    set((state) => ({
      toasts: state.toasts.filter((t) => t.id !== id),
    })),
  clearToasts: () => set({ toasts: [] }),
}));

export function useToast() {
  const { toasts, addToast, removeToast } = useToastStore();

  return {
    toasts,
    toast: (type: 'success' | 'error' | 'warning' | 'info', title: string, message?: string) =>
      addToast({ type, title, message, duration: type === 'error' ? 6000 : 4000 }),
    success: (title: string, message?: string) =>
      addToast({ type: 'success', title, message, duration: 4000 }),
    error: (title: string, message?: string) =>
      addToast({ type: 'error', title, message, duration: 6000 }),
    warning: (title: string, message?: string) =>
      addToast({ type: 'warning', title, message, duration: 5000 }),
    info: (title: string, message?: string) =>
      addToast({ type: 'info', title, message, duration: 4000 }),
    remove: removeToast,
  };
}