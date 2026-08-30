# FileMaster

> 跨平台文件批量重命名 + 分类 + 元数据提取工具（Windows/macOS/Linux）
> Python 3.10+ · PySide6 6.5+ · 单文件 exe 仅 ~30 MB

## 主题预览

| Light (Fluent) | Dark (Fluent) | Fluent (亚克力) | High Contrast |
| :---: | :---: | :---: | :---: |
| ![light](docs/screenshots/light.png) | ![dark](docs/screenshots/dark.png) | ![fluent](docs/screenshots/fluent.png) | ![high_contrast](docs/screenshots/high_contrast.png) |

## 5 分钟上手

```bash
# 1. 克隆
git clone https://github.com/<your-org>/filemaster.git
cd filemaster

# 2. 安装
pip install -e ".[dev]"

# 3. 跑测试（确保 66+ 用例全绿）
pytest --cov=src/filemaster

# 4. 启动 GUI
python scripts/hello_world.py
# 或
python scripts/screenshot_themes.py  # 渲染 4 套主题截图到 artifacts/screenshots/

# 5. 打包成单文件 exe（无需管理员权限）
pip install -e ".[build]"
pyinstaller build/filemaster.spec --clean --noconfirm
# 产物：dist/filemaster.exe
```

## 核心功能（W1 已实现接口）

- **重命名引擎** — `{Prefix} {OriginalName} {BaseName} {Extension} {Index:D3} {Date} {Title} {Author}` 占位符
- **分类器** — 内置 5 类（PDF / WORD / EXCEL / PPT / IMAGE）+ 自定义扩展
- **元数据** — PDF (PyMuPDF) / Word (python-docx) / Excel (openpyxl) / Image (EXIF)
- **去重** — MD5 / SHA1 / SHA256 / BLAKE2b
- **预览** — 前 N 文件元数据快照
- **撤销栈** — 50 步环形缓冲 + JSON 持久化
- **Excel 报告** — 7 列 + 冻结表头 + 自动筛选
- **4 套主题** — light / dark / fluent / high_contrast（QSS）
- **配置持久化** — 跨平台 `%APPDATA%` / `~/Library` / `$XDG_CONFIG_HOME`

## 项目状态

| 周次 | 目标 | 状态 |
|------|------|------|
| **W1** | 项目脚手架 + 4 主题 + 测试框架 | ✅ 完成（66 测试，3502 行） |
| W2 | 重命名引擎完整化 + 占位符扩展 | 🔜 |
| W3-W4 | Excel 导入/导出 + 配置 UI | 🔜 |
| W5-W6 | 异步任务 + 进度条 | 🔜 |
| W7-W10 | 元数据提取 + 去重 UI | 🔜 |
| W11-W13 | 飞书集成 + 右键菜单注册 | 🔜 |
| W14-W15 | 打包优化 + 自动更新 | 🔜 |
| W16 | v1.0 发布 | 🔜 |

## 16 周路线图

详见 `docs/roadmap.md`（W1 阶段暂未生成，W2 起建立）。

## 贡献指南

```bash
# Lint
ruff check src/ tests/

# Type check
mypy src/filemaster

# Coverage
pytest --cov=src/filemaster --cov-report=html
```

CI 跑通：`.github/workflows/test.yml`（3 OS × 3 Python 测试矩阵）+ `windows-smoke.yml`（Windows 打包冒烟测试）+ `build.yml`（发布 .exe）。

## 许可证

MIT
