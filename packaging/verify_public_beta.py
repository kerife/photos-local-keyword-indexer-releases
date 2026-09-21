#!/usr/bin/env python3
"""Validate a local unsigned public-beta candidate without publishing it."""

from __future__ import annotations

import argparse
import hashlib
import json
import plistlib
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DMG_PATTERN = re.compile(
    r"^PhotosLocalKeywordIndexer-(?P<version>[0-9]+\.[0-9]+\.[0-9]+)-(?P<build>[1-9][0-9]*)-dev-arm64\.dmg$"
)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
PUBLIC_DOC_MARKERS = ("unsigned", "manual", "sparkle")
ACCEPTANCE_FIELDS = frozenset(
    {
        "schema_version",
        "artifact_sha256",
        "clean_account_install",
        "first_launch",
        "photos_permission",
        "automation_permission",
        "dry_run",
        "apply_readback",
        "rollback_readback",
        "interruption_recovery",
        "reopen_no_duplicate",
        "accessibility_review",
        "temporary_cleanup",
    }
)


def is_safe_regular_file(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    return stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1


def sha256_file(path: Path) -> str | None:
    if not is_safe_regular_file(path):
        return None
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def validate_checksum_sidecar(dmg: Path, sidecar: Path) -> tuple[bool, str]:
    if not is_safe_regular_file(dmg) or not is_safe_regular_file(sidecar):
        return False, "BETA_ARTIFACT_PATH_INVALID"
    if sidecar.name != f"{dmg.name}.sha256":
        return False, "BETA_CHECKSUM_SIDECAR_NAME_INVALID"
    try:
        contents = sidecar.read_text(encoding="utf-8")
    except OSError:
        return False, "BETA_CHECKSUM_UNREADABLE"
    match = re.fullmatch(r"([0-9a-f]{64})  ([^/\n]+)\n", contents)
    if match is None:
        return False, "BETA_CHECKSUM_FORMAT_INVALID"
    expected_digest, recorded_name = match.groups()
    if recorded_name != dmg.name:
        return False, "BETA_CHECKSUM_ARTIFACT_MISMATCH"
    actual_digest = sha256_file(dmg)
    if actual_digest is None or actual_digest != expected_digest:
        return False, "BETA_CHECKSUM_INVALID"
    return True, "BETA_CHECKSUM_VALID"


def validate_acceptance_evidence(evidence: object, artifact_sha256: str) -> tuple[bool, str]:
    if not isinstance(evidence, dict) or set(evidence) != ACCEPTANCE_FIELDS:
        return False, "BETA_ACCEPTANCE_EVIDENCE_SCHEMA_INVALID"
    if evidence.get("schema_version") != 1 or evidence.get("artifact_sha256") != artifact_sha256:
        return False, "BETA_ACCEPTANCE_EVIDENCE_ARTIFACT_MISMATCH"
    if any(evidence.get(field) != "passed" for field in ACCEPTANCE_FIELDS - {"schema_version", "artifact_sha256"}):
        return False, "BETA_ACCEPTANCE_EVIDENCE_INCOMPLETE"
    return True, "BETA_ACCEPTANCE_EVIDENCE_VALID"


def validate_public_docs(root: Path, version: str, build: str) -> tuple[bool, str]:
    channel_paths = (
        root / "README.md",
        root / "docs/release/public-downloads-readme.md",
        root / f"docs/release/unsigned-beta-{version}-{build}.md",
    )
    companion_paths = (
        root / "docs/privacy.md",
        root / "docs/support.md",
        root / "docs/release/third-party-notices.md",
        root / "LICENSE",
    )
    try:
        contents = [path.read_text(encoding="utf-8").lower() for path in channel_paths]
    except OSError:
        return False, "BETA_PUBLIC_DOCS_MISSING"
    if not all(is_safe_regular_file(path) and path.stat().st_size > 0 for path in companion_paths):
        return False, "BETA_PUBLIC_COMPANION_DOCS_MISSING"
    if all(
        all(marker in text for marker in PUBLIC_DOC_MARKERS)
        and ("disabled" in text or "deshabilitado" in text)
        for text in contents
    ):
        return True, "BETA_PUBLIC_DOCS_VALID"
    return False, "BETA_PUBLIC_DOCS_INVALID"


def validate_embedded_public_documents(resources: Path) -> tuple[bool, str]:
    required = (
        "Documentation/LICENSE",
        "Documentation/Sparkle-LICENSE",
        "Documentation/docs/privacy.md",
        "Documentation/docs/support.md",
        "Documentation/docs/release/third-party-notices.md",
    )
    if all(is_safe_regular_file(resources / relative) and (resources / relative).stat().st_size > 0 for relative in required):
        return True, "BETA_EMBEDDED_DOCUMENTS_VALID"
    return False, "BETA_EMBEDDED_DOCUMENTS_MISSING"


def frozen_helper_inventory(
    info_plist: Path, source_fingerprint: Path, dependency_inventory: Path
) -> dict[str, object] | None:
    try:
        with info_plist.open("rb") as stream:
            info = plistlib.load(stream)
        fingerprint = source_fingerprint.read_text(encoding="utf-8").strip()
        dependencies = json.loads(dependency_inventory.read_text(encoding="utf-8"))
    except (OSError, plistlib.InvalidFileException, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(info, dict) or not SHA256_PATTERN.fullmatch(fingerprint) or not isinstance(dependencies, dict):
        return None
    dependency_rows = dependencies.get("dependencies")
    if (
        dependencies.get("schema_version") != 1
        or dependencies.get("membership_source") != "pyinstaller-analysis-toc-v1"
        or not isinstance(dependency_rows, list)
        or not dependency_rows
        or any(
            not isinstance(row, dict)
            or set(row) != {"name", "version", "license", "notice_files"}
            or not all(isinstance(row.get(field), str) and row[field] for field in ("name", "version", "license"))
            or not isinstance(row.get("notice_files"), list)
            or not row["notice_files"]
            or not all(
                isinstance(item, str)
                and item.startswith(f"{row.get('name')}/")
                and is_safe_regular_file(dependency_inventory.parent / "ThirdPartyNotices" / item)
                for item in row["notice_files"]
            )
            for row in dependency_rows
        )
    ):
        return None
    bundle_id = info.get("CFBundleIdentifier")
    version = info.get("CFBundleShortVersionString")
    build = info.get("CFBundleVersion")
    if not all(isinstance(value, str) and value for value in (bundle_id, version, build)):
        return None
    return {
        "bundle_id": bundle_id,
        "version": version,
        "build": build,
        "source_fingerprint": fingerprint,
        "dependencies": dependency_rows,
    }


def inventory_matches_candidate(inventory: dict[str, object], version: str, build: str, source_fingerprint: str) -> bool:
    return (
        inventory.get("bundle_id") == "com.photoslocalkeywordindexer.worker"
        and inventory.get("version") == version
        and inventory.get("build") == build
        and inventory.get("source_fingerprint") == source_fingerprint
    )


def current_helper_source_fingerprint() -> str | None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "packaging/helper_source_fingerprint.py"), str(ROOT)],
        capture_output=True,
        text=True,
        check=False,
    )
    fingerprint = result.stdout.strip()
    return fingerprint if result.returncode == 0 and SHA256_PATTERN.fullmatch(fingerprint) else None


def run_layout_verifier(dmg: Path) -> tuple[bool, str]:
    result = subprocess.run(
        [str(ROOT / "packaging/verify_dmg_layout.sh"), "--json", str(dmg)],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False, "BETA_DMG_LAYOUT_INVALID"
    if result.returncode == 0 and report.get("status") == "ready":
        return True, "BETA_DMG_LAYOUT_VALID"
    return False, "BETA_DMG_LAYOUT_INVALID"


def mounted_inventory(
    dmg: Path,
    expected_version: str,
    expected_build: str,
    expected_source_fingerprint: str,
) -> tuple[dict[str, str] | None, tuple[bool, str]]:
    temporary = Path(tempfile.mkdtemp(prefix="photos-indexer-beta-"))
    mount = temporary / "mount"
    mount.mkdir()
    attach = subprocess.run(
        ["hdiutil", "attach", "-nobrowse", "-readonly", "-mountpoint", str(mount), str(dmg)],
        capture_output=True,
        text=True,
        check=False,
    )
    device_match = re.search(r"(/dev/disk\d+(?:s\d+)?)", attach.stdout)
    if attach.returncode != 0 or device_match is None:
        return None, (False, "BETA_DMG_MOUNT_INVALID")
    def inspect_mounted_app() -> tuple[dict[str, object] | None, tuple[bool, str]]:
        app = mount / "PhotosLocalKeywordIndexer.app"
        resources = app / "Contents/Resources"
        app_info = app / "Contents/Info.plist"
        helper_info = app / "Contents/Helpers/PhotosIndexerWorker.app/Contents/Info.plist"
        helper_fingerprint = app / "Contents/Helpers/PhotosIndexerWorker.app/Contents/Resources/.photos-indexer-source-fingerprint"
        helper_dependencies = app / "Contents/Helpers/PhotosIndexerWorker.app/Contents/Resources/.photos-indexer-dependency-inventory.json"
        documents_ok, documents_code = validate_embedded_public_documents(resources)
        if not documents_ok:
            return None, (False, documents_code)
        compatibility = subprocess.run(
            [sys.executable, str(ROOT / "packaging/verify_macos_compatibility.py"), str(app)],
            capture_output=True,
            text=True,
            check=False,
        )
        if compatibility.returncode != 0:
            return None, (False, "BETA_MACOS_COMPATIBILITY_INVALID")
        if not all(is_safe_regular_file(path) for path in (app_info, helper_info, helper_fingerprint, helper_dependencies)):
            return None, (False, "BETA_FROZEN_HELPER_METADATA_INVALID")
        try:
            with app_info.open("rb") as stream:
                app_metadata = plistlib.load(stream)
        except (OSError, plistlib.InvalidFileException, ValueError):
            return None, (False, "BETA_APP_METADATA_INVALID")
        if not isinstance(app_metadata, dict):
            return None, (False, "BETA_APP_METADATA_INVALID")
        if app_metadata.get("SUFeedURL") is not None or app_metadata.get("SUEnableAutomaticChecks") not in (None, False):
            return None, (False, "BETA_SPARKLE_UPDATES_ENABLED")
        inventory = frozen_helper_inventory(helper_info, helper_fingerprint, helper_dependencies)
        if inventory is None or not inventory_matches_candidate(inventory, expected_version, expected_build, expected_source_fingerprint):
            return None, (False, "BETA_FROZEN_HELPER_METADATA_INVALID")
        return inventory, (True, "BETA_FROZEN_HELPER_INVENTORY_VALID")

    detach = None
    try:
        inventory, result = inspect_mounted_app()
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        inventory, result = None, (False, "BETA_DMG_INSPECTION_INVALID")
    finally:
        detach = subprocess.run(["hdiutil", "detach", device_match.group(1)], capture_output=True, text=True, check=False)
    assert detach is not None
    if detach.returncode != 0:
        return None, (False, "BETA_DMG_UNMOUNT_FAILED")
    try:
        mount.rmdir()
        temporary.rmdir()
    except OSError:
        return None, (False, "BETA_DMG_MOUNT_CLEANUP_FAILED")
    return inventory, result


def emit(status: str, checks: list[tuple[bool, str]], inventory: dict[str, str] | None, as_json: bool) -> None:
    if as_json:
        print(json.dumps({
            "schema_version": 1,
            "status": status.lower(),
            "checks": [{"state": "PASS" if passed else "FAIL", "code": code} for passed, code in checks],
            "frozen_helper": inventory,
        }, separators=(",", ":"), sort_keys=True))
        return
    for passed, code in checks:
        print(f"public_beta:{'PASS' if passed else 'FAIL'}:{code}")
    print(f"public_beta:{status}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--acceptance-evidence", type=Path)
    parser.add_argument("dmg", type=Path)
    parser.add_argument("checksum", type=Path)
    arguments = parser.parse_args()
    provided_dmg = arguments.dmg
    provided_checksum = arguments.checksum
    dmg = provided_dmg.resolve(strict=False)
    checksum = provided_checksum.resolve(strict=False)
    match = DMG_PATTERN.fullmatch(dmg.name)
    checks: list[tuple[bool, str]] = []
    checks.append((
        match is not None
        and provided_dmg.is_absolute()
        and provided_checksum.is_absolute()
        and is_safe_regular_file(provided_dmg)
        and is_safe_regular_file(provided_checksum),
        "BETA_DMG_NAME_VALID",
    ))
    if not checks[-1][0]:
        emit("BLOCKED", checks, None, arguments.json)
        return 2
    checksum_ok, checksum_code = validate_checksum_sidecar(dmg, checksum)
    checks.append((checksum_ok, checksum_code))
    layout_ok, layout_code = run_layout_verifier(dmg) if checksum_ok else (False, "BETA_DMG_LAYOUT_NOT_RUN")
    checks.append((layout_ok, layout_code))
    source_fingerprint = current_helper_source_fingerprint()
    if source_fingerprint is None:
        inventory, inventory_result = None, (False, "BETA_SOURCE_FINGERPRINT_UNAVAILABLE")
    elif layout_ok:
        inventory, inventory_result = mounted_inventory(
            dmg, match.group("version"), match.group("build"), source_fingerprint
        )
    else:
        inventory, inventory_result = None, (False, "BETA_FROZEN_HELPER_INVENTORY_NOT_RUN")
    checks.append(inventory_result)
    assert match is not None
    docs_ok, docs_code = validate_public_docs(ROOT, match.group("version"), match.group("build"))
    checks.append((docs_ok, docs_code))
    if not all(passed for passed, _ in checks):
        emit("BLOCKED", checks, inventory, arguments.json)
        return 1
    if arguments.acceptance_evidence is None:
        emit("READY_FOR_ACCEPTANCE", checks, inventory, arguments.json)
        return 0
    evidence_path = arguments.acceptance_evidence.resolve(strict=False)
    if not is_safe_regular_file(evidence_path):
        checks.append((False, "BETA_ACCEPTANCE_EVIDENCE_PATH_INVALID"))
        emit("BLOCKED", checks, inventory, arguments.json)
        return 1
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        evidence = None
    evidence_ok, evidence_code = validate_acceptance_evidence(evidence, sha256_file(dmg) or "")
    checks.append((evidence_ok, evidence_code))
    if not evidence_ok:
        emit("BLOCKED", checks, inventory, arguments.json)
        return 1
    emit("ACCEPTANCE_EVIDENCE_VALIDATED_PUBLICATION_REQUIRES_EXPLICIT_AUTHORIZATION", checks, inventory, arguments.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
