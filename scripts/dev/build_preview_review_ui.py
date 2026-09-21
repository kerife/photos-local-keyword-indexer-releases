#!/usr/bin/env python3
"""Build and verify the fictitious native UI host without launching any UI.

Uses all current production Swift files except the application entry point.
Requires a locally cached Sparkle framework; never resolves or downloads it.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile


def main() -> None:
    project = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=project / "build/ui-review-20260912")
    args = parser.parse_args()
    output = args.output_root.resolve()
    output.mkdir(parents=True, exist_ok=True)
    app = output / "Vista previa ficticia.app"
    executable = app / "Contents/MacOS/FictitiousPhotosUIPreview"
    executable.parent.mkdir(parents=True, exist_ok=True)
    source = project / "app/PhotosLocalKeywordIndexer"
    files = [p for p in sorted(source.rglob("*.swift")) if p.name != "PhotosLocalKeywordIndexerApp.swift"]
    entrypoints = [p for p in files if "@main" in p.read_text()]
    if entrypoints:
        raise SystemExit(f"Unexpected production entry point: {entrypoints}")
    files.append(Path(__file__).with_name("preview_review_ui.swift"))
    framework = project / "app/.build/artifacts/sparkle/Sparkle/Sparkle.xcframework/macos-arm64_x86_64/Sparkle.framework"
    if not framework.is_dir():
        raise SystemExit("No cached Sparkle framework. Build existing dependencies separately; this host does not download them.")
    embedded = app / "Contents/Frameworks/Sparkle.framework"
    if embedded.exists():
        shutil.rmtree(embedded)
    shutil.copytree(framework, embedded, symlinks=True)
    env = os.environ.copy()
    env.setdefault("DEVELOPER_DIR", "/Applications/Xcode.app/Contents/Developer")
    with tempfile.TemporaryDirectory(prefix="fictitious-ui-compile-") as cache:
        env["CLANG_MODULE_CACHE_PATH"] = str(Path(cache) / "clang")
        env["SWIFTPM_MODULECACHE_OVERRIDE"] = str(Path(cache) / "swiftpm")
        command = ["xcrun", "swiftc", "-parse-as-library", "-swift-version", "5",
                   "-target", "arm64-apple-macosx14.0",
                   "-module-name", "FictitiousPhotosUIPreview", "-module-cache-path", env["CLANG_MODULE_CACHE_PATH"],
                   "-F", str(embedded.parent), "-framework", "Sparkle",
                   "-Xlinker", "-rpath", "-Xlinker", "@executable_path/../Frameworks",
                   "-o", str(executable), *map(str, files)]
        subprocess.run(command, env=env, check=True)
    info = {
        "CFBundleIdentifier": "invalid.example.fictitious-photos-ui-review",
        "CFBundleName": "Vista previa ficticia",
        "CFBundleDisplayName": "Vista previa ficticia",
        "CFBundleExecutable": executable.name,
        "CFBundlePackageType": "APPL", "CFBundleVersion": "1",
        "CFBundleShortVersionString": "1.0", "LSMinimumSystemVersion": "14.0",
        "NSHighResolutionCapable": True, "NSPrincipalClass": "NSApplication",
        "PhotosLocalKeywordIndexerBuildChannel": "development",
    }
    with (app / "Contents/Info.plist").open("wb") as handle:
        plistlib.dump(info, handle)
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(app)], check=True)
    verification = subprocess.run([str(executable), "--verify-fixtures"], text=True, capture_output=True)
    (output / "fixture-verification.txt").write_text(verification.stdout + verification.stderr)
    print(verification.stdout, end="")
    if verification.returncode:
        raise SystemExit(verification.stderr or "Fictitious fixture verification failed")
    print(f"Built and verified; UI not launched: {app}")
    print("Launch via Codex computer use, or explicitly: open -n <app> --args --size 760x560 --scenario compact")
    print("Options: --scenario compact|populated|long-content|empty|checking|blocked; --size WIDTHxHEIGHT;")
    print("--appearance light|dark; --sidebar auto|open|closed; --reduce-motion; --route <Spanish route title>.")


if __name__ == "__main__":
    main()
