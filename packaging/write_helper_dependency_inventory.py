#!/usr/bin/env python3
"""Write a deterministic, artifact-derived dependency inventory for the frozen helper."""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata as metadata
import json
import re
import shutil
import sys
import sysconfig
from pathlib import Path


MODULE_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
LEGAL_NOTICE_PATTERN = re.compile(
    r"^(?:LICENSE|LICENCE|COPYING|NOTICE)(?:\.(?:txt|md|rst|license|licence|apache|bsd|mit|mpl|gpl|lgpl))?$",
    re.IGNORECASE,
)


def _strings(value: object) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, (tuple, list, set)):
        return set().union(*(_strings(item) for item in value))
    return set()


def analysis_modules(path: Path) -> set[str]:
    try:
        value = ast.literal_eval(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return set()
    return {item for item in _strings(value) if MODULE_PATTERN.fullmatch(item)}


def build_inventory(
    bundled_modules: set[str],
    distributions: dict[str, dict[str, object]],
    module_to_distribution: dict[str, str],
) -> dict[str, object]:
    bundled_roots = {module.split(".", 1)[0] for module in bundled_modules}
    included = {
        distribution
        for module, distribution in module_to_distribution.items()
        if module in bundled_roots and distribution in distributions and distribution != "photos-local-keyword-indexer"
    }
    dependencies = []
    for name in sorted(included):
        item = distributions[name]
        dependencies.append(
            {
                "name": name,
                "version": item["version"],
                "license": item["license"],
                "notice_files": item["notice_files"],
            }
        )
    return {
        "schema_version": 1,
        "membership_source": "pyinstaller-analysis-toc-v1",
        "dependencies": dependencies,
    }


def installed_distribution_data() -> tuple[dict[str, dict[str, object]], dict[str, str]]:
    distributions: dict[str, dict[str, object]] = {}
    module_to_distribution: dict[str, str] = {}
    for distribution in metadata.distributions():
        name = distribution.metadata.get("Name")
        version = distribution.version
        if not isinstance(name, str) or not name or not isinstance(version, str) or not version:
            continue
        normalized = name.lower().replace("_", "-")
        license_expression = distribution.metadata.get("License-Expression")
        license_name = distribution.metadata.get("License")
        notice_files = sorted(
            file.name
            for file in (distribution.files or [])
            if file.name.upper().startswith(("LICENSE", "COPYING", "NOTICE"))
        )
        distributions[normalized] = {
            "version": version,
            "license": (
                license_expression.strip()
                if isinstance(license_expression, str) and license_expression.strip()
                else license_name.strip()
                if isinstance(license_name, str) and license_name.strip()
                else "UNKNOWN"
            ),
            "notice_files": notice_files,
        }
        top_level = distribution.read_text("top_level.txt") or ""
        for module in top_level.splitlines():
            if MODULE_PATTERN.fullmatch(module):
                module_to_distribution.setdefault(module, normalized)
    for module, names in metadata.packages_distributions().items():
        if not MODULE_PATTERN.fullmatch(module) or not isinstance(names, list):
            continue
        for name in names:
            normalized = name.lower().replace("_", "-")
            if normalized in distributions:
                module_to_distribution.setdefault(module, normalized)
                break
    return distributions, module_to_distribution


def write_inventory(output: Path, inventory: dict[str, object]) -> None:
    output.write_text(json.dumps(inventory, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


def copy_notice_texts(notices: dict[str, list[Path]], destination: Path) -> dict[str, list[str]]:
    copied: dict[str, list[str]] = {}
    for package, paths in sorted(notices.items()):
        package_destination = destination / package
        written: list[str] = []
        for index, source in enumerate(sorted(paths, key=lambda path: path.name.lower())):
            safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", source.name)
            target_name = safe_name if index == 0 else f"{index}-{safe_name}"
            target = package_destination / target_name
            package_destination.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            written.append(f"{package}/{target_name}")
        copied[package] = written
    return copied


def installed_notice_paths(package: str) -> list[Path]:
    try:
        distribution = metadata.distribution(package)
    except metadata.PackageNotFoundError:
        return []
    paths: list[Path] = []
    for file in distribution.files or []:
        if LEGAL_NOTICE_PATTERN.fullmatch(file.name):
            candidate = Path(distribution.locate_file(file))
            if candidate.is_file():
                paths.append(candidate)
    return paths


def python_license_paths() -> list[Path]:
    stdlib = Path(sysconfig.get_path("stdlib"))
    for parent in (stdlib, *stdlib.parents):
        candidate = parent / "LICENSE.txt"
        if candidate.is_file():
            return [candidate]
    return []


def require_package_scoped_notices(
    dependency_rows: list[dict[str, object]], source_notices: dict[str, list[Path]]
) -> dict[str, list[Path]]:
    if any(not source_notices.get(row["name"]) for row in dependency_rows):
        raise ValueError("missing package-scoped legal notice")
    return source_notices


def add_bootloader_dependency(rows: list[dict[str, object]], distributions: dict[str, dict[str, object]]) -> None:
    # The executable includes PyInstaller's bootloader even when Analysis has
    # no importable PyInstaller package. Preserve its distribution exception.
    if not any(row["name"] == "pyinstaller" for row in rows):
        rows.append({"name": "pyinstaller", **distributions["pyinstaller"]})


def pyobjc_project_notice(version: str) -> list[Path]:
    """Use the audited License.txt shared by exact PyObjC 12.2.2 sdists."""
    if version != "12.2.2":
        return []
    notice = Path(__file__).with_name("licenses") / "pyobjc-12.2.2.txt"
    try:
        digest = hashlib.sha256(notice.read_bytes()).hexdigest()
    except OSError:
        return []
    return [notice] if digest == "0ca04b07928d4872b9d9bb22187ca0426dd8bfab08f26eada0999a71dc81aaff" else []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("analysis_toc", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--notices-dir", type=Path, required=True)
    arguments = parser.parse_args()
    modules = analysis_modules(arguments.analysis_toc)
    if not modules:
        return 1
    distributions, module_mapping = installed_distribution_data()
    inventory = build_inventory(modules, distributions, module_mapping)
    if not inventory["dependencies"]:
        return 1
    dependency_rows = inventory["dependencies"]
    assert isinstance(dependency_rows, list)
    add_bootloader_dependency(dependency_rows, distributions)
    dependency_rows.append({
        "name": "python-runtime",
        "version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "license": "PSF-2.0",
        "notice_files": [],
    })
    source_notices = {row["name"]: installed_notice_paths(row["name"]) for row in dependency_rows if row["name"] != "python-runtime"}
    source_notices["python-runtime"] = python_license_paths()
    # Resolve native runtime components from the exact source artifact before
    # PyInstaller's copied binaries are relocated and signed.
    from native_dependency_inventory import (
        NativeInventoryError,
        resolve_native_dependencies,
        resolve_pbs_static_dependencies,
    )

    runtime_library = Path(sys.base_prefix) / "lib/libpython3.12.dylib"
    try:
        if runtime_library.is_file():
            notice_root = Path(__file__).with_name("licenses") / "pbs-20260901"
            native = resolve_pbs_static_dependencies(
                notice_root / "PYTHON.static-dependencies.json", notice_root, runtime_library
            )
        else:
            native = resolve_native_dependencies(arguments.analysis_toc)
    except NativeInventoryError:
        return 1
    for row in native.rows:
        if row["name"] == "cpython":
            python_row = next(item for item in dependency_rows if item["name"] == "python-runtime")
            python_row["license"] = row["license"]
            source_notices["python-runtime"] = native.source_notices["cpython"]
        else:
            dependency_rows.append(row)
            source_notices[row["name"]] = native.source_notices[row["name"]]
    for row in dependency_rows:
        if row["name"].startswith("pyobjc-") and not source_notices[row["name"]]:
            source_notices[row["name"]] = pyobjc_project_notice(row["version"])
    try:
        source_notices = require_package_scoped_notices(dependency_rows, source_notices)
    except ValueError:
        return 1
    notice_index = copy_notice_texts(source_notices, arguments.notices_dir)
    for row in dependency_rows:
        row["notice_files"] = notice_index[row["name"]]
    write_inventory(arguments.output, inventory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
