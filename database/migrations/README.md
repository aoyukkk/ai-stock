# Database Migrations

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
