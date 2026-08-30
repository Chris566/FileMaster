"""生成 1w 文件压测夹具.

用法:
    python scripts/gen_fixtures.py --output tests/fixtures/10k_mixed --count 10000

默认生成：
- 8000 个小文件（每类约 1500）
- 1000 个中等文件
- 1000 个大文件（每文件 1MB）
- 包含 5% 重复（用于去重测试）
"""

from __future__ import annotations

import argparse
import random
import shutil
import sys
from pathlib import Path

# 把 src 加进 sys.path（独立运行）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from filemaster.core.classifier import BUILTIN_CATEGORIES


def gen_small_files(target: Path, count: int = 8000) -> None:
    """小文件（<10KB）."""
    random.seed(42)
    extensions_by_cat = BUILTIN_CATEGORIES
    all_exts = [ext for exts in extensions_by_cat.values() for ext in exts]

    for i in range(count):
        ext = random.choice(all_exts)
        cat = next(c for c, e in extensions_by_cat.items() if ext in e)
        subdir = target / cat
        subdir.mkdir(parents=True, exist_ok=True)
        # 文件名加噪声
        prefix = random.choice(["doc", "report", "data", "sample", "test", ""])
        name = f"{prefix}_{i:06d}{ext}" if prefix else f"{i:06d}{ext}"
        f = subdir / name
        f.write_bytes(b"\x00" * random.randint(100, 10_000))


def gen_medium_files(target: Path, count: int = 1000) -> None:
    """中等文件（100KB-1MB）."""
    random.seed(43)
    all_exts = [ext for exts in BUILTIN_CATEGORIES.values() for ext in exts]
    for i in range(count):
        ext = random.choice(all_exts)
        cat = next(c for c, e in BUILTIN_CATEGORIES.items() if ext in e)
        subdir = target / cat
        subdir.mkdir(parents=True, exist_ok=True)
        f = subdir / f"medium_{i:04d}{ext}"
        f.write_bytes(b"\x00" * random.randint(100_000, 1_000_000))


def gen_large_files(target: Path, count: int = 1000) -> None:
    """大文件（每文件 1MB，生成 1GB 总数据）."""
    random.seed(44)
    all_exts = [ext for exts in BUILTIN_CATEGORIES.values() for ext in exts]
    chunk = b"\x00" * (1024 * 100)  # 100KB per write
    for i in range(count):
        ext = random.choice(all_exts)
        cat = next(c for c, e in BUILTIN_CATEGORIES.items() if ext in e)
        subdir = target / cat
        subdir.mkdir(parents=True, exist_ok=True)
        f = subdir / f"large_{i:04d}{ext}"
        with f.open("wb") as fp:
            for _ in range(10):  # 10 * 100KB = 1MB
                fp.write(chunk)


def inject_duplicates(target: Path, ratio: float = 0.05) -> int:
    """注入重复（默认 5%）.

    复制现有文件到 _duplicates/ 子目录，文件名带 _dup 后缀。
    Returns:
        注入的重复文件数
    """
    all_files = list(target.rglob("*"))
    all_files = [f for f in all_files if f.is_file() and "_duplicates" not in f.parts]
    dup_count = int(len(all_files) * ratio)
    random.seed(45)
    samples = random.sample(all_files, min(dup_count, len(all_files)))

    dup_dir = target / "_duplicates"
    dup_dir.mkdir(exist_ok=True)
    for src in samples:
        dst = dup_dir / f"{src.stem}_dup{src.suffix}"
        shutil.copy2(src, dst)
    return len(samples)


def main() -> int:
    """主函数."""
    parser = argparse.ArgumentParser(description="FileMaster 1w 文件压测夹具生成器")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("tests/fixtures/10k_mixed"),
        help="输出目录",
    )
    parser.add_argument("--count", type=int, default=10_000, help="小文件数（默认 8000）")
    parser.add_argument("--no-large", action="store_true", help="不生成大文件（节省空间）")
    parser.add_argument("--duplicates", type=float, default=0.05, help="重复文件比例（默认 5%%）")
    args = parser.parse_args()

    output = args.output.resolve()
    if output.exists():
        print(f"[!] 清理旧目录: {output}")
        shutil.rmtree(output, ignore_errors=True)
    output.mkdir(parents=True, exist_ok=True)

    print(f"[*] 生成 1w 文件夹具 → {output}")
    print(f"    小文件: {args.count}")
    print(f"    大文件: {'否' if args.no_large else '1000 (1GB 总)'}")
    print(f"    重复比例: {args.duplicates * 100:.1f}%")
    print()

    print("[1/4] 小文件...")
    gen_small_files(output, args.count)
    print(f"      OK ({args.count} 个)")

    if not args.no_large:
        print("[2/4] 中等文件...")
        gen_medium_files(output, 1000)
        print("      OK (1000 个, 100KB-1MB)")

        print("[3/4] 大文件...")
        gen_large_files(output, 1000)
        print("      OK (1000 个, 1MB 每个)")
    else:
        print("[2/4] 中等文件: 跳过")
        print("[3/4] 大文件: 跳过")

    print(f"[4/4] 注入重复文件 ({args.duplicates * 100:.1f}%)...")
    n_dup = inject_duplicates(output, args.duplicates)
    print(f"      OK ({n_dup} 个)")

    total = sum(1 for _ in output.rglob("*") if _.is_file())
    total_size = sum(f.stat().st_size for f in output.rglob("*") if f.is_file())
    print()
    print(f"[完成] 总文件数: {total}  总大小: {total_size / 1024 / 1024:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
