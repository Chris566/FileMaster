# Contributing to FileMaster

## 开发环境

- Python 3.10+
- 系统依赖（仅 Linux 需要）：
  ```bash
  sudo apt-get install -y libxcb-xinerama0 libxcb-cursor0
  ```

## 安装

```bash
git clone https://github.com/<your-org>/filemaster.git
cd filemaster
pip install -e ".[dev]"
```

## 运行测试

```bash
pytest                    # 单元 + 集成
pytest --cov=src/filemaster --cov-report=html  # 带覆盖率
```

覆盖率目标：
- W1 阶段：核心模块 90%+（已达成）
- W2 阶段：总覆盖率 80%+
- W3+ 阶段：总覆盖率 85%+

## Lint / Type Check

```bash
ruff check src/ tests/    # 风格 + 简单错误
mypy src/filemaster       # 类型（首次跑允许有 warning）
```

## 提交规范

- commit message 风格：`<type>(<scope>): <subject>`
  - type: feat / fix / refactor / test / docs / chore
  - scope: core / ui / io / workers / tests
  - 例：`feat(core): 新增 {FileSize} {CreatedDate} 占位符`

## 主题开发

主题是 `src/filemaster/ui/styles/theme_<name>.qss` 的 QSS 文件。

新增主题：
1. 复制 `theme_light.qss` 为模板
2. 替换颜色变量（搜索 `#0078D4` 等）
3. 在 `src/filemaster/ui/main_window.py` 的 `THEMES` dict 添加条目
4. 加主题切换测试到 `tests/unit/test_*.py`

## 打包

```bash
pip install -e ".[build]"
pyinstaller build/filemaster.spec --clean --noconfirm
# 产物：dist/filemaster.exe（约 30 MB，UPX 压缩）
```

## 跨平台验证

每次提 PR 触发 GitHub Actions：
- `test.yml` — 3 OS × 3 Python 测试矩阵
- `windows-smoke.yml` — Windows 打包 + 4 主题截图冒烟
- `build.yml` — 标签推送时打 .exe 发布

## 发布流程

1. 更新 `CHANGELOG.md`
2. `git tag v0.2.0` 推送
3. GitHub Actions 自动 build.yml 打 .exe
4. .exe 自动附到 Release
