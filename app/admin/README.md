# VN-MateAI Admin Control Center

Enterprise-grade administration dashboard for the VN-MateAI RPA platform. Built with Next.js 14, Tailwind CSS, and shadcn/ui components.

## 🎯 Features

### Dynamic Form Engine (`DynamicForm`)
- **Schema-driven forms**: Render forms automatically from JSON Schema
- **Validation**: Built-in Zod validation with react-hook-form
- **Field types**: Text, Password, Select, Switch, Textarea, Hidden
- **Extensible**: Add new field types without modifying core code
- **Use case**: When backend adds new plugins (MISA, Zalo, etc.), UI auto-generates config forms

### Plugin & Credentials Vault (`/admin/plugins`)
- **Connector Grid**: Visual cards for M365, Legacy AI, AWS, OCI, Paperless
- **Status Indicators**: Real-time connection status (Connected/Disconnected/Error)
- **Config Modal**: DynamicForm-powered configuration dialogs
- **Secure Fields**: Password/secret fields with show/hide toggle
- **Test Connection**: One-click connectivity testing with toast notifications

### Smart Routing Builder (`/admin/routing`)
- **Drag-to-Reorder**: Priority-based rule ordering
- **Condition Builder**: Task type, risk level, user role, time range, custom expressions
- **Action Router**: M365 Direct, Legacy Bot, ESP32 Voice, Webhook, Email
- **Live Preview**: See routing logic in real-time
- **JSON Storage**: Rules stored as JSON in `routing_rules` table

### Command Center Dashboard (`/admin/dashboard`)
- **Real-time Stats**: Workers, plugins, tasks, approvals
- **System Health**: CPU, memory, uptime with visual indicators
- **Live Audit Log**: WebSocket-powered activity feed
- **Pending Approvals**: High-risk task approval queue with one-click actions
- **Worker Status**: External worker monitoring (Mac Minis, Ubuntu, ESP32)

### Settings (`/admin/settings`)
- **General**: App name, URL, timezone, maintenance mode
- **Security**: 2FA, IP whitelist, session timeout, audit logging
- **Notifications**: Email, Telegram, Slack, Webhook, SMS channels
- **Database**: Connection config, backup settings
- **API Keys**: Rotatable keys with environment variable mapping

## 🛠 Tech Stack

| Layer | Technology |
|-------|------------|
| Framework | Next.js 14 (App Router) |
| Styling | Tailwind CSS + shadcn/ui |
| Forms | react-hook-form + Zod |
| State | Zustand + React Query patterns |
| Real-time | WebSocket (Socket.io client) |
| Icons | Lucide React |
| Theme | Dark mode with Cyber Neon accents |

## 📁 Project Structure

```
admin/
├── app/
│   ├── admin/
│   │   ├── dashboard/     # Command Center
│   │   ├── plugins/       # Plugin Vault
│   │   ├── routing/       # Routing Builder
│   │   ├── workers/       # Worker Status
│   │   ├── settings/      # Settings
│   │   ├── layout.tsx     # Admin layout wrapper
│   │   └── page.tsx       # Redirect to dashboard
│   ├── globals.css        # Cyber Neon theme
│   └── layout.tsx         # Root layout
├── components/
│   ├── admin/
│   │   ├── DynamicForm.tsx    # Core form engine
│   │   ├── PluginCard.tsx     # Plugin display card
│   │   ├── RoutingBuilder.tsx # Routing rule builder
│   │   ├── DashboardComponents.tsx
│   │   ├── AuditLogFeed.tsx   # Live activity feed
│   │   ├── PendingApprovals.tsx
│   │   ├── WorkerStatus.tsx
│   │   └── Layout.tsx         # Sidebar + TopBar
│   └── ui/                    # shadcn/ui components
├── hooks/
│   ├── usePlugins.ts          # Plugin management
│   ├── useRoutingRules.ts     # Routing rules CRUD
│   ├── useDashboard.ts        # Dashboard data + WebSocket
│   └── useToast.ts            # Toast notifications
├── lib/
│   ├── api.ts                 # API client + types
│   └── utils.ts               # Helpers
├── .env.example
├── next.config.js
├── package.json
├── tailwind.config.ts
├── tsconfig.json
└── postcss.config.js
```

## 🚀 Getting Started

### Prerequisites
- Node.js 18+
- Backend API running on `http://localhost:8000`
- WebSocket server on `ws://localhost:8000`

### Installation

```bash
cd admin
npm install
cp .env.example .env.local
# Edit .env.local with your values
npm run dev
```

Open `http://localhost:3001` - you'll be redirected to `/admin/dashboard`

## 🔧 Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `NEXT_PUBLIC_API_BASE` | Backend REST API base URL | `http://localhost:8000/api/v1` |
| `NEXT_PUBLIC_WS_URL` | WebSocket server URL | `ws://localhost:8000` |
| `NEXTAUTH_SECRET` | NextAuth secret key | Required |
| `NEXTAUTH_URL` | Admin URL | `http://localhost:3001` |

### Backend API Endpoints Expected

```
GET    /api/v1/plugins                    # List all plugins
GET    /api/v1/plugins/:id/config         # Get plugin config
PUT    /api/v1/plugins/:id/config         # Update plugin config
POST   /api/v1/plugins/:id/test           # Test connection

GET    /api/v1/routing/rules              # List routing rules
POST   /api/v1/routing/rules              # Create rule
PATCH  /api/v1/routing/rules/:id          # Update rule
DELETE /api/v1/routing/rules/:id          # Delete rule

GET    /api/v1/dashboard/stats            # Dashboard statistics
GET    /api/v1/dashboard/audit-logs       # Audit logs
GET    /api/v1/dashboard/approvals        # Pending approvals
PATCH  /api/v1/dashboard/approvals/:id    # Approve/reject
GET    /api/v1/workers                    # Worker status

WS     /ws/admin                          # Admin real-time events
WS     /ws/workers                        # Worker status updates
```

## 🎨 Design System

### Colors (CSS Variables)
```css
--vnmate-cyan: #00f2fe      /* Primary accent */
--vnmate-neon: #00ffff      /* Bright accent */
--vnmate-emerald: #10b981   /* Success */
--vnmate-amber: #f59e0b     /* Warning */
--vnmate-pink: #ec4899      /* Error/Destructive */
--vnmate-purple: #a855f7    /* Special actions */
--vnmate-dark: #020813      /* Background */
--vnmate-darker: #01060e    /* Darker background */
```

### Typography
- **Orbitron**: Headers, branding, numbers
- **Rajdhani**: UI labels, buttons
- **Inter**: Body text
- **JetBrains Mono**: Code, technical data

### Component Classes
- `.btn-cyber` - Primary action button (cyan)
- `.btn-cyber-emerald` - Success action (emerald)
- `.btn-cyber-destructive` - Destructive action (pink)
- `.input-cyber` - Styled input
- `.glass-card` - Glassmorphism card
- `.neon-border` - Cyan glow border
- `.cyber-grid` - Background grid pattern

## 🔌 Extending the System

### Adding a New Plugin Type
1. Backend: Add plugin to `/api/v1/plugins` with `configSchema`
2. Frontend: No code changes needed - DynamicForm auto-renders
3. Optional: Add logo to plugin card

### Adding a New Field Type to DynamicForm
```typescript
// In DynamicForm.tsx, add to renderInput():
case 'your-widget':
  return <YourComponent {...baseProps} />;
```

### Adding a New Routing Action
1. Add to `ACTION_TYPES` in `RoutingBuilder.tsx`
2. Backend: Handle new action type in routing engine
3. Optional: Add custom config schema

## 📝 Development Notes

### Code Style
- TypeScript strict mode
- ESLint + Prettier
- Component-first architecture
- Custom hooks for data fetching
- Optimistic UI updates

### Performance
- Dynamic imports for heavy components
- WebSocket connection pooling
- Memoized computations
- Skeleton loading states

### Accessibility
- ARIA labels on all interactive elements
- Keyboard navigation support
- Focus management
- Color contrast compliance

## 🧪 Testing

```bash
npm run lint      # ESLint
npm run build     # Production build check
```

## 🚢 Deployment

### Docker
```dockerfile
FROM node:18-alpine
WORKDIR /app
COPY package*.json ./
RUN npm ci --only=production
COPY . .
RUN npm run build
EXPOSE 3001
CMD ["npm", "start"]
```

### Vercel (Recommended)
1. Connect repository
2. Set environment variables
3. Deploy automatically on push

## 📄 License

Internal use only - VN-MateAI Enterprise RPA Platform