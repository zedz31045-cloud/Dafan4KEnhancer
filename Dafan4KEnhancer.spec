# PyInstaller spec - build with:  pyinstaller --noconfirm Dafan4KEnhancer.spec
# One-folder build (faster start-up, easier to ship tools/ next to the EXE).
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = collect_data_files("customtkinter")          # themes, fonts, images used by CustomTkinter
datas += collect_data_files("tkinterdnd2")           # native tkdnd libraries for drag & drop
datas += [("assets/icon.ico", "assets"), ("assets/icon.png", "assets"), ("tools_manifest.json", ".")]
hiddenimports = collect_submodules("dafan") + ["tkinterdnd2", "cv2", "PIL._tkinter_finder"]

a = Analysis(
    ["app.py"],
    pathex=["src"],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["matplotlib", "scipy", "pandas"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Dafan4KEnhancer",
    console=False,                 # windowed app
    icon="assets/icon.ico",
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Dafan4KEnhancer", upx=False)
