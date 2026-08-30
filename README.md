# FileMaster — 文件批量处理工具

> 跨平台、零配置、不需要管理员权限的文件批量重命名与分类工具

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org)
[![PySide6](https://img.shields.io/badge/PySide6-6.5%2B-green)](https://wiki.qt.io/Qt_for_Python)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-pytest-blue)](tests/)

## 简介

FileMaster 是一款 Windows 桌面应用，帮助你：

- 📁 **批量重命名** —— 自定义模板 + 占位符（日期、序号、元数据）
- 🗂️ **智能分类** —— 按扩展名/MIME/正则/大小/时间，把文件分到子目录
- 🔍 **实时预览** —— 改模板时右栏实时显示前 100 个文件的"将会变成什么"
- ↶ **多步撤销** —— 撤销栈保留 50 步，随时回退到任意步
- 🧪 **Dry Run** —— 试运行模式：只生成报告不写文件，所见即所得
- 🔁 **正则替换** —— 任意模式匹配与替换（如 `\(v\d+\)`）
- 🏷️ **元数据占位符** —— 读 PDF/Word/Excel/图片 EXIF
- 🔁 **去重** —— MD5/SHA1/SHA256 哈希，重复文件移到 `_duplicates/`
- 📦 **压缩归档** —— 按类型/时间分卷 zip
- 🌐 **飞书同步** —— 处理结果推送到飞书多维表格
- 🤖 **AI 命名** —— 本地 Ollama / 云端 API，读 PDF 摘要
- 🎨 **Fluent UI** —— 浅色 / 暗色 / 跟随系统

## 5 分钟上手

```bash
# 1. 安装（开发模式）
git clone https://github.com/bl-filemaster/filemaster.git
cd filemaster
pip install -e ".[dev]"

# 2. 启动 GUI
filemaster-gui
# 或：
python -m filemaster

# 3. 跑测试
pytest

# 4. 跑 hello world
python scripts/hello_world.py
```

## 打包成 .exe（不需要 Python 环境）

```bash
# 第一次打包
pip install -e ".[build]"
pyinstaller build/filemaster.spec

# 产物
build/dist/FileMaster.exe   # 80-120 MB 单文件
```

接收方双击即可运行，**不需要任何 Python / Qt / VC++ 环境**。

## 不需要管理员权限

FileMaster 只在以下位置读写，**不碰任何系统保护区**：

- 配置文件：`%APPDATA%\FileMaster\config.json`
- 撤销栈：`%APPDATA%\FileMaster\undo\`
- 审计日志：`%APPDATA%\FileMaster\audit.db`
- 右键菜单：注册到 `HKCU\Software\Classes\*\shell\FileMaster`（当前用户区，不影响其他用户）

## 项目状态

🚧 **v0.1.0 (W1)** — 项目脚手架 + 核心接口骨架 + PySide6 hello world 跑通

完整 16 周路线图见 [docs/developer_guide.md](docs/developer_guide.md) 与飞书云文档 [FileMaster V2.0 立项方案](https://chinabaolong.feishu.cn/docx/JYm8d5T4zocXEyxECWycPGhNnxJ)。

| 周 | 交付 |
|----|------|
| W1  | 项目脚手架 + hello world + 4 套主题（**当前**） |
| W2  | 重命名引擎 + 模板系统 |
| W3  | 分类引擎 |
| W4  | Excel 报告 + 配置管理 |
| W5  | 撤销栈 + 审计日志 |
| W6  | 实时预览 + Dry Run |
| W7  | 异步 + 进度 + 暂停 |
| W8  | 元数据占位符 |
| W9  | 正则替换 + 多源 + 排除 |
| W10 | 去重 + 压缩 |
| W11 | 飞书同步 + CLI 模式 |
| W12 | Fluent UI 主题 + 暗色模式 + 图标 |
| W13 | i18n + 快捷键 + 托盘 |
| W14 | 打包 + 安装器 |
| W15 | 测试 + 文档 + 用户手册 |
| W16 | Buffer + 验收 + 发布 |

## 目录结构

```
filemaster/
├── pyproject.toml              # 项目元数据 + 依赖
├── README.md
├── CHANGELOG.md
├── LICENSE                     # MIT
├── src/filemaster/             # 主代码
│   ├── core/                   # 业务核心（无 UI 依赖，可单测）
│   ├── workers/                # 后台线程
│   ├── ui/                     # 界面层
│   │   ├── styles/             # QSS 主题（4 套）
│   │   ├── resources/          # 图标 / 字体
│   ├── io/                     # I/O（配置 / Excel / SQLite）
│   ├── platform/               # 平台集成（Windows 注册表 / 托盘）
│   ├── integrations/           # 飞书 / Ollama / OpenAI
│   └── utils/                  # 工具（hash / logger / i18n）
├── tests/                      # 测试
├── scripts/                    # 工具脚本（hello world / fixture）
├── build/                      # 打包配置
├── docs/                       # 文档
└── .github/workflows/          # CI
```

## 开发者

- **作者**：ECAS-空气悬架板块-空气悬架合肥工厂-技术开发科
- **维护**：吴东东
- **AI 协作**：小龙（aily agent）

## 许可证

MIT — 详见 [LICENSE](LICENSE)。
