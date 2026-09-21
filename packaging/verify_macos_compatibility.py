#!/usr/bin/env python3
"""Read-only macOS 14 arm64 deployment-target gate for a bundled app."""

from __future__ import annotations

import argparse
import re
import stat
import subprocess
from pathlib import Path


MINOS_PATTERN = re.compile(r"\bminos\s+(\d+)(?:\.(\d+))?")


def is_macho_description(description: str) -> bool:
    return "Mach-O" in description


def arm64_minos(output: str) -> tuple[int, int] | None:
    minos = MINOS_PATTERN.search(output)
    if minos is None or "platform MACOS" not in output:
        return None
    return int(minos.group(1)), int(minos.group(2) or "0")


def is_supported_minos(version: tuple[int, int]) -> bool:
    return version <= (14, 0)


def regular_bundle_files(app: Path) -> list[Path] | None:
    try:
        if not app.is_absolute() or app.is_symlink() or not app.is_dir() or app.suffix != ".app":
            return None
        files: list[Path] = []
        for candidate in app.rglob("*"):
            metadata = candidate.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                continue
            if stat.S_ISREG(metadata.st_mode):
                files.append(candidate)
        return files
    except OSError:
        return None


def verify(app: Path) -> tuple[bool, str]:
    files = regular_bundle_files(app)
    if files is None:
        return False, "MACOS_COMPATIBILITY_BUNDLE_INVALID"
    for candidate in files:
        file_result = subprocess.run(["file", "-b", str(candidate)], capture_output=True, text=True, check=False)
        if file_result.returncode != 0:
            return False, "MACOS_COMPATIBILITY_INSPECTION_INVALID"
        if not is_macho_description(file_result.stdout):
            continue
        lipo = subprocess.run(["lipo", "-archs", str(candidate)], capture_output=True, text=True, check=False)
        if lipo.returncode != 0 or "arm64" not in lipo.stdout.split():
            return False, "MACOS_COMPATIBILITY_ARCHITECTURE_INVALID"
        vtool = subprocess.run(["xcrun", "vtool", "-show-build", "-arch", "arm64", str(candidate)], capture_output=True, text=True, check=False)
        version = arm64_minos(vtool.stdout)
        if vtool.returncode != 0 or version is None:
            return False, "MACOS_COMPATIBILITY_INSPECTION_INVALID"
        if not is_supported_minos(version):
            return False, "MACOS_COMPATIBILITY_MINOS_UNSUPPORTED"
    return True, "MACOS_COMPATIBILITY_VALID"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path)
    arguments = parser.parse_args()
    passed, code = verify(arguments.app)
    print(f"macos_compatibility:{'PASS' if passed else 'FAIL'}:{code}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
