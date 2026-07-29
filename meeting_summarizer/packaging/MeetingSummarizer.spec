# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the macOS build.

Deliberately onedir, not onefile: the torch payload is well over a gigabyte and
onefile would re-extract all of it into a temp directory on every launch.
"""
import os

from PyInstaller.utils.hooks import (
    collect_all,
    collect_dynamic_libs,
    collect_submodules,
    copy_metadata,
)

SPEC_DIR = os.path.dirname(os.path.abspath(SPEC))
SRC_DIR = os.path.abspath(os.path.join(SPEC_DIR, os.pardir))
VERSION = os.environ.get("APP_VERSION", "1.0.0")

datas = [(os.path.join(SRC_DIR, "templates"), "templates")]
binaries = []
hiddenimports = [
    "app_paths",
    "settings",
    "web",
    "transcriber",
    "summarizer",
]

# Packages whose contents are resolved dynamically at runtime and so are invisible
# to PyInstaller's import graph.
for pkg in (
    "transformers",
    "tokenizers",
    "safetensors",
    "huggingface_hub",
    "soundfile",
    "soxr",
):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

# torch is handled by the bundled pyinstaller-hooks-contrib hook; collect_all on
# it roughly triples build time for no gain. Its dylibs are re-collected here
# only as a safety net.
binaries += collect_dynamic_libs("torch")

# uvicorn[standard] selects its event loop and protocol implementations by name.
hiddenimports += collect_submodules("uvicorn")
hiddenimports += [
    "uvloop",
    "httptools",
    "websockets",
    "websockets.legacy",
    "websockets.legacy.server",
]

# transformers and huggingface_hub gate features on installed package versions via
# importlib.metadata, which needs the dist-info directories present.
for dist in (
    "torch",
    "transformers",
    "tokenizers",
    "huggingface_hub",
    "safetensors",
    "numpy",
    "regex",
    "requests",
    "tqdm",
    "filelock",
    "packaging",
    "pyyaml",
    "fsspec",
    "openai",
):
    try:
        datas += copy_metadata(dist)
    except Exception:
        pass

# Optional heavyweights that transformers probes for but this app never uses.
excludes = [
    "tensorflow",
    "flax",
    "jax",
    "torchvision",
    "torchaudio",
    "matplotlib",
    "scipy",
    "numba",
    "llvmlite",
    "librosa",
    "sounddevice",
    "_sounddevice_data",
    "tkinter",
    "PIL",
    "IPython",
    "pytest",
    "notebook",
]

a = Analysis(
    [os.path.join(SRC_DIR, "launcher.py")],
    pathex=[SRC_DIR],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MeetingSummarizer",
    debug=False,
    bootloader_ignore_signals=False,
    # UPX corrupts torch's dylibs and breaks code signing.
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
    name="MeetingSummarizer",
)

app = BUNDLE(
    coll,
    name="MeetingSummarizer.app",
    icon=None,
    bundle_identifier="com.tnt.meetingsummarizer",
    version=VERSION,
    info_plist={
        "CFBundleName": "Meeting Summarizer",
        "CFBundleDisplayName": "Meeting Summarizer",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "LSMinimumSystemVersion": "11.0",
        "NSHighResolutionCapable": True,
        "LSApplicationCategoryType": "public.app-category.productivity",
        # The app itself does not open the mic (the browser does), but declaring
        # it keeps the bundle honest if a native capture path is ever added.
        "NSMicrophoneUsageDescription":
            "Meeting Summarizer ghi âm cuộc họp để tạo transcript và biên bản.",
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
    },
)
