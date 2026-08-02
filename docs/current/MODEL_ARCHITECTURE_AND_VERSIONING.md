# Model Architecture and Versioning

```text
Frozen V2 full-universe result
  -> hard-gate-free ranked Top100
  -> V3.1 one structured event search per stock (Shadow only)
  -> local freshness / URL / source / dedup eligibility
  -> confidence-gated event delta; no eligible evidence means delta=0
  -> immutable V3.1 snapshot and gated Web publication
  -> immutable FULL_SCORED_UNIVERSE snapshot
  -> local batch close outcomes
  -> daily cross-sectional metrics
  -> equal-weight aggregation by ranking day
  -> read-only API / Web / CSV / JSON / Markdown
```

版本必须显式指定：

- Quant: `TUSHARE_QUANT_V2_CORRECTED_SHADOW`
- Evaluation: `FULL_UNIVERSE_QUANT_EFFECTIVENESS_V1`
- Scope: `FULL_SCORED_UNIVERSE`
- Event Overlay: `LLM_SCREENING_V3_1_FRESH_EVENT_OVERLAY_SHADOW`
- Event Review: `EVENT_REVIEW_V3_FRESHNESS_VERIFIED_OUTCOME_SEPARATED`

不得自动选择最新版本或混合不同Quant Run。生产Quant、Flash、Prompt、Checkpoint、
候选、价格和订单均不受该研究模块影响。V3.1只能作为日常Shadow阶段运行，不得覆盖
V2正式结果或由LLM逐因子重算Quant。
