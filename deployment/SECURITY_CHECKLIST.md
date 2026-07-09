# Security Checklist

Required before development startup or packaging:

- `ENABLE_REAL_TRADING=false`.
- `AI_AUTO_REAL_ORDER_ENABLED=false`.
- `virtual_trading.real_trading_enabled=false`.
- Mock Provider only.
- Mock LLM only.
- No broker connection.
- No automatic real-market order placement.
- 禁止实盘自动交易.
- AI outputs are advisory only.
- Virtual trading is simulation only.
- Do not include `.env` in packaging artifacts.
- Do not include API Key, password, token, secret, private key, or broker credential.
- Electron keeps `nodeIntegration=false`.
- Electron keeps `contextIsolation=true`.
- Preload exposes only a small safe shell metadata API.

Run:

```powershell
python scripts/check_security_config.py
```

If any item fails, stop packaging and fix the configuration first.

Final freeze regression:

```powershell
python scripts/run_final_smoke.py
python -m pytest tests/test_final_safety_regression.py
```
