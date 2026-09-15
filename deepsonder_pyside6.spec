# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

project_root = Path(SPECPATH).resolve()
runtime_data = [
    (str(project_root / "assets" / "fonts"), "assets/fonts"),
    (str(project_root / "assets" / "app_icon.ico"), "assets"),
    (str(project_root / "assets" / "checkmark.svg"), "assets"),
    (str(project_root / "assets" / "chevron-down-dark.svg"), "assets"),
    (str(project_root / "assets" / "chevron-down-light.svg"), "assets"),
    (str(project_root / "assets" / "chevron-up-dark.svg"), "assets"),
    (str(project_root / "assets" / "chevron-up-light.svg"), "assets"),
    (str(project_root / "VERSION"), "."),
    (str(project_root / "licenses"), "licenses"),
    (str(project_root / "LICENSE"), "."),
    (str(project_root / "PRIVACY.md"), "."),
    (str(project_root / "README.md"), "."),
    (str(project_root / "THIRD_PARTY_NOTICES.md"), "."),
    (str(project_root / "config.example.json"), "."),
]

a = Analysis(
    [str(project_root / "main.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=runtime_data,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DeepSonder-PySide6",
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
    icon=str(project_root / "assets" / "app_icon.ico"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="DeepSonder-PySide6",
)
