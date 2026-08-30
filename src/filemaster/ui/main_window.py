"""FileMaster 主窗口.

W1 实现：3 栏布局骨架（配置 / 文件表 / 日志）+ 4 主题切换 + 工具栏 + 状态栏。
W2-W15 在此基础上扩展。
"""

from __future__ import annotations

import importlib.resources
from pathlib import Path

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

from filemaster.core.template import Template
from filemaster.io.config import Config, default_config_dir


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
        self.setWindowTitle("FileMaster — 文件批量处理工具 v0.1.0 (W1)")
        self.resize(1200, 760)

        # 加载配置
        self._config = Config.load()
        self._theme_name = self._config.theme

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

        self._btn_pause = QPushButton("⏸ 暂停")
        self._btn_pause.setEnabled(False)
        tb.addWidget(self._btn_pause)

        self._btn_undo = QPushButton("↶ 撤销")
        self._btn_undo.setShortcut(QKeySequence("Ctrl+Z"))
        self._btn_undo.clicked.connect(self._on_undo)
        tb.addWidget(self._btn_undo)

        tb.addSeparator()

        # 主题切换
        self._cmb_theme = QComboBox()
        for key, label in self.THEMES.items():
            self._cmb_theme.addItem(label, userData=key)
        self._cmb_theme.currentIndexChanged.connect(self._on_theme_change)
        tb.addWidget(QLabel(" 主题: "))
        tb.addWidget(self._cmb_theme)

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
        ])
        h_tpl.addWidget(self._cmb_template)
        v_tpl.addLayout(h_tpl)

        muted = QLabel("占位符：{Prefix} {OriginalName} {BaseName} {Extension} {Index:D3}")
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
        sb.showMessage("就绪 · v0.1.0 (W1)")

    # ---------- 主题 ----------

    def _apply_theme(self, theme_name: str) -> None:
        """应用 QSS 主题.

        Args:
            theme_name: light / dark / fluent / high_contrast
        """
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
        # 主题下拉
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

    def _on_start(self) -> None:
        """W2 详细实现：调用 BatchWorker."""
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
        self._log(f"W1 占位：开始处理源={source} 模板={tpl.raw} 前缀={self._txt_prefix.text()!r}")
        self._log("W2-W4 才会真正改文件。当前是骨架。")

    def _on_undo(self) -> None:
        """W5 详细实现：撤销栈."""
        self._log("W5 才会接入撤销栈。")

    def _on_redo(self) -> None:
        self._log("W5 才会接入重做。")

    def _on_about(self) -> None:
        cfg_dir = default_config_dir()
        QMessageBox.about(
            self,
            "关于 FileMaster",
            f"<h3>FileMaster v0.1.0 (W1)</h3>"
            f"<p>文件批量处理工具</p>"
            f"<p>Python + PySide6 + openpyxl + PyMuPDF</p>"
            f"<p>配置目录: <code>{cfg_dir}</code></p>"
            f"<p>© 2026 ECAS 技术开发科 · MIT License</p>",
        )

    # ---------- 工具 ----------

    def _log(self, msg: str) -> None:
        """写一条日志到右侧."""
        from datetime import datetime

        stamp = datetime.now().strftime("%H:%M:%S")
        self._txt_log.append(f"[{stamp}] {msg}")
