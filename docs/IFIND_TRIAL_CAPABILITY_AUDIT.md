# iFinD HTTP 能力审计

审计时间：2026-07-14T13:15:02.305918+00:00

## 结论

- 模式：REAL_HTTP_PROBE
- 状态：HTTP_PROBE_COMPLETED
- 传输方式：HTTP
- HTTP 认证：CONFIGURED
- 认证调用：1
- 数据调用：4/10
- 成功调用：4
- 失败调用：0
- 正式业务表写入：0
- 生产路由变更：False
- Scheduler：False
- 真实交易：False
- 原始响应保存：False

## 能力矩阵

| 能力 | Endpoint | 状态 | 行数 | 延迟 ms | 数据状态 |
|---|---|---|---:|---:|---|
| Index daily | cmd_history_quotation | AVAILABLE | 14 | 208 | CLOSED_SESSION_FINAL |
| Index realtime | real_time_quotation | AVAILABLE | 2 | 225 | CLOSED_SESSION_FINAL |
| Stock realtime | real_time_quotation | AVAILABLE | 4 | 198 | CLOSED_SESSION_FINAL |
| Minute bars | high_frequency | AVAILABLE | 11 | 194 | CLOSED_SESSION_FINAL |
| Five-level order book | - | NOT_TESTED | 0 | - | TIME_UNKNOWN |
| Opening auction | - | NOT_TESTED | 0 | - | TIME_UNKNOWN |
| THS industry classification and market data | - | NOT_TESTED | 0 | - | TIME_UNKNOWN |
| Concept classification and market data | - | NOT_TESTED | 0 | - | TIME_UNKNOWN |

## 认证与安全

- 凭据状态：CONFIGURED
- SDK 凭据状态：CONFIGURED
- HTTP 凭据状态：CONFIGURED
- Access Token 仅保存在进程内存和本地加密/环境配置中。
- 报告不包含 Token、用户名、密码、请求 Header 或完整原始响应。
- Tushare 继续作为全 A 日频、Quant、财务和历史回放主数据源。
- iFinD 生产开关和盘中监控保持关闭。

## 当前问题

- 无
