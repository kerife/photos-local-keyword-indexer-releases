#!/usr/bin/env python3
"""Print a deterministic fingerprint for the frozen helper's source inputs."""

from __future__ import annotations

import hashlib
from pathlib import Path
import stat
import sys


def source_inputs(root: Path) -> list[Path]:
    package = root / "src" / "photos_indexer"
    fixed = [
        root / "packaging" / "worker_entry.py",
        root / "packaging" / "PhotosIndexerWorker.spec",
        root / "pyproject.toml",
    ]
    python_sources = sorted(
        package.rglob("*.py"),
        key=lambda path: path.relative_to(root).as_posix(),
    )
    if not python_sources:
        raise ValueError
    return [*python_sources, *fixed]


def fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(source_inputs(root), key=lambda item: item.relative_to(root).as_posix()):
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ValueError
        relative = path.relative_to(root).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    root = Path(sys.argv[1])
    try:
        if not root.is_absolute() or root.is_symlink() or not root.is_dir():
            return 2
        print(fingerprint(root))
    except (OSError, RuntimeError, ValueError):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
