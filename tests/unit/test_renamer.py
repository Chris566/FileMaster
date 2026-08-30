"""重命名引擎测试."""

from __future__ import annotations

from pathlib import Path

from filemaster.core.renamer import RenameResult, Renamer
from filemaster.core.template import Template


class TestRenamerPlan:
    """plan() 只生成结果，不动文件."""

    def test_plan_with_prefix(self, sample_files: list[Path]) -> None:
        tpl = Template("{Prefix}{OriginalName}")
        renamer = Renamer(tpl, prefix="X_")
        results = renamer.plan(sample_files)
        assert len(results) == 3
        for r in results:
            assert r.status == "DRY_RUN"
            assert r.target is not None
            assert r.target.name.startswith("X_")

    def test_plan_increments_index(self, sample_files: list[Path]) -> None:
        tpl = Template("{Index:D3}_{OriginalName}")
        renamer = Renamer(tpl)
        results = renamer.plan(sample_files)
        # 序号从 1 起
        assert results[0].target.name.startswith("001_")
        assert results[1].target.name.startswith("002_")
        assert results[2].target.name.startswith("003_")

    def test_plan_skips_unchanged(self, sample_files: list[Path]) -> None:
        # 模板与原名相同（无前缀）
        tpl = Template("{OriginalName}")
        renamer = Renamer(tpl)
        results = renamer.plan(sample_files)
        for r in results:
            assert r.status == "SKIPPED"
            assert r.target is None

    def test_plan_start_index(self, sample_files: list[Path]) -> None:
        tpl = Template("{Index:D3}_{OriginalName}")
        renamer = Renamer(tpl, start_index=100)
        results = renamer.plan(sample_files)
        assert results[0].target.name.startswith("100_")
        assert results[2].target.name.startswith("102_")

    def test_reset_index(self, sample_files: list[Path]) -> None:
        tpl = Template("{Index:D3}_{OriginalName}")
        renamer = Renamer(tpl)
        renamer.plan(sample_files[:1])  # 推进 index
        renamer.reset_index()
        results = renamer.plan(sample_files[:1])
        assert results[0].target.name.startswith("001_")


class TestRenamerPrefix:
    """前缀识别."""

    def test_already_has_prefix(self, sample_files: list[Path]) -> None:
        renamer = Renamer(Template("X"), prefix="X_")
        # 文件名是 doc_001.pdf，没有 X_ 前缀
        assert not renamer.already_has_prefix(sample_files[0])

    def test_already_has_prefix_case_insensitive(self, tmp_path: Path) -> None:
        f = tmp_path / "X_Document.pdf"
        f.write_bytes(b"x")
        renamer = Renamer(Template("X"), prefix="x_")
        assert renamer.already_has_prefix(f)

    def test_empty_prefix(self, sample_files: list[Path]) -> None:
        renamer = Renamer(Template("X"), prefix="")
        assert not renamer.already_has_prefix(sample_files[0])


class TestRenamerSanitize:
    """文件名清理."""

    def test_removes_windows_illegal(self) -> None:
        assert Renamer.sanitize('a<b>c:d"e/f\\g|h?i*.txt') == "a_b_c_d_e_f_g_h_i_.txt"

    def test_keeps_normal_chars(self) -> None:
        assert Renamer.sanitize("normal_filename-v1.0.pdf") == "normal_filename-v1.0.pdf"

    def test_replaces_control_chars(self) -> None:
        assert Renamer.sanitize("file\x00name\x1f.pdf") == "file_name_.pdf"

    def test_empty_string(self) -> None:
        assert Renamer.sanitize("") == ""
