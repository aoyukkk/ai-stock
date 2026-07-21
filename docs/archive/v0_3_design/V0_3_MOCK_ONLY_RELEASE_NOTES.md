# V0.3 Mock-only Release Notes

## Completed Capabilities

- FastAPI backend with unified API responses.
- SQLAlchemy ORM and local SQLite workflow.
- Mock data provider.
- Quant engine.
- Mock-only LLM Gateway.
- LLM light screening.
- AI committee.
- Rule-based order price plans.
- Virtual trading simulator.
- Pre-market recheck and intraday alerts.
- Daily review.
- Memory system.
- Vue 3 + Electron control panel.
- Database-backed editable config with history.
- Local deployment and packaging preparation.
- Final smoke and safety regression tests.

## System Boundary

V0.3 is local, Mock-only, and advisory.

- No automatic real-market trading.
- No real broker integration.
- No real LLM integration.
- No real market/news data integration.
- Only local Mock testing and trading-assistance workflow validation.

## Main Modules

Backend, database, datasource, quant, screening, agents, order_price, trading, alerts, recheck, review, memory, llm_gateway, frontend, deployment scripts, and packaging skeletons.

## Test Summary

The final acceptance suite covers environment checks, security checks, final API smoke tests, backend unit/integration tests, frontend typecheck, frontend build, and Python compile checks.

## Known Limits

- Real data providers are not implemented.
- Real model providers are not implemented.
- Historical backtest is not implemented.
- Production permissions, monitoring, backup, and recovery are not implemented.
- Signed installer and auto-update are not implemented.
- Any real-market trading remains outside the system.

## Next Recommendation

After human review of V0.3, decide whether to enter PHASE 17 - Real Data Provider Integration.
