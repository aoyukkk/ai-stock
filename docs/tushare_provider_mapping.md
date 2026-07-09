# Tushare Provider Mapping

This document records the first-pass Tushare Pro mapping for the manual real-data provider. Token values are never stored here; runtime access uses `token_env: TUSHARE_TOKEN`.

| Module | Tushare API | Main fields | Permission note | Implemented | Used by quant | Fallback |
| --- | --- | --- | --- | --- | --- | --- |
| Basic stock universe | `stock_basic` | `ts_code`, `symbol`, `name`, `industry`, `market`, `list_date`, `list_status`, `is_hs` | 2000 points per official docs | Yes | Universe, ST filter | Mock/BaoStock universe only by manual script |
| Trade calendar | `trade_cal` | `cal_date`, `is_open`, `pretrade_date` | 2000 points | Yes | Date validation later | None |
| Daily kline | `daily` | `ts_code`, `trade_date`, `open`, `high`, `low`, `close`, `pre_close`, `vol`, `amount` | 120+ points, frequency depends on points | Yes | Technical, momentum, risk | BaoStock kline |
| Weekly/monthly kline | `weekly`, `monthly` | `ts_code`, `trade_date`, OHLC, `vol`, `amount` | 2000 points | Yes | Smoke/manual analysis | BaoStock daily-derived later |
| Adjustment factor | `adj_factor` | `ts_code`, `trade_date`, `adj_factor` | 2000 points | Yes | Factor detail | Skip factor |
| Daily basic | `daily_basic` | `turnover_rate`, `volume_ratio`, `pe`, `pb`, `total_mv`, `limit_status` | 2000+ points | Yes | Capital/risk detail | Skip enhancement |
| Limit price | `stk_limit` | `up_limit`, `down_limit`, `pre_close` | 2000+ points | Yes | Risk/detail | Mock fallback |
| ETF | `fund_basic`, `fund_daily` | `ts_code`, `name`, `trade_date`, OHLC | 2000 points | Yes | Not first-pass Top500 | Skip |
| Options | `opt_basic` | `ts_code`, `name`, `exercise_price`, dates | 2000+ points | Smoke only | No | Skip |
| Finance | `income`, `balancesheet`, `cashflow`, `fina_indicator` | revenue/profit/ROE/debt indicators | 2000+ points | Yes | Risk/detail, not heavy daily scoring | Skip enhancement |
| Macro | `shibor`, `shibor_lpr` | rate/date fields | Mixed point requirements | Skeleton | No | Skip |
| Pledge | `pledge_stat`, `pledge_detail` | `pledge_ratio`, `pledge_amount`, holder fields | 2000 points | Yes | Risk detail | Skip enhancement |
| Unlock | `share_float` | unlock date/amount fields | 3000 points | Yes | Risk detail | Skip enhancement |
| Repurchase | `repurchase` | announcement/date/amount fields | 2000 points | Yes | Risk detail | Skip enhancement |
| Holder trade | `stk_holdertrade` | `in_de`, `change_vol`, `change_ratio` | 2000 points | Yes | Risk detail | Skip enhancement |
| Top list | `top_list`, `top_inst` | `l_buy`, `l_sell`, `net_amount`, institution fields | 2000/5000 points | Yes | Capital detail | Skip enhancement |
| Margin | `margin`, `margin_detail` | `rzye`, `rqye`, `rzrqye`, `rzmre` | 2000 points | Yes | Capital/risk detail | Skip enhancement |
| Concept | `ths_index`, `ths_member` | concept index code/name, member stock fields | Official THS concept/industry APIs | Yes | Emotion detail | Skip enhancement |
| Moneyflow | `moneyflow` | small/medium/large/extra-large buy/sell, `net_mf_amount` | 2000 points | Yes | Capital factor enhancement | Skip enhancement |
| Broker recommendations | `broker_recommend` | `month`, `broker`, `ts_code`, `name` | 6000 points | Yes | Reference only | Skip |
| Chip distribution | `cyq_chips` | `price`, `percent` | Higher/feature permission | Yes | Coverage flag only | Skip |
| Quant factors | `stk_factor_pro` | OHLC plus technical factor columns | Higher/feature permission | Yes | Coverage flag/detail only | Do not override score |

Official references checked:

- Tushare HTTP API docs describe `api_name`, token, params, fields, and error code semantics.
- Tushare permission docs list point requirements for daily/weekly/monthly, daily_basic, top_list/top_inst, pledge, margin, moneyflow, finance, fund, option, macro, and feature APIs.
- Tushare plaintext endpoint docs were checked for `stock_basic`, `trade_cal`, `daily`, `weekly`, `monthly`, `adj_factor`, `daily_basic`, `stk_limit`, `moneyflow`, `top_list`, `top_inst`, `pledge_stat`, `pledge_detail`, `margin`, `margin_detail`, `ths_index`, `ths_member`, `broker_recommend`, `cyq_chips`, and `stk_factor_pro`.
