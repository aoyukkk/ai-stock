# AI Trader Assistant 1.0.0 安装说明

## 系统要求

- Windows 10 或 Windows 11，x64
- 建议至少 8 GB 内存和 3 GB 可用磁盘空间
- 查看内置历史结果不需要联网
- 更新行情或运行真实 LLM 分析时需要联网和相应密钥

目标电脑不需要安装 Python、Conda、Node.js、npm、PostgreSQL 或 Redis。

## 安装版

1. 运行 `AI-Trader-Assistant-Setup-1.0.0-x64-unsigned.exe`。
2. 选择安装目录并完成安装。
3. 从桌面或开始菜单启动 AI Trader Assistant。
4. 按首次设置向导检查数据目录，可选择跳过密钥配置。

未签名的内部发行版可能触发 Windows 安全提示。请先核对 `checksums/SHA256SUMS.txt` 中的 SHA-256，再确认运行。

## 可携版

解压 `AI-Trader-Assistant-Portable-1.0.0-x64-unsigned.zip` 到可写目录，运行 `AI Trader Assistant.exe`。不要直接在 ZIP 内运行。

## 升级与卸载

升级安装不会覆盖用户数据库和已有输出。启动前会备份已有 SQLite 数据库，默认保留最近 10 份。卸载默认保留用户数据；如需彻底删除，请先在设置或日志入口确认用户数据目录，再手动处理。

## 完整性校验

PowerShell 示例：

```powershell
Get-FileHash .\AI-Trader-Assistant-Setup-1.0.0-x64-unsigned.exe -Algorithm SHA256
```

将结果与发行目录内 `SHA256SUMS.txt` 对照。
