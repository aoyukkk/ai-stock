# Database Migrations

`20260713_add_workbench_run_registry.sql` adds only relationship metadata used to reconcile completed historical pipeline runs. It does not copy or modify Quant, Flash, Pro, order, position, fundamental, or LLM usage records.

Phase 2 provides the migration placeholder and table initialization script.

Current command:

```bash
python scripts/init_db.py
```

Alembic can be introduced in a later hardening phase. Do not place API keys,
database passwords, broker accounts, or real trading credentials in migration
files.

Existing databases created before the real LLM gateway readiness phase need the
one-time `20260710_add_llm_usage_prompt_version.sql` migration. New databases
created with `scripts/init_db.py` already include the column.

For the V0.4 tiered-routing audit fields, run the idempotent helper:

```bash
python scripts/migrate_llm_audit_v04.py
```
### 20260710_add_model_validation_v07.sql

Adds isolated, non-actionable MODEL_VALIDATION run, sample, LLM audit, read-only order plan,
validation account snapshot, and allocation tables. It does not alter formal order, broker,
paper-trading, or execution tables.

### 20260710_add_trader_demo_audit_fields_v08.sql

Adds compact error category, schema field, and sanitized message columns to model validation
LLM audit rows so per-stock failures remain diagnosable without storing model responses.

### 20260710_add_llm_failure_diagnostics_v09.sql

Adds sanitized response-boundary diagnostics and an append-only failure ledger. No raw model
response, prompt, API key, or chain-of-thought is stored.

### 20260710_add_pro_single_review_v11.sql

Adds single-stock Pro V3 resume and deterministic ranking fields.

### 20260710_add_flash_v4_order_semantics_v12.sql

Adds TP1/TP2 risk-reward, active target, and unrounded stop fields for advisory model validation.
# Selection Performance V1

Run `python scripts/migrate_selection_performance_v1.py` after upgrading an existing development database. The migration is additive and idempotent.
