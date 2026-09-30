# PyInstaller spec — build with:  python -m PyInstaller --noconfirm --clean SOL_Memecoin_Hunter.spec
# Produces a single-file windowed exe: dist/SOL_Memecoin_Hunter.exe
import os

block_cipher = None
SRC = os.path.join(os.path.abspath(SPECPATH), "src")

a = Analysis(
    [os.path.join(SRC, "main.py")],
    pathex=[SRC],
    binaries=[],
    datas=[(os.path.join(SRC, "i18n", "vi.json"), "i18n"), (os.path.join(SRC, "i18n", "en.json"), "i18n")],
    hiddenimports=["ui.app", "api.server", "pyqtgraph", "websockets", "websockets.asyncio.client"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "pandas", "scipy", "IPython", "pytest",
              "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
              "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtMultimedia", "PySide6.QtPdf"],
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name="SOL_Memecoin_Hunter",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    runtime_tmpdir=None,
)
