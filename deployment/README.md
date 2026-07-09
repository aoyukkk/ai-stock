# Deployment Notes

Phase 15 prepares local deployment and packaging skeletons for AI Trader Assistant V0.3.

The application remains an advisory system:

- Mock Provider only.
- Mock LLM only.
- AI Simulation / virtual trading only.
- Human operators make all real-market decisions manually.
- 禁止实盘自动交易.

Start with `WINDOWS_LOCAL_DEPLOYMENT.md`, then use `PACKAGING_GUIDE.md` when preparing local packaging artifacts.

Final V0.3 freeze checks:

```powershell
python scripts/run_final_smoke.py
python scripts/run_all_checks.py
```

Operator documentation lives in `../docs/OPERATOR_QUICK_START.md`.
