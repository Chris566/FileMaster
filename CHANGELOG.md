# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-08-31

### Added (W4) — Dedup 完整闭环
- **核心 (W4 v1-v3)** — `core/dedup.py`: 哈希分组（MD5/SHA1/SHA256/BLAKE2b）+ 重复检测 + 预览 N 个重复组
- **核心 (W4 v4)** — `core/dedup.py`: 4 种动作策略 `skip` / `move_subdir` / `hardlink` / `delete`，每步写 undo log 到 `~/.filemaster/undo/<timestamp>.json`
- **核心 (W4 v5)** — `core/dedup.py`: `list_undo_logs()` 枚举 undo log（损坏 JSON 自动跳过）+ `restore_undo_log()` 恢复 move 操作 + `RestoreResult` dataclass
- **Workers** — `workers/dedup.py` / `workers/preview.py`: QThread 异步任务，发送进度信号到 GUI
- **CLI** — `cli.py`: `dedup-scan` / `dedup-action` / `dedup-undo list` / `dedup-undo restore` 4 个子命令
- **GUI** — `ui/main_window.py`: "去重" 页 "扫描" / "预览" / "执行" 按钮 + 进度条 + 结果列表 + "↶ 撤销" 按钮 + `DedupUndoDialog`（QListWidget + 复选框 + 状态输出）
- **Tests** — 10 个新单测（3 TestDedupUndoButton + 7 TestDedupUndoDialog），覆盖按钮触发、对话框、勾选逻辑、恢复流程
- **累计**: 329 单测通过 / 0 失败 / 5 跳过 / ruff clean

### Added (W3) — 元数据
- `core/metadata.py`: PDF (PyMuPDF) / Word (python-docx) / Excel (openpyxl) / Image (EXIF) 提取
- 6 个新 placeholder: `{Title}` / `{Author}` / `{Pages}` / `{Width}` / `{Height}` / `{Camera}`
- 21 个新单测

### Added (W2) — 重命名引擎
- `core/renamer.py`: 占位符替换引擎（`{Prefix} {OriginalName} {BaseName} {Extension} {Index:D3} {Date} {Title} {Author}`）
- 异步扫描 QThread + 进度信号
- 6 个新 placeholder
- 跨平台 atomic overwrite（`os.replace`）

### Fixed

- **`actions/upload-artifact@v4` 拒绝 `../` 路径** — 改让脚本输出到 repo 内 `artifacts/`
- **Linux CI `libEGL.so.1` 缺失** — 加 `sudo apt-get install -y libegl1 libgl1 libxkbcommon0 libdbus-1-3`
- **PyInstaller spec 未入库** — `.gitignore` 加 `!build/filemaster.spec` 例外 + `git add -f`
- **Windows `cp936` 中文 print** — 顶部 `sys.stdout.reconfigure(encoding="utf-8")`
- **`format_date(-1)` 跨平台** — 改用 `datetime.fromtimestamp` + 错误处理
- **`monkeypatch.setattr` patch 错 namespace** — main_window 是局部 import，要 patch `filemaster.ui.main_window.list_undo_logs` 而不是 `dedup_mod.list_undo_logs`
- **Windows CI `subprocess.Path.home()` 不响应 HOME** — `ntpath.expanduser` 只看 USERPROFILE。`_run` 帮手 `sys.platform == "win32"` 时同步设 `USERPROFILE`/`HOMEDRIVE`/`HOMEPATH`（必须直接赋值，不能 `setdefault`，runner os.environ 已有 USERPROFILE，setdefault 不覆盖）

### Changed

- **GitHub PATCH ref URL** — `/git/refs/heads/<branch>`（复数），单数 `/git/ref/heads/<branch>` PATCH 时返回 404
- 路线图 W7-W10 "去重 UI" 提前到 W4 完成（见 README 项目状态表）

## [0.1.0] - 2026-08-30

### Added (W1)
- 项目脚手架（pyproject.toml + 目录结构 + CI 配置）
- 核心接口骨架（renamer / classifier / template / undo / metadata / dedup / archiver / preview）
- PySide6 hello world 跑通（`scripts/hello_world.py`）
- 4 套 QSS 主题（浅色 / 暗色 / Fluent / High Contrast）
- 单元测试框架（pytest + pytest-qt + pytest-cov）
- 1w 文件 fixture 生成器（`scripts/gen_fixtures.py`）
- README + 开发者指南骨架
- GitHub Actions CI 配置（test + build）
- PyInstaller 打包配置（`build/filemaster.spec`）
