#!/bin/zsh
set -euo pipefail

# PyInstaller creates the complete embedded runtime. Neutralize a permissive
# caller without making an installed app inaccessible to other local users;
# sensitive build markers are tightened explicitly below.
umask 022

project_root="${0:A:h:h}"
python_bin="${PYTHON_BIN:-python3.12}"
verify_helper="${VERIFY_HELPER:-1}"
build_root="${BUILD_ROOT:-$project_root/build/python-helper}"
build_root="${build_root:a}"

if [[ "$verify_helper" != "0" && "$verify_helper" != "1" ]]; then
  print -u2 -- "VERIFY_HELPER must be 0 or 1."
  exit 2
fi

source "$project_root/packaging/build_path_guard.zsh"
validate_build_path "$build_root" "$project_root/build" "BUILD_ROOT"

# PyInstaller and the fingerprint marker are created under this root, and an
# old helper is removed after preflight. Reject a tree another local account
# can mutate before any build dependency is loaded or stale output is touched.
if [[ ! -x /usr/bin/python3 ]]; then
  print -u2 -- "build_python_helper:FAIL:build_root_integrity_unavailable"
  exit 2
fi
build_root_integrity="$(
  unset PYTHONHOME PYTHONPATH
  /usr/bin/python3 - "$build_root" "$project_root/build" <<'PY'
import os
from pathlib import Path
import stat
import sys

candidate = Path(sys.argv[1])
root = Path(sys.argv[2])
try:
    candidate.relative_to(root)
    current = candidate
    while True:
        if current.exists():
            metadata = os.stat(current, follow_symlinks=False)
            if not stat.S_ISDIR(metadata.st_mode):
                print("invalid")
                break
            if metadata.st_uid != os.getuid():
                print("ownership")
                break
            if metadata.st_mode & 0o022:
                print("permissions")
                break
        if current == root:
            print("ok")
            break
        current = current.parent
except (OSError, ValueError):
    print("unavailable")
PY
)" || build_root_integrity="unavailable"
case "$build_root_integrity" in
  ok) ;;
  ownership)
    print -u2 -- "build_python_helper:FAIL:build_root_ownership"
    print -u2 -- "build_python_helper:HINT:build_root_ownership:rebuild_as_the_current_user"
    exit 2
    ;;
  permissions)
    print -u2 -- "build_python_helper:FAIL:build_root_permissions"
    print -u2 -- "build_python_helper:HINT:build_root_permissions:remove_group_world_write_then_retry"
    exit 2
    ;;
  invalid)
    print -u2 -- "build_python_helper:FAIL:build_root_invalid"
    exit 2
    ;;
  *)
    print -u2 -- "build_python_helper:FAIL:build_root_integrity_unavailable"
    exit 2
    ;;
esac

helper_dist="$build_root/dist"
if [[ -L "$helper_dist" ]]; then
  print -u2 -- "build_python_helper:FAIL:dist_symlink"
  print -u2 -- "build_python_helper:HINT:dist_symlink:remove_symlink_and_retry"
  exit 2
fi
if [[ -e "$helper_dist" && ! -d "$helper_dist" ]]; then
  print -u2 -- "build_python_helper:FAIL:dist_not_directory"
  exit 2
fi
for build_directory in "$build_root/work" "$build_root/spec"; do
  if [[ -L "$build_directory" ]]; then
    print -u2 -- "build_python_helper:FAIL:build_directory_symlink"
    exit 2
  fi
  if [[ -e "$build_directory" && ! -d "$build_directory" ]]; then
    print -u2 -- "build_python_helper:FAIL:build_directory_not_directory"
    exit 2
  fi
done

helper_app="$build_root/dist/PhotosIndexerWorker.app"
helper_output="$helper_app"
helper_dist_created_identity=""
helper_output_slot_owned=0
helper_app_validated_identity=""
record_helper_dist_identity() {
  if [[ -d "$helper_dist" && ! -L "$helper_dist" ]]; then
    helper_dist_created_identity="$(stat -f '%d:%i' -- "$helper_dist" 2>/dev/null || true)"
  fi
}
helper_dist_is_owned() {
  local current_identity
  [[ -n "$helper_dist_created_identity" && -d "$helper_dist" && ! -L "$helper_dist" ]] || return 1
  current_identity="$(stat -f '%d:%i' -- "$helper_dist" 2>/dev/null || true)"
  [[ -n "$current_identity" && "$current_identity" == "$helper_dist_created_identity" ]]
}
helper_app_is_validated() {
  local current_identity
  [[ -n "$helper_app_validated_identity" && -d "$helper_app" && ! -L "$helper_app" ]] || return 1
  helper_dist_is_owned || return 1
  current_identity="$(stat -f '%d:%i' -- "$helper_app" 2>/dev/null || true)"
  [[ -n "$current_identity" && "$current_identity" == "$helper_app_validated_identity" ]]
}
cleanup_helper_output() {
  # The dist directory is the owned boundary. PyInstaller is expected to
  # replace the bundle inode, so child-inode equality cannot identify its
  # partial output. Removing a root symlink removes only the link, never its
  # external target.
  if (( helper_output_slot_owned == 1 )) && helper_dist_is_owned; then
    if [[ -L "$helper_output" ]]; then
      rm -f -- "$helper_output"
    elif [[ -e "$helper_output" ]]; then
      rm -rf -- "$helper_output"
    fi
  fi
}

if [[ "$(uname -m)" != "arm64" ]]; then
  print -u2 -- "PhotosIndexerWorker is packaged only on Apple Silicon (arm64)."
  exit 2
fi

if ! command -v "$python_bin" >/dev/null 2>&1; then
  print -u2 -- "Python 3.12 is required. Set PYTHON_BIN to a Python 3.12 executable."
  exit 2
fi
# Keep a caller-provided relative path valid after changing into the checkout
# below.  Bare command names are resolved through PATH first; path-like values
# are made absolute before any build step can change the working directory.
python_bin="$(command -v "$python_bin")"
if [[ "$python_bin" != /* ]]; then
  # Make a relative venv path absolute without resolving its executable
  # symlink.  Resolving it would escape the venv and lose its site-packages.
  python_bin="${python_bin:a}"
fi

python_version="$($python_bin -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if [[ "$python_version" != "3.12" ]]; then
  print -u2 -- "Expected Python 3.12; found $python_version."
  exit 2
fi

python_architecture="$($python_bin -c 'import platform; print(platform.machine())' 2>/dev/null || print unknown)"
if [[ "$python_architecture" != "arm64" ]]; then
  print -u2 -- "Expected arm64 Python 3.12; found $python_architecture. Set PYTHON_BIN to an arm64 Python 3.12 executable."
  exit 2
fi

if ! "$python_bin" -c 'import PyInstaller' >/dev/null 2>&1; then
  print -u2 -- "Install build dependencies first with Python 3.12: python3.12 -m pip install -e '.[build]'"
  exit 2
fi
if ! "$python_bin" -c '
from importlib.metadata import version

expected_versions = {
    "photoscript": "0.5.3",
    "pyobjc-framework-Photos": "12.2.2",
    "pyobjc-framework-MapKit": "12.2.2",
    "pyobjc-framework-CoreLocation": "12.2.2",
    "pyobjc-framework-Quartz": "12.2.2",
}
for package, expected in expected_versions.items():
    if version(package) != expected:
        raise SystemExit(1)
 ' >/dev/null 2>&1
then
  print -u2 -- "build_python_helper:FAIL:python_runtime_provenance_invalid"
  print -u2 -- "build_python_helper:HINT:python_runtime_provenance_invalid:install_the_pinned_runtime_dependencies_then_retry"
  exit 2
fi
runtime_dependency_error=""
runtime_dependency_status=0
runtime_dependency_error="$(PYTHONPATH="$project_root/src" "$python_bin" -c '
import AppKit, CoreLocation, Foundation, MapKit, Photos, Quartz, objc, httpx
from photos_indexer.adapters import PhotoScriptBridge

PhotoScriptBridge.preflight_compatibility()
' 2>&1 >/dev/null)" || runtime_dependency_status=$?
if (( runtime_dependency_status != 0 )); then
  if [[ "$runtime_dependency_error" == *"-2741"* ]] \
    || { (( runtime_dependency_status == 134 )) && [[ "$runtime_dependency_error" == *"hiservices-xpcservice"* ]]; }; then
    print -u2 -- "build_python_helper:FAIL:photoscript_applescript_unavailable"
    print -u2 -- "build_python_helper:HINT:photoscript_applescript_unavailable:check_photoscript_and_macos_compatibility_before_retry"
    if [[ -x "$helper_output/Contents/MacOS/PhotosIndexerWorker" && ! -L "$helper_output/Contents/MacOS/PhotosIndexerWorker" ]]; then
      print -u2 -- "build_python_helper:INFO:previous_helper_preserved_not_release_ready"
    fi
  else
    print -u2 -- "build_python_helper:FAIL:python_runtime_dependencies_missing"
    print -u2 -- "build_python_helper:HINT:python_runtime_dependencies_missing:install_build_dependencies_with_python312"
  fi
  exit 2
fi
if ! command -v lipo >/dev/null 2>&1; then
  print -u2 -- "build_python_helper:FAIL:lipo_missing"
  print -u2 -- "build_python_helper:HINT:lipo_missing:install_xcode_command_line_tools_then_retry"
  exit 2
fi
if [[ ! -x /usr/bin/python3 ]]; then
  print -u2 -- "build_python_helper:FAIL:helper_source_fingerprint_unavailable"
  print -u2 -- "build_python_helper:HINT:helper_source_fingerprint_unavailable:install_macos_system_python_then_retry"
  exit 2
fi

# mkdir can create earlier targets before reporting a later target failure.
# Keep a temporary trap until the whole directory setup succeeds, and remove
# only empty directories that did not exist before this invocation.
build_root_preexisting=0
dist_preexisting=0
work_preexisting=0
spec_preexisting=0
[[ -e "$build_root" ]] && build_root_preexisting=1
[[ -e "$build_root/dist" ]] && dist_preexisting=1
[[ -e "$build_root/work" ]] && work_preexisting=1
[[ -e "$build_root/spec" ]] && spec_preexisting=1
build_directories_ready=0
cleanup_build_directories() {
  (( build_directories_ready == 1 )) && return
  (( dist_preexisting == 0 )) && [[ -d "$build_root/dist" && ! -L "$build_root/dist" ]] && rmdir -- "$build_root/dist" >/dev/null 2>&1 || true
  (( work_preexisting == 0 )) && [[ -d "$build_root/work" && ! -L "$build_root/work" ]] && rmdir -- "$build_root/work" >/dev/null 2>&1 || true
  (( spec_preexisting == 0 )) && [[ -d "$build_root/spec" && ! -L "$build_root/spec" ]] && rmdir -- "$build_root/spec" >/dev/null 2>&1 || true
  (( build_root_preexisting == 0 )) && [[ -d "$build_root" && ! -L "$build_root" ]] && rmdir -- "$build_root" >/dev/null 2>&1 || true
}
trap cleanup_build_directories EXIT
trap 'cleanup_build_directories; exit 130' INT TERM
mkdir -p "$build_root/dist" "$build_root/work" "$build_root/spec"
build_directories_ready=1
trap - EXIT
trap - INT TERM

# Resolve and validate bundle metadata before invoking PyInstaller so its
# native BUNDLE target receives the same version as the outer application.
helper_version="${APP_VERSION:-$(sed -n 's/^version = "\([^"]*\)"/\1/p' "$project_root/pyproject.toml" | head -1)}"
helper_build_number="${BUILD_NUMBER:-1}"
if [[ ! "$helper_version" =~ '^[0-9]+\.[0-9]+\.[0-9]+$' || ! "$helper_build_number" =~ '^[1-9][0-9]*$' ]]; then
  print -u2 -- "build_python_helper:FAIL:version_invalid"
  exit 2
fi

# Remove any previous helper only after every environment and tool preflight
# has passed. A failed retry must preserve the last usable helper instead of
# deleting it before discovering a missing interpreter or build tool.
record_helper_dist_identity
if ! helper_dist_is_owned; then
  print -u2 -- "build_python_helper:FAIL:dist_identity_changed"
  exit 1
fi
rm -rf -- "$build_root/dist/PhotosIndexerWorker" "$helper_app"
helper_output_slot_owned=1
trap cleanup_helper_output EXIT
trap 'cleanup_helper_output; exit 130' INT TERM

cd "$project_root"
APP_VERSION="$helper_version" BUILD_NUMBER="$helper_build_number" "$python_bin" -m PyInstaller \
  --noconfirm \
  --clean \
  --distpath "$build_root/dist" \
  --workpath "$build_root/work" \
  "$project_root/packaging/PhotosIndexerWorker.spec"

helper="$helper_app/Contents/MacOS/PhotosIndexerWorker"
legacy_python_runtime="$helper_app/Contents/Frameworks/Python"
standalone_python_runtime="$helper_app/Contents/Frameworks/libpython3.12.dylib"
if [[ ! -e "$legacy_python_runtime" && ! -e "$standalone_python_runtime" ]]; then
  print -u2 -- "PyInstaller did not produce the expected helper."
  exit 1
fi
if [[ ! -d "$helper_app" \
  || -L "$helper_app" \
  || ! -d "$helper_app/Contents/Frameworks" \
  || -L "$helper_app/Contents/Frameworks" \
  || ! -d "$helper_app/Contents/Resources" \
  || -L "$helper_app/Contents/Resources" \
  || ! -f "$helper_app/Contents/Info.plist" \
  || -L "$helper_app/Contents/Info.plist" \
  || ! -x "$helper" \
  || -L "$helper" ]]; then
  print -u2 -- "PyInstaller did not produce the expected helper."
  exit 1
fi

# PyInstaller intentionally creates relative links between Resources and
# Frameworks for mixed Python packages. Accept only links that resolve inside
# this native bundle and reject mutable or aliased payload files before any
# marker is written or helper code is executed.
if ! /usr/bin/env -u PYTHONHOME -u PYTHONPATH /usr/bin/python3 - \
  "$helper_app" "$build_root" "$helper_version" "$helper_build_number" <<'PY'
import os
from pathlib import Path
import plistlib
import stat
import sys

app = Path(sys.argv[1])
build_root = Path(sys.argv[2])
expected_version = sys.argv[3]
expected_build = sys.argv[4]
try:
    app_metadata = os.lstat(app)
    if stat.S_ISLNK(app_metadata.st_mode) or not stat.S_ISDIR(app_metadata.st_mode):
        raise ValueError("root")
    resolved_build_root = build_root.resolve(strict=True)
    resolved_app = app.resolve(strict=True)
    resolved_app.relative_to(resolved_build_root)
    if resolved_app.parent != (resolved_build_root / "dist"):
        raise ValueError("containment")
    with (app / "Contents/Info.plist").open("rb") as stream:
        info = plistlib.load(stream)
    expected = {
        "CFBundleExecutable": "PhotosIndexerWorker",
        "CFBundleIdentifier": "com.photoslocalkeywordindexer.worker",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": expected_version,
        "CFBundleVersion": expected_build,
        "LSUIElement": True,
    }
    if any(info.get(key) != value for key, value in expected.items()):
        raise ValueError("plist")
    for key in (
        "NSPhotoLibraryUsageDescription",
        "NSPhotoLibraryAddUsageDescription",
        "NSAppleEventsUsageDescription",
    ):
        if not isinstance(info.get(key), str) or not info[key].strip():
            raise ValueError("privacy")
    for candidate in (app, *app.rglob("*")):
        metadata = os.lstat(candidate)
        if metadata.st_uid != os.getuid() or metadata.st_mode & 0o022:
            raise ValueError("ownership_or_permissions")
        if stat.S_ISLNK(metadata.st_mode):
            candidate.resolve(strict=True).relative_to(resolved_app)
        elif stat.S_ISREG(metadata.st_mode) and metadata.st_nlink != 1:
            raise ValueError("hardlink")
except (OSError, ValueError, plistlib.InvalidFileException):
    raise SystemExit(1)
PY
then
  print -u2 -- "build_python_helper:FAIL:native_bundle_invalid"
  print -u2 -- "build_python_helper:HINT:native_bundle_invalid:rebuild_the_helper"
  exit 1
fi
helper_app_validated_identity="$(stat -f '%d:%i' -- "$helper_app" 2>/dev/null || true)"
if ! helper_app_is_validated; then
  print -u2 -- "build_python_helper:FAIL:native_bundle_changed"
  exit 1
fi

helper_architectures="$(lipo -archs "$helper" 2>/dev/null || print unknown)"
if [[ "$helper_architectures" != "arm64" ]]; then
  print -u2 -- "PhotosIndexerWorker must be arm64-only."
  exit 1
fi

# Bind the frozen payload to the exact application sources used for this
# build. Downstream app/DMG gates recompute this value, so a healthy but stale
# PyInstaller runtime can never be mistaken for the current worker.
helper_source_fingerprint="$(
  unset PYTHONHOME PYTHONPATH
  /usr/bin/python3 "$project_root/packaging/helper_source_fingerprint.py" "$project_root" 2>/dev/null || true
)"
if [[ ! "$helper_source_fingerprint" =~ '^[0-9a-f]{64}$' ]]; then
  print -u2 -- "build_python_helper:FAIL:helper_source_fingerprint_unavailable"
  print -u2 -- "build_python_helper:HINT:helper_source_fingerprint_unavailable:verify_helper_source_inputs_then_retry"
  exit 1
fi
if ! helper_app_is_validated; then
  print -u2 -- "build_python_helper:FAIL:native_bundle_changed"
  exit 1
fi
helper_source_marker="$helper_app/Contents/Resources/.photos-indexer-source-fingerprint"
if ! (
  set -o noclobber
  umask 077
  print -r -- "$helper_source_fingerprint" > "$helper_source_marker"
); then
  print -u2 -- "build_python_helper:FAIL:helper_source_fingerprint_write"
  print -u2 -- "build_python_helper:HINT:helper_source_fingerprint_write:rebuild_the_helper_in_a_private_directory"
  exit 1
fi
chmod 600 "$helper_source_marker"
recorded_helper_source_fingerprint="$(<"$helper_source_marker")"
if [[ "$recorded_helper_source_fingerprint" != "$helper_source_fingerprint" ]]; then
  print -u2 -- "build_python_helper:FAIL:helper_source_fingerprint_invalid"
  print -u2 -- "build_python_helper:HINT:helper_source_fingerprint_invalid:rebuild_the_helper"
  exit 1
fi

# Record only runtime dependencies proven by PyInstaller's Analysis TOC. This
# deliberately excludes build tooling merely installed in the Python 3.12
# environment; the public-beta verifier reads this compact, path-free record
# from the frozen helper after the DMG is mounted.
analysis_toc="$build_root/work/PhotosIndexerWorker/Analysis-00.toc"
helper_dependency_inventory="$helper_app/Contents/Resources/.photos-indexer-dependency-inventory.json"
helper_third_party_notices="$helper_app/Contents/Resources/ThirdPartyNotices"
if [[ ! -f "$analysis_toc" || -L "$analysis_toc" ]]; then
  print -u2 -- "build_python_helper:FAIL:dependency_inventory_source_missing"
  print -u2 -- "build_python_helper:HINT:dependency_inventory_source_missing:rebuild_the_helper_with_pyinstaller_analysis"
  exit 1
fi
if ! "$python_bin" "$project_root/packaging/write_helper_dependency_inventory.py" \
  "$analysis_toc" "$helper_dependency_inventory" --notices-dir "$helper_third_party_notices"; then
  print -u2 -- "build_python_helper:FAIL:dependency_inventory_invalid"
  print -u2 -- "build_python_helper:HINT:dependency_inventory_invalid:rebuild_the_helper_with_pinned_runtime_dependencies"
  exit 1
fi
chmod 600 "$helper_dependency_inventory"

if [[ "$verify_helper" == "1" ]]; then
  # Exercise the frozen runtime from outside the checkout with no Python
  # environment inherited from the build venv.  In particular, env -i drops
  # PYTHONPATH, PYTHONHOME and VIRTUAL_ENV so a source-tree import cannot make
  # an incomplete PyInstaller bundle appear healthy.
  isolation_root="$(mktemp -d "${TMPDIR:-/tmp}/photos-indexer-helper-check.XXXXXX")"
  isolation_root_created_identity=""
  record_isolation_root_identity() {
    if [[ -d "$isolation_root" && ! -L "$isolation_root" ]]; then
      isolation_root_created_identity="$(stat -f '%d:%i' -- "$isolation_root" 2>/dev/null || true)"
    fi
  }
  isolation_root_is_owned() {
    local current_identity
    [[ -n "$isolation_root_created_identity" && -d "$isolation_root" && ! -L "$isolation_root" ]] || return 1
    current_identity="$(stat -f '%d:%i' -- "$isolation_root" 2>/dev/null || true)"
    [[ -n "$current_identity" && "$current_identity" == "$isolation_root_created_identity" ]]
  }
  record_isolation_root_identity
  cleanup_helper_check() {
    if isolation_root_is_owned; then
      rm -rf -- "$isolation_root"
    fi
  }
  cleanup_all() {
    cleanup_helper_check
    cleanup_helper_output
  }
  trap cleanup_all EXIT
  trap 'cleanup_all; exit 130' INT TERM
  if ! isolation_root_is_owned; then
    print -u2 -- "build_python_helper:FAIL:helper_check_temp_invalid"
    print -u2 -- "build_python_helper:HINT:helper_check_temp_invalid:retry_the_private_helper_check"
    exit 1
  fi
  if ! helper_app_is_validated; then
    print -u2 -- "build_python_helper:FAIL:native_bundle_changed"
    exit 1
  fi
  if (
    cd "$isolation_root"
    env -i \
      PATH="/usr/bin:/bin:/usr/sbin:/sbin" \
      HOME="$isolation_root" \
      TMPDIR="$isolation_root" \
      "$helper" --self-check </dev/null
  ); then
    :
  else
    print -u2 -- "Packaged helper failed its isolated startup check."
    exit 1
  fi
  if ! isolation_root_is_owned; then
    print -u2 -- "build_python_helper:FAIL:helper_check_temp_invalid"
    print -u2 -- "build_python_helper:HINT:helper_check_temp_invalid:retry_the_private_helper_check"
    exit 1
  fi
  cleanup_helper_check
  if [[ -e "$isolation_root" || -L "$isolation_root" ]]; then
    print -u2 -- "build_python_helper:FAIL:final_cleanup_failed"
    print -u2 -- "build_python_helper:HINT:final_cleanup_failed:remove_stale_private_helper_state_then_retry"
    exit 1
  fi
  trap - EXIT INT TERM
  isolation_root=""
else
  trap - EXIT INT TERM
fi

legacy_helper="$build_root/dist/PhotosIndexerWorker"
if [[ -e "$legacy_helper" || -L "$legacy_helper" ]]; then
  if ! helper_dist_is_owned; then
    print -u2 -- "build_python_helper:FAIL:dist_identity_changed"
    exit 1
  fi
  if [[ -L "$legacy_helper" ]]; then
    rm -f -- "$legacy_helper"
  else
    rm -rf -- "$legacy_helper"
  fi
  if [[ -e "$legacy_helper" || -L "$legacy_helper" ]]; then
    print -u2 -- "build_python_helper:FAIL:legacy_collect_cleanup_failed"
    print -u2 -- "build_python_helper:HINT:legacy_collect_cleanup_failed:remove_stale_flat_helper_output_then_retry"
    exit 1
  fi
fi

print -- "release_helper:READY_FOR_APP_BUNDLE"
