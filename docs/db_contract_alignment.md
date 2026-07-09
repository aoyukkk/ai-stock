# DB Contract Alignment

Spec Alignment Phase A keeps the current ORM table names intact and documents
their V0.3 contract aliases. No destructive migration or `__tablename__` rename
is performed in this phase.

| Current ORM table | V0.3 contract table | Status | Notes |
| --- | --- | --- | --- |
| `news` | `news_raw` / `news_event` | Fields partially aligned, table name differs | Current table stores raw news-like content plus derived importance/sentiment fields. Future migration should split raw source payload from normalized event records. |
| `news_stock_relation` | `event_stock_link` | Fields partially aligned, table name differs | Current relation links news records to stocks. Future migration should point to normalized `news_event` records. |
| `trading_account` | `virtual_account` | Fields basically aligned, table name differs | Current account model is used only for virtual trading in Phase 1. Future migration can introduce a contract alias or rename with migration scripts. |
| `trade_order` | `virtual_order` | Fields basically aligned, table name differs | Current orders are virtual simulation records. API DTOs should expose virtual-order naming before any DB rename. |
| `position` | `virtual_position` | Fields basically aligned, table name differs | Current positions are simulation positions. Future migration should preserve historical virtual account references. |
| `trade_record` | `virtual_trade_record` | Fields basically aligned, table name differs | Current trade records are virtual fills. Future migration should retain audit linkage to order plans. |
| Not implemented | `manual_trade` | Field gap, future migration required | Manual real-trade comparison is documented but not implemented in this phase. |
| Not implemented | `stock_temporal_relation` | Field gap, future migration required | Temporal relation graph storage is documented but not implemented in this phase. |
| `stock_market_data` | `market_snapshot` | Fields partially aligned, table name differs | Current market data table stores quote-like snapshots. Future migration should define snapshot granularity and retention rules. |

## Compatibility Rules

- Existing ORM models and table names remain unchanged.
- Existing database tests continue to target the current ORM table names.
- API/service layers may expose V0.3 contract names through DTOs or aliases.
- Future migrations must be explicit, reversible, and tested against existing data.
- Real trading tables are not introduced in this phase.

## Future Migration TODO

- Add read-side DTO aliases for `virtual_account`, `virtual_order`,
  `virtual_position`, and `virtual_trade_record`.
- Split `news` into raw source records and normalized event records.
- Add `manual_trade` only when manual trade comparison is implemented.
- Add `stock_temporal_relation` only when temporal memory graph persistence is
  implemented.
