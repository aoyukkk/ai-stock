# 桌面运行时安全边界

状态：`SHADOW_READY_FOR_RELEASE_REVIEW`。该状态只证明源码、自动测试、隔离 `win-unpacked` 构建与本机烟测完成，不代表正式签名、安装器、发布或生产晋级。

## 信任模型

桌面应用采用以下边界：

```text
Renderer → preload 固定窄接口 → 受信 IPC → Main → 127.0.0.1 FastAPI
```

- Renderer 不读取后端 base URL 或 session token，不能传 Host、Header、Cookie、重定向目标或可执行命令。
- Main 持有随机本地 token，代理只允许固定 HTTP 方法、相对 `/api/` 与 `/health` 路径，敏感 runtime/Secret 路径另行拒绝。
- 每个敏感 IPC 校验当前主窗口 WebContents、主 Frame 和精确 renderer URL；子 Frame、DevTools、未知窗口与外部页面均拒绝。
- IPC 参数采用固定字段和枚举，Secret 限制为非空字符串、无 NUL、最多 8192 字节。

## BrowserWindow 与外链

- `nodeIntegration=false`、`contextIsolation=true`、`sandbox=true`。
- packaged 模式关闭 DevTools；未关闭 `webSecurity`，未启用 `allowRunningInsecureContent`。
- 新窗口、未知导航、下载和全部 renderer 权限申请默认拒绝，并对未来新建 WebContents 安装相同策略。
- 外链只能经 Main 的 `openExternal` 打开无凭据 HTTPS URL；拒绝 HTTP、file、javascript、data、vbscript、shell、cmd 及自定义协议。
- 开发 renderer 只允许精确的 `127.0.0.1:5173`；生产只允许应用自身的精确 `file:` 入口。

## Secret

- packaged 默认使用 Electron `safeStorage`；Windows 下由当前操作系统用户保护。
- 加密文件位于 `app.getPath("userData")/secrets/`，不在仓库。
- 状态接口只返回 configured、provider、storage_backend、updated_at、validation_status。
- Provider 必须显式列入 `AI_TRADER_DESKTOP_ENABLED_PROVIDERS` 才会在启动时解密注入；新设置是用户显式动作。
- packaged 子进程禁用项目 `.env` 自动加载，并过滤继承环境中的敏感变量名。
- 开发 fallback 必须同时满足非 packaged 和 `AI_TRADER_ALLOW_LEGACY_ENV_SECRET_FALLBACK=true`，日志只记录 `LEGACY_ENV_SECRET_FALLBACK` 标记。
- 迁移命令默认 dry-run，不输出值，不自动删除 `.env`；`--remove-source` 必须由用户单独授权。

## 发布验收

临时构建固定写入 `outputs/security_remediation_20260730/desktop_build/`。构建 allowlist 只含 renderer、Main/preload、生产 npm 依赖、后端可执行文件和 YAML 配置；不包含 `.env`、数据库、历史 outputs、backups、reports、开发服务器 URL或旧 token bridge。正式发布仍需在干净 Windows 主机完成签名、安装/卸载、权限、safeStorage round-trip 和最终敏感扫描。
