# Database Migrations

Phase 2 provides the migration placeholder and table initialization script.

Current command:

```bash
python scripts/init_db.py
```

Alembic can be introduced in a later hardening phase. Do not place API keys,
database passwords, broker accounts, or real trading credentials in migration
files.
