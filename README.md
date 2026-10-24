# simple-license

通用电子施工准入凭证平台，包含邀请填报、单级审批、许可证 PNG 与二维码、匿名现场核验、账户管理、审计日志和备份。

这是经过脱敏的源码版本。界面和默认签发单位使用示例名称，现场网络提示使用通用文字，部署地址需自行配置。平台生成内部准入凭证。

## Windows 本地运行

要求 Python 3.12+，在 PowerShell 执行：

```powershell
.\setup.ps1
.\start.ps1
```

访问 http://127.0.0.1:15001/staff/login 。核验服务监听 15002；两个端口根路径返回 404。

初始化创建 `admin` 和 `approver`，各自随机生成初始密码并仅在终端显示一次；重复初始化保留已有密码。也可通过 `INIT_ADMIN_PASSWORD`、`INIT_APPROVER_PASSWORD` 注入 12—128 位密码。停止服务：

```powershell
.\stop.ps1
```

`.env.example` 是本地配置模板。数据库、附件和图片默认保存在 `data/`。真实环境配置、业务资料、密钥和授权文件应在本地管理。

## 软件授权与部署

当前项目保留离线 Ed25519 软件授权机制。默认 `app/license_public_keys.json` 为空；未配置有效授权时业务写入受限，但查询、账户安全操作、初始化及备份等按原有规则可用。源码测试会在临时目录生成独立测试授权。

运行完整业务前，按 [开发者签发说明](developer_tools/README.md) 生成自有密钥、配置验签公钥，并为目标设备签发授权。私钥应保存在源码目录之外。Docker 构建会拒绝空的验签公钥集合。

Linux、Docker、PostgreSQL、离线交付和恢复操作见 [部署说明](deployment/DEPLOYMENT.md)。生产环境还需配置 HTTPS、反向代理、真实申请和核验地址、现场网络访问控制以及持久化备份。当前源码版本未进行生产容器及现场网络验收。

管理员可在平台设置修改单位名称、签发单位、网址前缀和界面标志。凭证每日允许时段为北京时间 08:00（含）至 18:30（不含）。匿名核验显示实时状态，图片保留签发快照。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

pytest 使用临时数据库与合成资料。可选的真实 HTTP 测试（15101/15102 需空闲）为：

```powershell
.\.venv\Scripts\python.exe -m tests.run_http_acceptance
```

HTTP 测试会生成大附件、独立测试数据和 `artifacts/` 报告；这些内容均不纳入版本控制。

## 源码目录

- `app/`：接口、数据模型、页面、样式、凭证生成与授权验证。
- `scripts/`、`migrations/`：初始化、服务、迁移、维护与备份。
- `tests/`：隔离测试与 HTTP 验收工具。
- `deployment/`、`build_support/`、`developer_tools/`：部署、构建检查和开发者授权签发工具。

上传范围与数据排除规则见 [PRIVACY.md](PRIVACY.md)。
