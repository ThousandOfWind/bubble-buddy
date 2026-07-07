# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the macOS Bubble Buddy .app bundle.

Build with:
    CVS_INCLUDE_LOCAL=0 uv run pyinstaller packaging/copilot-voice-shell-macos.spec --noconfirm

Produces:
    dist/macos/Bubble Buddy.app
"""

from PyInstaller.utils.hooks import collect_all, collect_submodules
import os

datas = []
binaries = []
hiddenimports = []

INCLUDE_LOCAL = os.environ.get("CVS_INCLUDE_LOCAL", "") not in ("", "0", "false", "False")
VERSION = os.environ.get("CVS_VERSION", "0.1.0")

_collect_all_pkgs = [
    "sounddevice",
    "soundfile",
    "pynput",
    "pyperclip",
    "websockets",
    "msal_extensions",
]

_local_pkgs = [
    "mlx_whisper",
    "mlx",
    "faster_whisper",
    "ctranslate2",
    "av",
    "onnxruntime",
    "tokenizers",
    "huggingface_hub",
]
if INCLUDE_LOCAL:
    _collect_all_pkgs += _local_pkgs

for _pkg in _collect_all_pkgs:
    try:
        d, b, h = collect_all(_pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

for _pkg in ("azure.identity", "azure.core", "objc", "AppKit", "Foundation"):
    try:
        hiddenimports += collect_submodules(_pkg)
    except Exception:
        pass

datas += [
    ("../config.example.json", "."),
    ("../replacements.example.json", "."),
]

_bundled_config = os.environ.get("CVS_BUNDLED_CONFIG", "")
if _bundled_config and os.path.isfile(_bundled_config):
    datas += [(_bundled_config, ".")]

_excluded_local = [] if INCLUDE_LOCAL else [
    "mlx_whisper",
    "mlx",
    "faster_whisper",
    "ctranslate2",
    "av",
    "onnxruntime",
    "torch",
    "torchaudio",
]

a = Analysis(
    ["app_launcher.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "pytest", "uiautomation", "comtypes"] + _excluded_local,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Bubble Buddy",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
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
    name="Bubble Buddy",
)

app = BUNDLE(
    coll,
    name="Bubble Buddy.app",
    icon=os.path.join(SPECPATH, "bb.icns"),
    bundle_identifier="com.thousandsofwind.bubblebuddy",
    info_plist={
        "CFBundleName": "Bubble Buddy",
        "CFBundleDisplayName": "Bubble Buddy",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "NSHighResolutionCapable": True,
        "LSUIElement": True,
        "NSMicrophoneUsageDescription": "Bubble Buddy records microphone audio when you press the hotkey or Start Recording.",
        "NSAppleEventsUsageDescription": "Bubble Buddy sends paste/submit keystrokes to the active application when enabled.",
    },
)
