# -*- mode: python ; coding: utf-8 -*-
import sys
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = ["music_filter", "PIL", "customtkinter"]

# Mac bundles are named ffmpeg and deno. Windows bundles are ffmpeg.exe and deno.exe.
# PyInstaller cannot cross-compile, so build the .app on a Mac and the .exe on Windows.
if sys.platform == "win32":
    binaries += [("ffmpeg.exe", "."), ("deno.exe", ".")]
else:
    binaries += [("ffmpeg", "."), ("deno", ".")]

for package in ("yt_dlp", "customtkinter"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# Windows is one file, so opening the exe does not depend on a side folder.
# Mac stays a folder inside the .app, which is what the Mac bundle needs.
if sys.platform == "win32":
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="YTHistoryMusic",
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
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="YTHistoryMusic",
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
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name="YTHistoryMusic",
    )
    app = BUNDLE(
        coll,
        name="YTHistoryMusic.app",
        icon=None,
        bundle_identifier="com.local.ythistorymusic",
    )
