#!/bin/zsh
# Read-only diagnostic for the helper that WorkerProcess launches from an app.
# It never invokes Photos, Apple Events, the local model service, signing, or network.
set -euo pipefail

if [[ $# -ne 1 ]]; then
  print -u2 -- "embedded_helper:FAIL:invalid_arguments"
  print -u2 -- "embedded_helper:HINT:invalid_arguments:provide_one_app_bundle_path"
  exit 2
fi

raw_app_path="$1"
if [[ -L "$raw_app_path" ]]; then
  print -u2 -- "embedded_helper:FAIL:app_symlink"
  exit 1
fi
app_path="${raw_app_path:a}"
if [[ ! -d "$app_path" || "$app_path" != *.app ]]; then
  print -u2 -- "embedded_helper:FAIL:app_invalid"
  exit 1
fi

for bundle_path in \
  "$app_path/Contents" \
  "$app_path/Contents/Helpers" \
  "$app_path/Contents/Helpers/PhotosIndexerWorker.app" \
  "$app_path/Contents/Helpers/PhotosIndexerWorker.app/Contents" \
  "$app_path/Contents/Helpers/PhotosIndexerWorker.app/Contents/MacOS" \
  "$app_path/Contents/Helpers/PhotosIndexerWorker.app/Contents/Frameworks" \
  "$app_path/Contents/Helpers/PhotosIndexerWorker.app/Contents/Resources"; do
  if [[ -L "$bundle_path" ]]; then
    print -u2 -- "embedded_helper:FAIL:bundle_path_invalid"
    exit 1
  fi
done

helper_root="$app_path/Contents/Helpers/PhotosIndexerWorker.app"
helper="$helper_root/Contents/MacOS/PhotosIndexerWorker"
helper_info="$helper_root/Contents/Info.plist"
helper_source_marker="$helper_root/Contents/Resources/.photos-indexer-source-fingerprint"
legacy_helper="$app_path/Contents/Helpers/PhotosIndexerWorker/PhotosIndexerWorker"
if [[ -e "$legacy_helper" || -L "$legacy_helper" \
  || ! -d "$helper_root" || -L "$helper_root" \
  || ! -d "$helper_root/Contents/MacOS" \
  || ! -d "$helper_root/Contents/Frameworks" \
  || ! -d "$helper_root/Contents/Resources" \
  || ! -f "$helper_info" || -L "$helper_info" ]]; then
  print -u2 -- "embedded_helper:FAIL:helper_bundle_invalid"
  exit 1
fi

current_helper_source_fingerprint="$(
  unset PYTHONHOME PYTHONPATH
  /usr/bin/python3 "${0:A:h}/helper_source_fingerprint.py" "${0:A:h:h}" 2>/dev/null || true
)"
bundle_identity_state="$(/usr/bin/python3 - "$helper_info" "$helper_source_marker" "$current_helper_source_fingerprint" <<'PY'
from pathlib import Path
import os
import plistlib
import re
import stat
import sys

info_path = Path(sys.argv[1])
marker_path = Path(sys.argv[2])
expected_fingerprint = sys.argv[3]
try:
    with info_path.open("rb") as stream:
        info = plistlib.load(stream)
    expected_info = {
        "CFBundleExecutable": "PhotosIndexerWorker",
        "CFBundleIdentifier": "com.photoslocalkeywordindexer.worker",
        "CFBundlePackageType": "APPL",
        "LSUIElement": True,
    }
    if any(info.get(key) != value for key, value in expected_info.items()):
        print("identity")
    elif any(
        not isinstance(info.get(key), str) or not info[key].strip()
        for key in (
            "NSPhotoLibraryUsageDescription",
            "NSPhotoLibraryAddUsageDescription",
            "NSAppleEventsUsageDescription",
        )
    ):
        print("identity")
    elif not re.fullmatch(r"[0-9a-f]{64}", expected_fingerprint):
        print("fingerprint")
    elif marker_path.is_symlink() or not marker_path.is_file():
        print("fingerprint")
    elif stat.S_IMODE(os.lstat(marker_path).st_mode) != 0o600:
        print("fingerprint")
    elif marker_path.read_text(encoding="utf-8").strip() != expected_fingerprint:
        print("fingerprint")
    else:
        print("ok")
except (OSError, ValueError, plistlib.InvalidFileException):
    print("identity")
PY
)"
case "$bundle_identity_state" in
  ok) ;;
  fingerprint)
    print -u2 -- "embedded_helper:FAIL:fingerprint"
    exit 1
    ;;
  *)
    print -u2 -- "embedded_helper:FAIL:bundle_identity"
    exit 1
    ;;
esac

payload_integrity_state="$(/usr/bin/python3 - "$helper_root" <<'PY'
import os
from pathlib import Path
import stat
import sys

root = Path(sys.argv[1])
try:
    for candidate in (root, *root.rglob("*")):
        metadata = os.lstat(candidate)
        if metadata.st_uid != os.getuid():
            print("ownership")
            break
        if not stat.S_ISLNK(metadata.st_mode) and metadata.st_mode & 0o022:
            print("permissions")
            break
        if stat.S_ISREG(metadata.st_mode) and metadata.st_nlink != 1:
            print("hardlink")
            break
    else:
        print("ok")
except OSError:
    print("scan")
PY
)"
case "$payload_integrity_state" in
  ok) ;;
  ownership) print -u2 -- "embedded_helper:FAIL:payload_ownership"; exit 1 ;;
  permissions) print -u2 -- "embedded_helper:FAIL:payload_permissions"; exit 1 ;;
  hardlink) print -u2 -- "embedded_helper:FAIL:payload_hardlink"; exit 1 ;;
  *) print -u2 -- "embedded_helper:FAIL:payload_scan_failed"; exit 1 ;;
esac

helper_root_real="${helper_root:A}"
payload_paths=""
if ! payload_paths="$(find "$helper_root" -type l -print 2>/dev/null)"; then
  print -u2 -- "embedded_helper:FAIL:payload_scan_failed"
  exit 1
fi
while IFS= read -r payload_path; do
  [[ -n "$payload_path" ]] || continue
  payload_target="$(readlink "$payload_path")"
  if [[ "$payload_target" != /* ]]; then
    payload_target="${payload_path:h}/$payload_target"
  fi
  payload_target="${payload_target:A}"
  case "$payload_target" in
    "$helper_root_real"/*) ;;
    *)
      print -u2 -- "embedded_helper:FAIL:payload_symlink"
      exit 1
      ;;
  esac
done <<< "$payload_paths"

if [[ ! -f "$helper" || -L "$helper" || ! -x "$helper" ]]; then
  print -u2 -- "embedded_helper:FAIL:helper_binary_invalid"
  exit 1
fi

if ! command -v lipo >/dev/null 2>&1; then
  print -u2 -- "embedded_helper:FAIL:lipo_missing"
  exit 1
fi
if ! command -v file >/dev/null 2>&1; then
  print -u2 -- "embedded_helper:FAIL:file_missing"
  exit 1
fi
helper_architectures="$(lipo -archs "$helper" 2>/dev/null || print unknown)"
if [[ "$helper_architectures" != "arm64" ]]; then
  print -u2 -- "embedded_helper:FAIL:architecture_not_arm64"
  exit 1
fi

# PyInstaller's launcher can be arm64 while a collected extension or library
# is not. Reject the complete frozen payload here, before it is copied into an
# app or reaches the signing pipeline.
payload_regular_paths=""
if ! payload_regular_paths="$(find "$helper_root" -type f -print 2>/dev/null)"; then
  print -u2 -- "embedded_helper:FAIL:payload_architecture_scan_failed"
  exit 1
fi
while IFS= read -r payload_path; do
  [[ -n "$payload_path" ]] || continue
  if [[ -f "$payload_path" && ! -L "$payload_path" ]]; then
    if ! payload_kind="$(file -b "$payload_path" 2>/dev/null)"; then
      print -u2 -- "embedded_helper:FAIL:payload_architecture_scan_failed"
      exit 1
    fi
  fi
  if [[ "${payload_kind:-}" == *"Mach-O"* ]]; then
    payload_architectures="$(lipo -archs "$payload_path" 2>/dev/null || print unknown)"
    if [[ "$payload_architectures" != "arm64" ]]; then
      print -u2 -- "embedded_helper:FAIL:nested_architecture"
      exit 1
    fi
  fi
  payload_kind=""
done <<< "$payload_regular_paths"

diagnostic_root="$(mktemp -d "${TMPDIR:-/tmp}/photos-indexer-app-check.XXXXXX")"
diagnostic_root_created_identity=""
record_diagnostic_root_identity() {
  if [[ -d "$diagnostic_root" && ! -L "$diagnostic_root" ]]; then
    diagnostic_root_created_identity="$(stat -f '%d:%i' -- "$diagnostic_root" 2>/dev/null || true)"
  fi
}
diagnostic_root_is_owned() {
  local current_identity
  [[ -n "$diagnostic_root_created_identity" && -d "$diagnostic_root" && ! -L "$diagnostic_root" ]] || return 1
  current_identity="$(stat -f '%d:%i' -- "$diagnostic_root" 2>/dev/null || true)"
  [[ -n "$current_identity" && "$current_identity" == "$diagnostic_root_created_identity" ]]
}
record_diagnostic_root_identity
cleanup_failed=0
cleanup_diagnostic() {
  if diagnostic_root_is_owned; then
    rm -rf -- "$diagnostic_root" >/dev/null 2>&1 || cleanup_failed=1
  fi
}
trap cleanup_diagnostic EXIT
trap 'exit 130' INT TERM

if ! diagnostic_root_is_owned; then
  print -u2 -- "embedded_helper:FAIL:diagnostic_temp_invalid"
  exit 1
fi

check_output=""
if ! check_output="$(
  cd "$diagnostic_root"
  env -i \
    PATH="/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin" \
    HOME="$diagnostic_root" \
    TMPDIR="$diagnostic_root" \
    "$helper" --self-check 2>/dev/null
)"; then
  print -u2 -- "embedded_helper:FAIL:self_check_failed"
  exit 1
fi
if ! diagnostic_root_is_owned; then
  print -u2 -- "embedded_helper:FAIL:diagnostic_temp_invalid"
  exit 1
fi
if [[ "$check_output" != '{"status":"ok","runtime":"embedded","architecture":"arm64","protocol":"jsonl"}' ]]; then
  print -u2 -- "embedded_helper:FAIL:self_check_invalid"
  exit 1
fi

cleanup_diagnostic
if (( cleanup_failed == 1 )) || [[ -e "$diagnostic_root" || -L "$diagnostic_root" ]]; then
  print -u2 -- "embedded_helper:FAIL:final_cleanup_failed"
  print -u2 -- "embedded_helper:HINT:final_cleanup_failed:remove_stale_private_diagnostic_state_then_retry"
  exit 1
fi
trap - EXIT INT TERM

print -- "embedded_helper:PASS:path"
print -- "embedded_helper:PASS:bundle_identity"
print -- "embedded_helper:PASS:fingerprint"
print -- "embedded_helper:PASS:architecture"
print -- "embedded_helper:PASS:runtime"
# This read-only diagnostic deliberately does not query either permission
# surface. The signed smoke test is the only authoritative TCC confirmation.
print -- "embedded_helper:PASS:photos_tcc:NOT_CHECKED"
print -- "embedded_helper:PASS:automation_tcc:NOT_CHECKED"
print -- "embedded_helper:READY"
