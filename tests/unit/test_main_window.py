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
