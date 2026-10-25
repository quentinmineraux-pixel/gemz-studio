# PyInstaller : python -m PyInstaller gemz_studio.spec
a = Analysis(
    ["run.py"],
    datas=[("assets/logo.png", "assets"), ("assets/icon.png", "assets")],
    excludes=["tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas,
    name="GemZ Studio",
    icon="assets/icon.ico",
    console=False,
    upx=False,
)
