# Database Migrations

This directory is reserved for Alembic migration files.

PHASE 2 only adds the migration framework foundation and ORM metadata. Do not connect to a real PostgreSQL database or generate production migrations until the database environment is explicitly configured.

Initial migration setup target:

```bash
alembic init database/migrations
```

The Alembic `target_metadata` should point to `database.base.Base.metadata` after all model modules are imported.
