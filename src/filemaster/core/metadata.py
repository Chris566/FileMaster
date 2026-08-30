"""元数据读取（W8 详细实现）.

- PDF：PyMuPDF (fitz)
- Word：python-docx
- Excel：openpyxl
- 图片：Pillow EXIF
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FileMetadata:
    """文件元数据."""

    title: str = ""
    author: str = ""
    subject: str = ""
    created: str = ""  # ISO 8601
    modified: str = ""
    page_count: int = 0
    extra: dict = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.extra is None:
            object.__setattr__(self, "extra", {})


class MetadataReader:
    """元数据读取器（按扩展名分发）."""

    PDF_EXTS = {".pdf"}
    WORD_EXTS = {".doc", ".docx", ".rtf"}
    EXCEL_EXTS = {".xls", ".xlsx", ".ods"}
    IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff"}

    def read(self, file: Path) -> FileMetadata:
        """读取文件元数据.

        Args:
            file: 源文件
        Returns:
            FileMetadata
        """
        ext = file.suffix.lower()
        if ext in self.PDF_EXTS:
            return self._read_pdf(file)
        if ext in self.WORD_EXTS:
            return self._read_word(file)
        if ext in self.EXCEL_EXTS:
            return self._read_excel(file)
        if ext in self.IMAGE_EXTS:
            return self._read_image(file)
        return FileMetadata()

    def _read_pdf(self, file: Path) -> FileMetadata:
        """PDF：PyMuPDF."""
        try:
            import fitz  # PyMuPDF

            with fitz.open(file) as doc:
                meta = doc.metadata or {}
                return FileMetadata(
                    title=meta.get("title", "") or "",
                    author=meta.get("author", "") or "",
                    subject=meta.get("subject", "") or "",
                    created=str(meta.get("creationDate", "") or ""),
                    modified=str(meta.get("modDate", "") or ""),
                    page_count=doc.page_count,
                )
        except Exception:
            return FileMetadata()

    def _read_word(self, file: Path) -> FileMetadata:
        """Word：python-docx."""
        try:
            from docx import Document

            doc = Document(file)
            cp = doc.core_properties
            return FileMetadata(
                title=cp.title or "",
                author=cp.author or "",
                subject=cp.subject or "",
                created=str(cp.created or ""),
                modified=str(cp.modified or ""),
                page_count=len(doc.paragraphs),  # 简化
            )
        except Exception:
            return FileMetadata()

    def _read_excel(self, file: Path) -> FileMetadata:
        """Excel：openpyxl."""
        try:
            from openpyxl import load_workbook

            wb = load_workbook(file, read_only=True, data_only=True)
            props = wb.properties
            return FileMetadata(
                title=props.title or "",
                author=props.creator or "",
                subject=props.subject or "",
                created=str(props.created or ""),
                modified=str(props.modified or ""),
            )
        except Exception:
            return FileMetadata()

    def _read_image(self, file: Path) -> FileMetadata:
        """图片：Pillow EXIF."""
        try:
            from PIL import Image
            from PIL.ExifTags import TAGS

            with Image.open(file) as img:
                exif = img.getexif() or {}
                decoded = {TAGS.get(k, k): v for k, v in exif.items()}
                return FileMetadata(
                    title="",
                    author=decoded.get("Artist", "") or "",
                    created=str(decoded.get("DateTime", "") or ""),
                    extra={"size": img.size, "format": img.format},
                )
        except Exception:
            return FileMetadata()

    def read_all(self, files: Iterable[Path]) -> dict[Path, FileMetadata]:
        """批量读取."""
        return {f: self.read(f) for f in files}
