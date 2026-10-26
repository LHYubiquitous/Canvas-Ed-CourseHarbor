# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, copy_metadata


spec_dir = Path(SPEC).resolve().parent
root = spec_dir.parent.parent
source = root / "src"
runtime_icon = source / "canvas_ed_mcp" / "ui" / "assets" / "courseharbor.png"
macos_icon = Path(os.environ["COURSEHARBOR_MACOS_ICON"]).resolve(strict=True)
short_version = os.environ["COURSEHARBOR_SHORT_VERSION"]
bundle_version = os.environ["COURSEHARBOR_BUNDLE_VERSION"]
hidden_imports = sorted(
    set(
        collect_submodules("keyring.backends")
        + [
            "mcp.server.fastmcp",
            "mcp.server.fastmcp.exceptions",
        ]
    )
)
metadata = copy_metadata("keyring")
bundle_data = metadata + [
    (str(runtime_icon), "canvas_ed_mcp/ui/assets"),
    (str(root / "LICENSE"), "legal"),
    (str(root / "THIRD_PARTY_NOTICES.md"), "legal"),
]

gui_analysis = Analysis(
    [str(root / "packaging" / "windows" / "courseharbor_gui.py")],
    pathex=[str(source)],
    binaries=[],
    datas=bundle_data,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
cli_analysis = Analysis(
    [str(root / "packaging" / "windows" / "courseharbor_cli.py")],
    pathex=[str(source)],
    binaries=[],
    datas=bundle_data,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
gui_pyz = PYZ(gui_analysis.pure)
gui_exe = EXE(
    gui_pyz,
    gui_analysis.dependencies,
    gui_analysis.scripts,
    [],
    exclude_binaries=True,
    name="CourseHarbor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(macos_icon),
)

cli_pyz = PYZ(cli_analysis.pure)
cli_exe = EXE(
    cli_pyz,
    cli_analysis.scripts,
    cli_analysis.binaries,
    cli_analysis.datas,
    [],
    exclude_binaries=False,
    name="courseharbor-cli",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(macos_icon),
)

collection = COLLECT(
    gui_exe,
    gui_analysis.binaries,
    gui_analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="CourseHarbor",
)

application = BUNDLE(
    collection,
    name="CourseHarbor.app",
    icon=str(macos_icon),
    bundle_identifier="io.github.lhyubiquitous.courseharbor",
    info_plist={
        "CFBundleDisplayName": "CourseHarbor",
        "CFBundleShortVersionString": short_version,
        "CFBundleVersion": bundle_version,
        "LSMinimumSystemVersion": "14.0",
        "NSHighResolutionCapable": True,
    },
)
