# Workbench Historical Run Reconciliation

The Workbench resolves completed historical results from the database. It does not rerun Quant, Flash, Pro, order planning, position sizing, fundamentals, providers, or LLM calls.

## Selection rule

The default is the latest compatible completed pipeline chain for the requested trade date. Quant, manifest, Flash, Pro, candidate hash, order, position, and export metadata must belong to the same chain. Incompatible records are not combined.

## Registry

`workbench_run_registry` stores only run relationships, counts, validation results, export path/hash, and reconciliation time. The unique key is `(trade_date, pipeline_run_id, candidate_set_hash)`, making reconciliation idempotent.

## Source modes

- `DATABASE`: a compatible persisted pipeline was resolved.
- `EMPTY`: no completed formal pipeline exists for the date.
- `MOCK`: available only through an explicit mock action and never used as an automatic fallback.

## Token ledger

Historical usage is reconstructed from persisted `model_validation_llm_audit` and `llm_usage` records. Request/call identifiers are deduplicated, cached or reused zero-token rows are not treated as missing usage, and unavailable records are reported separately.
