"""MainWindow GUI 集成测试（W4 v1：Classifier 集成）.

覆盖：
- 工具栏"📁 分类"按钮存在
- 表格列数（5 列：# / 文件名 / 大小 / 分类 / 置信度）
- _on_load_files_to_table 填表
- _on_filter_category 过滤
- 模板中 {Category} / {Category_zh} 占位符

跑在 pytest-qt offscreen 模式（conftest.py 已自动设）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.gui  # 可选 marker


# ============================================================
# Fixture
# ============================================================


@pytest.fixture
def main_window(qtbot):
    """构造 MainWindow 实例（offscreen 模式）."""
    from filemaster.ui.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)
    yield win
    win.close()


def _make_pdf(p: Path, name: str = "test") -> Path:
    f = p / f"{name}.pdf"
    f.write_bytes(b"%PDF-1.4\n")
    return f


def _make_png(p: Path, name: str = "test") -> Path:
    f = p / f"{name}.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)
    return f


def _make_py(p: Path, name: str = "test") -> Path:
    f = p / f"{name}.py"
    f.write_text("x = 1\n")
    return f


# ============================================================
# 工具栏 & 表格结构
# ============================================================


class TestToolbar:
    """工具栏 W4 v1 新增元素."""

    def test_classify_button_exists(self, main_window) -> None:
        assert hasattr(main_window, "_btn_classify")
        assert main_window._btn_classify.text() == "📁 分类"

    def test_scan_button_exists(self, main_window) -> None:
        assert hasattr(main_window, "_btn_scan")
        # W4 v2 改名: "📊 预览" → "🔄 扫描"（避免与右侧 Preview 面板语义混淆）
        assert main_window._btn_scan.text() == "🔄 扫描"

    def test_window_title_v4(self, main_window) -> None:
        assert "v0.3.0" in main_window.windowTitle()
        assert "W4" in main_window.windowTitle()


class TestTableStructure:
    """QTableWidget 5 列结构."""

    def test_table_has_5_columns(self, main_window) -> None:
        assert hasattr(main_window, "_table")
        assert main_window._table.columnCount() == 5

    def test_table_headers(self, main_window) -> None:
        headers = [
            main_window._table.horizontalHeaderItem(i).text()
            for i in range(main_window._table.columnCount())
        ]
        assert headers == ["#", "文件名", "大小", "分类", "置信度"]

    def test_table_starts_empty(self, main_window) -> None:
        assert main_window._table.rowCount() == 0


class TestFilterCombo:
    """左侧分类组的过滤下拉."""

    def test_filter_has_all_options(self, main_window) -> None:
        assert hasattr(main_window, "_cmb_filter")
        items = [
            main_window._cmb_filter.itemText(i)
            for i in range(main_window._cmb_filter.count())
        ]
        assert items[0] == "全部"
        assert "PDF" in items
        assert "IMAGE" in items
        assert "VIDEO" in items
        assert "CODE" in items
        # 12 个 Category + 1 个"全部" = 13
        assert len(items) == 13

    def test_default_filter_is_all(self, main_window) -> None:
        assert main_window._cmb_filter.currentText() == "全部"


# ============================================================
# 加载 & 刷新表格
# ============================================================


class TestLoadFilesToTable:
    """_on_load_files_to_table."""

    def test_load_with_no_source_shows_warning(self, main_window, tmp_path, monkeypatch) -> None:
        # 让 QMessageBox.warning 不弹窗
        from PySide6.QtWidgets import QMessageBox
        monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: 0)

        main_window._txt_source.setText("")  # 无源
        main_window._on_load_files_to_table()
        # _all_classifications 应保持空
        assert main_window._all_classifications == []

    def test_load_files_fills_table(self, main_window, tmp_path) -> None:
        # 创建混合文件
        _make_pdf(tmp_path, "doc1")
        _make_png(tmp_path, "img1")
        _make_py(tmp_path, "script")
        main_window._txt_source.setText(str(tmp_path))
        main_window._chk_classify_recursive.setChecked(False)

        main_window._on_load_files_to_table()

        # 3 个文件
        assert main_window._table.rowCount() == 3
        assert len(main_window._all_classifications) == 3

    def test_loaded_table_categories_correct(self, main_window, tmp_path) -> None:
        _make_pdf(tmp_path, "doc")
        _make_png(tmp_path, "img")
        main_window._txt_source.setText(str(tmp_path))
        main_window._chk_classify_recursive.setChecked(False)
        main_window._on_load_files_to_table()

        # 抓所有"分类"列内容
        cats = []
        for row in range(main_window._table.rowCount()):
            item = main_window._table.item(row, 3)
            assert item is not None
            cats.append(item.text())
        # 至少包含 PDF 和 IMAGE
        assert any("PDF" in c for c in cats)
        assert any("IMAGE" in c for c in cats)

    def test_loaded_table_confidence_column(self, main_window, tmp_path) -> None:
        _make_pdf(tmp_path, "doc")
        main_window._txt_source.setText(str(tmp_path))
        main_window._chk_classify_recursive.setChecked(False)
        main_window._on_load_files_to_table()

        item = main_window._table.item(0, 4)
        assert item is not None
        # 置信度应该是 0.00-1.00 范围
        text = item.text()
        conf = float(text)
        assert 0.0 <= conf <= 1.0


class TestFilterCategory:
    """过滤下拉."""

    def test_filter_to_pdf_hides_others(self, main_window, tmp_path) -> None:
        _make_pdf(tmp_path, "a")
        _make_png(tmp_path, "b")
        main_window._txt_source.setText(str(tmp_path))
        main_window._chk_classify_recursive.setChecked(False)
        main_window._on_load_files_to_table()

        assert main_window._table.rowCount() == 2  # 全部

        # 切到 PDF
        pdf_idx = main_window._cmb_filter.findText("PDF")
        main_window._cmb_filter.setCurrentIndex(pdf_idx)
        assert main_window._table.rowCount() == 1

        # 切回全部
        all_idx = main_window._cmb_filter.findText("全部")
        main_window._cmb_filter.setCurrentIndex(all_idx)
        assert main_window._table.rowCount() == 2

    def test_filter_to_nonexistent_hides_all(self, main_window, tmp_path) -> None:
        _make_pdf(tmp_path, "a")
        main_window._txt_source.setText(str(tmp_path))
        main_window._chk_classify_recursive.setChecked(False)
        main_window._on_load_files_to_table()

        video_idx = main_window._cmb_filter.findText("VIDEO")
        main_window._cmb_filter.setCurrentIndex(video_idx)
        assert main_window._table.rowCount() == 0


class TestStatsLabel:
    """类别统计标签."""

    def test_stats_label_updates(self, main_window, tmp_path) -> None:
        _make_pdf(tmp_path, "a")
        _make_pdf(tmp_path, "b")
        _make_png(tmp_path, "c")
        main_window._txt_source.setText(str(tmp_path))
        main_window._chk_classify_recursive.setChecked(False)
        main_window._on_load_files_to_table()

        text = main_window._lbl_stats.text()
        assert "共 3" in text
        assert "PDF" in text
        assert "IMAGE" in text

    def test_stats_label_empty_initially(self, main_window) -> None:
        assert "未扫描" in main_window._lbl_stats.text()


# ============================================================
# 模板占位符
# ============================================================


class TestTemplatePlaceholders:
    """{Category} / {Category_zh} 占位符可用."""

    def test_template_accepts_category_placeholder(self) -> None:
        from filemaster.core.template import Template

        # 不应抛错
        tpl = Template("{Category}_{OriginalName}")
        assert tpl.raw == "{Category}_{OriginalName}"

    def test_template_accepts_category_zh(self) -> None:
        from filemaster.core.template import Template
        tpl = Template("{Category_zh}_{OriginalName}")
        assert tpl.raw == "{Category_zh}_{OriginalName}"


class TestRenamerCategoryIntegration:
    """renamer 集成：模板里 {Category} 应被替换为分类枚举值."""

    def test_renamer_renders_category(self, tmp_path: Path) -> None:
        from filemaster.core.renamer import Renamer
        from filemaster.core.template import Template

        # 造文件
        f = tmp_path / "report.pdf"
        f.write_bytes(b"%PDF-1.4\n")

        # 模板里同时用 Category 和 Category_zh
        tpl = Template("{Category}_{Category_zh}_{OriginalName}")
        renamer = Renamer(template=tpl, prefix="")
        ctx = renamer._context_for(f)
        assert ctx["Category"] == "PDF"
        assert ctx["Category_zh"] == "PDF"

    def test_renamer_renders_for_image(self, tmp_path: Path) -> None:
        from filemaster.core.renamer import Renamer
        from filemaster.core.template import Template

        f = tmp_path / "photo.png"
        f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)

        tpl = Template("{Category_zh}_{OriginalName}")
        renamer = Renamer(template=tpl, prefix="")
        ctx = renamer._context_for(f)
        assert ctx["Category_zh"] == "图片"


# ============================================================
# W4 v3：Dedup 集成
# ============================================================


class TestDedupToolbar:
    """W4 v3 工具栏：🔍 去重 按钮."""

    def test_dedup_button_exists(self, main_window) -> None:
        assert hasattr(main_window, "_btn_dedup")
        assert main_window._btn_dedup.text() == "🔍 去重"

    def test_dedup_button_enabled_by_default(self, main_window) -> None:
        assert main_window._btn_dedup.isEnabled()


class TestDedupTable:
    """W4 v3 中间面板：去重表 6 列结构."""

    def test_dedup_table_exists(self, main_window) -> None:
        assert hasattr(main_window, "_table_dedup")
        assert main_window._table_dedup.columnCount() == 6

    def test_dedup_table_headers(self, main_window) -> None:
        headers = [
            main_window._table_dedup.horizontalHeaderItem(i).text()
            for i in range(main_window._table_dedup.columnCount())
        ]
        assert headers == ["#", "Hash(短)", "大小", "文件数", "浪费", "文件列表"]

    def test_dedup_table_starts_empty(self, main_window) -> None:
        assert main_window._table_dedup.rowCount() == 0

    def test_dedup_summary_label_initially_idle(self, main_window) -> None:
        assert hasattr(main_window, "_lbl_dedup_summary")
        assert "未执行" in main_window._lbl_dedup_summary.text() or "去重" in main_window._lbl_dedup_summary.text()


class TestCenterStack:
    """W4 v3 QStackedWidget 切换."""

    def test_center_stack_exists(self, main_window) -> None:
        assert hasattr(main_window, "_center_stack")
        # 两个页面: 分类 + 去重
        assert main_window._center_stack.count() == 2

    def test_default_page_is_classify(self, main_window) -> None:
        assert main_window._center_stack.currentIndex() == 0


class TestDedupHandler:
    """_on_dedup 入口（不真启动 worker, 只验状态）."""

    def test_dedup_with_no_source_warns(self, main_window, monkeypatch) -> None:
        from PySide6.QtWidgets import QMessageBox
        monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: 0)

        main_window._txt_source.setText("")
        main_window._on_dedup()  # 不应崩溃, 应弹 warning
        # dedup 按钮没被禁用 (因为根本没启 worker)
        assert main_window._btn_dedup.isEnabled()

    def test_dedup_with_nonexistent_source_warns(
        self, main_window, monkeypatch, tmp_path
    ) -> None:
        from PySide6.QtWidgets import QMessageBox
        monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: 0)

        main_window._txt_source.setText(str(tmp_path / "no_such_dir"))
        main_window._on_dedup()
        assert main_window._btn_dedup.isEnabled()

    def test_dedup_starts_worker_and_switches_stack(
        self, main_window, tmp_path, monkeypatch, qtbot
    ) -> None:
        """有效源 → 启动 worker, 切到去重表."""
        from PySide6.QtWidgets import QMessageBox
        monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: 0)
        monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: 0)

        # 放几个文件让 worker 跑
        (tmp_path / "a.txt").write_text("alpha")
        (tmp_path / "b.txt").write_text("alpha")  # dup
        (tmp_path / "c.txt").write_text("beta")
        main_window._txt_source.setText(str(tmp_path))

        main_window._on_dedup()
        # 切到去重表 (index 1)
        assert main_window._center_stack.currentIndex() == 1
        # 启动时 dedup 按钮被禁用
        assert not main_window._btn_dedup.isEnabled()
        # 等 worker 跑完
        qtbot.waitUntil(
            lambda: main_window._dedup_thread is None, timeout=10000
        )
        # 完成后按钮恢复
        assert main_window._btn_dedup.isEnabled()
        # 至少 1 组
        assert main_window._dedup_stats is not None
        assert main_window._dedup_stats.duplicate_groups >= 1
        # 表格被填
        assert main_window._table_dedup.rowCount() >= 1


class TestDedupTableRefresh:
    """_refresh_dedup_table 单元."""

    def test_refresh_with_empty_groups(self, main_window) -> None:
        main_window._dedup_groups = []
        main_window._refresh_dedup_table()
        assert main_window._table_dedup.rowCount() == 0

    def test_refresh_with_groups(self, main_window, tmp_path) -> None:
        from filemaster.core.dedup import DuplicateFile, DuplicateGroup
        p1, p2, p3 = [tmp_path / f"f{i}.txt" for i in range(3)]
        meta = (
            DuplicateFile(p1, 100, mtime=1.0, ctime=0.0),
            DuplicateFile(p2, 100, mtime=2.0, ctime=0.0),
            DuplicateFile(p3, 100, mtime=3.0, ctime=0.0),
        )
        g = DuplicateGroup(
            hash_value="abcdef1234567890" * 4,  # 64 hex
            algorithm="md5",
            files=(p1, p2, p3),
            files_with_meta=meta,
            hash_size=100,
            wasted_bytes=200,
        )
        main_window._dedup_groups = [g]
        main_window._refresh_dedup_table()
        assert main_window._table_dedup.rowCount() == 1
        # Hash(短) 列: 12 位 + …
        hash_item = main_window._table_dedup.item(0, 1).text()
        assert hash_item.startswith("abcdef123456") and "…" in hash_item
        # 大小
        assert "100" in main_window._table_dedup.item(0, 2).text()
        # 文件数
        assert main_window._table_dedup.item(0, 3).text() == "3"
        # 浪费
        assert "200" in main_window._table_dedup.item(0, 4).text()
        # 文件列表
        files_text = main_window._table_dedup.item(0, 5).text()
        assert "f0.txt" in files_text
        assert "f2.txt" in files_text


class TestDedupRowSelection:
    """点击去重表行 → 联动 Preview 面板."""

    def test_row_selection_triggers_preview(
        self, main_window, tmp_path, qtbot
    ) -> None:
        from PySide6.QtWidgets import QMessageBox
        from filemaster.core.dedup import DuplicateFile, DuplicateGroup
        monkeypatch_set = None
        # 准备一个 keeper 文件
        p_keeper = tmp_path / "keeper.txt"
        p_keeper.write_text("dup content")
        p_dup = tmp_path / "dup.txt"
        p_dup.write_text("dup content")

        meta = (
            DuplicateFile(p_keeper, 11, mtime=1.0, ctime=0.0),  # keeper
            DuplicateFile(p_dup, 11, mtime=2.0, ctime=0.0),
        )
        g = DuplicateGroup(
            hash_value="abc",
            algorithm="md5",
            files=(p_keeper, p_dup),
            files_with_meta=meta,
            hash_size=11,
            wasted_bytes=11,
        )
        main_window._dedup_groups = [g]
        main_window._refresh_dedup_table()

        # 选中第 0 行
        main_window._table_dedup.selectRow(0)
        qtbot.waitUntil(
            lambda: main_window._preview_thread is not None, timeout=3000
        )
        # 元信息侧栏应被刷成 keeper
        assert main_window._lbl_meta_name.text() == "keeper.txt"
        # 等 preview 跑完
        qtbot.waitUntil(
            lambda: main_window._preview_thread is None, timeout=5000
        )
