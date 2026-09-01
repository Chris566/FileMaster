# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.9.0] - 2026-09-01

### Added (W9) — 硬中断 safe_rename

把单文件 `os.replace` 操作拆成可中断的两步，源文件始终在可控状态。W7+W8 解决了"文件之间能停"，W9 进一步解决"大文件 GB 级也能秒级响应取消"。

**核心变更**：
- `core/safe_rename.py` 新模块：`safe_rename(src, dst, is_cancelled) -> SafeRenameResult` · `make_tmp_path` · `find_orphan_tmps` · `cleanup_orphan_tmps`
- `core/renamer.py` — `_apply_one` 改用 `safe_rename` 替代直接 `os.replace`；`apply` 入口调 `_cleanup_tmps` 清理残留
- `utils/hash.py` — `file_hash` 加 `is_cancelled` 参数；新增 `HashCancelledError(InterruptedError)`

**两阶段 rename 流程**：
1. Step A: `shutil.move(src, src+".filemaster.tmp.<8hex>")`
2. 中断检查点: 调 `is_cancelled()` — True 时回滚
3. Step B: `os.replace(tmp, dst)` 原子覆盖

**状态语义**：
- `OK` — 成功，可入 UndoStack
- `ROLLBACK` — 取消，src 保留，**不入 UndoStack**（没真完成）
- `ERROR` — 失败，残留 .tmp 需 `cleanup_orphan_tmps`

**W7+W8+W9 三层取消契约**：
- W7：文件循环顶部（文件之间）
- W8：所有 worker 暴露 `cancellation_token` property
- W9：单文件 `safe_rename` Step A 后（**单文件之内**）

**Tests** — 29 个新单测：
- `test_safe_rename.py` — 18 个（make_tmp_path 4 / safe_rename normal 4 / cancel rollback 3 / errors 2 / orphan tmps 5）
- `test_renamer.py` — 6 个（apply_with_progress rollback 4 / apply entry cleanup 2）
- `test_batch.py` — 2 个（hard cancel keeps source 1 / normal no orphan 1）
- `test_hash.py` — 3 个（is_cancelled None / always False / immediate raise）

**累计**：431 单测通过 / 0 失败 / 5 跳过（Windows-only）/ ruff clean。

## [0.8.0] - 2026-08-31

### Added (W8) — CancellationToken 扩展到 Dedup / Preview Worker

把 W7 的 `CancellationToken` 模式从 BatchWorker 推广到所有 Worker（DedupWorker / DedupActionWorker / PreviewWorker），形成**全 worker 统一取消契约**：

- **`workers/dedup.py` — `DedupWorker` + `DedupActionWorker`**：`_cancel_requested: bool` → `_token: CancellationToken`；新增 `cancelled = Signal(int)` 信号（在文件之间检查取消，emit 已处理文件数）；暴露 `cancellation_token` property 供外部状态查询；`cancel()` 改为 `self._token.cancel()`
- **`workers/preview.py` — `PreviewWorker`**：同上 + `cancelled = Signal()`（无参数 — 单文件 IO 没有"已处理 N 个"概念）
- **`ui/main_window.py` — 3 个 `_on_*_cancelled` handler**：`_on_dedup_cancelled(processed_count)` / `_on_dedup_action_cancelled(processed_count)` / `_on_preview_cancelled()`，只刷 UI 状态（log + status bar + 摘要标签），thread 清理统一交给 `_on_*_finished`

### Tests (W8)

- **5 个 DedupWorker / DedupActionWorker cancellation 单测**：cancel_before_run / cancel_during_run (用 monkey-patch `file_hash` / `_run_single` 减速)/ cancellation_token_property
- **2 个 PreviewWorker cancellation 单测**：cancel_before_run / cancellation_token_property
- 总测试数 **389 → 405**（+16, 5 Windows-only 跳过）
- **0 ruff lint 警告**（整理 import 顺序）

### Design 决策

- **DedupActionWorker 取消时已处理文件结果保留** — 协作式取消在文件之间检查，已执行的文件动作**不会回滚**（move 已发生、delete 已发生）；这跟 W7 BatchWorker 行为一致
- **PreviewWorker 用 `Signal()` 无参** — 跟 BatchWorker/DedupWorker 的 `Signal(int)` 区分（语义不同：批处理有"已处理 N 个"，单文件没有）
- **`*args` 兼容 finished 信号** — DedupWorker emit `Signal(list, object)` (groups, stats) vs DedupActionWorker emit `Signal(object)` (BatchActionResult)，`_DedupSignalRecorder._on_finished(*args)` 自动包成 tuple
- **monkey-patch 减速模式** — W7 batch 测试用 `file_done.connect(hook)` 触发 cancel，dedup 测试因为 `progressed` 信号只在 `i%10==0` 才发（5 个文件不够），改用 `monkey-patch.setattr(workers.dedup, "file_hash", slow_hash)` 减慢到 50ms/file，再在第 N 次 call 调 cancel

## [0.7.0] - 2026-08-31

### Added (W7) — 协作式 cancellation token

- **`core/cancellation.py` — `CancellationToken`**：协作式取消令牌（状态对象，`is_cancelled` property + `cancel()` 幂等 + `reset()` 复用）
- **`core/renamer.py` — `apply_with_progress` 加 `is_cancelled: Callable[[], bool] | None = None` 参数**：在文件之间（for 循环顶部）检查取消，不打断单文件。已收集的 `results` 仍返回，已写入的 `undo entries` 仍入栈。**默认 None → 全部文件处理（W5/W6 行为完全不变，backward compat）**
- **`workers/batch.py` — 集成 `CancellationToken`**：`_cancelled` bool → `_token: CancellationToken`；`cancel()` 内部调 `token.cancel()`；`run()` 传 `is_cancelled=lambda: self._token.is_cancelled` 给引擎
- **`workers/batch.py` — 新增 `cancelled(int)` 信号**：取消时发已处理文件数，让 UI 知道处理了多少
- **`workers/batch.py` — 暴露 `cancellation_token` property**：供外部状态查询 / 测试断言
- **`ui/main_window.py` — 接 `cancelled` 信号**：`_on_cancelled(processed_count)` handler 写日志 `⏹ 已取消 · 已处理 N 个文件, 剩余未处理` + 状态栏同步
- **Tests** — 8 个新单测（5 renamer cancellation + 3 worker cancellation + 改 1 个过时 W6 限制 docstring）
  - renamer: 取消立即停 / 中段取消 / undo 只入已处理 / 预取消 0 处理 / backward compat
  - worker: run 中取消触发 cancelled 信号 / 预取消 0 文件 / token property 状态查询
- **累计**: 389 单测通过 / 0 失败 / 5 跳过（Windows-only）/ ruff clean

### Fixed

- W6 CHANGELOG 标注的已知局限「`apply_with_progress` 不响应 `_cancelled` 标志」已解决 —— cancel 按钮可立即生效，UI 显示已处理文件数

## [0.6.0] - 2026-08-31

### Added (W6) — 异步 rename 重构 + GUI 进度条升级

- **`workers/batch.py` — `BatchWorker.run()` 重构**：用 W5 `Renamer.apply_with_progress(on_progress)` 替代手动循环 `apply([single_file])`，保持 `self._index` 连续性、复用 W5 进度回调基础设施（内部 `contextlib.suppress` 吞回调异常）
- **`workers/batch.py` — ETA 估算**：用前 5 个文件耗时滑动窗口算 ETA，进度消息格式 `i/t (pct) ETA Ns` 始终展示（即使 ETA=0s 仍展示，增强用户进度感）
- **`ui/main_window.py` — 进度条格式升级**：`setFormat(f"{file} · {i}/{t} ({pct}%) ETA {n}s")` 替代裸 percent
- **`ui/main_window.py` — 每文件结果状态图标**：✅ OK/RENAMED/OVERWRITTEN/DRY_RUN / ⚠️ CONFLICT / ⏭ SKIPPED / ❌ 其他错误
- **`ui/main_window.py` — 同步到右侧可见日志面板**：每文件结果同时写入 `_txt_log`（之前只写隐藏的 `_list_files`，用户看不到）
- **Tests** — 6 个新单测（基础流式 file_done / ETA 消息格式 / 空文件 / UndoStack 联动 / worker 不崩溃 / failed 信号兜底）
- **累计**: 381 单测通过 / 0 失败 / 5 跳过（Windows-only）/ ruff clean
- **已知局限**：`apply_with_progress` 不响应 `_cancelled` 标志，cancel 按钮只能等当前文件完成（W7+ 通过 cancellation token 解决）

## [0.5.0] - 2026-08-31

### Added (W5) — 重命名引擎收尾

- **`core/metadata.py`** — 5 类文件全部 metadata 字段提取
  - 新字段：`paragraphs`（Word 段落数）/ `sheets_count`（Excel 表数）/ `taken_at`（EXIF 拍摄时间）/ `camera_make` / `camera_model` / `image_format` / `width` / `height` / `aspect_ratio`
  - 新 helper：`_compute_aspect_ratio(width, height)` — 匹配 16:9 / 4:3 / 1:1 / 3:2 / 21:9 / 5:4 / 2:3 / 9:16（2% 容差）
  - EXIF 提取从 PNG 转向 JPEG 容器（PNG 不存 EXIF）
- **`core/renamer.py`** — 4 套**命名空间占位符**（lazy load：未用到的 namespace 不读文件）
  - `{pdf_title}` / `{pdf_author}` / `{pdf_subject}` / `{pdf_pages}` / `{pdf_created}` / `{pdf_modified}`
  - `{word_title}` / `{word_author}` / `{word_subject}` / `{word_paragraphs}` / `{word_created}` / `{word_modified}`
  - `{excel_title}` / `{excel_author}` / `{excel_subject}` / `{excel_sheets}` / `{excel_sheet_name}` / `{excel_created}` / `{excel_modified}`
  - `{image_width}` / `{image_height}` / `{image_taken_at}` / `{image_camera_make}` / `{image_camera_model}` / `{image_format}` / `{image_aspect_ratio}`
- **`core/renamer.py`** — `apply_with_progress(on_progress)` 逐文件回调（CLI/GUI 进度条统一接口）
  - 进度回调异常内部 `contextlib.suppress` 吞掉，不影响主流程
- **`cli.py` — `rename` 子命令真实集成**（之前是 W5 占位）
  - 参数：`-s`（单文件/目录）/ `-t`（模板）/ `-p`（前缀）/ `--start-index` / `--conflict {skip,overwrite,rename_new}` / `--dry-run` / `--json` / `-r`（递归）
  - `threading.Thread` 包装 + ASCII 进度条 `[████░░░] 50.0% (1/2)`
  - JSON 模式不打 header，`json.loads(stdout)` 直接解析
- **Tests** — 16 个新单测（10 renamer namespace + 4 apply_with_progress + 15 CLI rename，含 3 collision + 1 JSON output + 2 namespace placeholder）
- **累计**: 375 单测通过 / 0 失败 / 5 跳过 / ruff clean

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

## [0.10.0] - 2026-09-01

### Added (W10)

- **核心 `core/archiver.py`（~260 行）** — `ArchiveFormat` enum（zip / tar.gz / tar.bz2，extension + from_path）/ `ArchiveTask` + `ArchiveResult` + `ArchiveEntry` dataclass / `Archiver.archive()` 同步基础版 / `Archiver.archive_with_progress()` 带进度+取消 / `Archiver.archive_by_category()` 内置分类分卷 / `cleanup_archive_tmps()` 复用 W9 safe_rename 工具
- **后台 `workers/archiver.py`（~160 行）** — `ArchiveWorker(QObject + QThread)` 模式；5 个信号（progressed / archive_done / cancelled / finished / failed）；与 `BatchWorker` 一致的 `cancellation_token` property 暴露；单卷 / 按 category 双模式；UndoStack 集成
- **CLI `archive` 子命令** — `-s/--source` `-o/--output` `-n/--name` `--format {zip,tar.gz,tar.bz2}` `--compression 0-9` `--by-category` `-r/--recursive` `--dry-run` `--json`；threading 包装 + 进度条 + JSON 输出
- **撤销支持** — `core/undo.py` 加 `"Archive"` 到 `OperationType` Literal；`UndoEntry(operation="Archive", target=archive_path)` 写入 UndoStack
- **W9 集成** — `archive_with_progress` 走 `make_tmp_path` + `safe_rename` 双步；取消时 close + unlink tmp；原子移到目标路径不留残留

### Tests (W10)

- `test_archiver.py` — 34 个：ArchiveFormat enum (7) / Dataclasses (3) / archive 基础 3 格式 (5) / archive_with_progress (10, 含 3 种取消时序) / archive_by_category (4) / cleanup_archive_tmps (3) / safe_rename 协作 (2)
- `test_archive_worker.py` — 10 个：单卷信号触发 (4) / 按 category (2) / 取消 (2) / 失败 (1) / 基础属性 (1)
- `test_cli.py::TestArchiveCLI` — 7 个：dry-run JSON (1) / 真实 zip (1) / tar.gz (1) / tar.bz2 (1) / 源不存在 (1) / by-category (1) / --json 输出 (1)

**累计**：486 单测通过 / 0 失败 / 5 跳过（Windows-only）/ ruff clean。

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
