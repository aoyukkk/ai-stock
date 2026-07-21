# Cloudflare Tunnel 共享账号部署

本方案供四名项目合伙人共同使用。应用通过 `trader.<your-zone>` 暴露，源站仅监听 `127.0.0.1:8080`；Cloudflare Tunnel 只建立出站连接，不开放 Windows 入站端口，也不配置路由器端口映射。应用自身使用一个共享账号和共享密码认证。

> 共享账号只能留下“合伙人共享账号”的项目级审计记录，无法判断具体是哪一位合伙人执行了操作。需要个人追责时，应改回 Cloudflare Access 四邮箱模式。

真实交易与业务调度器保持关闭：`ENABLE_REAL_TRADING=false`、`AI_AUTO_REAL_ORDER_ENABLED=false`、`SCHEDULER_ENABLED=false`。

## 1. 准备

1. 选择一个非根子域名，例如 `trader.<your-zone>`。不要修改根域名。
2. Cloudflare API Token 仅授予：`Cloudflare Tunnel 编辑`、`DNS 编辑`、`区域读取`。
3. Token 仅放在当前 PowerShell 进程，不写入 `.env`、仓库、日志或命令脚本。
4. 在项目 Conda 环境中执行构建命令。

```powershell
conda activate <project-environment>
powershell -ExecutionPolicy Bypass -File scripts/build_internal_web_release.ps1 -Python python
```

发布包包含后端、前端和共享密码管理工具，目标机不依赖全局 Python 或 Node。

## 2. 安装内部 Web 服务

以管理员 PowerShell 执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install_internal_web_service.ps1 `
  -PublicHostname 'trader.<your-zone>' `
  -AuthMode LOCAL_SHARED_PASSWORD `
  -SharedUsername partners
```

安装器会要求静默输入两次共享密码。密码至少 12 位，只以 Argon2id Hash 写入受 ACL 保护的数据库，明文不会进入环境文件或日志。服务配置位于 `C:\ProgramData\AITraderAssistant\config\internal-web.env`。

需要改密码时，停止服务后以管理员身份执行：

```powershell
& 'C:\Program Files\AITraderAssistant\InternalWeb\ai-trader-internal-web.exe' set-shared-password
Restart-Service AITraderInternalWeb
```

## 3. 创建 Tunnel 与 DNS

只在当前 PowerShell 进程设置变量：

```powershell
$env:AUTH_MODE = 'LOCAL_SHARED_PASSWORD'
$env:CLOUDFLARE_ACCOUNT_ID = '<account-id>'
$env:CLOUDFLARE_ZONE_ID = '<zone-id>'
$env:CLOUDFLARE_API_TOKEN = '<temporary-api-token>'
$env:APP_PUBLIC_HOSTNAME = 'trader.<your-zone>'
$env:CLOUDFLARE_TUNNEL_NAME = 'ai-trader-internal'

python scripts/cloudflare/bootstrap_internal_access.py --dry-run
python scripts/cloudflare/bootstrap_internal_access.py --apply
```

共享模式不会读取或创建 Access Organization、Identity Provider、Access Application 或邮箱策略。脚本只创建或复用指定 Tunnel，写入精确 Ingress，创建一个代理 CNAME，并将 Tunnel Token 保存到 `C:\ProgramData\AITraderAssistant\secrets\cloudflared-token.txt`。检测到冲突 DNS 或非本项目 Ingress 时会停止，不覆盖现有资源。

操作结束后清除部署 Token：

```powershell
Remove-Item Env:CLOUDFLARE_API_TOKEN
```

## 4. 安装 cloudflared

安装 Cloudflare 官方 Windows MSI，确认 `cloudflared.exe --version` 后执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install_cloudflared_service.ps1
```

## 5. 验收

```powershell
powershell -File scripts/status_internal_web_service.ps1
powershell -File scripts/status_cloudflared_service.ps1
python scripts/cloudflare/verify_internal_access.py --verify
```

验收项目：

1. 8080 仅监听 `127.0.0.1` 或 `::1`。
2. 外部 HTTPS 打开应用后显示“合伙人共享登录”。
3. 未登录 API 返回 `LOCAL_SESSION_REQUIRED`，错误账号或密码返回 401。
4. 登录成功后使用 Secure、HttpOnly、SameSite=Strict Cookie；写请求要求 CSRF。
5. 连续五次密码失败后锁定 15 分钟。
6. Quant、午盘、监测、盘后、Excel 下载和 SSE 正常。
7. 重启两个 Windows 服务后可自动恢复，历史数据库和输出仍存在。
8. 局域网其他主机无法直接访问服务器 8080。

## 6. 回滚

```powershell
python scripts/cloudflare/remove_internal_access.py --dry-run
python scripts/cloudflare/remove_internal_access.py --rollback-plan
powershell -File scripts/uninstall_cloudflared_service.ps1 -DeleteTokenFile
powershell -File scripts/uninstall_internal_web_service.ps1
python scripts/cloudflare/remove_internal_access.py --apply
```

删除脚本只处理主机名、Tunnel 名称和 Ingress 均精确匹配的资源；默认保留 `ProgramData` 中的业务数据库、输出、备份和应用 Secret。

## 7. 常见错误

- `PREEXISTING_DNS_CONFLICT`：子域名已有其他 DNS 目标，脚本拒绝覆盖。
- `TUNNEL_HAS_UNMANAGED_INGRESS`：同名 Tunnel 存在不属于本项目的 Ingress。
- `LOCAL_PASSWORD_NOT_CONFIGURED`：尚未通过管理工具写入共享密码。
- `LOCAL_PASSWORD_LOCKED`：连续失败次数过多，等待 15 分钟或由管理员重新设密。
- `INTERNAL_WEB_LOOPBACK_HEALTH_CHECK_FAILED`：检查 8080 占用与 Windows 服务日志。

原有 `CLOUDFLARE_ACCESS` 和 `CLOUDFLARE_ACCESS_PLUS_LOCAL_PASSWORD` 模式仍受支持，但不属于当前共享账号部署流程。
