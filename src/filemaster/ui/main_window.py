"""FileMaster 主窗口.

W1：3 栏布局骨架（配置 / 文件表 / 日志）+ 4 主题切换 + 工具栏 + 状态栏。
W2：重命名引擎真实 IO 接入 + 异步任务 UI（进度条 / 取消）。
W4 v1：Classifier 集成 — 工具栏加"📁 分类"按钮 + 中间文件表升级 QTableWidget
       加 Category/Confidence 列 + 左侧分类组升级为按类别过滤下拉。
W4 v2：Preview 面板 — 点击中间表格行时,右侧上方显示元信息+内容预览
       (文本/图片/PDF/Office/二进制 hex 降级)。
W4 v3：Dedup 面板 — 工具栏加"🔍 去重"按钮 + 中间 QStackedWidget 切到 Dedup 表
       6 列(组号/Hash/大小/文件数/浪费/文件列表) + 复用 W4 v2 右侧预览
       (只查+表格预览, 不动文件; 集成 metadata 在表格里)。
"""

from __future__ import annotations

import importlib.resources
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QThread
from PySide6.QtGui import QAction, QBrush, QColor, QImage, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from filemaster.core.classifier import (
    Category,
    Classification,
    classify_batch,
    classify_file,
)
from filemaster.core.dedup import (
    Deduper,
    DedupStats,
    DuplicateGroup,
    RestoreResult,
    UndoLog,
    delete_duplicates,
    hardlink_duplicates,
    list_undo_logs,
    move_duplicates,
    restore_undo_log,
)
from filemaster.core.preview import FileMetadata, PreviewContent, PreviewKind
from filemaster.core.renamer import ConflictStrategy
from filemaster.core.template import Template
from filemaster.core.undo import UndoStack
from filemaster.io.config import Config, default_config_dir
from filemaster.workers.batch import BatchWorker
from filemaster.workers.classify import ClassifyWorker
from filemaster.workers.dedup import DedupActionWorker, DedupWorker
from filemaster.workers.preview import PreviewWorker

# Category 颜色映射（GUI 表格列染色）
CATEGORY_COLORS: dict[Category, QColor] = {
    Category.PDF: QColor("#FF6B6B"),
    Category.DOCUMENT: QColor("#4ECDC4"),
    Category.SPREADSHEET: QColor("#95E1D3"),
    Category.PRESENTATION: QColor("#F38181"),
    Category.IMAGE: QColor("#AA96DA"),
    Category.VIDEO: QColor("#FCBAD3"),
    Category.AUDIO: QColor("#A8D8EA"),
    Category.ARCHIVE: QColor("#FFFFD2"),
    Category.CODE: QColor("#3D5A80"),
    Category.CONFIG: QColor("#98C1D9"),
    Category.OTHER: QColor("#CCCCCC"),
    Category.UNKNOWN: QColor("#EEEEEE"),
}


class MainWindow(QMainWindow):
    """FileMaster 主窗口."""

    THEMES = {
        "light": "浅色 (Fluent Light)",
        "dark": "暗色 (Fluent Dark)",
        "fluent": "Fluent Acrylic",
        "high_contrast": "高对比度",
    }

    # 过滤下拉：全部 + 12 个 Category
    FILTER_OPTIONS = ["全部"] + [c.value for c in Category]

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("FileMaster — 文件批量处理工具 v0.3.0 (W4)")
        self.resize(1280, 800)

        # 加载配置
        self._config = Config.load()
        self._theme_name = self._config.theme

        # 撤销栈
        cfg_dir = default_config_dir()
        self._undo_stack = UndoStack(persist_dir=cfg_dir / "undo")

        # W2：线程/Worker 状态（重命名）
        self._thread: QThread | None = None
        self._worker: BatchWorker | None = None

        # W4 v1：分类线程/Worker
        self._classify_thread: QThread | None = None
        self._classify_worker: ClassifyWorker | None = None
        # 当前表格里的所有分类结果（过滤时复用）
        self._all_classifications: list[Classification] = []

        # W4 v2：Preview 线程/Worker（点击中间表格行触发）
        self._preview_thread: QThread | None = None
        self._preview_worker: PreviewWorker | None = None

        # W4 v3：Dedup 线程/Worker（去重模式）
        self._dedup_thread: QThread | None = None
        self._dedup_worker: DedupWorker | None = None
        self._dedup_groups: list[DuplicateGroup] = []  # 缓存去重结果
        self._dedup_stats: DedupStats | None = None  # 缓存统计

        # W4 v4：Dedup 动作线程/Worker（move/delete/hardlink 异步执行）
        self._dedup_action_thread: QThread | None = None
        self._dedup_action_worker: DedupActionWorker | None = None

        # 构造 UI
        self._build_menu()
        self._build_toolbar()
        self._build_central()
        self._build_statusbar()

        # 应用主题
        self._apply_theme(self._theme_name)

        # 同步配置到 UI
        self._sync_config_to_ui()

        # W4 v2：把表格的"选中变更"接到 Preview Worker
        self._table.itemSelectionChanged.connect(self._on_table_selection_changed)

        # W4 v3：去重表的"选中变更"接到 Preview Worker（共享同一面板）
        if hasattr(self, "_table_dedup"):
            self._table_dedup.itemSelectionChanged.connect(
                self._on_dedup_table_selection_changed
            )

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

        m_classify = menubar.addMenu("分类(&C)")
        m_classify.addAction(self._make_action(
            "📁 分类到子目录(&C)...", "Ctrl+Shift+C", self._on_classify
        ))
        m_classify.addAction(self._make_action(
            "📊 扫描并预览分类(&P)", "", self._on_load_files_to_table
        ))

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

        # W4 v1：分类按钮
        self._btn_classify = QPushButton("📁 分类")
        self._btn_classify.setToolTip("按文件类型分类复制/移动到子目录（Ctrl+Shift+C）")
        self._btn_classify.clicked.connect(self._on_classify)
        tb.addWidget(self._btn_classify)

        self._btn_scan = QPushButton("🔄 扫描")
        self._btn_scan.setToolTip("扫描源目录并在中间表格预览分类结果（点击行可在右侧查看文件预览）")
        self._btn_scan.clicked.connect(self._on_load_files_to_table)
        tb.addWidget(self._btn_scan)

        tb.addSeparator()

        # W4 v3：去重按钮
        self._btn_dedup = QPushButton("🔍 去重")
        self._btn_dedup.setToolTip("按文件 hash 在源目录里找重复文件（只查不动，表格预览）")
        self._btn_dedup.clicked.connect(self._on_dedup)
        tb.addWidget(self._btn_dedup)

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

        # 中：文件预览表（W4 v1：升级为 QTableWidget）
        root.addWidget(self._build_center_panel(), stretch=5)

        # 右：日志
        root.addWidget(self._build_right_panel(), stretch=3)

    def _build_left_panel(self) -> QWidget:
        """左侧配置面板."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

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
            "W2: {FileSize} {FileSizeBytes} {CreatedDate} {ModifiedDate} {HashShort} {Sheet}\n"
            "W3: {Title} {Author} {Subject} {PageCount} {ImageWidth} {ImageHeight}\n"
            "W4: {Category} {Category_zh}"
        )
        muted.setProperty("role", "muted")
        muted.setWordWrap(True)
        v_tpl.addWidget(muted)

        layout.addWidget(gb_tpl)

        # 分组：分类（W4 v1：加"按类别过滤"下拉）
        gb_cls = QGroupBox("分类（W4 v1）")
        v_cls = QVBoxLayout(gb_cls)

        # 启用分类 checkbox
        self._chk_classify = QCheckBox("启用按类型分类复制")
        self._chk_classify.setChecked(True)
        v_cls.addWidget(self._chk_classify)

        # 过滤下拉：表格里只显示该类别
        h_filter = QHBoxLayout()
        h_filter.addWidget(QLabel("过滤:"))
        self._cmb_filter = QComboBox()
        for opt in self.FILTER_OPTIONS:
            self._cmb_filter.addItem(opt)
        self._cmb_filter.currentIndexChanged.connect(self._on_filter_category)
        h_filter.addWidget(self._cmb_filter)
        v_cls.addLayout(h_filter)

        # 类别统计标签
        self._lbl_stats = QLabel("未扫描")
        self._lbl_stats.setProperty("role", "muted")
        self._lbl_stats.setWordWrap(True)
        v_cls.addWidget(self._lbl_stats)

        layout.addWidget(gb_cls)

        # 分组：选项
        gb_opt = QGroupBox("选项")
        v_opt = QVBoxLayout(gb_opt)
        self._chk_dry = QCheckBox("试运行（Dry Run）")
        v_opt.addWidget(self._chk_dry)
        self._chk_keep_ext = QCheckBox("保留原扩展名")
        self._chk_keep_ext.setChecked(True)
        v_opt.addWidget(self._chk_keep_ext)
        self._chk_classify_recursive = QCheckBox("递归子目录")
        self._chk_classify_recursive.setChecked(True)
        v_opt.addWidget(self._chk_classify_recursive)
        layout.addWidget(gb_opt)

        layout.addStretch()
        return panel

    def _build_center_panel(self) -> QWidget:
        """中间面板：W4 v3 改用 QStackedWidget 切两个表.

        - Page 0: 分类预览表 (W4 v1 原始 5 列)
        - Page 1: 去重结果表 (W4 v3 新增 6 列)
        工具栏的"🔄 扫描"→ 切到 Page 0；"🔍 去重"→ 切到 Page 1。
        """
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)

        # QStackedWidget
        self._center_stack = QStackedWidget()
        layout.addWidget(self._center_stack)

        # ---- Page 0: 分类预览表 ----
        gb_classify = QGroupBox("文件预览（实时）— W4：含分类列")
        v_cls = QVBoxLayout(gb_classify)

        # 5 列：# / 文件名 / 大小 / 分类 / 置信度
        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(["#", "文件名", "大小", "分类", "置信度"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        v_cls.addWidget(self._table)

        # 隐藏旧 QListWidget 兼容（W2 仍可被 _on_file_done 用作日志追加）
        self._list_files = QListWidget()
        self._list_files.setVisible(False)  # 不显示，但保留引用
        v_cls.addWidget(self._list_files)

        self._center_stack.addWidget(gb_classify)  # index 0

        # ---- Page 1: 去重结果表 (W4 v3) ----
        gb_dedup = QGroupBox("去重结果（W4 v3）— 按 hash 找出重复文件（只查）")
        v_dd = QVBoxLayout(gb_dedup)

        # 摘要标签：共 N 组 / 重复 M 个 / 浪费 X GB
        self._lbl_dedup_summary = QLabel("未执行去重 — 点击工具栏「🔍 去重」按钮")
        self._lbl_dedup_summary.setProperty("role", "muted")
        self._lbl_dedup_summary.setWordWrap(True)
        v_dd.addWidget(self._lbl_dedup_summary)

        # 6 列：# / Hash(短) / 大小 / 文件数 / 浪费 / 文件列表
        self._table_dedup = QTableWidget(0, 6)
        self._table_dedup.setHorizontalHeaderLabels([
            "#", "Hash(短)", "大小", "文件数", "浪费", "文件列表",
        ])
        self._table_dedup.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._table_dedup.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._table_dedup.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table_dedup.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._table_dedup.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self._table_dedup.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self._table_dedup.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table_dedup.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        v_dd.addWidget(self._table_dedup)

        # ---- W4 v4: 动作按钮 + dry-run + 目标目录 ----
        gb_dedup_actions = QGroupBox("W4 v4 动作（作用于选中的行）")
        v_act = QVBoxLayout(gb_dedup_actions)

        # 目标目录行（move 用）
        h_tgt = QHBoxLayout()
        h_tgt.addWidget(QLabel("目标目录:"))
        self._txt_dedup_target = QLineEdit()
        self._txt_dedup_target.setPlaceholderText("<source>/_duplicates/ (move 时默认)")
        btn_tgt = QPushButton("📁")
        btn_tgt.setFixedWidth(32)
        btn_tgt.setToolTip("选择 move 动作的目标目录")
        btn_tgt.clicked.connect(self._on_choose_dedup_target)
        h_tgt.addWidget(self._txt_dedup_target, stretch=1)
        h_tgt.addWidget(btn_tgt)
        v_act.addLayout(h_tgt)

        # 选项行
        h_opt = QHBoxLayout()
        self._chk_dedup_dryrun = QCheckBox("Dry-run (推荐先开)")
        self._chk_dedup_dryrun.setChecked(True)
        h_opt.addWidget(self._chk_dedup_dryrun)
        self._chk_dedup_overwrite = QCheckBox("覆盖已存在")
        self._chk_dedup_overwrite.setChecked(False)
        h_opt.addWidget(self._chk_dedup_overwrite)
        self._chk_dedup_trash = QCheckBox("删时进回收站")
        self._chk_dedup_trash.setChecked(True)
        h_opt.addWidget(self._chk_dedup_trash)
        h_opt.addStretch(1)
        v_act.addLayout(h_opt)

        # 3 个动作按钮
        h_btn = QHBoxLayout()
        self._btn_dedup_move = QPushButton("📁 移动到目标")
        self._btn_dedup_move.setToolTip("把选中组的 duplicates 移到目标目录（keep 最老）")
        self._btn_dedup_move.clicked.connect(lambda: self._on_dedup_action("move"))
        h_btn.addWidget(self._btn_dedup_move)

        self._btn_dedup_delete = QPushButton("🗑️ 删除")
        self._btn_dedup_delete.setProperty("role", "danger")
        self._btn_dedup_delete.setToolTip("删选中组的 duplicates（默认进回收站）")
        self._btn_dedup_delete.clicked.connect(lambda: self._on_dedup_action("delete"))
        h_btn.addWidget(self._btn_dedup_delete)

        self._btn_dedup_hardlink = QPushButton("🔗 硬链接")
        self._btn_dedup_hardlink.setToolTip("用硬链替换 duplicates 指向 keeper（Unix 推荐, Windows 可能失败）")
        self._btn_dedup_hardlink.clicked.connect(lambda: self._on_dedup_action("hardlink"))
        h_btn.addWidget(self._btn_dedup_hardlink)

        # W4 v6: 撤销按钮（打开 undo 日志列表对话框）
        self._btn_dedup_undo = QPushButton("↶ 撤销")
        self._btn_dedup_undo.setToolTip("查看 ~/.filemaster/undo/ 下的 undo 日志并恢复 move 操作的副本（来自 dedup 动作）")
        self._btn_dedup_undo.clicked.connect(self._on_dedup_undo)
        h_btn.addWidget(self._btn_dedup_undo)

        h_btn.addStretch(1)
        v_act.addLayout(h_btn)

        v_dd.addWidget(gb_dedup_actions)

        self._center_stack.addWidget(gb_dedup)  # index 1

        return panel

    def _build_right_panel(self) -> QWidget:
        """右侧：Preview 元信息 + 内容 + 日志（W4 v2 新增上方预览区）."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # ===== W4 v2：Preview 元信息侧栏 =====
        gb_meta = QGroupBox("文件元信息（W4 v2）")
        form = QFormLayout(gb_meta)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self._lbl_meta_name = QLabel("—")
        self._lbl_meta_size = QLabel("—")
        self._lbl_meta_mtime = QLabel("—")
        self._lbl_meta_ctime = QLabel("—")
        self._lbl_meta_mode = QLabel("—")
        self._lbl_meta_mime = QLabel("—")
        form.addRow("文件名:", self._lbl_meta_name)
        form.addRow("大小:", self._lbl_meta_size)
        form.addRow("修改时间:", self._lbl_meta_mtime)
        form.addRow("创建时间:", self._lbl_meta_ctime)
        form.addRow("权限:", self._lbl_meta_mode)
        form.addRow("MIME:", self._lbl_meta_mime)
        layout.addWidget(gb_meta)

        # ===== W4 v2：Preview 内容区（QStackedWidget 按 kind 切换） =====
        gb_preview = QGroupBox("内容预览")
        v_prev = QVBoxLayout(gb_preview)
        self._lbl_preview_kind = QLabel("请先扫描目录,然后在中间表格选中一个文件")
        self._lbl_preview_kind.setProperty("role", "muted")
        v_prev.addWidget(self._lbl_preview_kind)

        self._stack_preview = QStackedWidget()
        # Page 0: 文本（QTextEdit 只读）
        self._txt_preview_text = QTextEdit()
        self._txt_preview_text.setReadOnly(True)
        self._txt_preview_text.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self._stack_preview.addWidget(self._txt_preview_text)
        # Page 1: 图片（QLabel）
        self._lbl_preview_image = QLabel()
        self._lbl_preview_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_preview_image.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._lbl_preview_image.setStyleSheet("background:#222;color:#888;")
        self._stack_preview.addWidget(self._lbl_preview_image)
        # Page 2: 兜底/不支持（QLabel 多行）
        self._lbl_preview_fallback = QLabel("—")
        self._lbl_preview_fallback.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._lbl_preview_fallback.setWordWrap(True)
        self._lbl_preview_fallback.setStyleSheet(
            "QLabel { background:#1e1e1e; color:#ccc; padding:8px;"
            " font-family: 'Consolas','Courier New',monospace; }"
        )
        self._lbl_preview_fallback.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._stack_preview.addWidget(self._lbl_preview_fallback)
        v_prev.addWidget(self._stack_preview, stretch=1)

        layout.addWidget(gb_preview, stretch=2)

        # ===== 原有日志区 =====
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

        layout.addWidget(gb, stretch=1)
        return panel

    def _build_statusbar(self) -> None:
        """状态栏."""
        sb = QStatusBar()
        self.setStatusBar(sb)
        self._progress = QProgressBar()
        self._progress.setMaximumWidth(220)
        self._progress.setVisible(False)
        sb.addPermanentWidget(self._progress)
        sb.showMessage("就绪 · v0.3.0 (W4)")

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
        """扫描源目录下所有文件（非递归，按 W2 简化）."""
        source = self._txt_source.text().strip()
        root = Path(source)
        if not root.is_dir():
            return []
        return [p for p in sorted(root.iterdir()) if p.is_file()]

    # ---------- W4 v1：分类相关 ----------

    def _on_classify(self) -> None:
        """分类子目录操作：弹确认对话框 + 启动 ClassifyWorker."""
        source = self._txt_source.text().strip()
        target = self._txt_target.text().strip()
        if not source or not Path(source).is_dir():
            QMessageBox.warning(self, "路径无效", "请先选择有效的源目录")
            return
        if not target:
            QMessageBox.warning(self, "路径无效", "请先选择目标目录（分类结果会写入 <目标>/<Category>/）")
            return
        if Path(source).resolve() == Path(target).resolve():
            QMessageBox.warning(
                self, "路径冲突",
                "源目录与目标目录相同，无法分类（会无限循环或覆盖源文件）",
            )
            return

        # 询问复制还是移动
        choice = QMessageBox.question(
            self,
            "分类模式",
            f"将 {source} 下的文件按类型分类到 {target}\n\n"
            f"点击 Yes 复制，点击 No 移动，点击 Cancel 取消",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Yes,
        )
        if choice == QMessageBox.StandardButton.Cancel:
            return
        mode = "copy" if choice == QMessageBox.StandardButton.Yes else "move"
        dry_run = self._chk_dry.isChecked()
        recursive = self._chk_classify_recursive.isChecked()

        # 启动 Worker
        self._run_classify_worker(
            source=Path(source),
            destination=Path(target),
            mode=mode,
            recursive=recursive,
            dry_run=dry_run,
        )

    def _run_classify_worker(
        self, source: Path, destination: Path, mode: str,
        recursive: bool, dry_run: bool,
    ) -> None:
        """启动 ClassifyWorker（异步执行）."""
        if self._classify_thread is not None and self._classify_thread.isRunning():
            QMessageBox.information(self, "运行中", "已有分类任务进行中，请先取消或等待完成")
            return

        self._classify_thread = QThread(self)
        self._classify_worker = ClassifyWorker(
            source=source,
            destination=destination,
            mode=mode,
            recursive=recursive,
            dry_run=dry_run,
        )
        self._classify_worker.moveToThread(self._classify_thread)
        self._classify_thread.started.connect(self._classify_worker.run)
        self._classify_worker.progressed.connect(self._on_classify_progress)
        self._classify_worker.finished.connect(self._on_classify_finished)
        self._classify_worker.failed.connect(self._on_classify_failed)
        self._classify_thread.start()

        # 进度对话框
        verb = "复制" if mode == "copy" else "移动"
        self._classify_dialog = QProgressDialog(
            f"正在{verb}分类…", "取消", 0, 100, self
        )
        self._classify_dialog.setWindowTitle(f"FileMaster — 分类{verb}")
        self._classify_dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        self._classify_dialog.setMinimumDuration(0)
        self._classify_dialog.canceled.connect(self._on_classify_cancel)
        self._classify_dialog.setValue(0)
        self._classify_dialog.show()

        # 禁用分类按钮
        self._btn_classify.setEnabled(False)
        self._btn_scan.setEnabled(False)

    def _on_classify_progress(self, percent: int, message: str) -> None:
        if hasattr(self, "_classify_dialog") and self._classify_dialog is not None:
            self._classify_dialog.setValue(percent)
            self._classify_dialog.setLabelText(message)
        self.statusBar().showMessage(f"分类: {message}")

    def _on_classify_finished(self, classifications: list, summary: str) -> None:
        """分类 Worker 完成回调."""
        self._log(summary)
        self.statusBar().showMessage(summary[:80])

        # 关闭进度对话框
        if hasattr(self, "_classify_dialog") and self._classify_dialog is not None:
            self._classify_dialog.setValue(100)
            self._classify_dialog.close()
            self._classify_dialog = None

        # 更新表格
        if classifications:
            self._all_classifications = classifications
            self._refresh_table()

        # 收尾 UI
        self._btn_classify.setEnabled(True)
        self._btn_scan.setEnabled(True)
        if self._classify_thread is not None:
            self._classify_thread.quit()
            self._classify_thread.wait(2000)
        self._classify_thread = None
        self._classify_worker = None

        QMessageBox.information(self, "分类完成", summary)

    def _on_classify_failed(self, file: str, error: str) -> None:
        self._log(f"分类失败: {file} - {error}")

    def _on_classify_cancel(self) -> None:
        if self._classify_worker is not None:
            self._classify_worker.cancel()
            self._log("已请求取消分类，等待当前文件完成…")

    def _on_load_files_to_table(self) -> None:
        """扫描源目录并填表预览分类结果（不复制/移动）."""
        source = self._txt_source.text().strip()
        if not source or not Path(source).is_dir():
            QMessageBox.warning(self, "路径无效", "请先选择有效的源目录")
            return
        recursive = self._chk_classify_recursive.isChecked()
        root = Path(source)
        if recursive:
            files = sorted(p for p in root.rglob("*") if p.is_file())
        else:
            files = sorted(p for p in root.iterdir() if p.is_file())

        if not files:
            self._log(f"源目录下无文件: {source}")
            return

        self._log(f"扫描到 {len(files)} 个文件，开始分类…")
        self._all_classifications = classify_batch(files)
        self._refresh_table()
        self._log(f"预览完成: {len(self._all_classifications)} 个文件已分类")
        # W4 v3：切回分类表
        if hasattr(self, "_center_stack"):
            self._center_stack.setCurrentIndex(0)

    def _on_filter_category(self, index: int) -> None:
        """过滤下拉变更：刷新表格."""
        self._refresh_table()

    def _refresh_table(self) -> None:
        """根据 _all_classifications 和过滤下拉刷表格."""
        if not hasattr(self, "_table") or self._table is None:
            return

        selected = self._cmb_filter.currentText() if hasattr(self, "_cmb_filter") else "全部"
        if selected == "全部":
            rows = self._all_classifications
        else:
            target_cat = Category(selected)
            rows = [c for c in self._all_classifications if c.category == target_cat]

        self._table.setRowCount(len(rows))
        for row_idx, c in enumerate(rows):
            # 列 0：#
            item_idx = QTableWidgetItem(str(row_idx + 1))
            item_idx.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row_idx, 0, item_idx)

            # 列 1：文件名
            item_name = QTableWidgetItem(c.source.name)
            self._table.setItem(row_idx, 1, item_name)

            # 列 2：大小
            try:
                size_bytes = c.source.stat().st_size
                size_str = self._format_size(size_bytes)
            except OSError:
                size_str = "?"
            item_size = QTableWidgetItem(size_str)
            item_size.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._table.setItem(row_idx, 2, item_size)

            # 列 3：分类（带颜色背景）
            cat_text = f"{c.category.value} ({c.category.label_zh})"
            item_cat = QTableWidgetItem(cat_text)
            color = CATEGORY_COLORS.get(c.category, QColor("#FFFFFF"))
            item_cat.setBackground(QBrush(color))
            self._table.setItem(row_idx, 3, item_cat)

            # 列 4：置信度
            conf_text = f"{c.confidence:.2f}"
            item_conf = QTableWidgetItem(conf_text)
            item_conf.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row_idx, 4, item_conf)

        # 统计标签
        if self._all_classifications:
            from collections import Counter
            cnt = Counter(c.category for c in self._all_classifications)
            stats = " · ".join(f"{cat.value}: {n}" for cat, n in cnt.most_common(5))
            if len(cnt) > 5:
                stats += f" · +{len(cnt) - 5} 其他"
            self._lbl_stats.setText(f"共 {len(self._all_classifications)} 个文件 · {stats}")
        else:
            self._lbl_stats.setText("未扫描")

    @staticmethod
    def _format_size(n: int) -> str:
        """字节数 → 人类可读."""
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if n < 1024:
                return f"{n:.1f} {unit}" if unit != "B" else f"{n} {unit}"
            n /= 1024
        return f"{n:.1f} PB"

    # ---------- W4 v2：文件预览（点击表格行触发） ----------

    def _on_table_selection_changed(self) -> None:
        """表格选中行变化 → 启动 PreviewWorker."""
        # 没有扫描结果时（_refresh_table 还没跑过）也安全：取不到行就 return
        items = self._table.selectedItems()
        if not items:
            return
        row = items[0].row()
        # 找到当前选中行对应的原始 Classification（可能被过滤挡掉）
        # 简化策略：用 _refresh_table 当下填充表格的顺序反推
        if not self._all_classifications:
            return
        selected = (
            self._cmb_filter.currentText() if hasattr(self, "_cmb_filter") else "全部"
        )
        if selected == "全部":
            rows = self._all_classifications
        else:
            try:
                target_cat = Category(selected)
                rows = [c for c in self._all_classifications if c.category == target_cat]
            except ValueError:
                rows = self._all_classifications
        if row < 0 or row >= len(rows):
            return
        path = rows[row].source
        self._run_preview_worker(path)

    def _run_preview_worker(self, path: Path) -> None:
        """异步启动 PreviewWorker（切换行时取消旧 worker）."""
        # 取消旧 worker（若有）
        if self._preview_thread is not None and self._preview_thread.isRunning():
            if self._preview_worker is not None:
                self._preview_worker.cancel()
            self._preview_thread.quit()
            self._preview_thread.wait(1000)
            self._preview_thread = None
            self._preview_worker = None

        # 立刻把元信息区填上（同步可取的部分，避免预览 worker 慢时右侧空白）
        self._update_meta_labels(path)

        # 启动新 worker
        self._preview_thread = QThread(self)
        self._preview_worker = PreviewWorker(path)
        self._preview_worker.moveToThread(self._preview_thread)
        self._preview_thread.started.connect(self._preview_worker.run)
        self._preview_worker.succeeded.connect(self._on_preview_succeeded)
        self._preview_worker.failed.connect(self._on_preview_failed)
        self._preview_worker.finished.connect(self._on_preview_finished)
        self._preview_thread.start()

    def _update_meta_labels(self, path: Path) -> None:
        """同步刷元信息（os.stat 不重, 立即返回）."""
        try:
            st = path.stat()
            self._lbl_meta_name.setText(path.name)
            self._lbl_meta_size.setText(self._format_size(st.st_size))
            from datetime import datetime
            self._lbl_meta_mtime.setText(
                datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            )
            self._lbl_meta_ctime.setText(
                datetime.fromtimestamp(st.st_ctime).strftime("%Y-%m-%d %H:%M:%S")
            )
            self._lbl_meta_mode.setText(oct(st.st_mode & 0o777))
        except OSError as e:
            for lbl in (
                self._lbl_meta_name, self._lbl_meta_size,
                self._lbl_meta_mtime, self._lbl_meta_ctime,
                self._lbl_meta_mode, self._lbl_meta_mime,
            ):
                lbl.setText("—")
            self._lbl_meta_mime.setText(f"(stat 失败: {e})")
            return
        # MIME 由 classify_for_preview 推一个 best-effort
        from filemaster.core.preview import (
            _guess_mime,  # type: ignore[attr-defined]
            classify_for_preview,
        )
        self._lbl_meta_mime.setText(_guess_mime(path))

    def _on_preview_succeeded(
        self, meta: FileMetadata, content: PreviewContent
    ) -> None:
        """PreviewWorker 成功 → 把内容刷到右侧 stacked widget."""
        kind = content.kind
        # 标题条
        note = f" ({content.note})" if content.note else ""
        trunc = " · 已截断" if content.truncated else ""
        self._lbl_preview_kind.setText(
            f"类型: {kind.value}{note}{trunc}"
        )

        if kind == PreviewKind.TEXT and isinstance(content.payload, str):
            self._txt_preview_text.setPlainText(content.payload)
            self._stack_preview.setCurrentIndex(0)
        elif kind == PreviewKind.IMAGE and isinstance(content.payload, QImage):
            pix = QPixmap.fromImage(content.payload)
            # 缩放到不超过 stacked widget 尺寸, 保持比例
            max_w = max(self._stack_preview.width() - 16, 100)
            max_h = max(self._stack_preview.height() - 16, 100)
            if not pix.isNull():
                pix = pix.scaled(
                    max_w, max_h,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            self._lbl_preview_image.setPixmap(pix)
            self._stack_preview.setCurrentIndex(1)
        elif kind == PreviewKind.PDF and isinstance(content.payload, QImage):
            # PDF 第一页也走 IMAGE 通道
            pix = QPixmap.fromImage(content.payload)
            max_w = max(self._stack_preview.width() - 16, 100)
            max_h = max(self._stack_preview.height() - 16, 100)
            if not pix.isNull():
                pix = pix.scaled(
                    max_w, max_h,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            self._lbl_preview_image.setPixmap(pix)
            self._stack_preview.setCurrentIndex(1)
        elif kind in (
            PreviewKind.OFFICE_DOC,
            PreviewKind.OFFICE_SHEET,
            PreviewKind.OFFICE_SLIDE,
        ) and isinstance(content.payload, str):
            # Office 渲染的文本片段（行数受限）放文本框
            self._txt_preview_text.setPlainText(content.payload)
            self._stack_preview.setCurrentIndex(0)
        else:
            # BINARY / UNSUPPORTED / 其它 → fallback label
            payload = content.payload if isinstance(content.payload, str) else "(无内容)"
            self._lbl_preview_fallback.setText(payload)
            self._stack_preview.setCurrentIndex(2)

    def _on_preview_failed(self, path: str, error: str) -> None:
        """PreviewWorker 失败 → 在 fallback label 显示错误."""
        self._lbl_preview_kind.setText(f"预览失败: {path}")
        self._lbl_preview_fallback.setText(f"❌ {error}")
        self._stack_preview.setCurrentIndex(2)
        self._log(f"预览失败: {path} - {error}")

    def _on_preview_finished(self) -> None:
        """PreviewWorker 结束 → 清理 thread 引用."""
        if self._preview_thread is not None:
            self._preview_thread.quit()
            self._preview_thread.wait(2000)
        self._preview_thread = None
        self._preview_worker = None

    # ---------- W4 v3：去重（只查 + 表格预览） ----------

    def _on_dedup(self) -> None:
        """工具栏「🔍 去重」入口：校验 + 启动 DedupWorker."""
        source = self._txt_source.text().strip()
        if not source or not Path(source).is_dir():
            QMessageBox.warning(self, "路径无效", "请先选择有效的源目录")
            return

        # 切换到去重表
        if hasattr(self, "_center_stack"):
            self._center_stack.setCurrentIndex(1)

        recursive = self._chk_classify_recursive.isChecked()
        self._run_dedup_worker(Path(source), recursive=recursive)

    def _run_dedup_worker(self, source: Path, *, recursive: bool) -> None:
        """异步启动 DedupWorker（先取消旧 worker）."""
        # 取消旧 worker
        if self._dedup_thread is not None and self._dedup_thread.isRunning():
            if self._dedup_worker is not None:
                self._dedup_worker.cancel()
            self._dedup_thread.quit()
            self._dedup_thread.wait(1000)
            self._dedup_thread = None
            self._dedup_worker = None

        # 重置表格 + 摘要
        self._dedup_groups = []
        self._dedup_stats = None
        self._lbl_dedup_summary.setText(f"🔍 正在去重: {source} ...")
        self._table_dedup.setRowCount(0)

        # 启动新 worker
        self._dedup_thread = QThread(self)
        self._dedup_worker = DedupWorker(
            source=source,
            algorithm="md5",
            recursive=recursive,
        )
        self._dedup_worker.moveToThread(self._dedup_thread)
        self._dedup_thread.started.connect(self._dedup_worker.run)
        self._dedup_worker.progressed.connect(self._on_dedup_progressed)
        self._dedup_worker.finished.connect(self._on_dedup_finished)
        self._dedup_worker.failed.connect(self._on_dedup_failed)
        self._dedup_thread.start()

        # UI 状态
        self._btn_dedup.setEnabled(False)
        self._btn_scan.setEnabled(False)
        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._log(f"去重开始: 源={source} 递归={recursive} 算法=md5")

    def _on_dedup_progressed(self, percent: int, message: str) -> None:
        self._progress.setValue(percent)
        self.statusBar().showMessage(f"去重: {message}")

    def _on_dedup_finished(
        self, groups: list, stats: DedupStats
    ) -> None:
        """DedupWorker 完成 → 缓存结果 + 刷表 + 提示."""
        self._dedup_groups = list(groups)
        self._dedup_stats = stats
        self._log(
            f"去重完成: 共 {stats.total_files} 个文件 / "
            f"{stats.duplicate_groups} 组重复 / "
            f"{stats.duplicate_files} 个文件可清理 / "
            f"浪费 {stats.wasted_human} / 耗时 {stats.duration_ms} ms"
        )
        self.statusBar().showMessage(
            f"去重完成 · {stats.duplicate_groups} 组 / 浪费 {stats.wasted_human}"
        )

        # 刷摘要 + 表格
        self._lbl_dedup_summary.setText(
            f"📊 扫描 {stats.total_files} 个文件 · "
            f"发现 {stats.duplicate_groups} 组重复 · "
            f"{stats.duplicate_files} 个可清理 · "
            f"浪费 {stats.wasted_human} · 耗时 {stats.duration_ms} ms"
        )
        self._refresh_dedup_table()

        # 进度条收尾
        self._progress.setVisible(False)
        self._btn_dedup.setEnabled(True)
        self._btn_scan.setEnabled(True)
        if self._dedup_thread is not None:
            self._dedup_thread.quit()
            self._dedup_thread.wait(2000)
        self._dedup_thread = None
        self._dedup_worker = None

        if stats.duplicate_groups == 0:
            QMessageBox.information(self, "去重完成", "未发现重复文件 ✓")
        else:
            QMessageBox.information(
                self,
                "去重完成",
                f"发现 {stats.duplicate_groups} 组重复文件，"
                f"共 {stats.duplicate_files} 个可清理，"
                f"浪费 {stats.wasted_human}。\n\n"
                f"（W4 v3 范围：只查 + 表格预览，不动文件）",
            )

    def _on_dedup_failed(self, error: str) -> None:
        self._log(f"去重失败: {error}")
        self._lbl_dedup_summary.setText(f"❌ 去重失败: {error}")
        self._progress.setVisible(False)
        self._btn_dedup.setEnabled(True)
        self._btn_scan.setEnabled(True)
        if self._dedup_thread is not None:
            self._dedup_thread.quit()
            self._dedup_thread.wait(2000)
        self._dedup_thread = None
        self._dedup_worker = None
        QMessageBox.warning(self, "去重失败", error)

    def _refresh_dedup_table(self) -> None:
        """根据 _dedup_groups 刷 6 列表格."""
        if not hasattr(self, "_table_dedup") or self._table_dedup is None:
            return
        self._table_dedup.setRowCount(len(self._dedup_groups))
        for row_idx, g in enumerate(self._dedup_groups):
            # 列 0：组号
            item_idx = QTableWidgetItem(str(row_idx + 1))
            item_idx.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table_dedup.setItem(row_idx, 0, item_idx)

            # 列 1：Hash（短 12 位 + 提示）
            hash_short = g.hash_value[:12] if len(g.hash_value) > 12 else g.hash_value
            item_hash = QTableWidgetItem(f"{hash_short}…")
            item_hash.setToolTip(f"算法: {g.algorithm}\n完整: {g.hash_value}")
            item_hash.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table_dedup.setItem(row_idx, 1, item_hash)

            # 列 2：单文件大小
            item_size = QTableWidgetItem(self._format_size(g.hash_size))
            item_size.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._table_dedup.setItem(row_idx, 2, item_size)

            # 列 3：文件数
            item_cnt = QTableWidgetItem(str(g.count))
            item_cnt.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table_dedup.setItem(row_idx, 3, item_cnt)

            # 列 4：浪费字节
            item_waste = QTableWidgetItem(
                self._format_size(g.wasted_bytes) if g.wasted_bytes > 0 else "—"
            )
            item_waste.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._table_dedup.setItem(row_idx, 4, item_waste)

            # 列 5：文件列表（路径用 " | " 拼起来）
            file_strs = [str(p) for p in g.files]
            item_files = QTableWidgetItem("\n".join(file_strs))
            item_files.setToolTip("\n".join(file_strs))
            self._table_dedup.setItem(row_idx, 5, item_files)

    def _on_dedup_table_selection_changed(self) -> None:
        """去重表行选中 → 联动右侧 Preview 面板（预览组内第一个文件）."""
        items = self._table_dedup.selectedItems()
        if not items:
            return
        row = items[0].row()
        if row < 0 or row >= len(self._dedup_groups):
            return
        group = self._dedup_groups[row]
        # 取 keeper（最早 mtime 的）作为预览目标
        target = group.keeper
        # 同步刷元信息侧栏
        self._update_meta_labels(target)
        # 启动 PreviewWorker
        self._run_preview_worker(target)

    # ---------- W4 v4: Dedup 动作 (move/delete/hardlink) ----------

    def _on_choose_dedup_target(self) -> None:
        """选择 move 目标目录."""
        path = QFileDialog.getExistingDirectory(
            self, "选择 move 目标目录", self._txt_dedup_target.text() or ""
        )
        if path:
            self._txt_dedup_target.setText(path)

    def _get_selected_dedup_group(self) -> DuplicateGroup | None:
        """拿当前表格里选中的那个 DuplicateGroup."""
        items = self._table_dedup.selectedItems()
        if not items:
            QMessageBox.information(self, "未选择", "请先在表格里选中一行重复组")
            return None
        row = items[0].row()
        if row < 0 or row >= len(self._dedup_groups):
            return None
        return self._dedup_groups[row]

    def _on_dedup_action(self, action: str) -> None:
        """3 个动作的统一入口: 校验 + 二次确认 + 启 worker."""
        group = self._get_selected_dedup_group()
        if group is None:
            return

        # 已经在跑 → 不允许
        if self._dedup_action_thread is not None and self._dedup_action_thread.isRunning():
            QMessageBox.information(self, "运行中", "已有动作进行中, 请先等待完成")
            return

        dry_run = self._chk_dedup_dryrun.isChecked()
        overwrite = self._chk_dedup_overwrite.isChecked()
        use_trash = self._chk_dedup_trash.isChecked()

        # 目标目录(只 move 用)
        target_dir: Path | None = None
        if action == "move":
            txt = self._txt_dedup_target.text().strip()
            if txt:
                target_dir = Path(txt).resolve()

        # 二次确认(非 dry-run 时)
        if not dry_run:
            label_map = {"move": "移动", "delete": "删除", "hardlink": "硬链接"}
            action_label = label_map[action]
            keeper = group.keeper
            dups = group.duplicates
            preview = "\n".join(f"  • {d}" for d in dups[:5])
            if len(dups) > 5:
                preview += f"\n  ... 还有 {len(dups) - 5} 个"
            confirm_msg = (
                f"确认要{action_label}这 {len(dups)} 个重复文件吗？\n\n"
                f"保留（keeper）: {keeper}\n\n"
                f"{action_label}：\n{preview}\n\n"
            )
            if action == "move" and target_dir:
                confirm_msg += f"目标目录: {target_dir}\n"
            if action == "delete":
                if use_trash:
                    confirm_msg += "⚠️ 将移到回收站 (send2trash)\n"
                else:
                    confirm_msg += "🚨 直接永久删除（不进回收站）\n"
            if action == "hardlink":
                confirm_msg += "⚠️ 硬链: 删原文件, 用 os.link 建链到 keeper（无 undo）\n"
            confirm_msg += "\n确定继续？"
            reply = QMessageBox.question(
                self,
                f"确认{action_label}",
                confirm_msg,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                self._log(f"用户取消 {action}")
                return

        self._run_dedup_action_worker(
            group, action,
            target_dir=target_dir,
            dry_run=dry_run,
            overwrite=overwrite,
            use_trash=use_trash,
        )

    def _run_dedup_action_worker(
        self,
        group: DuplicateGroup,
        action: str,
        *,
        target_dir: Path | None,
        dry_run: bool,
        overwrite: bool,
        use_trash: bool,
    ) -> None:
        """异步启动 DedupActionWorker."""
        # 取消旧 worker
        if self._dedup_action_thread is not None and self._dedup_action_thread.isRunning():
            if self._dedup_action_worker is not None:
                self._dedup_action_worker.cancel()
            self._dedup_action_thread.quit()
            self._dedup_action_thread.wait(1000)
            self._dedup_action_thread = None
            self._dedup_action_worker = None

        mode = "DRY-RUN" if dry_run else "EXEC"
        self._log(
            f"{action} 启动 [{mode}]: group={group.hash_value[:12]}... "
            f"keeper={group.keeper} duplicates={len(group.duplicates)}"
        )
        self._lbl_dedup_summary.setText(
            f"⚙️ 正在 {action} ... ({mode}, {len(group.duplicates)} 个文件)"
        )

        # 启动新 worker
        self._dedup_action_thread = QThread(self)
        self._dedup_action_worker = DedupActionWorker(
            group=group,
            action=action,
            target_dir=target_dir,
            dry_run=dry_run,
            overwrite=overwrite,
            use_trash=use_trash,
        )
        self._dedup_action_worker.moveToThread(self._dedup_action_thread)
        self._dedup_action_thread.started.connect(self._dedup_action_worker.run)
        self._dedup_action_worker.progressed.connect(self._on_dedup_action_progressed)
        self._dedup_action_worker.finished.connect(self._on_dedup_action_finished)
        self._dedup_action_worker.failed.connect(self._on_dedup_action_failed)
        self._dedup_action_thread.start()

        # UI 状态
        self._btn_dedup.setEnabled(False)
        self._btn_dedup_move.setEnabled(False)
        self._btn_dedup_delete.setEnabled(False)
        self._btn_dedup_hardlink.setEnabled(False)
        self._progress.setValue(0)
        self._progress.setVisible(True)

    def _on_dedup_action_progressed(self, percent: int, message: str) -> None:
        self._progress.setValue(percent)
        self.statusBar().showMessage(f"动作: {message}")

    def _on_dedup_action_finished(self, batch) -> None:
        """DedupActionWorker 完成 → 汇总 + 刷表格 + 提示."""
        from filemaster.core.dedup import BatchActionResult

        if not isinstance(batch, BatchActionResult):
            self._log(f"⚠️ 异常: 收到非 BatchActionResult: {type(batch)}")
            return

        # 汇总日志
        self._log(
            f"{batch.action} 完成 [{'DRY-RUN' if batch.dry_run else 'EXEC'}]: "
            f"成功 {batch.success_count}/{len(batch.results)} "
            f"失败 {batch.fail_count}"
        )
        for r in batch.results:
            if r.success:
                if r.dry_run:
                    self._log(f"  [DRY] {r.action} {r.source} → {r.target or '(delete)'}")
                else:
                    if r.target:
                        self._log(f"  ✓ {r.action}: {r.source} → {r.target}")
                    else:
                        self._log(f"  ✓ {r.action}: {r.source}")
            else:
                self._log(f"  ✗ {r.source}: {r.error}")
        if batch.undo_log_path:
            self._log(f"  ↩ undo log: {batch.undo_log_path}")

        # 摘要
        mode = "DRY-RUN" if batch.dry_run else "EXEC"
        self._lbl_dedup_summary.setText(
            f"✅ {batch.action} 完成 ({mode}): 成功 {batch.success_count} / "
            f"失败 {batch.fail_count} / "
            f"{'dry-run' if batch.dry_run else '实际'}"
        )

        # 进度条收尾
        self._progress.setVisible(False)
        self._btn_dedup.setEnabled(True)
        self._btn_dedup_move.setEnabled(True)
        self._btn_dedup_delete.setEnabled(True)
        self._btn_dedup_hardlink.setEnabled(True)
        if self._dedup_action_thread is not None:
            self._dedup_action_thread.quit()
            self._dedup_action_thread.wait(2000)
        self._dedup_action_thread = None
        self._dedup_action_worker = None

        # 真动作后建议重新扫描
        if not batch.dry_run and batch.success_count > 0:
            ret = QMessageBox.question(
                self,
                f"{batch.action} 完成",
                f"成功 {batch.success_count} 个 / 失败 {batch.fail_count} 个\n\n"
                f"建议重新扫描以更新表格。\n\n现在重新扫描？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if ret == QMessageBox.StandardButton.Yes:
                self._on_dedup()  # 重新启动 DedupWorker
        else:
            QMessageBox.information(
                self,
                f"{batch.action} 完成",
                f"成功 {batch.success_count} / 失败 {batch.fail_count}\n"
                f"{'（DRY-RUN, 未实际改动）' if batch.dry_run else ''}",
            )

    def _on_dedup_action_failed(self, error: str) -> None:
        self._log(f"动作失败: {error}")
        self._lbl_dedup_summary.setText(f"❌ 动作失败: {error}")
        self._progress.setVisible(False)
        self._btn_dedup.setEnabled(True)
        self._btn_dedup_move.setEnabled(True)
        self._btn_dedup_delete.setEnabled(True)
        self._btn_dedup_hardlink.setEnabled(True)
        if self._dedup_action_thread is not None and self._dedup_action_thread.isRunning():
            self._dedup_action_thread.quit()
            self._dedup_action_thread.wait(2000)
        self._dedup_action_thread = None
        self._dedup_action_worker = None
        QMessageBox.warning(self, "动作失败", error)

    # ---------- W4 v6: 撤销（undo log 恢复） ----------

    def _on_dedup_undo(self) -> None:
        """打开撤销对话框：列 ~/.filemaster/undo/ 下的 undo log, 选一个恢复.

        行为:
        - 拉 list_undo_logs() 按时间倒序
        - 选条目 + dry-run/overwrite 选项 → restore_undo_log()
        - 完成后刷主窗口日志（_log） + 提示对话框
        - 不启 worker 线程：restore_undo_log 内部走 shutil.move 通常毫秒级
        """
        logs = list_undo_logs()
        if not logs:
            QMessageBox.information(
                self,
                "没有 undo log",
                "~/.filemaster/undo/ 目录下没有 undo 日志。\n\n"
                "undo 日志由「移动」和「删除（不进回收站）」动作写入，"
                "「硬链接」和「移到回收站」不写。",
            )
            self._log("↩ undo: 目录为空")
            return

        # 已经有打开的对话框 → 关掉再开新的（避免 stale 引用）
        if hasattr(self, "_dedup_undo_dialog") and self._dedup_undo_dialog is not None:
            import contextlib
            with contextlib.suppress(RuntimeError):
                self._dedup_undo_dialog.close()
            self._dedup_undo_dialog = None

        dlg = DedupUndoDialog(self, logs=logs, log_callback=self._log)
        self._dedup_undo_dialog = dlg
        dlg.show()

    # ---------- W2：重命名 ----------

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

        strat_value = self._cmb_conflict.currentData()
        strategy = ConflictStrategy(strat_value) if strat_value else ConflictStrategy.SKIP

        if self._chk_dry.isChecked():
            strategy_label = "(Dry Run)" + self._conflict_label(strategy)
        else:
            strategy_label = self._conflict_label(strategy)

        self._log(
            f"开始: 文件={len(files)} 模板={tpl.raw!r} 前缀={self._txt_prefix.text()!r} 冲突={strategy_label}"
        )

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
        self._progress.setValue(percent)
        # W6: 进度条文本升级, 复用 W5 worker 传来的 "i/t (pct) ETA Ns" 格式
        self._progress.setFormat(f"{file} · {message}" if message else f"{file} {percent}%")
        self.statusBar().showMessage(f"{message} · {file}")

    def _on_file_done(self, result) -> None:
        try:
            name = result.target.name if result.target else result.source.name
        except Exception:
            name = str(result.source)
        # W6: 状态图标 - ✅ / ⚠️ / ⏭ / ❌ 让结果一眼可读 (W5 的 status 多了 RENAMED/OVERWRITTEN/DRY_RUN)
        ok_statuses = ("OK", "RENAMED", "OVERWRITTEN", "DRY_RUN")
        if result.status in ok_statuses:
            icon = "✅"
        elif result.status == "CONFLICT":
            icon = "⚠️"
        elif result.status == "SKIPPED":
            icon = "⏭"
        else:
            icon = "❌"
        line = f"{icon} [{result.status:>10}] {result.source.name} → {name}"
        if result.message:
            line += f"  ({result.message})"
        # 写到隐藏的 _list_files (兼容 W2 调用) + 右侧可见日志面板
        self._list_files.addItem(line)
        self._list_files.scrollToBottom()
        self._log(line)

    def _on_failed(self, file: str, error: str) -> None:
        self._log(f"失败: {file} - {error}")

    def _on_finished(self, results) -> None:
        total = len(results)
        ok = sum(1 for r in results if r.status in ("OK", "RENAMED", "OVERWRITTEN"))
        conflict = sum(1 for r in results if r.status == "CONFLICT")
        skipped = sum(1 for r in results if r.status == "SKIPPED")
        err = sum(1 for r in results if r.status == "ERROR")
        self._log(
            f"完成: 总数={total} 成功={ok} 冲突跳过={conflict} 跳过={skipped} 失败={err}"
        )
        self.statusBar().showMessage(f"完成 · 成功 {ok}/{total}")

        self._progress.setVisible(False)
        self._btn_start.setEnabled(True)
        self._btn_cancel.setEnabled(False)
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(2000)
        self._thread = None
        self._worker = None

    def closeEvent(self, event) -> None:  # type: ignore[override]  # noqa: N802
        """窗口关闭时清理线程."""
        if self._thread is not None and self._thread.isRunning():
            if self._worker is not None:
                self._worker.cancel()
            self._thread.quit()
            self._thread.wait(2000)
        if self._classify_thread is not None and self._classify_thread.isRunning():
            if self._classify_worker is not None:
                self._classify_worker.cancel()
            self._classify_thread.quit()
            self._classify_thread.wait(2000)
        if self._preview_thread is not None and self._preview_thread.isRunning():
            if self._preview_worker is not None:
                self._preview_worker.cancel()
            self._preview_thread.quit()
            self._preview_thread.wait(2000)
        if self._dedup_thread is not None and self._dedup_thread.isRunning():
            if self._dedup_worker is not None:
                self._dedup_worker.cancel()
            self._dedup_thread.quit()
            self._dedup_thread.wait(2000)
        if self._dedup_action_thread is not None and self._dedup_action_thread.isRunning():
            if self._dedup_action_worker is not None:
                self._dedup_action_worker.cancel()
            self._dedup_action_thread.quit()
            self._dedup_action_thread.wait(2000)
        super().closeEvent(event)

    # ---------- 撤销 / 关于 ----------

    def _on_undo(self) -> None:
        """W5 详细实现."""
        batch = self._undo_stack.pop()
        if not batch:
            self._log("撤销栈为空")
            return
        import shutil

        for entry in batch:
            try:
                if entry.operation == "RenameOnly" and entry.target and entry.source:
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
            f"<h3>FileMaster v0.3.0 (W4)</h3>"
            f"<p>文件批量处理工具</p>"
            f"<p>Python + PySide6 + openpyxl + PyMuPDF</p>"
            f"<p>W4 v1: Classifier 集成（11 类 + magic bytes）</p>"
            f"<p>W4 v2: Preview 面板（文本/图片/PDF/Office/二进制）</p>"
            f"<p>W4 v3: Dedup 按 hash 去重（只查 + 表格预览）</p>"
            f"<p>配置目录: <code>{cfg_dir}</code></p>"
            f"<p>撤销栈深度: {len(self._undo_stack)}</p>"
            f"<p>© 2026 ECAS 技术开发科 · MIT License</p>",
        )

    # ---------- 工具 ----------

    def _log(self, msg: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self._txt_log.append(f"[{stamp}] {msg}")


# ============================================================
# W4 v6: Dedup 撤销对话框
# ============================================================


class DedupUndoDialog(QDialog):
    """撤销日志列表 + 恢复对话框（W4 v6）.

    设计：
    - 上半: QListWidget 列所有 undo log（按时间倒序,最新的在前）
      每行展示: [可恢复?] timestamp action  keeper  entries
    - 中间: 选项行（dry-run / overwrite）
    - 按钮: 「🔄 恢复」+「关闭」
    - 下半: QPlainTextEdit 状态输出（恢复结果/错误）

    不开 worker 线程：restore_undo_log 走 shutil.move 毫秒级,
    小批量（< 100 文件）同步即可; 大批量可后续切到 worker.
    """

    def __init__(
        self,
        parent: QWidget | None,
        *,
        logs: list[UndoLog],
        log_callback,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("W4 v6 · 撤销 (Dedup)")
        self.resize(720, 540)
        self.setModal(True)

        self._logs = logs
        self._log_callback = log_callback  # 调主窗口 _log

        # ----- 顶部: undo log 列表 -----
        self._list = QListWidget()
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        for log in logs:
            flag = "✓" if log.can_restore else "✗"
            label = (
                f"{flag}  {log.timestamp}  "
                f"action={log.action}  "
                f"entries={log.entry_count}  "
                f"keeper={log.keeper}"
            )
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, log)
            if not log.can_restore:
                # 不可恢复（delete/trash）灰显 + 提示
                from PySide6.QtGui import QBrush, QColor
                item.setForeground(QBrush(QColor("#888")))
                item.setToolTip(
                    f"{log.action} 操作不可恢复 (文件已永久删除或已在回收站)"
                )
            self._list.addItem(item)
        # 默认选第一个
        if self._list.count() > 0:
            self._list.setCurrentRow(0)
        self._list.itemSelectionChanged.connect(self._on_selection_changed)

        # ----- 中部: 选项行 -----
        self._chk_dry = QCheckBox("Dry-run (推荐先开,只看不真动)")
        self._chk_dry.setChecked(True)
        self._chk_overwrite = QCheckBox("覆盖已存在的目标")
        self._chk_overwrite.setChecked(False)

        h_opt = QHBoxLayout()
        h_opt.addWidget(self._chk_dry)
        h_opt.addWidget(self._chk_overwrite)
        h_opt.addStretch(1)

        # ----- 下部: 状态输出 -----
        self._txt_status = QPlainTextEdit()
        self._txt_status.setReadOnly(True)
        self._txt_status.setPlaceholderText("恢复结果会显示在这里…")

        # ----- 按钮 -----
        self._btn_restore = QPushButton("🔄 恢复")
        self._btn_restore.clicked.connect(self._on_restore_clicked)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.accept)

        h_btn = QHBoxLayout()
        h_btn.addStretch(1)
        h_btn.addWidget(self._btn_restore)
        h_btn.addWidget(btn_close)

        # ----- 主布局 -----
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(8)
        v.addWidget(QLabel(f"找到 {len(logs)} 个 undo log (按时间倒序):"))
        v.addWidget(self._list, stretch=2)
        v.addLayout(h_opt)
        v.addWidget(QLabel("状态:"))
        v.addWidget(self._txt_status, stretch=1)
        v.addLayout(h_btn)

        # 初始状态
        self._on_selection_changed()

    # ---------- 事件 ----------

    def _on_selection_changed(self) -> None:
        """选中变更 → 启用/禁用恢复按钮 + 在状态区贴详情."""
        log = self._current_log()
        if log is None:
            self._btn_restore.setEnabled(False)
            self._txt_status.clear()
            return

        self._btn_restore.setEnabled(log.can_restore)
        # 详情显示
        lines = [
            f"📄 {log.path.name}",
            f"  action:    {log.action}",
            f"  timestamp: {log.timestamp}",
            f"  group_hash: {log.group_hash[:16]}...",
            f"  keeper:    {log.keeper}",
            f"  entries:   {log.entry_count}",
        ]
        if log.can_restore:
            lines.append("  ✓ 可恢复（move 操作, 反向 shutil.move 回原位）")
        else:
            lines.append("  ✗ 不可恢复 (delete/trash 不可逆)")
            lines.append("  💡 提示: 用专业工具 (testdisk/photorec) 或从备份还原")
        # 列出每个 entry
        for i, e in enumerate(log.entries[:10], 1):
            op = e.get("op", "?")
            if op == "move":
                lines.append(f"  [{i}] move: {e.get('from', '?')} → {e.get('to', '?')}")
            else:
                lines.append(f"  [{i}] {op}: {e.get('path', e.get('from', '?'))}")
        if len(log.entries) > 10:
            lines.append(f"  ... 还有 {len(log.entries) - 10} 个 entry")
        self._txt_status.setPlainText("\n".join(lines))

    def _current_log(self) -> UndoLog | None:
        item = self._list.currentItem()
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def _on_restore_clicked(self) -> None:
        log = self._current_log()
        if log is None or not log.can_restore:
            return

        dry_run = self._chk_dry.isChecked()
        overwrite = self._chk_overwrite.isChecked()
        mode = "DRY-RUN" if dry_run else "EXEC"

        # 非 dry-run 二次确认
        if not dry_run:
            from PySide6.QtWidgets import QMessageBox
            reply = QMessageBox.question(
                self,
                f"确认恢复 [{mode}]",
                f"将从 {log.path.name} 恢复 {log.entry_count} 个文件\n\n"
                f"action: {log.action}\n"
                f"keeper: {log.keeper}\n\n"
                f"⚠️ 恢复会把文件从「{log.action} 后的位置」移回「原始位置」\n"
                f"目标已存在时{'会覆盖' if overwrite else '会跳过'}\n\n"
                f"确定继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                self._append_status(f"[{mode}] 用户取消")
                return

        # 真正调用 restore_undo_log
        try:
            results = restore_undo_log(
                log.path,
                overwrite=overwrite,
                dry_run=dry_run,
            )
        except FileNotFoundError as e:
            self._append_status(f"❌ {e}")
            self._log_callback(f"↩ undo 失败: {e}")
            return
        except ValueError as e:
            self._append_status(f"❌ {e}")
            self._log_callback(f"↩ undo 失败: {e}")
            return

        # 汇总结果
        success = sum(1 for r in results if r.success)
        skipped = sum(1 for r in results if r.skipped)
        failed = sum(1 for r in results if not r.success and not r.skipped)

        self._append_status(
            f"\n🔄 恢复完成 [{mode}]: 成功 {success} / 跳过 {skipped} / 失败 {failed}"
        )
        for r in results:
            if r.success:
                tag = "[DRY]" if dry_run else "✓"
                self._append_status(f"  {tag} {r.source} → {r.target}")
            elif r.skipped:
                self._append_status(f"  ⏭️  跳过 {r.source} → {r.target} ({r.error})")
            else:
                self._append_status(f"  ✗ {r.source} → {r.target}: {r.error}")

        # 同步刷主窗口日志
        self._log_callback(
            f"↩ undo [{mode}] {log.path.name}: "
            f"成功 {success} / 跳过 {skipped} / 失败 {failed}"
        )

    def _append_status(self, line: str) -> None:
        self._txt_status.appendPlainText(line)
