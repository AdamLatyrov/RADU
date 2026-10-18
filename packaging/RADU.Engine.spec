# PyInstaller build for the background speech engine used by the WPF interface.
from pathlib import Path
from PyInstaller.utils.hooks import collect_dynamic_libs

root = Path(SPECPATH).resolve().parent

a = Analysis(
    [str(root / "assistant" / "main.py")],
    pathex=[str(root)],
    binaries=collect_dynamic_libs("vosk"),
    datas=[],
    hiddenimports=["_cffi_backend", "pkg_resources", "soundfile", "sounddevice", "webrtcvad", "vosk"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "pytest", "numba", "scipy", "matplotlib"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="RADU.Engine",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="RADU.Engine",
)
