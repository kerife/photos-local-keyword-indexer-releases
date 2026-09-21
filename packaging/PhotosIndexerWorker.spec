# -*- mode: python ; coding: utf-8 -*-
"""Build specification for the private Python helper bundled by the macOS app."""

import os
import tomllib
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


project_root = Path(SPECPATH).resolve().parent
source_root = project_root / "src"
entry_point = project_root / "packaging" / "worker_entry.py"
with (project_root / "pyproject.toml").open("rb") as stream:
    project_version = tomllib.load(stream)["project"]["version"]
helper_version = os.environ.get("APP_VERSION", project_version)
helper_build_number = os.environ.get("BUILD_NUMBER", "1")

# PhotoScript compiles its global AppleScript object at import time.  On
# headless build hosts that can abort the process while PyInstaller's
# ``collect_submodules`` probes the package.  Keep the package's audited
# modules explicit; PyInstaller still bundles their source without executing
# the bridge during module discovery.
hiddenimports = sorted(set(
    collect_submodules("photos_indexer")
    + [
        "photoscript",
        "photoscript._version",
        "photoscript.exceptions",
        "photoscript.script_loader",
        "photoscript.utils",
        "AppKit",
        "CoreLocation",
        "Foundation",
        "MapKit",
        "Photos",
        "Quartz",
        "objc",
    ]
))
datas = collect_data_files("photoscript")

analysis = Analysis(
    [str(entry_point)],
    pathex=[str(source_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="PhotosIndexerWorker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    target_arch="arm64",
)
onedir = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="PhotosIndexerWorker",
)
helper_app = BUNDLE(
    onedir,
    name="PhotosIndexerWorker.app",
    bundle_identifier="com.photoslocalkeywordindexer.worker",
    version=helper_version,
    info_plist={
        "CFBundleVersion": helper_build_number,
        "LSMinimumSystemVersion": "14.0",
        "LSBackgroundOnly": False,
        "LSUIElement": True,
        "NSPhotoLibraryUsageDescription": (
            "Analiza localmente las fotos que el usuario elige para un dry-run."
        ),
        "NSPhotoLibraryAddUsageDescription": (
            "Añade únicamente keywords y captions aprobados por el usuario."
        ),
        "NSAppleEventsUsageDescription": (
            "Usa PhotoScript para leer, exportar y actualizar únicamente los metadatos "
            "aprobados en Fotos."
        ),
    },
)
