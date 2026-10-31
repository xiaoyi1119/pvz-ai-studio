from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


root = Path(SPEC).resolve().parent
src = root / "src"
icon_file = root / "assets" / "pvz.ico"

a = Analysis(
    [str(root / "desktop.py")],
    pathex=[str(src)],
    binaries=[],
    datas=[
        (str(src / "pvz_agent_plugin" / "static"), "pvz_agent_plugin/static"),
        (str(root / ".env.example"), "."),
    ] + collect_data_files("webview"),
    hiddenimports=collect_submodules("webview"),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PVZ AI Studio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=str(icon_file) if icon_file.exists() else None,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name="PVZ AI Studio",
)

