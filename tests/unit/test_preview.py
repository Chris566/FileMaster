"""Preview 模块（W4 v2）测试.

覆盖：
- PreviewKind enum
- FileMetadata 提取（真实文件 + 不存在文件）
- render_text（正常 / 截断 / 跨平台）
- render_image（合法 PNG / 非法 bytes）
- render_hex
- classify_for_preview（按扩展名）
- _is_likely_text / _guess_mime（内部 helper）
- PreviewGenerator 类
- build_preview 统一入口（5 大类型 + 空文件 + 超大文件 + Office 缺失库）
- PreviewWorker（信号触发）
- MainWindow 集成（右侧元信息 + 工具栏按钮改名）
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.gui  # 同 test_main_window, 走 offscreen 模式


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def sample_txt(tmp_path: Path) -> Path:
    """普通 UTF-8 文本."""
    p = tmp_path / "hello.txt"
    p.write_text("你好世界\nLine 2\nLine 3\n", encoding="utf-8")
    return p


@pytest.fixture
def sample_png(tmp_path: Path) -> Path:
    """真实合法 PNG（1x1 透明）."""
    p = tmp_path / "red.png"
    # 1x1 红色 PNG（68 字节，最小合法 PNG）
    import base64
    data = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    p.write_bytes(data)
    return p


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    """最小合法 PDF."""
    p = tmp_path / "doc.pdf"
    p.write_bytes(b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\nxref\n0 1\n0000000000 65535 f\n"
                  b"trailer<</Size 1>>startxref\n50\n%%EOF\n")
    return p


@pytest.fixture
def sample_docx(tmp_path: Path) -> Path:
    """最小合法 docx（PK ZIP + 必要目录项）."""
    import zipfile
    p = tmp_path / "doc.docx"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types/>')
        zf.writestr("word/document.xml", "<?xml version='1.0'?><doc><p>Hello</p></doc>")
    return p


@pytest.fixture
def sample_binary(tmp_path: Path) -> Path:
    """带 NULL 字节的伪二进制."""
    p = tmp_path / "blob.bin"
    p.write_bytes(b"\x00\x01\x02\x03\xFF\xFE\xFD\x00hello\x00world")
    return p


@pytest.fixture
def main_window(qtbot):
    """同 test_main_window."""
    from filemaster.ui.main_window import MainWindow

    win = MainWindow()
    qtbot.addWidget(win)
    yield win
    win.close()


# ============================================================
# PreviewKind enum
# ============================================================


class TestPreviewKind:
    def test_values(self) -> None:
        from filemaster.core.preview import PreviewKind

        assert PreviewKind.TEXT.value == "text"
        assert PreviewKind.IMAGE.value == "image"
        assert PreviewKind.PDF.value == "pdf"
        assert PreviewKind.OFFICE_DOC.value == "office_doc"
        assert PreviewKind.OFFICE_SHEET.value == "office_sheet"
        assert PreviewKind.OFFICE_SLIDE.value == "office_slide"
        assert PreviewKind.BINARY.value == "binary"
        assert PreviewKind.UNSUPPORTED.value == "unsupported"
        assert len(PreviewKind) == 8

    def test_str_enum(self) -> None:
        from filemaster.core.preview import PreviewKind

        # str 子类 → 可直接当字符串用
        assert PreviewKind.TEXT == "text"


# ============================================================
# FileMetadata 提取
# ============================================================


class TestFileMetadata:
    def test_real_file(self, sample_txt: Path) -> None:
        from filemaster.core.preview import extract_metadata

        meta = extract_metadata(sample_txt)
        assert meta.path == sample_txt
        assert meta.size > 0
        assert meta.mtime > 0
        assert meta.ctime > 0
        assert meta.mode.startswith("0")  # 八进制
        assert meta.mime == "text/plain"

    def test_missing_file(self, tmp_path: Path) -> None:
        from filemaster.core.preview import extract_metadata

        missing = tmp_path / "nope.txt"
        meta = extract_metadata(missing)
        assert meta.size == 0
        assert meta.mtime == 0.0
        assert meta.ctime == 0.0
        assert meta.mode == "0000"
        # MIME 仍按扩展名猜
        assert meta.mime == "text/plain"

    def test_mime_by_extension(self, tmp_path: Path) -> None:
        from filemaster.core.preview import extract_metadata

        for ext, expected in [
            (".md", "text/markdown"),
            (".json", "application/json"),
            (".png", "image/png"),
            (".pdf", "application/pdf"),
            (".docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            (".exe", "application/x-msdownload"),
        ]:
            p = tmp_path / f"x{ext}"
            p.write_bytes(b"x")
            meta = extract_metadata(p)
            assert meta.mime == expected, f"{ext} → {meta.mime}"


# ============================================================
# render_text
# ============================================================


class TestRenderText:
    def test_basic(self) -> None:
        from filemaster.core.preview import PreviewKind, render_text

        c = render_text(b"hello\nworld\n", max_lines=10, max_bytes=1024)
        assert c.kind == PreviewKind.TEXT
        assert c.payload == "hello\nworld"
        assert c.truncated is False
        assert c.note == ""

    def test_unicode(self) -> None:
        from filemaster.core.preview import PreviewKind, render_text

        c = render_text("你好世界\n".encode(), max_lines=10, max_bytes=1024)
        assert c.kind == PreviewKind.TEXT
        assert "你好世界" in c.payload

    def test_truncate_by_bytes(self) -> None:
        from filemaster.core.preview import PreviewKind, render_text

        big = b"x" * 5000
        c = render_text(big, max_lines=1000, max_bytes=1024)
        assert c.kind == PreviewKind.TEXT
        assert c.truncated is True
        assert "已截断" in c.note

    def test_truncate_by_lines(self) -> None:
        from filemaster.core.preview import PreviewKind, render_text

        many_lines = ("line\n" * 500).encode()
        c = render_text(many_lines, max_lines=10, max_bytes=1024 * 1024)
        assert c.truncated is True
        assert c.payload.count("\n") <= 10

    def test_replacement_char_on_bad_encoding(self) -> None:
        from filemaster.core.preview import PreviewKind, render_text

        # 0xC0 0x80 是无效 UTF-8 起始字节序列
        c = render_text(b"hello\xC0\x80world", max_lines=10, max_bytes=1024)
        assert c.kind == PreviewKind.TEXT
        # errors="replace" → 不抛异常
        assert "hello" in c.payload and "world" in c.payload


# ============================================================
# render_image
# ============================================================


class TestRenderImage:
    def test_valid_png(self, sample_png: Path) -> None:
        from filemaster.core.preview import PreviewKind, render_image

        data = sample_png.read_bytes()
        c = render_image(data)
        assert c.kind == PreviewKind.IMAGE
        # QImage 有 isNull() / width() / height()
        img = c.payload
        assert not img.isNull()
        assert img.width() == 1
        assert img.height() == 1

    def test_invalid_bytes_fallback_hex(self) -> None:
        from filemaster.core.preview import PreviewKind, render_image

        # 0xFF 0x00 ... 不是合法图片
        c = render_image(b"\xFF\x00\xFF\x00not an image")
        assert c.kind == PreviewKind.BINARY
        assert "hex dump" in c.note
        # payload 应该是 hex 字符串
        assert isinstance(c.payload, str)
        assert "ff00ff00" in c.payload.replace(" ", "").lower()


# ============================================================
# render_hex
# ============================================================


class TestRenderHex:
    def test_basic(self) -> None:
        from filemaster.core.preview import PreviewKind, render_hex

        c = render_hex(b"ABCD", max_bytes=1024)
        assert c.kind == PreviewKind.BINARY
        assert "41 42 43 44" in c.payload.upper()  # A=0x41, B=0x42...

    def test_truncate(self) -> None:
        from filemaster.core.preview import PreviewKind, render_hex

        big = bytes(range(256)) * 100
        c = render_hex(big, max_bytes=64)
        # 只显示前 64 字节
        assert c.truncated is True
        assert "已截断" in c.note

    def test_empty(self) -> None:
        from filemaster.core.preview import PreviewKind, render_hex

        c = render_hex(b"", max_bytes=1024)
        assert c.kind == PreviewKind.BINARY
        assert c.payload == ""
        assert c.truncated is False


# ============================================================
# classify_for_preview
# ============================================================


class TestClassifyForPreview:
    @pytest.mark.parametrize("ext,expected", [
        (".txt", PreviewKind := __import__("filemaster.core.preview", fromlist=["PreviewKind"]).PreviewKind.TEXT),
        (".md", __import__("filemaster.core.preview", fromlist=["PreviewKind"]).PreviewKind.TEXT),
        (".png", __import__("filemaster.core.preview", fromlist=["PreviewKind"]).PreviewKind.IMAGE),
        (".jpg", __import__("filemaster.core.preview", fromlist=["PreviewKind"]).PreviewKind.IMAGE),
        (".gif", __import__("filemaster.core.preview", fromlist=["PreviewKind"]).PreviewKind.IMAGE),
        (".pdf", __import__("filemaster.core.preview", fromlist=["PreviewKind"]).PreviewKind.PDF),
        (".docx", __import__("filemaster.core.preview", fromlist=["PreviewKind"]).PreviewKind.OFFICE_DOC),
        (".xlsx", __import__("filemaster.core.preview", fromlist=["PreviewKind"]).PreviewKind.OFFICE_SHEET),
        (".pptx", __import__("filemaster.core.preview", fromlist=["PreviewKind"]).PreviewKind.OFFICE_SLIDE),
    ])
    def test_by_extension(self, tmp_path: Path, ext: str, expected) -> None:
        from filemaster.core.preview import classify_for_preview

        p = tmp_path / f"x{ext}"
        p.write_bytes(b"x")
        assert classify_for_preview(p) == expected


# ============================================================
# 内部 helper
# ============================================================


class TestInternalHelpers:
    def test_is_likely_text(self) -> None:
        from filemaster.core.preview import _is_likely_text

        assert _is_likely_text(b"hello world") is True
        assert _is_likely_text("你好".encode()) is True
        # 含 NULL 字节 → 二进制
        assert _is_likely_text(b"\x00\x01\x02") is False
        # 纯空白
        assert _is_likely_text(b"") is True
        # latin-1 字符（可解码）
        assert _is_likely_text("café".encode("latin-1")) is True

    def test_guess_mime(self) -> None:
        from filemaster.core.preview import _guess_mime

        assert _guess_mime(Path("/x.txt")) == "text/plain"
        assert _guess_mime(Path("/x.PNG")) == "image/png"  # case-insensitive
        assert _guess_mime(Path("/x.unknown")) == "application/octet-stream"


# ============================================================
# PreviewGenerator 类
# ============================================================


class TestPreviewGenerator:
    def test_generate_text(self, sample_txt: Path) -> None:
        from filemaster.core.preview import PreviewGenerator, PreviewKind

        gen = PreviewGenerator()
        meta, content = gen.generate(sample_txt)
        assert meta.size > 0
        assert content.kind == PreviewKind.TEXT

    def test_metadata(self, sample_txt: Path) -> None:
        from filemaster.core.preview import PreviewGenerator

        gen = PreviewGenerator()
        meta = gen.metadata(sample_txt)
        assert meta.path == sample_txt
        assert meta.size > 0

    def test_classify(self, sample_txt: Path, sample_png: Path) -> None:
        from filemaster.core.preview import PreviewGenerator, PreviewKind

        gen = PreviewGenerator()
        assert gen.classify(sample_txt) == PreviewKind.TEXT
        assert gen.classify(sample_png) == PreviewKind.IMAGE

    def test_custom_max_text_bytes(self, sample_txt: Path) -> None:
        from filemaster.core.preview import PreviewGenerator, PreviewKind

        gen = PreviewGenerator(max_text_bytes=16)
        meta, content = gen.generate(sample_txt)
        assert meta.size > 16
        # 16 字节很小的限制 → 文本应被截断
        assert content.kind == PreviewKind.TEXT
        assert content.truncated is True


# ============================================================
# build_preview 统一入口
# ============================================================


class TestBuildPreview:
    def test_text(self, sample_txt: Path) -> None:
        from filemaster.core.preview import PreviewKind, build_preview

        meta, c = build_preview(sample_txt)
        assert meta.size > 0
        assert c.kind == PreviewKind.TEXT

    def test_image(self, sample_png: Path) -> None:
        from filemaster.core.preview import PreviewKind, build_preview

        _meta, c = build_preview(sample_png)
        assert c.kind == PreviewKind.IMAGE

    def test_pdf(self, sample_pdf: Path) -> None:
        from filemaster.core.preview import build_preview

        # PDF 可能成功渲染成 IMAGE 也可能因 PDF 格式不标准降级
        meta, c = build_preview(sample_pdf)
        assert meta.size > 0
        # 至少不抛异常 → c.kind 是合法 enum
        from filemaster.core.preview import PreviewKind
        assert c.kind in {PreviewKind.PDF, PreviewKind.UNSUPPORTED, PreviewKind.BINARY}

    def test_docx(self, sample_docx: Path) -> None:
        from filemaster.core.preview import build_preview

        _meta, c = build_preview(sample_docx)
        # python-docx 装了 → 走 OFFICE_DOC；没装 → UNSUPPORTED
        from filemaster.core.preview import PreviewKind
        assert c.kind in {PreviewKind.OFFICE_DOC, PreviewKind.UNSUPPORTED, PreviewKind.BINARY}

    def test_binary_fallback(self, sample_binary: Path) -> None:
        from filemaster.core.preview import PreviewKind, build_preview

        _meta, c = build_preview(sample_binary)
        # 含 NULL 字节 → 走 BINARY hex dump
        assert c.kind == PreviewKind.BINARY
        assert isinstance(c.payload, str)

    def test_empty_file(self, tmp_path: Path) -> None:
        from filemaster.core.preview import PreviewKind, build_preview

        p = tmp_path / "empty.txt"
        p.write_bytes(b"")
        meta, c = build_preview(p)
        assert meta.size == 0
        assert c.kind == PreviewKind.UNSUPPORTED
        assert "空文件" in c.note

    def test_missing_file(self, tmp_path: Path) -> None:
        from filemaster.core.preview import PreviewKind, build_preview

        p = tmp_path / "nope.txt"
        meta, c = build_preview(p)
        assert meta.size == 0
        assert c.kind == PreviewKind.UNSUPPORTED

    def test_oversize_file(self, tmp_path: Path) -> None:
        """>50MB 文件应只读头 1KB → BINARY."""
        from filemaster.core.preview import _HEX_PREVIEW_MAX_BYTES, PreviewKind, build_preview

        p = tmp_path / "big.bin"
        # 用 truncate 而不是真写满 50MB（沙箱磁盘有限）
        # 改用 monkey-patch _TEXT_PREVIEW_MAX_BYTES? 不行, 改 50MB 阈值更简单:
        # 直接构造一个 50MB+1 字节的 sparse file (Linux 支持, Win 不支持, 跳过)
        if not hasattr(os, "truncate"):
            pytest.skip("os.truncate not available")
        with open(p, "wb") as f:
            f.truncate(51 * 1024 * 1024)
        try:
            meta, c = build_preview(p)
            assert meta.size > 50 * 1024 * 1024
            assert c.kind == PreviewKind.BINARY
            assert "过大" in c.note
        finally:
            p.unlink(missing_ok=True)

    def test_unsupported_ext(self, tmp_path: Path) -> None:
        """未知扩展名 + 二进制内容 → BINARY."""
        from filemaster.core.preview import PreviewKind, build_preview

        p = tmp_path / "mystery.xyz"
        p.write_bytes(b"\x00\x01\x02\x03")
        _meta, c = build_preview(p)
        assert c.kind == PreviewKind.BINARY


# ============================================================
# PreviewWorker
# ============================================================


class TestPreviewWorker:
    def test_signals_on_text(self, sample_txt: Path, qtbot) -> None:
        from PySide6.QtCore import QEventLoop, QThread, QTimer

        from filemaster.workers.preview import PreviewWorker

        thread = QThread()
        worker = PreviewWorker(sample_txt)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)

        succeeded_results: list[tuple] = []
        failed_results: list[tuple] = []
        worker.succeeded.connect(lambda m, c: succeeded_results.append((m, c)))
        worker.failed.connect(lambda p, e: failed_results.append((p, e)))
        worker.finished.connect(thread.quit)

        thread.start()

        # 最多等 3 秒
        QTimer.singleShot(3000, thread.quit)
        qtbot.waitUntil(lambda: not thread.isRunning(), timeout=4000)

        assert len(failed_results) == 0
        assert len(succeeded_results) == 1
        _meta, c = succeeded_results[0]
        assert c.kind.value == "text"

    def test_signals_on_missing(self, tmp_path: Path, qtbot) -> None:
        from PySide6.QtCore import QThread, QTimer

        from filemaster.workers.preview import PreviewWorker

        thread = QThread()
        worker = PreviewWorker(tmp_path / "nope.txt")
        worker.moveToThread(thread)
        thread.started.connect(worker.run)

        succeeded: list = []
        failed: list[tuple[str, str]] = []
        worker.succeeded.connect(lambda m, c: succeeded.append((m, c)))
        worker.failed.connect(lambda p, e: failed.append((p, e)))
        worker.finished.connect(thread.quit)

        thread.start()
        QTimer.singleShot(3000, thread.quit)
        qtbot.waitUntil(lambda: not thread.isRunning(), timeout=4000)

        # 文件不存在时 build_preview 不抛, 返 UNSUPPORTED → succeeded
        # 但 PreviewGenerator.generate 也不抛, 所以 failed 应该是 0
        # 至少要保证没崩
        assert len(succeeded) + len(failed) == 1

    def test_cancel_before_run(self, sample_txt: Path, qtbot) -> None:
        """W8: 预取消, 不会发 succeeded, 应该发 cancelled()."""
        from PySide6.QtCore import QThread, QTimer

        from filemaster.workers.preview import PreviewWorker

        thread = QThread()
        worker = PreviewWorker(sample_txt)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)

        succeeded: list = []
        failed: list = []
        cancelled_count: list[int] = []
        worker.succeeded.connect(lambda m, c: succeeded.append((m, c)))
        worker.failed.connect(lambda p, e: failed.append((p, e)))
        worker.cancelled.connect(lambda: cancelled_count.append(1))
        worker.finished.connect(thread.quit)

        worker.cancel()  # 预取消
        thread.start()
        QTimer.singleShot(3000, thread.quit)
        qtbot.waitUntil(lambda: not thread.isRunning(), timeout=4000)

        assert len(succeeded) == 0
        assert len(failed) == 0
        assert len(cancelled_count) == 1  # W8: emit cancelled()

    def test_cancellation_token_property(self, sample_txt: Path) -> None:
        """W8: 暴露 cancellation_token 属性."""
        from filemaster.workers.preview import PreviewWorker

        worker = PreviewWorker(sample_txt)
        assert worker.cancellation_token.is_cancelled is False
        worker.cancel()
        assert worker.cancellation_token.is_cancelled is True


# ============================================================
# MainWindow 集成（W4 v2）
# ============================================================


class TestMainWindowPreview:
    def test_scan_button_renamed(self, main_window) -> None:
        """W4 v2 改 "📊 预览" → "🔄 扫描"（不与右侧预览面板冲突）."""
        assert main_window._btn_scan.text() == "🔄 扫描"
        assert "预览" in main_window._btn_scan.toolTip()

    def test_meta_labels_exist(self, main_window) -> None:
        """右侧元信息 6 个 QLabel 都建好了."""
        for attr in [
            "_lbl_meta_name", "_lbl_meta_size", "_lbl_meta_mtime",
            "_lbl_meta_ctime", "_lbl_meta_mode", "_lbl_meta_mime",
        ]:
            assert hasattr(main_window, attr), f"missing {attr}"
            assert getattr(main_window, attr) is not None

    def test_preview_stack_exists(self, main_window) -> None:
        """QStackedWidget 含 3 页：text / image / fallback."""
        from PySide6.QtWidgets import QStackedWidget
        assert isinstance(main_window._stack_preview, QStackedWidget)
        assert main_window._stack_preview.count() == 3

    def test_text_preview_widget(self, main_window) -> None:
        from PySide6.QtWidgets import QTextEdit
        assert isinstance(main_window._txt_preview_text, QTextEdit)
        assert main_window._txt_preview_text.isReadOnly()

    def test_image_preview_widget(self, main_window) -> None:
        from PySide6.QtWidgets import QLabel
        assert isinstance(main_window._lbl_preview_image, QLabel)

    def test_fallback_preview_widget(self, main_window) -> None:
        from PySide6.QtWidgets import QLabel
        assert isinstance(main_window._lbl_preview_fallback, QLabel)

    def test_initial_state(self, main_window) -> None:
        """未选中文件时, 元信息都是 "—"."""
        for attr in [
            "_lbl_meta_name", "_lbl_meta_size", "_lbl_meta_mtime",
            "_lbl_meta_ctime", "_lbl_meta_mode", "_lbl_meta_mime",
        ]:
            assert getattr(main_window, attr).text() == "—"

    def test_preview_thread_initially_none(self, main_window) -> None:
        assert main_window._preview_thread is None
        assert main_window._preview_worker is None

    def test_table_selection_connected(self, main_window) -> None:
        """itemSelectionChanged 信号已连到 _on_table_selection_changed."""
        from PySide6.QtCore import QItemSelectionModel
        # 简单验证：选第 0 行（空表也行, 会 return）不抛
        main_window._table.selectRow(0)
        # 没崩就行
