# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, copy_metadata


spec_dir = Path(SPEC).resolve().parent
root = spec_dir.parent.parent
source = root / "src"
runtime_icon = source / "canvas_ed_mcp" / "ui" / "assets" / "courseharbor.png"
windows_icon = root / "packaging" / "windows" / "courseharbor.ico"
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
bundle_data = metadata + [(str(runtime_icon), "canvas_ed_mcp/ui/assets")]

gui_analysis = Analysis(
    [str(spec_dir / "courseharbor_gui.py")],
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
    [str(spec_dir / "courseharbor_cli.py")],
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
MERGE(
    (gui_analysis, "courseharbor_gui", "CourseHarbor"),
    (cli_analysis, "courseharbor_cli", "courseharbor-cli"),
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
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(windows_icon),
)

cli_pyz = PYZ(cli_analysis.pure)
cli_exe = EXE(
    cli_pyz,
    cli_analysis.dependencies,
    cli_analysis.scripts,
    [],
    exclude_binaries=True,
    name="courseharbor-cli",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(windows_icon),
)

bundle = COLLECT(
    gui_exe,
    gui_analysis.binaries,
    gui_analysis.datas,
    cli_exe,
    cli_analysis.binaries,
    cli_analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="CourseHarbor",
)
