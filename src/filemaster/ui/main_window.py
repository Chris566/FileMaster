"""FileMaster 主窗口.

W1：3 栏布局骨架（配置 / 文件表 / 日志）+ 4 主题切换 + 工具栏 + 状态栏。
W2：重命名引擎真实 IO 接入 + 异步任务 UI（进度条 / 取消）。
"""

from __future__ import annotations

import importlib.resources
from pathlib import Path

from PySide6.QtCore import QThread
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStatusBar,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from filemaster.core.renamer import ConflictStrategy
from filemaster.core.template import Template
from filemaster.core.undo import UndoStack
from filemaster.io.config import Config, default_config_dir
from filemaster.workers.batch import BatchWorker


class MainWindow(QMainWindow):
    """FileMaster 主窗口."""

    THEMES = {
        "light": "浅色 (Fluent Light)",
        "dark": "暗色 (Fluent Dark)",
        "fluent": "Fluent Acrylic",
        "high_contrast": "高对比度",
    }

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("FileMaster — 文件批量处理工具 v0.2.0 (W2)")
        self.resize(1200, 760)

        # 加载配置
        self._config = Config.load()
        self._theme_name = self._config.theme

        # W2：撤销栈（持久化到配置目录/undo）
        cfg_dir = default_config_dir()
        self._undo_stack = UndoStack(persist_dir=cfg_dir / "undo")

        # W2：线程/Worker 状态
        self._thread: QThread | None = None
        self._worker: BatchWorker | None = None

        # 构造 UI
        self._build_menu()
        self._build_toolbar()
        self._build_central()
        self._build_statusbar()

        # 应用主题
        self._apply_theme(self._theme_name)

        # 同步配置到 UI
        self._sync_config_to_ui()

    # ---------- 构建 UI ----------

    def _build_menu(self) -> None:
        """菜单栏."""
        menubar = self.menuBar()

        m_file = menubar.addMenu("文件(&F)")
        m_file.addAction(self._make_action("选择源目录(&O)...", "Ctrl+O", self._on_choose_source))
        m_file.addAction(self._make_action("选择目标目录(&D)...", "Ctrl+D", self._on_choose_target))
        m_file.addSeparator()
        m_file.addAction(self._make_action("退出(&X)", "Ctrl+Q", self.close))

        m_edit = menubar.addMenu("编辑(&E)")
        m_edit.addAction(self._make_action("撤销(&Z)", "Ctrl+Z", self._on_undo))
        m_edit.addAction(self._make_action("重做(&Y)", "Ctrl+Y", self._on_redo))

        m_view = menubar.addMenu("视图(&V)")
        m_theme = m_view.addMenu("主题")
        for key, label in self.THEMES.items():
            m_theme.addAction(self._make_action(label, "", lambda checked=False, k=key: self._apply_theme(k)))

        m_help = menubar.addMenu("帮助(&H)")
        m_help.addAction(self._make_action("关于(&A)...", "", self._on_about))

    def _build_toolbar(self) -> None:
        """工具栏."""
        tb = QToolBar("主工具栏")
        tb.setMovable(False)
        self.addToolBar(tb)

        self._btn_start = QPushButton("▶ 开始")
        self._btn_start.setProperty("role", "primary")
        self._btn_start.setShortcut(QKeySequence("F5"))
        self._btn_start.clicked.connect(self._on_start)
        tb.addWidget(self._btn_start)

        self._btn_cancel = QPushButton("⏹ 取消")
        self._btn_cancel.setEnabled(False)
        self._btn_cancel.clicked.connect(self._on_cancel)
        tb.addWidget(self._btn_cancel)

        self._btn_undo = QPushButton("↶ 撤销")
        self._btn_undo.setShortcut(QKeySequence("Ctrl+Z"))
        self._btn_undo.clicked.connect(self._on_undo)
        tb.addWidget(self._btn_undo)

        tb.addSeparator()

        # 冲突策略下拉
        tb.addWidget(QLabel(" 冲突: "))
        self._cmb_conflict = QComboBox()
        for strat in ConflictStrategy:
            self._cmb_conflict.addItem(self._conflict_label(strat), userData=strat.value)
        self._cmb_conflict.setCurrentIndex(0)
        tb.addWidget(self._cmb_conflict)

        tb.addSeparator()

        # 主题切换
        self._cmb_theme = QComboBox()
        for key, label in self.THEMES.items():
            self._cmb_theme.addItem(label, userData=key)
        self._cmb_theme.currentIndexChanged.connect(self._on_theme_change)
        tb.addWidget(QLabel(" 主题: "))
        tb.addWidget(self._cmb_theme)

    @staticmethod
    def _conflict_label(s: ConflictStrategy) -> str:
        return {
            ConflictStrategy.SKIP: "跳过 (skip)",
            ConflictStrategy.OVERWRITE: "覆盖 (overwrite)",
            ConflictStrategy.RENAME_NEW: "改名 (rename_new)",
        }.get(s, s.value)

    def _build_central(self) -> None:
        """中央 3 栏布局."""
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # 左：任务配置
        root.addWidget(self._build_left_panel(), stretch=2)

        # 中：文件预览表
        root.addWidget(self._build_center_panel(), stretch=5)

        # 右：日志
        root.addWidget(self._build_right_panel(), stretch=3)

    def _build_left_panel(self) -> QWidget:
        """左侧配置面板."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # 标题
        header = QLabel("FileMaster")
        header.setProperty("role", "header")
        layout.addWidget(header)

        # 分组：源 / 目标
        gb_io = QGroupBox("路径")
        v_io = QVBoxLayout(gb_io)

        h_src = QHBoxLayout()
        self._txt_source = QLineEdit()
        self._txt_source.setPlaceholderText("源目录")
        btn_src = QPushButton("📁")
        btn_src.setFixedWidth(32)
        btn_src.clicked.connect(self._on_choose_source)
        h_src.addWidget(self._txt_source, stretch=1)
        h_src.addWidget(btn_src)
        v_io.addLayout(h_src)

        h_dst = QHBoxLayout()
        self._txt_target = QLineEdit()
        self._txt_target.setPlaceholderText("目标目录（分类复制时用）")
        btn_dst = QPushButton("📁")
        btn_dst.setFixedWidth(32)
        btn_dst.clicked.connect(self._on_choose_target)
        h_dst.addWidget(self._txt_target, stretch=1)
        h_dst.addWidget(btn_dst)
        v_io.addLayout(h_dst)

        layout.addWidget(gb_io)

        # 分组：模板
        gb_tpl = QGroupBox("命名模板")
        v_tpl = QVBoxLayout(gb_tpl)

        h_prefix = QHBoxLayout()
        h_prefix.addWidget(QLabel("前缀:"))
        self._txt_prefix = QLineEdit()
        h_prefix.addWidget(self._txt_prefix)
        v_tpl.addLayout(h_prefix)

        h_tpl = QHBoxLayout()
        h_tpl.addWidget(QLabel("模板:"))
        self._cmb_template = QComboBox()
        self._cmb_template.setEditable(True)
        self._cmb_template.addItems([
            "{Prefix}{OriginalName}",
            "{Prefix}_{Index:D3}_{OriginalName}",
            "{Index:D3}_{OriginalName}",
            "{OriginalName}",
            "{CreatedDate}_{OriginalName}",
            "{FileSize}_{OriginalName}",
        ])
        h_tpl.addWidget(self._cmb_template)
        v_tpl.addLayout(h_tpl)

        muted = QLabel(
            "基础: {Prefix} {OriginalName} {BaseName} {Extension} {Index:D3}\n"
            "W2新增: {FileSize} {FileSizeBytes} {CreatedDate} {ModifiedDate} {HashShort} {Sheet}"
        )
        muted.setProperty("role", "muted")
        muted.setWordWrap(True)
        v_tpl.addWidget(muted)

        layout.addWidget(gb_tpl)

        # 分组：分类
        gb_cls = QGroupBox("分类")
        v_cls = QVBoxLayout(gb_cls)
        self._chk_classify = QCheckBox("启用按类型分类复制")
        self._chk_classify.setChecked(True)
        v_cls.addWidget(self._chk_classify)
        for cat in ("PDF", "WORD", "EXCEL", "PPT", "IMAGE"):
            cb = QCheckBox(cat)
            cb.setChecked(True)
            v_cls.addWidget(cb)
        layout.addWidget(gb_cls)

        # 分组：选项
        gb_opt = QGroupBox("选项")
        v_opt = QVBoxLayout(gb_opt)
        self._chk_dry = QCheckBox("试运行（Dry Run）")
        v_opt.addWidget(self._chk_dry)
        self._chk_keep_ext = QCheckBox("保留原扩展名")
        self._chk_keep_ext.setChecked(True)
        v_opt.addWidget(self._chk_keep_ext)
        layout.addWidget(gb_opt)

        layout.addStretch()
        return panel

    def _build_center_panel(self) -> QWidget:
        """中间文件表."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)

        gb = QGroupBox("文件预览（实时）")
        v = QVBoxLayout(gb)
        self._list_files = QListWidget()
        v.addWidget(self._list_files)
        layout.addWidget(gb)
        return panel

    def _build_right_panel(self) -> QWidget:
        """右侧日志."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)

        gb = QGroupBox("日志")
        v = QVBoxLayout(gb)
        self._txt_log = QTextEdit()
        self._txt_log.setReadOnly(True)
        v.addWidget(self._txt_log)

        h = QHBoxLayout()
        btn_clear = QPushButton("清空")
        btn_clear.clicked.connect(lambda: self._txt_log.clear())
        h.addStretch()
        h.addWidget(btn_clear)
        v.addLayout(h)

        layout.addWidget(gb)
        return panel

    def _build_statusbar(self) -> None:
        """状态栏."""
        sb = QStatusBar()
        self.setStatusBar(sb)
        self._progress = QProgressBar()
        self._progress.setMaximumWidth(220)
        self._progress.setVisible(False)
        sb.addPermanentWidget(self._progress)
        sb.showMessage("就绪 · v0.2.0 (W2)")

    # ---------- 主题 ----------

    def _apply_theme(self, theme_name: str) -> None:
        """应用 QSS 主题."""
        if theme_name not in self.THEMES:
            theme_name = "light"
        qss_file = f"theme_{theme_name}.qss"
        try:
            qss = (
                importlib.resources.files("filemaster.ui.styles")
                .joinpath(qss_file)
                .read_text(encoding="utf-8")
            )
            QApplication.instance().setStyleSheet(qss)
            self._theme_name = theme_name
            self._config.theme = theme_name
            self._config.save()
            self._log(f"主题已切换: {self.THEMES[theme_name]}")
        except Exception as e:
            QMessageBox.warning(self, "主题加载失败", f"无法加载 {qss_file}：\n{e}")

    def _on_theme_change(self, index: int) -> None:
        """主题下拉变更."""
        key = self._cmb_theme.itemData(index)
        if key:
            self._apply_theme(key)

    # ---------- 配置同步 ----------

    def _sync_config_to_ui(self) -> None:
        """配置 → UI."""
        self._txt_source.setText(self._config.last_source_dir)
        self._txt_target.setText(self._config.last_target_dir)
        self._txt_prefix.setText(self._config.last_prefix)
        self._cmb_template.setCurrentText(self._config.last_template)
        self._chk_classify.setChecked(self._config.classify_enabled)
        self._chk_dry.setChecked(self._config.dry_run)
        for i in range(self._cmb_theme.count()):
            if self._cmb_theme.itemData(i) == self._theme_name:
                self._cmb_theme.setCurrentIndex(i)
                break

    def _sync_ui_to_config(self) -> None:
        """UI → 配置."""
        self._config.last_source_dir = self._txt_source.text()
        self._config.last_target_dir = self._txt_target.text()
        self._config.last_prefix = self._txt_prefix.text()
        self._config.last_template = self._cmb_template.currentText()
        self._config.classify_enabled = self._chk_classify.isChecked()
        self._config.dry_run = self._chk_dry.isChecked()
        self._config.save()

    # ---------- 事件处理 ----------

    def _make_action(self, text: str, shortcut: str, slot) -> QAction:
        """快捷创建 QAction."""
        act = QAction(text, self)
        if shortcut:
            act.setShortcut(QKeySequence(shortcut))
        act.triggered.connect(slot)
        return act

    def _on_choose_source(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择源目录", self._txt_source.text() or "")
        if path:
            self._txt_source.setText(path)
            self._sync_ui_to_config()

    def _on_choose_target(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择目标目录", self._txt_target.text() or "")
        if path:
            self._txt_target.setText(path)
            self._sync_ui_to_config()

    def _scan_files(self) -> list[Path]:
        """扫描源目录下所有文件（非递归，按 W2 简化）。"""
        source = self._txt_source.text().strip()
        root = Path(source)
        if not root.is_dir():
            return []
        return [p for p in sorted(root.iterdir()) if p.is_file()]

    def _on_start(self) -> None:
        """W2：启动 BatchWorker."""
        if self._thread is not None and self._thread.isRunning():
            QMessageBox.information(self, "运行中", "已有任务进行中，请先取消或等待完成")
            return

        self._sync_ui_to_config()
        source = self._txt_source.text().strip()
        if not source or not Path(source).is_dir():
            QMessageBox.warning(self, "路径无效", "请先选择有效的源目录")
            return
        try:
            tpl = Template(self._cmb_template.currentText())
        except ValueError as e:
            QMessageBox.warning(self, "模板无效", str(e))
            return

        files = self._scan_files()
        if not files:
            QMessageBox.information(self, "没有文件", f"源目录下没有文件：{source}")
            return

        # 冲突策略
        strat_value = self._cmb_conflict.currentData()
        strategy = ConflictStrategy(strat_value) if strat_value else ConflictStrategy.SKIP

        # dry-run 检查
        if self._chk_dry.isChecked():
            strategy_label = "(Dry Run)" + self._conflict_label(strategy)
        else:
            strategy_label = self._conflict_label(strategy)

        self._log(
            f"开始: 文件={len(files)} 模板={tpl.raw!r} 前缀={self._txt_prefix.text()!r} 冲突={strategy_label}"
        )

        # 启动线程
        self._thread = QThread(self)
        self._worker = BatchWorker(
            files=files,
            template=tpl,
            prefix=self._txt_prefix.text(),
            conflict_strategy=strategy,
            undo_stack=self._undo_stack if not self._chk_dry.isChecked() else None,
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progressed.connect(self._on_progress)
        self._worker.file_done.connect(self._on_file_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._on_finished)
        self._thread.start()

        # UI 状态
        self._btn_start.setEnabled(False)
        self._btn_cancel.setEnabled(True)
        self._progress.setValue(0)
        self._progress.setVisible(True)

    def _on_cancel(self) -> None:
        """请求取消."""
        if self._worker is not None:
            self._worker.cancel()
            self._log("已请求取消，等待当前文件完成后停止…")

    def _on_progress(self, percent: int, file: str, index: int, total: int, message: str) -> None:
        """进度回调（在 Worker 线程 → Qt queued connection 转回主线程）."""
        self._progress.setValue(percent)
        self.statusBar().showMessage(f"{message} · {file}")

    def _on_file_done(self, result) -> None:
        """单文件完成（更新预览表）."""
        try:
            name = result.target.name if result.target else result.source.name
        except Exception:
            name = str(result.source)
        line = f"[{result.status:>10}] {result.source.name} → {name}"
        if result.message:
            line += f"  ({result.message})"
        self._list_files.addItem(line)
        # 滚动到底
        self._list_files.scrollToBottom()

    def _on_failed(self, file: str, error: str) -> None:
        self._log(f"失败: {file} - {error}")

    def _on_finished(self, results) -> None:
        """任务结束."""
        # 统计
        total = len(results)
        ok = sum(1 for r in results if r.status in ("OK", "RENAMED", "OVERWRITTEN"))
        conflict = sum(1 for r in results if r.status == "CONFLICT")
        skipped = sum(1 for r in results if r.status == "SKIPPED")
        err = sum(1 for r in results if r.status == "ERROR")
        self._log(
            f"完成: 总数={total} 成功={ok} 冲突跳过={conflict} 跳过={skipped} 失败={err}"
        )
        self.statusBar().showMessage(f"完成 · 成功 {ok}/{total}")

        # 收尾 UI
        self._progress.setVisible(False)
        self._btn_start.setEnabled(True)
        self._btn_cancel.setEnabled(False)
        # 清 Worker 引用
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(2000)
        self._thread = None
        self._worker = None

    def closeEvent(self, event) -> None:  # type: ignore[override]  # noqa: N802 (PySide6 约定)
        """窗口关闭时清理线程."""
        if self._thread is not None and self._thread.isRunning():
            if self._worker is not None:
                self._worker.cancel()
            self._thread.quit()
            self._thread.wait(2000)
        super().closeEvent(event)

    def _on_undo(self) -> None:
        """W5 详细实现：撤销栈。当前 W2 简化：只支持 Renamer 写入的 RENAME_ONLY。"""
        batch = self._undo_stack.pop()
        if not batch:
            self._log("撤销栈为空")
            return
        import shutil

        for entry in batch:
            try:
                if entry.operation == "RenameOnly" and entry.target and entry.source:
                    # 还原：target 改回 source 名字
                    if entry.target.exists():
                        entry.target.rename(entry.source)
                        self._log(f"撤销: {entry.target.name} → {entry.source.name}")
                elif (
                    entry.operation == "OverwriteOnly"
                    and entry.target
                    and entry.backup_path
                    and entry.backup_path.exists()
                ):
                    shutil.copy2(entry.backup_path, entry.target)
                    self._log(f"恢复覆盖: {entry.target.name}")
            except OSError as e:
                self._log(f"撤销失败: {entry.target} - {e}")
        self._log(f"已撤销 {len(batch)} 条操作")

    def _on_redo(self) -> None:
        self._log("W5 才会接入重做栈。")

    def _on_about(self) -> None:
        cfg_dir = default_config_dir()
        QMessageBox.about(
            self,
            "关于 FileMaster",
            f"<h3>FileMaster v0.2.0 (W2)</h3>"
            f"<p>文件批量处理工具</p>"
            f"<p>Python + PySide6 + openpyxl + PyMuPDF</p>"
            f"<p>配置目录: <code>{cfg_dir}</code></p>"
            f"<p>撤销栈深度: {len(self._undo_stack)}</p>"
            f"<p>© 2026 ECAS 技术开发科 · MIT License</p>",
        )

    # ---------- 工具 ----------

    def _log(self, msg: str) -> None:
        """写一条日志到右侧."""
        from datetime import datetime

        stamp = datetime.now().strftime("%H:%M:%S")
        self._txt_log.append(f"[{stamp}] {msg}")
