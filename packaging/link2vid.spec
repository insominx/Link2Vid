# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_all,
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)

block_cipher = None

ROOT = Path(SPECPATH).parent

datas = []
binaries = []
hiddenimports = []

# Selenium loads webdriver submodules lazily and launches Chrome through its
# bundled selenium-manager binary; static analysis finds neither. pywebview
# ships its WebView2 interop DLLs and injected JS as package data.
for pkg in ("yt_dlp", "webview", "selenium"):
    pkg_datas, pkg_binaries, pkg_hiddenimports = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hiddenimports

# The HTML/CSS/JS frontend is loaded from disk by the WebView at runtime.
datas += [
    (str(ROOT / "link2vid" / "ui" / "web"), "link2vid/ui/web"),
    (str(ROOT / "link2vid.ico"), "."),
]
datas += collect_data_files("faster_whisper")
binaries += collect_dynamic_libs("ctranslate2")
hiddenimports += collect_submodules("faster_whisper")

hiddenimports += [
    "yt_dlp_ejs",
    "clr",
    "clr_loader",
    "m3u8",
    "selenium",
    "requests",
    "ctranslate2",
    "ctranslate2._ext",
    "av",
    "onnxruntime",
    "tokenizers",
    "huggingface_hub",
    "huggingface_hub._snapshot_download",
]

a = Analysis(
    [str(ROOT / "video_downloader.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # CTranslate2's converter namespace probes optional ML frameworks that are
    # not used for Faster Whisper inference. Keep those stacks and model
    # weights out of the portable CPU-capable distribution.
    excludes=[
        "nvidia",
        "torch",
        "transformers",
        "tensorflow",
        "scipy",
        "sklearn",
        "cv2",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Link2Vid",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=str(ROOT / "link2vid.ico"),
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Link2Vid",
)
