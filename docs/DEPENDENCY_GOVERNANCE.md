# Python 依赖治理

`pyproject.toml` 是生产运行依赖的唯一主声明。`requirements.txt` 仅作为现有开发环境兼容输入，不定义另一套版本策略；`environment.yml` 保留 Conda 环境入口，并应与 `pyproject.toml` 的直接依赖范围同步。

- `requirements-runtime.lock.txt`：当前 Windows x64 / Python 3.12 验证环境的直接运行依赖精确版本，不用于无评审升级模型栈。
- `requirements-build.lock.txt`：构建、测试和安全门禁工具的已验证版本。
- `requirements-security.txt`：可独立安装的 pip-audit、Bandit、Ruff；不得打入最小桌面运行时。
- `frontend/package-lock.json`：前端与 Electron 的唯一 npm 锁文件。

更新锁定前必须先运行完整测试、`pip check`、`pip-audit` 和桌面隔离构建。Torch、NumPy、Pandas、PyArrow、scikit-learn、matplotlib 等模型/数据栈不得仅为消除无关提示而升级。审计例外必须记录漏洞编号、依赖路径、可达性、负责人、到期日和替代控制；禁止无期限通配忽略。
