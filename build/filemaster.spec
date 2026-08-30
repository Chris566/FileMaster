# -*- mode: python ; coding: utf-8 -*-
"""FileMaster PyInstaller 打包配置.

W14 详细调优（图标 / 资源 / 体积优化）。
当前是 W1 占位。
"""

import os
from pathlib import Path

block_cipher = None

# 项目根
PROJECT_ROOT = Path(os.path.abspath(SPECPATH)).parent

a = Analysis(
    [str(PROJECT_ROOT / "src" / "filemaster" / "__main__.py")],
    pathex=[str(PROJECT_ROOT / "src")],
    binaries=[],
    datas=[
        # QSS 主题
        (str(PROJECT_ROOT / "src" / "filemaster" / "ui" / "styles"), "filemaster/ui/styles"),
        # 图标 + 字体（如有）
        # (str(PROJECT_ROOT / "src/filemaster/ui/resources"), "filemaster/ui/resources"),
    ],
    hiddenimports=[
        "filemaster.core",
        "filemaster.core.renamer",
        "filemaster.core.template",
        "filemaster.core.classifier",
        "filemaster.core.preview",
        "filemaster.core.undo",
        "filemaster.core.metadata",
        "filemaster.core.dedup",
        "filemaster.core.archiver",
        "filemaster.workers",
        "filemaster.workers.batch",
        "filemaster.workers.signals",
        "filemaster.ui",
        "filemaster.ui.main_window",
        "filemaster.io",
        "filemaster.io.config",
        "filemaster.io.excel",
        "filemaster.platform",
        "filemaster.platform.paths",
        "filemaster.utils",
        "filemaster.utils.hash",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 减小体积
        "tkinter",
        "matplotlib",
        "numpy.tests",
        "scipy",
        "pandas.tests",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="FileMaster",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,  # UPX 压缩
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # GUI 应用，无控制台
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # 图标
    icon=str(PROJECT_ROOT / "build" / "icon.ico") if (PROJECT_ROOT / "build" / "icon.ico").exists() else None,
)
