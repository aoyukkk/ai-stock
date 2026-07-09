# Final Integration Checklist

V0.3 Mock-only freeze checklist:

- `python scripts/check_environment.py` passes.
- `python scripts/check_security_config.py` passes.
- `python scripts/run_final_smoke.py` passes.
- `python -m pytest` passes.
- `python -m compileall .` passes.
- `cd frontend && npm run typecheck` passes.
- `cd frontend && npm run build` passes.
- `ENABLE_REAL_TRADING=false`.
- `real_trading_enabled=false`.
- `llm.mock_only=true`.
- Data providers are mock-only.
- Electron preload does not expose shell execution or filesystem write APIs.
- Packaging config does not reference runtime env files.
- No real broker adapter is enabled.
- No automatic real-market order placement exists.
- API documentation exists.
- Operator quick start exists.
- Release notes exist.
- Next phase roadmap exists.

Freeze status: V0.3 is ready for human review as a local Mock-only advisory and simulation system.
