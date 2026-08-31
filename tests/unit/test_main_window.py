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
        from filemaster.core.dedup import DuplicateFile, DuplicateGroup
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


# ============================================================
# W4 v4: Dedup 动作 (move/delete/hardlink) GUI 集成测试
# ============================================================


class TestDedupActionButtons:
    """W4 v4: 3 个动作按钮 + 选项存在."""

    def test_action_buttons_exist(self, main_window) -> None:
        """3 个动作按钮都建出来."""
        assert hasattr(main_window, "_btn_dedup_move")
        assert hasattr(main_window, "_btn_dedup_delete")
        assert hasattr(main_window, "_btn_dedup_hardlink")
        assert "移动" in main_window._btn_dedup_move.text()
        assert "删除" in main_window._btn_dedup_delete.text()
        assert "硬链" in main_window._btn_dedup_hardlink.text()

    def test_action_buttons_enabled_by_default(self, main_window) -> None:
        """没在 dedup 任务时, 3 个动作按钮都可点."""
        assert main_window._btn_dedup_move.isEnabled()
        assert main_window._btn_dedup_delete.isEnabled()
        assert main_window._btn_dedup_hardlink.isEnabled()

    def test_dryrun_checked_by_default(self, main_window) -> None:
        """默认开 dry-run (安全)."""
        assert main_window._chk_dedup_dryrun.isChecked()

    def test_target_dir_widget_exists(self, main_window) -> None:
        """目标目录 line edit + 按钮存在."""
        assert hasattr(main_window, "_txt_dedup_target")
        assert main_window._txt_dedup_target.text() == ""  # 默认空 → 走默认路径

    def test_overwrite_unchecked_by_default(self, main_window) -> None:
        """覆盖默认不勾."""
        assert not main_window._chk_dedup_overwrite.isChecked()

    def test_trash_checked_by_default(self, main_window) -> None:
        """删时进回收站默认勾上."""
        assert main_window._chk_dedup_trash.isChecked()


class TestGetSelectedDedupGroup:
    """_get_selected_dedup_group 选行/未选行行为."""

    def test_no_selection_returns_none_and_shows_dialog(
        self, main_window, monkeypatch
    ) -> None:
        """没选行 → 弹窗提示 + 返 None."""
        from PySide6.QtWidgets import QMessageBox

        called = []
        monkeypatch.setattr(
            QMessageBox, "information",
            lambda *a, **kw: called.append(a) or QMessageBox.StandardButton.Ok,
        )
        # 确保没选
        main_window._table_dedup.clearSelection()
        result = main_window._get_selected_dedup_group()
        assert result is None
        assert called  # 弹了窗

    def test_returns_group_when_selected(self, main_window, tmp_path) -> None:
        """选了一行 → 返对应 group."""
        from filemaster.core.dedup import DuplicateFile, DuplicateGroup

        p1 = tmp_path / "a.txt"
        p1.write_text("hello")
        p2 = tmp_path / "a_copy.txt"
        p2.write_text("hello")
        meta = (
            DuplicateFile(p1, 5, mtime=1.0, ctime=0.0),
            DuplicateFile(p2, 5, mtime=2.0, ctime=0.0),
        )
        g = DuplicateGroup(
            hash_value="x", algorithm="md5",
            files=(p1, p2), files_with_meta=meta, hash_size=5, wasted_bytes=5,
        )
        main_window._dedup_groups = [g]
        main_window._refresh_dedup_table()
        main_window._table_dedup.selectRow(0)
        result = main_window._get_selected_dedup_group()
        assert result is g


class TestDedupActionConfirm:
    """_on_dedup_action 二次确认 + 取消逻辑."""

    def test_dry_run_skips_confirm_dialog(
        self, main_window, tmp_path, monkeypatch, qtbot
    ) -> None:
        """dry-run=True 时不弹确认, 直接起 worker, 完成后弹 information 也 mock 掉."""
        from PySide6.QtWidgets import QMessageBox

        from filemaster.core.dedup import DuplicateFile, DuplicateGroup

        called = []
        monkeypatch.setattr(
            QMessageBox, "question",
            lambda *a, **kw: called.append("question_called") or QMessageBox.StandardButton.Yes,
        )
        # 完成后的 QMessageBox.information 也要 mock, 否则测试环境会卡死
        monkeypatch.setattr(
            QMessageBox, "information",
            lambda *a, **kw: called.append("info_called") or QMessageBox.StandardButton.Ok,
        )
        # 注入一个 group
        p1 = tmp_path / "a.txt"
        p1.write_text("hello")
        p2 = tmp_path / "a_copy.txt"
        p2.write_text("hello")
        meta = (
            DuplicateFile(p1, 5, mtime=1.0, ctime=0.0),
            DuplicateFile(p2, 5, mtime=2.0, ctime=0.0),
        )
        g = DuplicateGroup(
            hash_value="x", algorithm="md5",
            files=(p1, p2), files_with_meta=meta, hash_size=5, wasted_bytes=5,
        )
        main_window._dedup_groups = [g]
        main_window._refresh_dedup_table()
        main_window._table_dedup.selectRow(0)
        main_window._chk_dedup_dryrun.setChecked(True)

        # dry-run 跑 move → 走 worker, 不弹 question
        main_window._on_dedup_action("move")
        # 关键断言: worker 起来了, 但 question 没被调
        assert main_window._dedup_action_thread is not None
        assert "question_called" not in called

        # 等 worker 线程跑完 (dr dry-run 很快)
        worker_thread = main_window._dedup_action_thread
        assert worker_thread is not None
        qtbot.wait_until(
            lambda: not worker_thread.isRunning(),
            timeout=5000,
        )

        # 再 drain 一次事件循环, 让 finished 的 slot 跑完 (调 information)
        qtbot.wait(200)

        # 确认 information 被调了 (完成弹窗) 且 question 没被调
        assert "info_called" in called
        assert "question_called" not in called

    def test_real_action_shows_confirm_dialog(
        self, main_window, tmp_path, monkeypatch
    ) -> None:
        """非 dry-run 时必弹 question, 用户点 No → 不跑."""
        from PySide6.QtWidgets import QMessageBox

        from filemaster.core.dedup import DuplicateFile, DuplicateGroup

        called = []
        monkeypatch.setattr(
            QMessageBox, "question",
            lambda *a, **kw: called.append("shown") or QMessageBox.StandardButton.No,
        )
        p1 = tmp_path / "a.txt"
        p1.write_text("hello")
        p2 = tmp_path / "a_copy.txt"
        p2.write_text("hello")
        meta = (
            DuplicateFile(p1, 5, mtime=1.0, ctime=0.0),
            DuplicateFile(p2, 5, mtime=2.0, ctime=0.0),
        )
        g = DuplicateGroup(
            hash_value="x", algorithm="md5",
            files=(p1, p2), files_with_meta=meta, hash_size=5, wasted_bytes=5,
        )
        main_window._dedup_groups = [g]
        main_window._refresh_dedup_table()
        main_window._table_dedup.selectRow(0)
        main_window._chk_dedup_dryrun.setChecked(False)  # 关键: 关 dry-run

        main_window._on_dedup_action("delete")
        # 用户点 No → 没起 worker
        assert main_window._dedup_action_thread is None
        assert "shown" in called


# ============================================================
# W4 v6: Dedup Undo GUI 集成
# ============================================================


def _write_undo_log(
    log_dir: Path,
    *,
    ts: str,
    action: str,
    entries: list[dict],
    keeper: str = "/tmp/keeper",
    group_hash: str = "abc123def456",
) -> Path:
    """在 log_dir 写一个 undo log JSON（测试 helper）."""
    import json as _json

    log_dir.mkdir(parents=True, exist_ok=True)
    p = log_dir / f"{ts}_{group_hash[:8]}_{action}.json"
    payload = {
        "action": action,
        "timestamp": ts,
        "group_hash": group_hash,
        "keeper": keeper,
        "entries": entries,
    }
    p.write_text(_json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return p


class TestDedupUndoButton:
    """W4 v6: 工具栏"↶ 撤销"按钮 + 触发逻辑."""

    def test_undo_button_exists(self, main_window) -> None:
        """撤销按钮在 dedup 动作组里."""
        assert hasattr(main_window, "_btn_dedup_undo")
        assert main_window._btn_dedup_undo.text() == "↶ 撤销"

    def test_undo_button_enabled_by_default(self, main_window) -> None:
        assert main_window._btn_dedup_undo.isEnabled()

    def test_undo_button_clicked_when_no_logs_shows_info(
        self, main_window, monkeypatch, tmp_path
    ) -> None:
        """没 undo log 时点按钮 → 弹 information 不弹窗崩溃."""
        from PySide6.QtWidgets import QMessageBox

        # monkeypatch main_window 局部导入的 list_undo_logs
        # (不要 patch dedup_mod 自身的, main_window 顶部 import 的是另一份引用)
        from filemaster.ui import main_window as mw_mod
        monkeypatch.setattr(mw_mod, "list_undo_logs", lambda *a, **kw: [])

        called = []
        monkeypatch.setattr(
            QMessageBox, "information",
            lambda *a, **kw: called.append(a) or QMessageBox.StandardButton.Ok,
        )
        main_window._btn_dedup_undo.click()
        # 弹了信息框
        assert called
        # 主日志也写了
        log_text = main_window._txt_log.toPlainText()
        assert "undo" in log_text.lower() or "↩" in log_text

    def test_undo_button_opens_dialog_with_logs(
        self, main_window, monkeypatch, tmp_path
    ) -> None:
        """有 undo log 时点按钮 → 弹 DedupUndoDialog."""
        from filemaster.core import dedup as dedup_mod
        from filemaster.ui import main_window as mw_mod
        from filemaster.ui.main_window import DedupUndoDialog

        # 造一个 fake UndoLog
        log_path = _write_undo_log(
            tmp_path,
            ts="20260831_120000",
            action="move",
            entries=[
                {"op": "move", "from": str(tmp_path / "moved.txt"), "to": str(tmp_path / "src.txt")},
            ],
            keeper=str(tmp_path / "src.txt"),
        )
        fake_log = dedup_mod.UndoLog.from_path(log_path)
        # patch main_window 局部导入的 list_undo_logs
        monkeypatch.setattr(mw_mod, "list_undo_logs", lambda *a, **kw: [fake_log])

        main_window._btn_dedup_undo.click()
        # 对话框被创建并显示
        assert hasattr(main_window, "_dedup_undo_dialog")
        assert main_window._dedup_undo_dialog is not None
        assert isinstance(main_window._dedup_undo_dialog, DedupUndoDialog)
        # 列表里有 1 项
        assert main_window._dedup_undo_dialog._list.count() == 1

        # 清理
        main_window._dedup_undo_dialog.close()


class TestDedupUndoDialog:
    """DedupUndoDialog 行为."""

    @pytest.fixture
    def sample_move_log(self, tmp_path: Path) -> Path:
        """造 1 个可恢复的 move undo log."""
        return _write_undo_log(
            tmp_path,
            ts="20260831_120000",
            action="move",
            entries=[
                {
                    "op": "move",
                    "from": str(tmp_path / "moved1.txt"),
                    "to": str(tmp_path / "src1.txt"),
                },
                {
                    "op": "move",
                    "from": str(tmp_path / "moved2.txt"),
                    "to": str(tmp_path / "src2.txt"),
                },
            ],
            keeper=str(tmp_path / "src1.txt"),
        )

    @pytest.fixture
    def sample_delete_log(self, tmp_path: Path) -> Path:
        """造 1 个不可恢复的 delete undo log."""
        return _write_undo_log(
            tmp_path,
            ts="20260831_130000",
            action="delete",
            entries=[
                {"op": "delete", "path": str(tmp_path / "deleted.txt")},
            ],
            keeper=str(tmp_path / "src.txt"),
        )

    def test_dialog_creation_with_move_log(self, sample_move_log) -> None:
        from filemaster.core.dedup import UndoLog
        from filemaster.ui.main_window import DedupUndoDialog

        log = UndoLog.from_path(sample_move_log)
        dlg = DedupUndoDialog(None, logs=[log], log_callback=lambda m: None)
        # 默认选第一个
        assert dlg._list.count() == 1
        assert dlg._list.currentRow() == 0
        # 可恢复 → 按钮可用
        assert dlg._btn_restore.isEnabled()
        dlg.close()

    def test_dialog_creation_with_delete_log_disables_button(
        self, sample_delete_log
    ) -> None:
        from filemaster.core.dedup import UndoLog
        from filemaster.ui.main_window import DedupUndoDialog

        log = UndoLog.from_path(sample_delete_log)
        dlg = DedupUndoDialog(None, logs=[log], log_callback=lambda m: None)
        # 不可恢复 → 按钮禁用
        assert not dlg._btn_restore.isEnabled()
        # 状态区给了提示
        status = dlg._txt_status.toPlainText()
        assert "不可恢复" in status
        dlg.close()

    def test_dialog_default_dryrun_checked(self, sample_move_log) -> None:
        from filemaster.core.dedup import UndoLog
        from filemaster.ui.main_window import DedupUndoDialog

        log = UndoLog.from_path(sample_move_log)
        dlg = DedupUndoDialog(None, logs=[log], log_callback=lambda m: None)
        assert dlg._chk_dry.isChecked()  # 安全默认
        assert not dlg._chk_overwrite.isChecked()
        dlg.close()

    def test_dialog_mixed_logs_marks_can_restore(
        self, sample_move_log, sample_delete_log
    ) -> None:
        """move + delete 混合, move 可点 delete 灰显."""
        from filemaster.core.dedup import UndoLog
        from filemaster.ui.main_window import DedupUndoDialog

        log_move = UndoLog.from_path(sample_move_log)
        log_del = UndoLog.from_path(sample_delete_log)
        # 顺序: move (新) + delete (更早, 但 list_undo_logs 反序, 这里手动排)
        dlg = DedupUndoDialog(
            None, logs=[log_move, log_del], log_callback=lambda m: None
        )
        assert dlg._list.count() == 2
        # 选第 0 (move) → 按钮可用
        dlg._list.setCurrentRow(0)
        assert dlg._btn_restore.isEnabled()
        # 选第 1 (delete) → 按钮禁用
        dlg._list.setCurrentRow(1)
        assert not dlg._btn_restore.isEnabled()
        dlg.close()

    def test_dialog_dryrun_does_not_move_files(
        self, sample_move_log, tmp_path
    ) -> None:
        """dry-run 模式点恢复 → 文件不动, 状态区显示 [DRY] 标记."""
        from filemaster.core.dedup import UndoLog
        from filemaster.ui.main_window import DedupUndoDialog

        # 让 entries 里的 from 存在 + to 不存在（dry-run 仍会报"会做什么"）
        moved1 = tmp_path / "moved1.txt"
        moved1.write_text("hi")
        src1 = tmp_path / "src1.txt"
        # 重写 log entries 用新 path
        log_path = _write_undo_log(
            tmp_path,
            ts="20260831_120000",
            action="move",
            entries=[
                {"op": "move", "from": str(moved1), "to": str(src1)},
            ],
        )
        log = UndoLog.from_path(log_path)
        dlg = DedupUndoDialog(None, logs=[log], log_callback=lambda m: None)
        dlg._chk_dry.setChecked(True)  # dry-run
        dlg._btn_restore.click()

        status = dlg._txt_status.toPlainText()
        assert "DRY" in status.upper() or "Dry" in status
        # dry-run 不该动文件
        assert moved1.exists()
        assert not src1.exists()
        dlg.close()

    def test_dialog_real_restore_moves_files_back(self, tmp_path) -> None:
        """非 dry-run 点恢复 → 文件从 moved 位置回到 src 位置."""
        from PySide6.QtWidgets import QMessageBox

        from filemaster.core.dedup import UndoLog
        from filemaster.ui.main_window import DedupUndoDialog

        # 准备：文件在 "moved" 位置（restore 会移回 src）
        moved = tmp_path / "moved.txt"
        moved.write_text("hello")
        src = tmp_path / "src.txt"
        # src 故意不创建（restore_undo_log 会 mkdir parent）

        log_path = _write_undo_log(
            tmp_path,
            ts="20260831_120000",
            action="move",
            entries=[{"op": "move", "from": str(moved), "to": str(src)}],
        )
        log = UndoLog.from_path(log_path)
        # 跳过二次确认
        dlg = DedupUndoDialog(
            None, logs=[log],
            log_callback=lambda m: None,
        )
        # monkeypatch QMessageBox.question → Yes (不在 dialog 里, 用 patch)
        orig_q = QMessageBox.question
        QMessageBox.question = staticmethod(
            lambda *a, **kw: QMessageBox.StandardButton.Yes
        )
        try:
            dlg._chk_dry.setChecked(False)  # 真恢复
            dlg._btn_restore.click()
        finally:
            QMessageBox.question = orig_q

        # 文件移回 src 位置
        assert src.exists()
        assert src.read_text() == "hello"
        assert not moved.exists()
        dlg.close()
