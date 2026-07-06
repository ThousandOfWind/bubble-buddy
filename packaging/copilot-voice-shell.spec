# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for copilot-voice-shell (Windows one-folder build).

Build with:
    uv run pyinstaller packaging/copilot-voice-shell.spec --noconfirm

Produces dist/copilot-voice-shell/copilot-voice-shell.exe (double-click to run
the desktop overlay).
"""

from PyInstaller.utils.hooks import collect_all, collect_submodules
import os

datas = []
binaries = []
hiddenimports = []

# Local Whisper (offline transcription) pulls ~185 MB of native libs
# (ctranslate2 + av/ffmpeg + onnxruntime). The shipped config uses the Azure
# backend, so we EXCLUDE that stack by default for a lean installer. Set the
# env var CVS_INCLUDE_LOCAL=1 before building to bundle offline transcription.
INCLUDE_LOCAL = os.environ.get("CVS_INCLUDE_LOCAL", "") not in ("", "0", "false", "False")

# Heavy / plugin-based packages that PyInstaller can't fully trace statically.
# collect_all grabs their python modules, data files and bundled native libs.
#
# NOTE: PySide6/shiboken6 are intentionally NOT collect_all'd. The built-in
# PyInstaller hook already bundles exactly the Qt modules the app imports
# (QtCore/QtGui/QtWidgets). collect_all("PySide6") would instead drag in the
# ENTIRE Qt stack (QtWebEngine, Qt3D, QtQuick, QtCharts, ...), which alone
# accounts for several hundred MB of dead weight.
_collect_all_pkgs = [
    "qtawesome",
    "sounddevice",
    "soundfile",
    "pynput",
    "uvicorn",
    "fastapi",
    "websockets",
    # azure-identity persistent token cache backend (MSAL extensions).
    "msal_extensions",
]
_local_pkgs = ["faster_whisper", "ctranslate2", "av", "onnxruntime", "tokenizers", "huggingface_hub"]
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

# azure-identity pulls a lot of lazily-imported submodules.
for _pkg in ("azure.identity", "azure.core"):
    try:
        hiddenimports += collect_submodules(_pkg)
    except Exception:
        pass

# Windows-only UI automation stack used by focus_context.
for _pkg in ("uiautomation", "comtypes"):
    try:
        d, b, h = collect_all(_pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

# Ship example config next to the exe so first run has a template.
datas += [
    ("../config.example.json", "."),
    ("../replacements.example.json", "."),
]

# Large Qt modules the overlay never uses. Excluding them keeps the frozen app
# lean even if a transitive hook tries to pull them in.
_excluded_qt = [
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel",
    "PySide6.QtWebSockets",
    "PySide6.QtWebView",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickWidgets",
    "PySide6.QtQuickControls2",
    "PySide6.QtQml",
    "PySide6.QtQmlModels",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DExtras",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtGraphs",
    "PySide6.QtGraphsWidgets",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtHttpServer",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtSpatialAudio",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtPositioning",
    "PySide6.QtLocation",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtSerialBus",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtNetworkAuth",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtSql",
    "PySide6.QtStateMachine",
    "PySide6.QtTest",
    "PySide6.QtUiTools",
    "PySide6.QtTextToSpeech",
    "PySide6.QtDBus",
]

block_cipher = None

# When building the lean (Azure-only) bundle, exclude the offline-transcription
# modules entirely so their native libs never get pulled in transitively.
_excluded_local = [] if INCLUDE_LOCAL else [
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
    excludes=["tkinter", "matplotlib", "pytest"] + _excluded_qt + _excluded_local,
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
    name="copilot-voice-shell",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # GUI app: no console window on double-click
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
    name="copilot-voice-shell",
)
