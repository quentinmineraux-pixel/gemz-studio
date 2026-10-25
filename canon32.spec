# PyInstaller, avec un Python 32 bits : python -m PyInstaller canon32.spec
a = Analysis(
    ["canon32.py"],
    excludes=["tkinter", "PySide6", "PIL", "imageio_ffmpeg", "watchdog"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas,
    name="gemz-canon32",
    console=True,
    upx=False,
)
