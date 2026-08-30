"""开发者指南（W1 骨架）."""

# FileMaster 开发者指南

> 面向接手开发 FileMaster 的工程师，包含架构、模块、贡献流程。

## 1. 项目定位

FileMaster 是一款 Windows 桌面应用（也可在 macOS / Linux 运行），帮助工程团队：
- 批量重命名（按规则、按元数据）
- 按类型分类（PDF/Word/Excel/PPT/Image）
- 去重（哈希）
- 压缩归档
- 与飞书多维表格同步

**目标用户**：保隆汽车技术开发科（合肥），后续推广到其他部门。

## 2. 技术栈

- **语言**：Python 3.10+
- **GUI**：PySide6 6.5+（Qt 6）
- **数据**：openpyxl（Excel）、SQLite（审计）
- **文件处理**：PyMuPDF（PDF 元数据）、python-docx（Word）、Pillow（EXIF）
- **打包**：PyInstaller 6+（首版），备选 Nuitka
- **测试**：pytest + pytest-qt + pytest-cov
- **CI**：GitHub Actions

## 3. 目录结构

```
src/filemaster/
├── core/        # 业务核心（无 UI 依赖）
├── workers/     # Qt 后台线程
├── ui/          # 界面层
│   ├── styles/  # 4 套 QSS 主题
│   └── resources/
├── io/          # 配置 / Excel / SQLite
├── platform/    # 平台集成
├── integrations/# 飞书 / Ollama / OpenAI
└── utils/       # 工具
```

## 4. 设计原则

- **核心层零 UI 依赖**：`core/` 下的所有模块不应 `import PySide6`，便于单测
- **dataclass 优于 dict**：配置 / 结果用 `@dataclass` 表达
- **frozen / immutable**：模板、占位符规格不可变
- **fail-soft**：配置损坏时静默回退默认值，不弹窗打扰
- **可恢复优于不可恢复**：所有破坏性操作都有 undo 入口

## 5. 贡献流程

1. Fork → 新建分支 `feature/xxx`
2. 跑 `pytest` 全绿
3. 跑 `ruff check src/`
4. 跑 `mypy src/filemaster`
5. 提交 PR，附测试用例

## 6. 16 周路线图

见 [README.md](../../README.md) 表格 + [飞书云文档](https://chinabaolong.feishu.cn/docx/JYm8d5T4zocXEyxECWycPGhNnxJ)。

## 7. 主题开发

QSS 文件在 `src/filemaster/ui/styles/`，4 套：

| 主题 | 文件 | 场景 |
|------|------|------|
| 浅色 | `theme_light.qss` | 默认 / 白天办公 |
| 暗色 | `theme_dark.qss` | 晚间 / 长时间使用 |
| Fluent | `theme_fluent.qss` | Windows 11 风格 |
| 高对比度 | `theme_high_contrast.qss` | 无障碍 / WCAG AAA |

新增主题：复制 `theme_light.qss` → 改色 → 在 `MainWindow.THEMES` 加条目。

## 8. 测试

```bash
# 全跑
pytest

# 跳过慢测试
pytest -m "not slow"

# 单文件
pytest tests/unit/test_template.py

# 覆盖率
pytest --cov=filemaster --cov-report=html
open htmlcov/index.html
```

## 9. 打包

W14 详细实现。当前（W1）`build/filemaster.spec` 是占位。

```bash
pip install -e ".[build]"
pyinstaller build/filemaster.spec
# 产物: build/dist/FileMaster.exe (80-120 MB)
```

## 10. 常见问题

**Q: 配置文件存哪？**
A: Windows: `%APPDATA%\FileMaster\config.json`；Mac: `~/Library/Application Support/FileMaster/`；Linux: `~/.config/filemaster/`。不需要管理员权限。

**Q: 为什么用 PySide6 而不是 PyQt6？**
A: PySide6 是 Qt 官方 Python 绑定，LGPL 协议（更宽松），社区维护更活跃。

**Q: 怎么加新的占位符？**
A: 在 `Template.render()` 里加分支；`Template._tokenize()` 自动支持。

**Q: 怎么加新的分类类型？**
A: 在 `core/classifier.py` 的 `BUILTIN_CATEGORIES` 加条目；或运行时用 `ClassificationRule(...)` 构造。
