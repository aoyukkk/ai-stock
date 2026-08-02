# Engineering Change Rules

1. 全量快照不可覆盖；相同业务键Hash不同时报
   `FULL_UNIVERSE_SNAPSHOT_IMMUTABLE_CONFLICT`。
2. 原始排名、缺失股票和失败状态必须保留。
3. 约5300只股票使用批量缓存和批量数据库写入。
4. 不建立第二套Provider或逐股API路径。
5. 评估结果不得反向修改模型参数。
6. 新结果必须带run_id、evaluation_version、input/outcome/report Hash。
7. Excel组件不可用时明确标记 `EXCEL_EXPORT_UNAVAILABLE`。
8. 真实订单、虚拟订单、LLM、搜索、外部API和Scheduler调用均须为0。
9. 不执行Git commit，除非用户另行明确要求。
