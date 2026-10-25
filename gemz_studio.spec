# PyInstaller : python -m PyInstaller gemz_studio.spec
import os

datas = [("assets/logo.png", "assets"), ("assets/icon.png", "assets")]
# Module 32 bits pour les kits Canon 32 bits (fabriqué avant par canon32.spec)
if os.path.exists("dist/gemz-canon32.exe"):
    datas.append(("dist/gemz-canon32.exe", "canon32"))

a = Analysis(
    ["run.py"],
    datas=datas,
    hiddenimports=["anthropic", "requests", "certifi"],
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
