# AI Trader Assistant Frontend

Phase 13 frontend control panel foundation for the AI Trader Assistant V0.3 backend.

This is an advisory trading control panel. It displays mock data, mock LLM status, AI simulation records, alerts, daily review, and memory notes. It does not provide real trading entry points and must rely on backend `real_trading_enabled` status for safety display.

## Commands

```bash
npm install
npm run dev:web
npm run typecheck
npm run build
```

Electron is a basic shell only in this phase:

```bash
npm run dev
```

## Safety

- `VITE_ENABLE_REAL_TRADING=false` by default.
- Real trading cannot be enabled from the frontend.
- AI Simulation actions are clearly labeled as virtual trading.
- No API keys, secrets, passwords, account credentials, or tokens are stored or displayed.
