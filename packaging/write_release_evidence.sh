#!/bin/zsh
set -euo pipefail

dmg_path="${1:-}"
app_path="${2:-}"
notary_result="${3:-}"
output_path="${4:-}"

if [[ -z "$dmg_path" || -z "$app_path" || -z "$notary_result" || -z "$output_path" \
  || "$dmg_path" != /* || "$app_path" != /* || "$notary_result" != /* || "$output_path" != /* \
  || -L "$dmg_path" || -L "$app_path" || -L "$notary_result" || -L "$output_path" \
  || ! -f "$dmg_path" || ! -d "$app_path" || ! -f "$notary_result" ]]; then
  print -u2 -- "release_evidence:FAIL:input_path_invalid"
  print -u2 -- "release_evidence:HINT:input_path_invalid:provide_private_absolute_release_artifacts"
  exit 2
fi

reject_symlink_components() {
  local candidate="$1"
  while [[ "$candidate" != "/" ]]; do
    # macOS exposes these two stable system aliases; artifact-owned links are
    # still rejected at every deeper component.
    if [[ -L "$candidate" && "$candidate" != "/var" && "$candidate" != "/tmp" ]]; then
      print -u2 -- "Release evidence paths must not contain symlinks."
      exit 2
    fi
    candidate="${candidate:h}"
  done
}

reject_symlink_components "$dmg_path"
reject_symlink_components "$app_path"
reject_symlink_components "$notary_result"
reject_symlink_components "$output_path"

expected_output="${dmg_path:r}.release-evidence.json"
if [[ "$output_path" != "$expected_output" || -e "$output_path" ]]; then
  print -u2 -- "Release evidence must be a new sidecar next to the DMG."
  exit 2
fi
output_parent="${output_path:h}"
if [[ -L "$output_parent" ]]; then
  print -u2 -- "Release evidence output directory must not be a symlink."
  exit 2
fi

/usr/bin/python3 - "$dmg_path" "$app_path" "$notary_result" "$output_path" <<'PY'
import hashlib
import json
import os
import plistlib
import re
import signal
import stat
import sys
import tempfile
from pathlib import Path

dmg_path, app_path, notary_path, output_path = map(Path, sys.argv[1:])
try:
    output_parent_metadata = output_path.parent.lstat()
    if (
        not stat.S_ISDIR(output_parent_metadata.st_mode)
        or output_parent_metadata.st_uid != os.getuid()
        or output_parent_metadata.st_mode & 0o022
    ):
        print("Release evidence output directory is invalid.", file=sys.stderr)
        raise SystemExit(1)
except (OSError, ValueError):
    print("Release evidence output directory is invalid.", file=sys.stderr)
    raise SystemExit(1)
try:
    dmg_metadata = dmg_path.lstat()
    if (
        not stat.S_ISREG(dmg_metadata.st_mode)
        or dmg_metadata.st_nlink != 1
        or dmg_metadata.st_uid != os.getuid()
        or dmg_metadata.st_mode & 0o022
        or dmg_metadata.st_size <= 0
    ):
        print("Release disk image is invalid.", file=sys.stderr)
        raise SystemExit(1)
except (OSError, ValueError):
    print("Release disk image is invalid.", file=sys.stderr)
    raise SystemExit(1)
try:
    notary_metadata = notary_path.lstat()
    if (
        not stat.S_ISREG(notary_metadata.st_mode)
        or notary_metadata.st_nlink != 1
        or notary_metadata.st_uid != os.getuid()
        or notary_metadata.st_mode & 0o022
        or notary_metadata.st_size <= 0
    ):
        print("Notarization result input is invalid.", file=sys.stderr)
        raise SystemExit(1)
except (OSError, ValueError):
    print("Notarization result input is invalid.", file=sys.stderr)
    raise SystemExit(1)
try:
    app_metadata = app_path.lstat()
    if not stat.S_ISDIR(app_metadata.st_mode):
        raise ValueError
except (OSError, ValueError):
    print("Release app bundle is invalid.", file=sys.stderr)
    raise SystemExit(1)
info_path = app_path / "Contents" / "Info.plist"
sparkle_info_path = (
    app_path
    / "Contents"
    / "Frameworks"
    / "Sparkle.framework"
    / "Versions"
    / "Current"
    / "Resources"
    / "Info.plist"
)
helper_app = app_path / "Contents" / "Helpers" / "PhotosIndexerWorker.app"
helper_info_path = helper_app / "Contents" / "Info.plist"
legacy_helper_path = (
    app_path / "Contents" / "Helpers" / "PhotosIndexerWorker" / "PhotosIndexerWorker"
)


def bundle_tree_fingerprint(root: Path) -> str:
    """Hash bundle content and relative names without retaining local paths.

    Directories are included to make additions/removals observable. Symlink
    targets are recorded as link text and never followed, while regular files
    contribute their mode, size and content digest. Sorting by relative POSIX path
    makes identical bundles reproducible across checkout locations.
    """
    fingerprint = hashlib.sha256()
    entries = sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix())
    for entry in entries:
        relative = entry.relative_to(root).as_posix()
        entry_metadata = entry.lstat()
        if stat.S_ISLNK(entry_metadata.st_mode):
            record = f"L\\0{relative}\\0{os.readlink(entry)}\\n".encode("utf-8")
        elif stat.S_ISDIR(entry_metadata.st_mode):
            record = f"D\\0{relative}\\n".encode("utf-8")
        elif stat.S_ISREG(entry_metadata.st_mode):
            content_digest = hashlib.sha256()
            descriptor = os.open(entry, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as stream:
                opened_bundle_metadata = os.fstat(stream.fileno())
                if not same_file_state(entry_metadata, opened_bundle_metadata):
                    raise ValueError("bundle file identity changed")
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    content_digest.update(chunk)
                hashed_bundle_metadata = os.fstat(stream.fileno())
            if not same_file_state(opened_bundle_metadata, hashed_bundle_metadata):
                raise ValueError("bundle file changed while hashing")
            if not same_file_state(hashed_bundle_metadata, entry.lstat()):
                raise ValueError("bundle file changed after hashing")
            record = (
                f"F\\0{relative}\\0{stat.S_IMODE(hashed_bundle_metadata.st_mode):o}"
                f"\\0{hashed_bundle_metadata.st_size}\\0{content_digest.hexdigest()}\\n"
            ).encode("utf-8")
        else:
            raise ValueError("bundle contains unsupported filesystem entry")
        fingerprint.update(record)
    return fingerprint.hexdigest()


def strict_json_object(pairs: list[tuple[object, object]]) -> dict[object, object]:
    """Reject ambiguous duplicate keys in the notarytool result."""
    result: dict[object, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


try:
    with notary_path.open("r", encoding="utf-8") as stream:
        notarization = json.load(stream, object_pairs_hook=strict_json_object)
    with info_path.open("rb") as stream:
        bundle = plistlib.load(stream)
    with helper_info_path.open("rb") as stream:
        helper_bundle = plistlib.load(stream)
except (OSError, ValueError, RecursionError, plistlib.InvalidFileException):
    print("Release evidence input is invalid.", file=sys.stderr)
    raise SystemExit(1)

if not isinstance(notarization, dict) or not isinstance(bundle, dict) or not isinstance(helper_bundle, dict):
    print("Release evidence input is invalid.", file=sys.stderr)
    raise SystemExit(1)

status = notarization.get("status")
submission_id = notarization.get("id")
if status != "Accepted":
    print("Notarization status must be Accepted before writing release evidence.", file=sys.stderr)
    raise SystemExit(1)
if not isinstance(submission_id, str) or not re.fullmatch(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
    submission_id,
):
    print("Notarization submission ID is invalid.", file=sys.stderr)
    raise SystemExit(1)

expected_bundle = {
    "identifier": bundle.get("CFBundleIdentifier"),
    "version": bundle.get("CFBundleShortVersionString"),
    "build": bundle.get("CFBundleVersion"),
    "minimum_macos": bundle.get("LSMinimumSystemVersion"),
}
expected_identity = {
    "CFBundleIdentifier": "com.photoslocalkeywordindexer.app",
    "CFBundleExecutable": "PhotosLocalKeywordIndexer",
    "CFBundlePackageType": "APPL",
    "CFBundleIconFile": "AppIcon.icns",
    "LSMinimumSystemVersion": "14.0",
}
if any(bundle.get(key) != value for key, value in expected_identity.items()):
    print("Release bundle identity is invalid.", file=sys.stderr)
    raise SystemExit(1)
if expected_bundle["identifier"] != "com.photoslocalkeywordindexer.app":
    print("Release bundle identifier is invalid.", file=sys.stderr)
    raise SystemExit(1)
if not isinstance(expected_bundle["version"], str) or not re.fullmatch(
    r"[0-9]+\.[0-9]+\.[0-9]+", expected_bundle["version"]
):
    print("Release bundle version is invalid.", file=sys.stderr)
    raise SystemExit(1)
if not isinstance(expected_bundle["build"], str) or not re.fullmatch(
    r"[1-9][0-9]*", expected_bundle["build"]
):
    print("Release bundle build is invalid.", file=sys.stderr)
    raise SystemExit(1)
if expected_bundle["minimum_macos"] != "14.0":
    print("Release minimum macOS version is invalid.", file=sys.stderr)
    raise SystemExit(1)
expected_helper_identity = {
    "CFBundleIdentifier": "com.photoslocalkeywordindexer.worker",
    "CFBundleExecutable": "PhotosIndexerWorker",
    "CFBundlePackageType": "APPL",
    "LSUIElement": True,
}
if any(helper_bundle.get(key) != value for key, value in expected_helper_identity.items()):
    print("Release helper bundle identity is invalid.", file=sys.stderr)
    raise SystemExit(1)
if helper_bundle.get("CFBundleShortVersionString") != expected_bundle["version"]:
    print("Release helper bundle version is invalid.", file=sys.stderr)
    raise SystemExit(1)
if helper_bundle.get("CFBundleVersion") != expected_bundle["build"]:
    print("Release helper bundle build is invalid.", file=sys.stderr)
    raise SystemExit(1)
for key in (
    "NSPhotoLibraryUsageDescription",
    "NSPhotoLibraryAddUsageDescription",
    "NSAppleEventsUsageDescription",
):
    if not isinstance(helper_bundle.get(key), str) or not helper_bundle[key].strip():
        print("Release helper privacy declaration is invalid.", file=sys.stderr)
        raise SystemExit(1)
def require_regular(path: Path, *, executable: bool = False) -> None:
    """Reject incomplete or symlinked release nodes before publishing evidence."""
    if not path.is_file() or path.is_symlink():
        raise SystemExit("Release app bundle is incomplete.")
    if executable and not (path.stat().st_mode & stat.S_IXUSR):
        raise SystemExit("Release app bundle is incomplete.")


if app_path.is_symlink() or not app_path.is_dir():
    raise SystemExit("Release app bundle is incomplete.")
if legacy_helper_path.exists() or legacy_helper_path.is_symlink():
    raise SystemExit("Release app bundle is incomplete.")


def verify_bundle_integrity(root: Path) -> None:
    """Do not publish evidence for a bundle mutable outside this process."""
    try:
        candidates = [root, *root.rglob("*")]
        for candidate in candidates:
            metadata = candidate.lstat()
            if metadata.st_uid != os.getuid():
                raise ValueError("foreign bundle entry")
            if stat.S_ISREG(metadata.st_mode) and metadata.st_nlink != 1:
                raise ValueError("hardlinked bundle entry")
            if not stat.S_ISLNK(metadata.st_mode) and metadata.st_mode & 0o022:
                raise ValueError("writable bundle entry")
    except (OSError, RuntimeError, ValueError):
        raise SystemExit("Release app bundle integrity is invalid.")


verify_bundle_integrity(app_path)


def verify_symlink_targets(root: Path) -> None:
    """Reject bundle links that would make evidence read outside the app."""
    root_real = root.resolve(strict=False)
    try:
        candidates = root.rglob("*")
        for candidate in candidates:
            if not candidate.is_symlink():
                continue
            target = candidate.resolve(strict=False)
            target.relative_to(root_real)
    except (OSError, RuntimeError, ValueError):
        raise SystemExit("Release app bundle contains an invalid symlink.")


verify_symlink_targets(app_path)
sparkle_framework = app_path / "Contents" / "Frameworks" / "Sparkle.framework"
if sparkle_framework.is_symlink() or not sparkle_framework.is_dir():
    raise SystemExit("Release app bundle is incomplete.")
try:
    with sparkle_info_path.open("rb") as stream:
        sparkle = plistlib.load(stream)
except (OSError, ValueError, plistlib.InvalidFileException):
    print("Release evidence input is invalid.", file=sys.stderr)
    raise SystemExit(1)
if not isinstance(sparkle, dict):
    print("Release evidence input is invalid.", file=sys.stderr)
    raise SystemExit(1)
if sparkle.get("CFBundleShortVersionString") != "2.9.2":
    print("Sparkle framework version is invalid.", file=sys.stderr)
    raise SystemExit(1)
require_regular(app_path / "Contents" / "MacOS" / "PhotosLocalKeywordIndexer", executable=True)
require_regular(
    helper_app / "Contents" / "MacOS" / "PhotosIndexerWorker",
    executable=True,
)
require_regular(app_path / "Contents" / "Resources" / "AppIcon.icns")
require_regular(
    sparkle_framework / "Versions" / "Current" / "Sparkle",
    executable=True,
)

app_name = app_path.name
if not app_name.endswith(".app"):
    print("Release app filename is invalid.", file=sys.stderr)
    raise SystemExit(1)
expected_artifact_name = (
    f"{app_name[:-len('.app')]}-{expected_bundle['version']}-"
    f"{expected_bundle['build']}.dmg"
)
if dmg_path.name != expected_artifact_name:
    print("Release DMG filename does not match bundle version and build.", file=sys.stderr)
    raise SystemExit(1)

def same_file_state(first: os.stat_result, second: os.stat_result) -> bool:
    return (
        first.st_dev == second.st_dev
        and first.st_ino == second.st_ino
        and first.st_size == second.st_size
        and first.st_mtime_ns == second.st_mtime_ns
    )


digest = hashlib.sha256()
try:
    with dmg_path.open("rb") as stream:
        opened_dmg_metadata = os.fstat(stream.fileno())
        if not same_file_state(dmg_metadata, opened_dmg_metadata):
            raise OSError
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
        hashed_dmg_metadata = os.fstat(stream.fileno())
    if not same_file_state(opened_dmg_metadata, hashed_dmg_metadata):
        raise OSError
    dmg_metadata = hashed_dmg_metadata
except OSError:
    print("Release disk image changed while hashing.", file=sys.stderr)
    raise SystemExit(1)

try:
    bundle_fingerprint = bundle_tree_fingerprint(app_path)
except (OSError, ValueError):
    print("Release bundle fingerprint failed.", file=sys.stderr)
    raise SystemExit(1)

evidence = {
    "schema_version": 1,
    "artifact": {
        "filename": dmg_path.name,
        "sha256": digest.hexdigest(),
        "size_bytes": dmg_path.stat().st_size,
    },
    "bundle": expected_bundle,
    "bundle_fingerprint": {
        "algorithm": "sha256-tree-v1",
        "sha256": bundle_fingerprint,
    },
    "sparkle": {"framework_version": "2.9.2"},
    "notarization": {
        "status": "Accepted",
        "submission_id": submission_id,
    },
    "verification": {
        "bundle_release_policy": "passed",
        "gatekeeper_app": "accepted",
        "gatekeeper_dmg": "accepted",
        "stapler_ticket": "validated",
    },
}


def same_identity(path: Path, expected: os.stat_result) -> bool:
    try:
        current = path.lstat()
    except (OSError, ValueError):
        return False
    return (
        current.st_dev == expected.st_dev
        and current.st_ino == expected.st_ino
        and current.st_mode == expected.st_mode
        and current.st_size == expected.st_size
        and current.st_mtime_ns == expected.st_mtime_ns
    )


if not all(
    same_identity(path, expected)
    for path, expected in (
        (dmg_path, dmg_metadata),
        (app_path, app_metadata),
        (notary_path, notary_metadata),
        (output_path.parent, output_parent_metadata),
    )
):
    print("Release evidence input identity changed.", file=sys.stderr)
    raise SystemExit(1)

temporary_name = None
temporary_stat = None
published_output_stat = None


def cleanup_temporary_file() -> None:
    if temporary_name is not None and temporary_stat is not None:
        try:
            current_temporary_stat = os.stat(temporary_name, follow_symlinks=False)
        except FileNotFoundError:
            return
        if (
            current_temporary_stat.st_ino == temporary_stat.st_ino
            and current_temporary_stat.st_dev == temporary_stat.st_dev
        ):
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass


def handle_signal(signum: int, _frame: object) -> None:
    cleanup_temporary_file()
    raise SystemExit(128 + signum)


signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)

try:
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=output_path.parent,
        prefix=f".{output_path.name}.",
        suffix=".tmp",
        delete=False,
    ) as stream:
        temporary_name = stream.name
        os.fchmod(stream.fileno(), 0o600)
        temporary_stat = os.fstat(stream.fileno())
        json.dump(evidence, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    # Publish with a create-only link.  The shell preflight rejects an
    # existing destination, but a concurrent process could create one after
    # that check; os.replace would then silently overwrite it.  os.link is
    # atomic and fails with EEXIST instead, preserving the no-overwrite
    # contract even across that race.
    os.link(temporary_name, output_path)
    published_output_stat = os.stat(output_path, follow_symlinks=False)
    if (
        published_output_stat.st_ino != temporary_stat.st_ino
        or published_output_stat.st_dev != temporary_stat.st_dev
    ):
        raise OSError("published evidence inode mismatch")
    cleanup_temporary_file()
    temporary_name = None
    temporary_stat = None
    directory_fd = os.open(output_path.parent, os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
except BaseException as error:
    cleanup_temporary_file()
    if published_output_stat is not None:
        try:
            current_output_stat = os.stat(output_path, follow_symlinks=False)
        except FileNotFoundError:
            current_output_stat = None
        if (
            current_output_stat is not None
            and current_output_stat.st_ino == published_output_stat.st_ino
            and current_output_stat.st_dev == published_output_stat.st_dev
        ):
            try:
                os.unlink(output_path)
            except FileNotFoundError:
                pass
    if isinstance(error, SystemExit):
        raise
    print("Release evidence publication failed.", file=sys.stderr)
    raise SystemExit(1) from None
PY

print -- "release_evidence:RECORDED"
