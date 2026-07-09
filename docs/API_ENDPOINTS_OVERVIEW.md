# API Endpoints Overview

Canonical `/api/...` endpoints use the V0.3 response envelope:

```json
{"success": true, "data": {}, "error": null, "trace_id": "..."}
```

Legacy `/api/v1/...` endpoints remain available for compatibility and keep the
legacy response envelope:

```json
{"success": true, "code": "OK", "message": "ok", "data": {}, "trace_id": "..."}
```

For each implemented `/api/v1/...` business endpoint, the matching `/api/...`
alias reuses the same handler and returns the same core `data`.

No endpoint below performs real trading. No endpoint requires or returns a real API key.

| Module | Method | Path | Purpose | Mock-only status | Writes database | Real trading involved |
| --- | --- | --- | --- | --- | --- | --- |
| Health | GET | `/health` | Backend health and safety state | Mock-only | No | No |
| System | GET | `/api/v1/system/config-summary` | Safe runtime config summary | Mock-only | No | No |
| Database | GET | `/api/v1/database/health` | Local database connectivity | Mock-only | No | No |
| Data Sources | GET | `/api/v1/data-sources/status` | Provider status summary | Mock Provider only | No | No |
| Data Sources | GET | `/api/v1/data-sources/mock/stocks` | Mock stock universe | Mock Provider only | No | No |
| Data Sources | GET | `/api/v1/data-sources/mock/quotes` | Mock quote snapshot | Mock Provider only | No | No |
| Quant | GET | `/api/v1/quant/scan` | Rule-based quant ranking | Mock data only | Optional when `persist=true` | No |
| Quant | GET | `/api/v1/quant/config` | Quant config summary | Mock-only | No | No |
| Quant | POST | `/api/pools/run-quant-real` | Manual AKShare/BaoStock quant Top500 debug run | Manual real data debug, no LLM | Optional when `save_to_db=true` | No |
| LLM | GET | `/api/v1/llm/status` | LLM Gateway status | Mock LLM only | No | No |
| LLM | POST | `/api/v1/llm/mock-chat` | Mock chat response | Mock LLM only | Optional usage log | No |
| LLM | GET | `/api/v1/llm/usage-summary` | Mock usage summary | Mock LLM only | No | No |
| Screening | GET | `/api/v1/screening/light/run` | Light screening over quant candidates | Mock LLM only | Optional when `persist=true` | No |
| Screening | GET | `/api/v1/screening/light/config` | Screening config | Mock LLM only | No | No |
| Committee | GET | `/api/v1/committee/run` | AI committee ranking | Mock LLM only | Optional when `persist=true` | No |
| Committee | GET | `/api/v1/committee/config` | Committee config | Mock LLM only | No | No |
| Order Price | GET | `/api/v1/order-price/plans` | Advisory order-price plans | Rule engine + mock data | Optional when `persist=true` | No |
| Order Price | GET | `/api/v1/order-price/config` | Order price config | Mock-only | No | No |
| Virtual Trading | POST | `/api/v1/virtual-trading/accounts/default` | Create AI Simulation account | Virtual only | Yes | No |
| Virtual Trading | POST | `/api/v1/virtual-trading/run-plans` | Simulate order-plan execution | Virtual only | Yes | No |
| Virtual Trading | GET | `/api/v1/virtual-trading/account` | Virtual account summary | Virtual only | No | No |
| Virtual Trading | GET | `/api/v1/virtual-trading/orders` | Virtual orders | Virtual only | No | No |
| Virtual Trading | GET | `/api/v1/virtual-trading/positions` | Virtual positions | Virtual only | No | No |
| Virtual Trading | GET | `/api/v1/virtual-trading/trades` | Virtual trades | Virtual only | No | No |
| Virtual Trading | POST | `/api/v1/virtual-trading/orders/{order_id}/cancel` | Cancel virtual order | Virtual only | Yes | No |
| Virtual Trading | POST | `/api/v1/virtual-trading/orders/{order_id}/reprice` | Reprice virtual order | Virtual only | Yes | No |
| Alerts | POST | `/api/v1/alerts/intraday/scan` | Mock intraday alert scan | Mock data only | Yes | No |
| Alerts | GET | `/api/v1/alerts/recent` | Recent alerts | Mock data only | No | No |
| Alerts | GET | `/api/v1/alerts/config` | Alert config | Mock-only | No | No |
| Recheck | POST | `/api/v1/recheck/pre-market/run` | Pre-market advisory recheck | Mock data only | Yes | No |
| Recheck | POST | `/api/v1/recheck/order-plans/{order_plan_id}` | Recheck one advisory order plan | Mock data only | Yes | No |
| Recheck | GET | `/api/v1/recheck/config` | Recheck config | Mock-only | No | No |
| Review | POST | `/api/v1/review/daily/run` | Daily review generation | Mock LLM optional | Yes | No |
| Review | GET | `/api/v1/review/daily/{review_date}` | Daily review lookup | Mock-only | No | No |
| Review | POST | `/api/v1/review/predictions/evaluate` | Prediction evaluation | Mock-only | Yes | No |
| Review | POST | `/api/v1/review/order-plans/evaluate` | Order-plan evaluation | Mock-only | Yes | No |
| Review | GET | `/api/v1/review/config` | Review config | Mock-only | No | No |
| Memory | POST | `/api/v1/memory/notes` | Create memory note | Local database only | Yes | No |
| Memory | POST | `/api/v1/memory/search` | Search local memory | Local database only | Optional retrieval log | No |
| Memory | GET | `/api/v1/memory/notes/{note_id}` | Memory note lookup | Local database only | No | No |
| Memory | POST | `/api/v1/memory/links` | Link memory notes | Local database only | Yes | No |
| Memory | POST | `/api/v1/memory/reflection/from-review/{review_id}` | Reflection memory from review | Mock/local only | Yes | No |
| Memory | POST | `/api/v1/memory/playbook/from-review/{review_id}` | Playbook from review | Mock/local only | Yes | No |
| Memory | GET | `/api/v1/memory/playbooks` | Playbook list | Local database only | No | No |
| Memory | GET | `/api/v1/memory/config` | Memory config | Local database only | No | No |
| Config | GET | `/api/v1/config/effective` | Effective config | Mock-only safe config | No | No |
| Config | GET | `/api/v1/config/editable` | Editable config whitelist | Mock-only safe config | No | No |
| Config | PUT | `/api/v1/config/values/{config_key}` | Persist one whitelisted config | Safe whitelist only | Yes | No |
| Config | POST | `/api/v1/config/bulk` | Persist whitelisted config values | Safe whitelist only | Yes | No |
| Config | POST | `/api/v1/config/values/{config_key}/reset` | Reset config override | Safe whitelist only | Yes | No |
| Config | GET | `/api/v1/config/history` | Config change history | Safe whitelist only | No | No |
