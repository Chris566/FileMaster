"""分类引擎.

W4 v1 实现：
- 11 类（PDF/DOC/SPREADSHEET/PRESENTATION/IMAGE/VIDEO/AUDIO/ARCHIVE/CODE/CONFIG/OTHER/UNKNOWN）
- Magic bytes 检测（不依赖 python-magic，沙箱友好）
- 扩展名兜底
- 置信度（0.0-1.0）
- 保留旧 rule-based API（向后兼容 W3 test）
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

# ============================================================
# 枚举与数据结构
# ============================================================


class Category(str, Enum):
    """文件分类枚举（str 化以便 JSON 序列化）."""

    PDF = "PDF"
    DOCUMENT = "DOCUMENT"  # doc/rtf/odt
    SPREADSHEET = "SPREADSHEET"  # xls/csv/ods
    PRESENTATION = "PRESENTATION"  # ppt/odp
    IMAGE = "IMAGE"
    VIDEO = "VIDEO"
    AUDIO = "AUDIO"
    ARCHIVE = "ARCHIVE"  # zip/7z/rar/tar
    CODE = "CODE"  # py/js/ts/go/rs
    CONFIG = "CONFIG"  # json/yaml/toml/ini
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"

    @property
    def label_zh(self) -> str:
        """中文标签（GUI/CLI 展示用）."""
        return {
            Category.PDF: "PDF",
            Category.DOCUMENT: "文档",
            Category.SPREADSHEET: "表格",
            Category.PRESENTATION: "演示",
            Category.IMAGE: "图片",
            Category.VIDEO: "视频",
            Category.AUDIO: "音频",
            Category.ARCHIVE: "压缩包",
            Category.CODE: "代码",
            Category.CONFIG: "配置",
            Category.OTHER: "其他",
            Category.UNKNOWN: "未知",
        }[self]


class DetectionMethod(str, Enum):
    """分类方法（决定 confidence 区间）."""

    MAGIC = "magic"  # 文件头签名命中
    EXTENSION = "extension"  # 扩展名 + mimetypes 一致
    EXT_ONLY = "ext_only"  # 仅扩展名命中
    FALLBACK = "fallback"  # 兜底（OTHER/UNKNOWN）


@dataclass(frozen=True)
class Classification:
    """单文件分类结果."""

    source: Path
    category: Category
    confidence: float  # 0.0-1.0
    method: DetectionMethod
    mime_type: str | None = None

    def to_dict(self) -> dict:
        """转字典（CLI --json 用）."""
        return {
            "source": str(self.source),
            "category": self.category.value,
            "category_zh": self.category.label_zh,
            "confidence": round(self.confidence, 3),
            "method": self.method.value,
            "mime_type": self.mime_type,
        }


# ============================================================
# Magic bytes 表
# ============================================================

# 格式：(offset, magic_bytes_hex, category, mime)
# offset=-1 表示前缀匹配；其他表示固定 offset
MAGIC_SIGNATURES: list[tuple[int, bytes, Category, str]] = [
    # PDF
    (0, b"%PDF-", Category.PDF, "application/pdf"),
    # PNG
    (0, b"\x89PNG\r\n\x1a\n", Category.IMAGE, "image/png"),
    # JPEG
    (0, b"\xff\xd8\xff", Category.IMAGE, "image/jpeg"),
    # GIF
    (0, b"GIF87a", Category.IMAGE, "image/gif"),
    (0, b"GIF89a", Category.IMAGE, "image/gif"),
    # BMP
    (0, b"BM", Category.IMAGE, "image/bmp"),
    # WebP
    (8, b"WEBP", Category.IMAGE, "image/webp"),
    # TIFF (little/big endian)
    (0, b"II*\x00", Category.IMAGE, "image/tiff"),
    (0, b"MM\x00*", Category.IMAGE, "image/tiff"),
    # ZIP / Office (xlsx/docx/pptx 都是 zip 容器)
    (0, b"PK\x03\x04", Category.ARCHIVE, "application/zip"),
    (0, b"PK\x05\x06", Category.ARCHIVE, "application/zip"),  # empty zip
    (0, b"PK\x07\x08", Category.ARCHIVE, "application/zip"),  # spanned zip
    # 7z
    (0, b"7z\xbc\xaf\x27\x1c", Category.ARCHIVE, "application/x-7z-compressed"),
    # RAR
    (0, b"Rar!\x1a\x07\x00", Category.ARCHIVE, "application/vnd.rar"),
    (0, b"Rar!\x1a\x07\x01", Category.ARCHIVE, "application/vnd.rar"),
    # Gzip
    (0, b"\x1f\x8b", Category.ARCHIVE, "application/gzip"),
    # Bzip2
    (0, b"BZh", Category.ARCHIVE, "application/x-bzip2"),
    # XZ
    (0, b"\xfd7zXZ\x00", Category.ARCHIVE, "application/x-xz"),
    # TAR (magic at offset 257, skip for simplicity - extension suffices)
    # ELF
    (0, b"\x7fELF", Category.CODE, "application/x-executable"),
    # Mach-O (Mach-O universal)
    (0, b"\xca\xfe\xba\xbe", Category.CODE, "application/x-mach-binary"),
    # Mach-O 64
    (0, b"\xcf\xfa\xed\xfe", Category.CODE, "application/x-mach-binary"),
    # Windows PE (MZ)
    (0, b"MZ", Category.CODE, "application/x-msdownload"),
    # Class file (Java)
    (0, b"\xca\xfe\xba\xbe", Category.CODE, "application/java-vm"),
    # MP4/MOV (ftyp box at offset 4)
    (4, b"ftyp", Category.VIDEO, "video/mp4"),
    # AVI
    (0, b"RIFF", Category.VIDEO, "video/x-msvideo"),
    # MKV/WebM
    (0, b"\x1a\x45\xdf\xa3", Category.VIDEO, "video/x-matroska"),
    # FLV
    (0, b"FLV\x01", Category.VIDEO, "video/x-flv"),
    # OGG
    (0, b"OggS", Category.AUDIO, "audio/ogg"),
    # WAV
    (0, b"RIFF", Category.AUDIO, "audio/wav"),
    # FLAC
    (0, b"fLaC", Category.AUDIO, "audio/flac"),
    # MP3 (ID3v2 tag)
    (0, b"ID3", Category.AUDIO, "audio/mpeg"),
    # MP3 (frame sync)
    # skip - too many false positives
]


# ============================================================
# 扩展名映射（兜底）
# ============================================================

# 格式：ext -> category
EXTENSION_MAP: dict[str, Category] = {
    # PDF
    ".pdf": Category.PDF,
    # DOCUMENT
    ".doc": Category.DOCUMENT,
    ".docx": Category.DOCUMENT,  # 也可能 magic 命中 ZIP（Office 容器）
    ".rtf": Category.DOCUMENT,
    ".odt": Category.DOCUMENT,
    ".txt": Category.DOCUMENT,
    ".md": Category.DOCUMENT,
    # SPREADSHEET
    ".xls": Category.SPREADSHEET,
    ".xlsx": Category.SPREADSHEET,
    ".csv": Category.SPREADSHEET,
    ".ods": Category.SPREADSHEET,
    # PRESENTATION
    ".ppt": Category.PRESENTATION,
    ".pptx": Category.PRESENTATION,
    ".odp": Category.PRESENTATION,
    # IMAGE
    ".jpg": Category.IMAGE,
    ".jpeg": Category.IMAGE,
    ".png": Category.IMAGE,
    ".gif": Category.IMAGE,
    ".bmp": Category.IMAGE,
    ".webp": Category.IMAGE,
    ".tif": Category.IMAGE,
    ".tiff": Category.IMAGE,
    ".svg": Category.IMAGE,
    ".heic": Category.IMAGE,
    ".ico": Category.IMAGE,
    # VIDEO
    ".mp4": Category.VIDEO,
    ".avi": Category.VIDEO,
    ".mov": Category.VIDEO,
    ".mkv": Category.VIDEO,
    ".webm": Category.VIDEO,
    ".flv": Category.VIDEO,
    ".wmv": Category.VIDEO,
    ".m4v": Category.VIDEO,
    # AUDIO
    ".mp3": Category.AUDIO,
    ".wav": Category.AUDIO,
    ".flac": Category.AUDIO,
    ".ogg": Category.AUDIO,
    ".m4a": Category.AUDIO,
    ".wma": Category.AUDIO,
    ".aac": Category.AUDIO,
    # ARCHIVE
    ".zip": Category.ARCHIVE,
    ".rar": Category.ARCHIVE,
    ".7z": Category.ARCHIVE,
    ".tar": Category.ARCHIVE,
    ".gz": Category.ARCHIVE,
    ".bz2": Category.ARCHIVE,
    ".xz": Category.ARCHIVE,
    # CODE
    ".py": Category.CODE,
    ".pyi": Category.CODE,
    ".js": Category.CODE,
    ".jsx": Category.CODE,
    ".ts": Category.CODE,
    ".tsx": Category.CODE,
    ".go": Category.CODE,
    ".rs": Category.CODE,
    ".c": Category.CODE,
    ".cpp": Category.CODE,
    ".cc": Category.CODE,
    ".cxx": Category.CODE,
    ".h": Category.CODE,
    ".hpp": Category.CODE,
    ".java": Category.CODE,
    ".kt": Category.CODE,
    ".swift": Category.CODE,
    ".rb": Category.CODE,
    ".php": Category.CODE,
    ".sh": Category.CODE,
    ".bash": Category.CODE,
    ".ps1": Category.CODE,
    ".bat": Category.CODE,
    ".cmd": Category.CODE,
    # CONFIG
    ".json": Category.CONFIG,
    ".yaml": Category.CONFIG,
    ".yml": Category.CONFIG,
    ".toml": Category.CONFIG,
    ".ini": Category.CONFIG,
    ".cfg": Category.CONFIG,
    ".conf": Category.CONFIG,
    ".xml": Category.CONFIG,
    ".env": Category.CONFIG,
}


# ============================================================
# 顶层 API（W4 v1 新增）
# ============================================================


def _read_magic(path: Path, size: int = 16) -> bytes | None:
    """读文件头 size 字节（用于 magic bytes 匹配）.

    Args:
        path: 文件路径
        size: 读取字节数（默认 16，覆盖所有已知签名）
    Returns:
        文件头字节，文件不存在/不可读返回 None
    """
    try:
        with path.open("rb") as f:
            return f.read(size)
    except (OSError, PermissionError):
        return None


def _detect_magic(path: Path) -> tuple[Category, str] | None:
    """magic bytes 检测.

    Returns:
        (Category, mime_type) 或 None（未命中）
    """
    header = _read_magic(path)
    if not header:
        return None

    for offset, sig, cat, mime in MAGIC_SIGNATURES:
        if offset < 0:
            if header.startswith(sig):
                return (cat, mime)
        else:
            if len(header) > offset and header[offset : offset + len(sig)] == sig:
                return (cat, mime)
    return None


def _classify_by_extension(path: Path) -> tuple[Category, str | None]:
    """扩展名检测（含 mimetypes 标准库交叉验证）.

    Returns:
        (Category, mime_type) 或 (Category.OTHER, None)
    """
    import mimetypes

    ext = path.suffix.lower()
    if not ext:
        return (Category.OTHER, None)

    # 1. 查映射表
    cat = EXTENSION_MAP.get(ext)
    mime = mimetypes.guess_type(path.name)[0]

    if cat is not None:
        return (cat, mime)

    # 2. mimetypes 兜底（标准库能识别一些）
    if mime is not None:
        if mime.startswith("image/"):
            return (Category.IMAGE, mime)
        if mime.startswith("video/"):
            return (Category.VIDEO, mime)
        if mime.startswith("audio/"):
            return (Category.AUDIO, mime)
        if mime.startswith("text/"):
            return (Category.DOCUMENT, mime)
        if "zip" in mime or "compressed" in mime or "tar" in mime:
            return (Category.ARCHIVE, mime)

    return (Category.OTHER, mime)


def classify_file(path: Path) -> Classification:
    """分类单个文件.

    优先级：magic > extension（含 mimetypes 交叉验证） > OTHER
    Office 文件（docx/xlsx/pptx）特殊处理：magic 命中 ZIP，但扩展名升级到对应分类。

    Args:
        path: 文件路径（不存在/不可读也安全降级为 UNKNOWN）
    Returns:
        Classification 结果
    """
    # 1. magic bytes 优先
    magic_result = _detect_magic(path)
    if magic_result is not None:
        cat, mime = magic_result
        # 修正：Office 文件（docx/xlsx/pptx）和 LibreOffice（odt/ods/odp）都是 ZIP 容器
        # magic 命中 ARCHIVE，但扩展名应升级到对应分类
        ext = path.suffix.lower()
        if ext in {".docx", ".doc"} and cat == Category.ARCHIVE:
            cat = Category.DOCUMENT
        elif ext in {".xlsx", ".xls"} and cat == Category.ARCHIVE:
            cat = Category.SPREADSHEET
        elif ext in {".pptx", ".ppt"} and cat == Category.ARCHIVE:
            cat = Category.PRESENTATION
        elif ext == ".odt" and cat == Category.ARCHIVE:
            cat = Category.DOCUMENT
        elif ext == ".ods" and cat == Category.ARCHIVE:
            cat = Category.SPREADSHEET
        elif ext == ".odp" and cat == Category.ARCHIVE:
            cat = Category.PRESENTATION
        return Classification(
            source=path,
            category=cat,
            confidence=0.95,
            method=DetectionMethod.MAGIC,
            mime_type=mime,
        )

    # 2. 扩展名兜底
    cat, mime = _classify_by_extension(path)
    if cat != Category.OTHER:
        return Classification(
            source=path,
            category=cat,
            confidence=0.75,
            method=DetectionMethod.EXTENSION,
            mime_type=mime,
        )

    # 3. 兜底：OTHER
    if mime is not None:
        return Classification(
            source=path,
            category=Category.OTHER,
            confidence=0.4,
            method=DetectionMethod.EXT_ONLY,
            mime_type=mime,
        )

    # 4. 完全没有线索
    return Classification(
        source=path,
        category=Category.UNKNOWN,
        confidence=0.0,
        method=DetectionMethod.FALLBACK,
        mime_type=None,
    )


def classify_batch(paths: Iterable[Path]) -> list[Classification]:
    """批量分类.

    Args:
        paths: 文件路径可迭代对象
    Returns:
        Classification 列表（与输入顺序一致）
    """
    return [classify_file(p) for p in paths]


def group_by_category(
    classifications: Iterable[Classification],
) -> dict[Category, list[Classification]]:
    """按 Category 分组.

    Returns:
        OrderedDict-like dict（按 Category 枚举顺序）
    """
    groups: dict[Category, list[Classification]] = {c: [] for c in Category}
    for c in classifications:
        groups[c.category].append(c)
    return {k: v for k, v in groups.items() if v}


# ============================================================
# 旧 rule-based API（保留，W3 兼容）
# ============================================================


# 内置分类
BUILTIN_CATEGORIES: dict[str, tuple[str, ...]] = {
    "PDF": (".pdf",),
    "WORD": (".doc", ".docx", ".rtf", ".odt"),
    "EXCEL": (".xls", ".xlsx", ".csv", ".ods"),
    "PPT": (".ppt", ".pptx", ".odp"),
    "IMAGE": (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".svg"),
}


@dataclass(frozen=True)
class ClassificationRule:
    """单条分类规则."""

    category: str
    extensions: tuple[str, ...] = ()
    pattern: str | None = None
    min_size: int | None = None
    max_size: int | None = None
    enabled: bool = True


@dataclass
class ClassificationResult:
    """分类结果（旧 API，保留向后兼容）."""

    source: Path
    target_dir: Path
    category: str
    matched_rule: str | None = None


@dataclass
class Classifier:
    """旧 rule-based 分类器（保留，W3 test 用）."""

    rules: list[ClassificationRule] = field(default_factory=list)
    target_root: Path | None = None

    @classmethod
    def from_builtin(cls) -> Classifier:
        """用内置 5 类构造分类器."""
        rules = [
            ClassificationRule(category=cat, extensions=exts)
            for cat, exts in BUILTIN_CATEGORIES.items()
        ]
        return cls(rules=rules)

    def classify(self, file: Path) -> ClassificationResult | None:
        """对单个文件分类."""
        for rule in self.rules:
            if not rule.enabled:
                continue
            if rule.extensions and file.suffix.lower() not in rule.extensions:
                continue
            if rule.pattern and not re.search(rule.pattern, file.name):
                continue
            if rule.min_size is not None and file.stat().st_size < rule.min_size:
                continue
            if rule.max_size is not None and file.stat().st_size > rule.max_size:
                continue
            assert self.target_root is not None
            return ClassificationResult(
                source=file,
                target_dir=self.target_root / rule.category,
                category=rule.category,
                matched_rule=rule.category,
            )
        return None

    def classify_all(self, files: Iterable[Path]) -> list[ClassificationResult]:
        """批量分类."""
        return [r for r in (self.classify(f) for f in files) if r is not None]
