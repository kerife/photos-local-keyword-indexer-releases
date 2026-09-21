#!/usr/bin/env python3
"""Derive a path-free inventory for native dylibs frozen by PyInstaller.

The build integration owns copying the returned local notice paths into the
helper. This module never serializes Homebrew paths: its canonical output has
the same dependency-row shape as the Python runtime inventory.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
from collections.abc import Callable, Iterable
from pathlib import Path


NATIVE_DYLIB_FORMULAS = {
    "libmpdec.4.dylib": "mpdecimal",
    "liblzma.5.dylib": "xz",
    "libcrypto.3.dylib": "openssl@3",
    "libssl.3.dylib": "openssl@3",
}
NOTICE_NAME = re.compile(r"^(?:LICENSE|LICENCE|COPYING|NOTICE)(?:[._-].*)?$", re.IGNORECASE)
SIMPLE_LICENSE = re.compile(r'^\s*license\s+"([^"]+)"\s*$', re.MULTILINE)
COMPOUND_LICENSE = re.compile(r"^\s*license\s+(all_of|any_of):\s*\[(.*?)\]", re.MULTILINE | re.DOTALL)
QUOTED_LICENSE = re.compile(r'"([A-Za-z0-9.+()\-]+)"')
MINIMUM_MACOS = re.compile(r"\bminos\s+([0-9]+(?:\.[0-9]+){0,2})\b")


class NativeInventoryError(ValueError):
    """The frozen native payload cannot be attributed safely."""


class NativeDependencyResolution:
    def __init__(self, rows: list[dict[str, object]], source_notices: dict[str, list[Path]]) -> None:
        self.rows = rows
        self.source_notices = source_notices


PBS_STATIC_LINK_NAMES = {
    "bz2": "bzip2",
    "crypto": "openssl",
    "expat": "expat",
    "ffi": "libffi",
    "lzma": "liblzma",
    "mpdec": "mpdecimal",
    "sqlite3": "sqlite",
    "ssl": "openssl",
    "tclstub": "tcl-tk",
    "tkstub": "tcl-tk",
    "uuid": "libuuid",
}


def _pbs_notice_paths(notice_root: Path, relative_paths: object) -> list[Path]:
    if not isinstance(relative_paths, list) or not all(isinstance(path, str) for path in relative_paths):
        raise NativeInventoryError("PBS dependency license paths are invalid")
    paths: list[Path] = []
    for relative_path in relative_paths:
        source = notice_root / Path(relative_path).name
        if not source.is_file():
            raise NativeInventoryError("PBS dependency notice is unavailable")
        paths.append(source)
    return paths


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise NativeInventoryError("PBS runtime library is unavailable") from error
    return digest.hexdigest()


def resolve_pbs_static_dependencies(
    metadata_path: Path,
    notice_root: Path,
    runtime_libpython: Path,
) -> NativeDependencyResolution:
    """Resolve static PBS components from its checked-in full-archive metadata.

    Static component versions are intentionally the exact enclosing PBS build
    identifier: PBS metadata records component licenses but not their individual
    source versions. Call this before the build rewrites the dylib install name
    and signs it: the supplied pristine runtime must match the recorded source
    artifact byte-for-byte.
    """
    try:
        metadata = json.loads(Path(metadata_path).read_text(encoding="utf-8"))
        source = json.loads((Path(notice_root) / "SOURCE.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise NativeInventoryError("PBS metadata is unavailable") from error
    expected_digest = source.get("libpython3_12_dylib_sha256")
    if not isinstance(expected_digest, str) or _sha256(Path(runtime_libpython)) != expected_digest:
        raise NativeInventoryError("PBS runtime library does not match the audited payload")
    version = metadata.get("python_version")
    build = source.get("build")
    if not isinstance(version, str) or not isinstance(build, str):
        raise NativeInventoryError("PBS version metadata is invalid")
    enclosing_version = f"bundled-with-cpython-{version}+{build}"
    extensions = metadata.get("build_info", {}).get("extensions", {})
    if not isinstance(extensions, dict):
        raise NativeInventoryError("PBS extension metadata is invalid")
    rows: list[dict[str, object]] = []
    notices: dict[str, list[Path]] = {}
    core_notices = _pbs_notice_paths(Path(notice_root), [metadata.get("license_path")])
    for provenance in (Path(notice_root) / "SOURCE.json", Path(metadata_path)):
        if not provenance.is_file():
            raise NativeInventoryError("PBS provenance metadata is unavailable")
        core_notices.append(provenance)
    core_licenses = metadata.get("licenses")
    if not isinstance(core_licenses, list) or not all(isinstance(value, str) for value in core_licenses):
        raise NativeInventoryError("PBS core license metadata is invalid")
    rows.append({"name": "cpython", "version": version, "license": "upstream-lists: " + ", ".join(core_licenses), "notice_files": []})
    notices["cpython"] = core_notices
    components: dict[str, tuple[set[str], set[Path]]] = {}
    for candidates in extensions.values():
        if not isinstance(candidates, list):
            raise NativeInventoryError("PBS static extension metadata is invalid")
        for component in candidates:
            if not isinstance(component, dict):
                raise NativeInventoryError("PBS static extension metadata is invalid")
            static_links = [link for link in component.get("links", []) if isinstance(link, dict) and "path_static" in link]
            if not static_links:
                continue
            licenses = component.get("licenses")
            if not isinstance(licenses, list) or not all(isinstance(value, str) for value in licenses):
                raise NativeInventoryError("PBS static license metadata is invalid")
            component_notices = set(_pbs_notice_paths(Path(notice_root), component.get("license_paths")))
            for link in static_links:
                link_name = link.get("name")
                name = PBS_STATIC_LINK_NAMES.get(link_name)
                if name is None:
                    raise NativeInventoryError("PBS static library has no audited mapping")
                known_licenses, known_notices = components.setdefault(name, (set(), set()))
                known_licenses.update(licenses)
                known_notices.update(component_notices)
    for name, (licenses, component_notices) in components.items():
        rows.append({"name": name, "version": enclosing_version, "license": "upstream-lists: " + ", ".join(sorted(licenses)), "notice_files": []})
        notices[name] = sorted(component_notices, key=lambda path: path.name)
    rows.sort(key=lambda row: str(row["name"]))
    return NativeDependencyResolution(rows, notices)


def _walk(value: object) -> Iterable[tuple[object, ...]]:
    if isinstance(value, tuple):
        yield value
        for item in value:
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def _analysis_native_sources(analysis_toc: Path) -> dict[str, Path]:
    try:
        value = ast.literal_eval(analysis_toc.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError) as error:
        raise NativeInventoryError("native analysis toc is invalid") from error
    sources: dict[str, Path] = {}
    for entry in _walk(value):
        if len(entry) < 3 or entry[2] != "BINARY" or not isinstance(entry[1], str):
            continue
        source = Path(entry[1])
        name = source.name
        if not name.endswith(".dylib"):
            continue
        if name not in NATIVE_DYLIB_FORMULAS:
            raise NativeInventoryError("bundled native dylib has no audited formula mapping")
        try:
            resolved = source.resolve(strict=True)
        except OSError as error:
            raise NativeInventoryError("native dylib source is unavailable") from error
        existing = sources.get(name)
        if existing is not None and existing != resolved:
            raise NativeInventoryError("native dylib source is ambiguous")
        sources[name] = resolved
    missing = set(NATIVE_DYLIB_FORMULAS) - set(sources)
    if missing:
        raise NativeInventoryError("expected native dylib is missing from analysis")
    return sources


def _cellar_prefix(source: Path, formula: str) -> tuple[Path, str]:
    parts = source.parts
    try:
        index = parts.index("Cellar")
        source_formula = parts[index + 1]
        version = parts[index + 2]
    except (ValueError, IndexError) as error:
        raise NativeInventoryError("native dylib is not a resolved Homebrew Cellar artifact") from error
    if source_formula != formula or not version or any(part in {".", ".."} for part in parts[index + 3 :]):
        raise NativeInventoryError("native dylib formula identity is invalid")
    prefix = Path(*parts[: index + 3])
    if source.parent != prefix / "lib":
        raise NativeInventoryError("native dylib is outside the formula library directory")
    return prefix, version


def _formula_license(prefix: Path, formula: str) -> str:
    formula_path = prefix / ".brew" / f"{formula}.rb"
    try:
        formula_text = formula_path.read_text(encoding="utf-8")
    except OSError as error:
        raise NativeInventoryError("native formula license metadata is unavailable") from error
    simple = SIMPLE_LICENSE.search(formula_text)
    if simple:
        return simple.group(1)
    compound = COMPOUND_LICENSE.search(formula_text)
    if compound:
        values = QUOTED_LICENSE.findall(compound.group(2))
        if values:
            operator = " AND " if compound.group(1) == "all_of" else " OR "
            return operator.join(values)
    raise NativeInventoryError("native formula license metadata is unsupported")


def _notice_paths(prefix: Path) -> list[Path]:
    notices = [
        path for path in prefix.rglob("*")
        if path.is_file() and not path.is_symlink() and NOTICE_NAME.fullmatch(path.name)
    ]
    return sorted(notices, key=lambda path: path.relative_to(prefix).as_posix())


def resolve_native_dependencies(analysis_toc: Path) -> NativeDependencyResolution:
    """Return schema-compatible rows plus private source notice paths.

    Rows have no Homebrew path. Callers must copy ``source_notices`` under a
    package-scoped bundle directory before placing their names in ``notice_files``.
    """
    sources = _analysis_native_sources(Path(analysis_toc))
    grouped: dict[str, tuple[Path, str]] = {}
    for name, source in sources.items():
        formula = NATIVE_DYLIB_FORMULAS[name]
        prefix, version = _cellar_prefix(source, formula)
        current = grouped.get(formula)
        if current is not None and current != (prefix, version):
            raise NativeInventoryError("native formula resolves to multiple versions")
        grouped[formula] = (prefix, version)
    rows: list[dict[str, object]] = []
    source_notices: dict[str, list[Path]] = {}
    for formula in sorted(grouped):
        prefix, version = grouped[formula]
        notices = _notice_paths(prefix)
        if not notices:
            raise NativeInventoryError("native formula has no package-scoped legal notice")
        rows.append({
            "name": formula,
            "version": version,
            "license": _formula_license(prefix, formula),
            "notice_files": [],
        })
        source_notices[formula] = notices
    return NativeDependencyResolution(rows, source_notices)


def canonical_inventory_json(rows: list[dict[str, object]]) -> str:
    """Serialize only shareable dependency rows; never emit source paths."""
    return json.dumps(rows, sort_keys=True, separators=(",", ":")) + "\n"


def minimum_macos_versions(
    libraries: Iterable[Path],
    *,
    run: Callable[[list[str]], str] | None = None,
) -> dict[str, str]:
    """Return dylib-name keyed minOS diagnostics without retaining paths."""
    def invoke(command: list[str]) -> str:
        if run is not None:
            return run(command)
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise NativeInventoryError("native minos inspection failed")
        return result.stdout

    values: dict[str, str] = {}
    for library in libraries:
        path = Path(library)
        if path.name not in NATIVE_DYLIB_FORMULAS:
            raise NativeInventoryError("native minos target is not audited")
        output = invoke(["xcrun", "vtool", "-show-build", str(path)])
        matches = MINIMUM_MACOS.findall(output)
        if len(matches) != 1:
            raise NativeInventoryError("native minos inspection is ambiguous")
        values[path.name] = matches[0]
    return dict(sorted(values.items()))


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("analysis_toc", type=Path)
    arguments = parser.parse_args()
    try:
        resolved = resolve_native_dependencies(arguments.analysis_toc)
    except NativeInventoryError:
        return 1
    print(canonical_inventory_json(resolved.rows), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
