// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
import type { Metadata } from 'next';
import { Inter, JetBrains_Mono, Orbitron, Rajdhani } from 'next/font/google';
import './globals.css';

const inter = Inter({ subsets: ['latin'], variable: '--font-inter' });
const jetbrains = JetBrains_Mono({ subsets: ['latin'], variable: '--font-jetbrains' });
const orbitron = Orbitron({ subsets: ['latin'], weight: ['500', '700', '800', '900'], variable: '--font-orbitron' });
const rajdhani = Rajdhani({ subsets: ['latin'], weight: ['500', '600', '700'], variable: '--font-rajdhani' });

export const metadata: Metadata = {
  title: 'VN-MateAI Admin Control Center',
  description: 'Enterprise RPA Administration Dashboard',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="vi" className={`${inter.variable} ${jetbrains.variable} ${orbitron.variable} ${rajdhani.variable} dark`}>
      <head>
        {/* Orbitron / Rajdhani / Share Tech Mono tự lưu (scripts/vendor_web_assets.py) — chạy được khi mạng LAN không ra Internet */}
        <link rel="stylesheet" href="/static/fonts-hud.css?v=1" />
      </head>
      <body className="min-h-screen bg-vnmate-dark text-vnmate-neon">
        {children}
      </body>
    </html>
  );
}