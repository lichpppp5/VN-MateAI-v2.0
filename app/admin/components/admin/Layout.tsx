// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
'use client';

import React from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/Button';
import { 
  LayoutDashboard, 
  PlugZap, 
  GitBranch, 
  Settings, 
  Users, 
  Shield, 
  LogOut, 
  ChevronLeft, 
  Menu, 
  Bell, 
  HelpCircle,
  Server,
  Sun,
  Network,
  MonitorPlay
} from 'lucide-react';

const navigation = [
  { name: 'Topology', href: '/admin/topology', icon: Network },
  { name: 'Computer-Use', href: '/admin/computer-use', icon: MonitorPlay },
  { name: 'Dashboard', href: '/dashboard', icon: LayoutDashboard },
  { name: 'Plugins', href: '/plugins', icon: PlugZap },
  { name: 'Routing', href: '/routing', icon: GitBranch },
  { name: 'Workers', href: '/workers', icon: Server },
  { name: 'Settings', href: '/settings', icon: Settings },
];

export function Sidebar() {
  const pathname = usePathname();
  const [collapsed, setCollapsed] = React.useState(false);

  /**
   * Đăng xuất thật: xoá token ở CẢ khoá rồi quay về portal.
   *
   * Trước đây nút này có onClick rỗng kèm comment "logout" — bấm không làm
   * gì. Một nút đăng xuất không đăng xuất là nói dối người dùng về việc phiên
   * đã kết thúc, trong khi token vẫn nằm trong máy.
   *
   * Xoá cả `vnmateai_token` (portal) lẫn `vnmate_token` (admin) vì cả hai
   * cùng đọc chung token trong localStorage.
   */
  const handleSignOut = () => {
    try {
      localStorage.removeItem('vnmateai_token');
      localStorage.removeItem('vnmate_token');
    } finally {
      // Về portal để đăng nhập lại, thay vì đứng ở trang Admin với token đã
      // xoá và mọi lệnh gọi API sau đó đều 401.
      window.location.href = '/';
    }
  };

  return (
    <aside
      className={cn(
        'fixed left-0 top-0 z-40 h-screen bg-vnmate-slate-900/95 backdrop-blur-xl border-r border-vnmate-slate-800 transition-all duration-300',
        collapsed ? 'w-20' : 'w-64'
      )}
    >
      <div className="flex flex-col h-full">
        {/* Logo */}
        <div className={cn('flex items-center justify-between h-16 px-4 border-b border-vnmate-slate-800', collapsed && 'justify-center')}>
          <Link href="/dashboard" className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-vnmate-cyan to-vnmate-blue flex items-center justify-center">
              <Shield className="h-6 w-6 text-vnmate-darker" />
            </div>
            {!collapsed && (
              <span className="font-orbitron font-bold text-xl text-vnmate-cyan">VN-MateAI</span>
            )}
          </Link>
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setCollapsed(!collapsed)}
            className="text-vnmate-slate-400 hover:text-vnmate-neon"
          >
            {collapsed ? <ChevronLeft className="h-5 w-5 rotate-180" /> : <Menu className="h-5 w-5" />}
          </Button>
        </div>

        {/* Navigation */}
        <nav className="flex-1 p-4 space-y-1 overflow-y-auto" role="navigation" aria-label="Main navigation">
          {navigation.map((item) => {
            const isActive = pathname === item.href || (item.href !== '/admin/dashboard' && pathname.startsWith(item.href));
            return (
              <Link
                key={item.name}
                href={item.href}
                className={cn(
                  'flex items-center gap-3 px-3 py-2.5 rounded-lg transition-all duration-200',
                  isActive
                    ? 'bg-vnmate-cyan/10 text-vnmate-cyan border border-vnmate-cyan/30 shadow-glow-cyan'
                    : 'text-vnmate-slate-400 hover:text-vnmate-neon hover:bg-vnmate-slate-800/50',
                  collapsed && 'justify-center'
                )}
                title={collapsed ? item.name : undefined}
              >
                <item.icon className="h-5 w-5 flex-shrink-0" aria-hidden="true" />
                {!collapsed && <span className="font-medium">{item.name}</span>}
              </Link>
            );
          })}
        </nav>

        {/* Bottom section */}
        <div className={cn('p-4 border-t border-vnmate-slate-800', collapsed && 'hidden')}>
          <div className="mt-4 pt-4 border-t border-vnmate-slate-800">
            <Button
              variant="outline"
              className="w-full justify-start gap-2 text-vnmate-pink hover:text-vnmate-pink border-vnmate-pink/30 hover:border-vnmate-pink/50"
              onClick={handleSignOut}
            >
              <LogOut className="h-4 w-4" />
              <span>Đăng xuất</span>
            </Button>
          </div>
        </div>
      </div>
    </aside>
  );
}

export function TopBar({ onMenuClick }: { onMenuClick?: () => void }) {
  const pathname = usePathname();

  return (
    <header className="sticky top-0 z-30 h-16 bg-vnmate-slate-900/80 backdrop-blur-xl border-b border-vnmate-slate-800">
      <div className="flex items-center justify-between h-full px-6">
        <div className="flex items-center gap-4">
          {onMenuClick && (
            <Button variant="ghost" size="icon" onClick={onMenuClick} className="md:hidden">
              <Menu className="h-5 w-5" />
            </Button>
          )}
          <h1 className="text-xl font-orbitron font-bold text-vnmate-cyan hidden md:block">
            {pathname.split('/').pop()?.replace(/-/g, ' ').replace(/\b\w/g, c => c.toUpperCase()) || 'Dashboard'}
          </h1>
        </div>

        <div className="flex items-center gap-4">
          {/* Notifications */}
          <Button variant="ghost" size="icon" className="relative">
            <Bell className="h-5 w-5" />
            <span className="absolute -top-1 -right-1 w-4 h-4 bg-vnmate-pink text-xs font-bold rounded-full flex items-center justify-center">
              3
            </span>
          </Button>

          {/* Theme toggle */}
          <Button variant="ghost" size="icon">
            <Sun className="h-5 w-5" />
          </Button>

          {/* User menu */}
          <div className="flex items-center gap-3 pl-4 border-l border-vnmate-slate-800">
            <div className="w-8 h-8 rounded-full bg-vnmate-cyan/20 flex items-center justify-center text-vnmate-cyan font-semibold">
              A
            </div>
            <div className="hidden md:block text-left">
              <p className="text-sm font-medium text-vnmate-neon">Admin User</p>
              <p className="text-xs text-vnmate-slate-500">Administrator</p>
            </div>
          </div>
        </div>
      </div>
    </header>
  );
}

