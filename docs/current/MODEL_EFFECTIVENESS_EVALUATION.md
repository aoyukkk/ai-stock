# Model Effectiveness Evaluation

- Evaluation version: `FULL_UNIVERSE_QUANT_EFFECTIVENESS_V1`
- Parent module: `MODEL_STAGE_FORWARD_EFFECTIVENESS_V2`
- Scope: `FULL_SCORED_UNIVERSE`
- Return basis: `RAW_CLOSE_SIGNAL_RETURN`
- Horizons: D1/D3/D5/D10 A股交易日
- Production mode: `SHADOW`

每日先独立计算全A Rank IC、Score IC、十分位收益、固定500名分组和前排非等宽分组，
再对成熟推荐日等权汇总。禁止把不同日期股票直接合池。Bootstrap以推荐日为抽样单位；
不足5个成熟推荐日时不输出置信区间。

行业超额仅在推荐日行业映射可证明PIT安全时计算。当前历史行业字段未满足该合同，因此
行业超额为NULL并标记 `INDUSTRY_MAPPING_NOT_PIT_SAFE`。

固定验收为2026-07-24、27、28、29四个推荐日，行情最多使用到2026-07-30。
2026-07-31数据禁止进入该验收。Top100既有结果、Flash覆盖门禁和冻结Decision均作为
回归基线，不得覆盖。
