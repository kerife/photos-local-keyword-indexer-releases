#!/bin/zsh
# Read-only release prerequisite check. This script never signs, uploads, or
# changes Ollama/Photos state; it only reports which gates are available.
set -u

project_root="${0:A:h:h}"
python_bin="${PYTHON_BIN:-python3.12}"
source "$project_root/packaging/build_path_guard.zsh"
failed=0
next_code=""
next_action_action=""
json_mode=0
typeset -a json_checks=()
typeset -a json_hints=()
helper_check_root=""
helper_check_root_created_identity=""
cleanup_failed=0

if (( $# > 0 )); then
  if [[ $# -eq 1 && "$1" == "--json" ]]; then
    json_mode=1
  else
    print -u2 -- "release_preflight:FAIL:invalid_arguments"
    print -u2 -- "release_preflight:HINT:invalid_arguments:use_no_arguments_or_json"
    exit 2
  fi
fi

cleanup_helper_check() {
  if helper_check_root_is_owned; then
    rm -rf -- "$helper_check_root" >/dev/null 2>&1 || cleanup_failed=1
  fi
}
record_helper_check_root_identity() {
  if [[ -d "$helper_check_root" && ! -L "$helper_check_root" ]]; then
    helper_check_root_created_identity="$(stat -f '%d:%i' -- "$helper_check_root" 2>/dev/null || true)"
  fi
}
helper_check_root_is_owned() {
  local current_identity
  [[ -n "$helper_check_root_created_identity" && -d "$helper_check_root" && ! -L "$helper_check_root" ]] || return 1
  current_identity="$(stat -f '%d:%i' -- "$helper_check_root" 2>/dev/null || true)"
  [[ -n "$current_identity" && "$current_identity" == "$helper_check_root_created_identity" ]]
}
trap cleanup_helper_check EXIT
trap 'cleanup_helper_check; exit 130' INT TERM

hint_action() {
  local code="$1"
  case "$code" in
    "darwin_required"|"arm64_required") print -- "run_on_apple_silicon_macos_release_host" ;;
    "xcodebuild_missing"|"xcode_select_missing"|"swift_missing"|"swift_toolchain_unavailable"|"xcode_toolchain_unavailable"|"xcode_command_line_tools_active") print -- "install_full_xcode_and_select_developer_directory" ;;
    "xcode_full_installation_missing") print -- "install_full_xcode" ;;
    "xcode_full_installation_not_selected") print -- "select_installed_xcode" ;;
    "python3.12_missing"|"python3.12_wrong_version"|"python3.12_wrong_architecture") print -- "install_arm64_python312_and_set_python_bin" ;;
    "pyinstaller_missing") print -- "install_build_dependencies_with_python312" ;;
    "release_metadata_missing"|"release_metadata_invalid") print -- "set_matching_app_version_and_positive_build_number" ;;
    "helper_missing") print -- "run_verify_helper_1_build_python_helper" ;;
    "helper_path_invalid") print -- "rebuild_helper_inside_build_root" ;;
    "helper_payload_symlink"|"helper_payload_scan_failed") print -- "rebuild_helper_without_payload_symlinks_and_retry" ;;
    "helper_payload_permissions"|"helper_payload_ownership"|"helper_payload_hardlink"|"helper_payload_type_invalid"|"helper_payload_integrity_unavailable") print -- "rebuild_helper_with_verify_helper_1" ;;
    "helper_binary_invalid") print -- "rebuild_helper_with_verify_helper_1" ;;
    "helper_runtime_invalid") print -- "rebuild_helper_with_verify_helper_1" ;;
    "helper_source_fingerprint_missing"|"helper_source_fingerprint_stale"|"helper_source_fingerprint_unavailable") print -- "rebuild_helper_with_verify_helper_1" ;;
    "helper_wrong_architecture"|"helper_architecture_uncheckable") print -- "rebuild_helper_with_arm64_python312_and_lipo" ;;
    "helper_check_cleanup_failed") print -- "remove_stale_private_helper_check_then_retry" ;;
    "developer_id_missing"|"developer_id_identity_missing"|"developer_id_identity_unavailable") print -- "install_or_select_a_developer_id_application_identity" ;;
    "notarytool_missing") print -- "install_xcode_command_line_tools_then_retry" ;;
    "system_python3_missing") print -- "install_xcode_command_line_tools_then_retry" ;;
    "notary_profile_missing") print -- "configure_with_xcrun_notarytool_store_credentials_then_retry" ;;
    "notary_profile_unavailable") print -- "verify_network_and_recreate_notary_profile_then_retry" ;;
    "sparkle_lock_invalid") print -- "restore_the_pinned_sparkle_lock_and_retry" ;;
    "sparkle_update_configuration_missing") print -- "configure_sparkle_feed_and_ed25519_key_then_retry" ;;
    "sparkle_update_configuration_invalid") print -- "replace_sparkle_feed_or_ed25519_key_then_retry" ;;
    "sips_missing") print -- "run_on_macos_release_host_with_sips" ;;
    *) print -- "review_release_preflight" ;;
  esac
}

emit_hint() {
  local code="$1"
  if (( json_mode == 1 )); then
    json_hints+=("$code|$(hint_action "$code")")
    return
  fi
  case "$code" in
    "darwin_required"|"arm64_required")
      print -- "release_preflight:HINT:${code}:run_on_apple_silicon_macos_release_host"
      ;;
    "xcodebuild_missing"|"xcode_select_missing"|"swift_missing"|"swift_toolchain_unavailable"|"xcode_toolchain_unavailable"|"xcode_command_line_tools_active")
      print -- "release_preflight:HINT:${code}:install_full_xcode_and_select_developer_directory"
      ;;
    "xcode_full_installation_missing")
      print -- "release_preflight:HINT:${code}:install_full_xcode"
      ;;
    "xcode_full_installation_not_selected")
      print -- "release_preflight:HINT:${code}:select_installed_xcode"
      ;;
    "python3.12_missing"|"python3.12_wrong_version"|"python3.12_wrong_architecture")
      print -- "release_preflight:HINT:${code}:install_arm64_python312_and_set_PYTHON_BIN"
      ;;
    "pyinstaller_missing")
      print -- "release_preflight:HINT:${code}:install_build_dependencies_with_python312"
      ;;
    "release_metadata_missing"|"release_metadata_invalid")
      print -- "release_preflight:HINT:${code}:set_matching_app_version_and_positive_build_number"
      ;;
    "helper_missing")
      print -- "release_preflight:HINT:${code}:run_verify_helper_1_build_python_helper"
      ;;
    "helper_path_invalid")
      print -- "release_preflight:HINT:${code}:rebuild_helper_inside_build_root"
      ;;
    "helper_payload_symlink"|"helper_payload_scan_failed")
      print -- "release_preflight:HINT:${code}:rebuild_helper_without_payload_symlinks_and_retry"
      ;;
    "helper_payload_permissions"|"helper_payload_ownership"|"helper_payload_hardlink"|"helper_payload_type_invalid"|"helper_payload_integrity_unavailable")
      print -- "release_preflight:HINT:${code}:rebuild_helper_with_verify_helper_1"
      ;;
    "helper_binary_invalid")
      print -- "release_preflight:HINT:${code}:rebuild_helper_with_verify_helper_1"
      ;;
    "helper_runtime_invalid")
      print -- "release_preflight:HINT:${code}:rebuild_helper_with_verify_helper_1"
      ;;
    "helper_source_fingerprint_missing"|"helper_source_fingerprint_stale"|"helper_source_fingerprint_unavailable")
      print -- "release_preflight:HINT:${code}:rebuild_helper_with_verify_helper_1"
      ;;
    "helper_wrong_architecture"|"helper_architecture_uncheckable")
      print -- "release_preflight:HINT:${code}:rebuild_helper_with_arm64_python312_and_lipo"
      ;;
    "helper_check_cleanup_failed")
      print -- "release_preflight:HINT:${code}:remove_stale_private_helper_check_then_retry"
      ;;
    "developer_id_missing"|"developer_id_identity_missing"|"developer_id_identity_unavailable")
      print -- "release_preflight:HINT:${code}:install_or_select_a_developer_id_application_identity"
      ;;
    "notarytool_missing")
      print -- "release_preflight:HINT:${code}:install_xcode_command_line_tools_then_retry"
      ;;
    "system_python3_missing")
      print -- "release_preflight:HINT:${code}:install_xcode_command_line_tools_then_retry"
      ;;
    "notary_profile_missing")
      print -- "release_preflight:HINT:notary_profile_missing:configure_with_xcrun_notarytool_store_credentials_then_retry"
      ;;
    "notary_profile_unavailable")
      print -- "release_preflight:HINT:notary_profile_unavailable:verify_network_and_recreate_notary_profile_then_retry"
      ;;
    "sparkle_lock_invalid")
      print -- "release_preflight:HINT:${code}:restore_the_pinned_sparkle_lock_and_retry"
      ;;
    "sparkle_update_configuration_missing")
      print -- "release_preflight:HINT:${code}:configure_sparkle_feed_and_ed25519_key_then_retry"
      ;;
    "sparkle_update_configuration_invalid")
      print -- "release_preflight:HINT:${code}:replace_sparkle_feed_or_ed25519_key_then_retry"
      ;;
    "sips_missing")
      print -- "release_preflight:HINT:${code}:run_on_macos_release_host_with_sips"
      ;;
    *)
      print -- "release_preflight:HINT:${code}:review_release_preflight"
      ;;
  esac
}

report() {
  local state="$1"
  local code="$2"
  if (( json_mode == 1 )); then
    json_checks+=("$state|$code")
  else
    print -- "release_preflight:${state}:${code}"
  fi
  if [[ "$state" != "PASS" ]]; then
    failed=1
    if [[ -z "$next_code" ]]; then
      next_code="$code"
      next_action_action="$(hint_action "$code")"
    fi
    emit_hint "$code"
  fi
}

check_command() {
  local code="$1"
  local command_name="$2"
  if command -v "$command_name" >/dev/null 2>&1; then
    report PASS "$code"
  else
    report FAIL "${code}_missing"
  fi
}

if [[ "$(uname -s)" == "Darwin" ]]; then
  report PASS darwin
else
  report FAIL darwin_required
fi

if [[ "$(uname -m)" == "arm64" ]]; then
  report PASS arm64
else
  report FAIL arm64_required
fi

check_command xcodebuild xcodebuild
check_command xcode_select xcode-select
check_command swift swift
check_command plutil plutil
check_command sips /usr/bin/sips
check_command lipo lipo
check_command file file
check_command readlink readlink
check_command system_python3 /usr/bin/python3
selected_developer_dir="${DEVELOPER_DIR:-}"
if [[ -z "$selected_developer_dir" ]] && command -v xcode-select >/dev/null 2>&1; then
  selected_developer_dir="$(xcode-select -p 2>/dev/null || print unknown)"
fi
if [[ "$selected_developer_dir" == "/Library/Developer/CommandLineTools" ]]; then
  if [[ -d /Applications/Xcode.app && ! -L /Applications/Xcode.app ]]; then
    report FAIL xcode_full_installation_not_selected
  else
    report FAIL xcode_full_installation_missing
  fi
  report FAIL xcode_command_line_tools_active
elif command -v xcodebuild >/dev/null 2>&1 && xcodebuild -version >/dev/null 2>&1; then
  report PASS xcode_toolchain
else
  report FAIL xcode_toolchain_unavailable
fi
swift_toolchain_bin=""
if command -v xcrun >/dev/null 2>&1; then
  if [[ -n "$selected_developer_dir" ]]; then
    swift_toolchain_bin="$(DEVELOPER_DIR="$selected_developer_dir" xcrun --find swift 2>/dev/null || true)"
  else
    swift_toolchain_bin="$(xcrun --find swift 2>/dev/null || true)"
  fi
fi
if [[ -n "$swift_toolchain_bin" && -x "$swift_toolchain_bin" ]]; then
  report PASS swift_toolchain
else
  report FAIL swift_toolchain_unavailable
fi
if [[ -x /usr/libexec/PlistBuddy ]]; then
  report PASS plistbuddy
else
  report FAIL plistbuddy_missing
fi

if command -v "$python_bin" >/dev/null 2>&1; then
  python_version="$($python_bin -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || print unknown)"
  if [[ "$python_version" == "3.12" ]]; then
    report PASS python3.12
    python_architecture="$($python_bin -c 'import platform; print(platform.machine())' 2>/dev/null || print unknown)"
    if [[ "$python_architecture" == "arm64" ]]; then
      report PASS python3.12_architecture
    else
      report FAIL python3.12_wrong_architecture
    fi
    if "$python_bin" -c 'import PyInstaller' >/dev/null 2>&1; then
      report PASS pyinstaller
    else
      report FAIL pyinstaller_missing
    fi
  else
    report FAIL "python3.12_wrong_version"
  fi
else
  report FAIL python3.12_missing
fi

app_version="${APP_VERSION:-}"
build_number="${BUILD_NUMBER:-}"
project_version="$(sed -n 's/^version = "\([^"]*\)"/\1/p' "$project_root/pyproject.toml" | head -1)"
release_metadata_valid=0
if [[ -z "$app_version" || -z "$build_number" ]]; then
  report FAIL release_metadata_missing
elif [[ "$app_version" =~ '^[0-9]+\.[0-9]+\.[0-9]+$' \
  && "$build_number" =~ '^[1-9][0-9]*$' \
  && -n "$project_version" \
  && "$app_version" == "$project_version" ]]; then
  report PASS release_metadata
  release_metadata_valid=1
else
  report FAIL release_metadata_invalid
fi

helper_app="$project_root/build/python-helper/dist/PhotosIndexerWorker.app"
helper="$helper_app/Contents/MacOS/PhotosIndexerWorker"
helper_info="$helper_app/Contents/Info.plist"
helper_frameworks="$helper_app/Contents/Frameworks"
helper_resources="$helper_app/Contents/Resources"
legacy_helper="$project_root/build/python-helper/dist/PhotosIndexerWorker/PhotosIndexerWorker"
helper_path_valid=1
helper_payload_safe=1
if ! validate_build_path "$helper_app" "$project_root/build" "helper source" >/dev/null 2>&1; then
  report FAIL helper_path_invalid
  helper_path_valid=0
fi
if (( helper_path_valid == 1 )) && [[ -d "$helper_app" && ! -L "$helper_app" ]]; then
  if [[ -e "$legacy_helper" || -L "$legacy_helper" \
    || ! -f "$helper_info" || -L "$helper_info" \
    || ! -d "$helper_frameworks" || -L "$helper_frameworks" \
    || ! -d "$helper_resources" || -L "$helper_resources" ]]; then
    report FAIL helper_binary_invalid
    helper_path_valid=0
  elif [[ ! -x /usr/bin/python3 ]]; then
    report FAIL helper_payload_integrity_unavailable
    helper_payload_safe=0
  elif ! /usr/bin/python3 - "$helper_info" <<'PY'
import plistlib
import sys

with open(sys.argv[1], "rb") as stream:
    info = plistlib.load(stream)
expected = {
    "CFBundleExecutable": "PhotosIndexerWorker",
    "CFBundleIdentifier": "com.photoslocalkeywordindexer.worker",
    "CFBundlePackageType": "APPL",
    "LSUIElement": True,
}
if not isinstance(info, dict) or any(info.get(key) != value for key, value in expected.items()):
    raise SystemExit(1)
for key in (
    "NSPhotoLibraryUsageDescription",
    "NSPhotoLibraryAddUsageDescription",
    "NSAppleEventsUsageDescription",
):
    if not isinstance(info.get(key), str) or not info[key].strip():
        raise SystemExit(1)
PY
  then
    report FAIL helper_binary_invalid
    helper_path_valid=0
  elif (( release_metadata_valid == 1 )) && ! /usr/bin/python3 - "$helper_info" "$app_version" "$build_number" <<'PY'
import plistlib
import sys

with open(sys.argv[1], "rb") as stream:
    info = plistlib.load(stream)
if not isinstance(info, dict):
    raise SystemExit(1)
if info.get("CFBundleShortVersionString") != sys.argv[2]:
    raise SystemExit(1)
if info.get("CFBundleVersion") != sys.argv[3]:
    raise SystemExit(1)
PY
  then
    report FAIL helper_release_metadata_invalid
    helper_path_valid=0
  fi
fi
if (( helper_path_valid == 1 )) && [[ -d "$helper_app" && ! -L "$helper_app" ]]; then
  helper_payload_scan_status=0
  helper_payload_symlinks="$(find "$helper_app" -type l -print 2>/dev/null)" || helper_payload_scan_status=$?
  if (( helper_payload_scan_status != 0 )); then
    report FAIL helper_payload_scan_failed
    helper_path_valid=0
  elif [[ -n "$helper_payload_symlinks" ]]; then
    helper_root_real="${helper_app:A}"
    helper_payload_invalid=0
    while IFS= read -r helper_payload_path; do
      [[ -n "$helper_payload_path" ]] || continue
      helper_payload_target="$(readlink "$helper_payload_path" 2>/dev/null || true)"
      if [[ -z "$helper_payload_target" ]]; then
        helper_payload_invalid=1
        break
      fi
      if [[ "$helper_payload_target" != /* ]]; then
        helper_payload_target="${helper_payload_path:h}/$helper_payload_target"
      fi
      helper_payload_target="${helper_payload_target:A}"
      case "$helper_payload_target" in
        "$helper_root_real"/*) ;;
        *) helper_payload_invalid=1; break ;;
      esac
    done <<< "$helper_payload_symlinks"
    if (( helper_payload_invalid == 1 )); then
      report FAIL helper_payload_symlink
      helper_path_valid=0
    fi
  fi
fi
if (( helper_path_valid == 1 )) && [[ -d "$helper_app" && ! -L "$helper_app" ]]; then
  if [[ ! -x /usr/bin/python3 ]]; then
    report FAIL helper_payload_integrity_unavailable
    helper_payload_safe=0
  else
    helper_payload_integrity="$(/usr/bin/python3 - "$helper_app" <<'PY'
import os
import stat
import sys

root = sys.argv[1]
try:
    candidates = [root]
    for directory, directories, files in os.walk(root, followlinks=False):
        candidates.extend(os.path.join(directory, name) for name in directories)
        candidates.extend(os.path.join(directory, name) for name in files)
    for candidate in candidates:
        metadata = os.stat(candidate, follow_symlinks=False)
        if metadata.st_uid != os.getuid():
            print("ownership")
            raise SystemExit(0)
        if stat.S_ISREG(metadata.st_mode) and metadata.st_nlink != 1:
            print("hardlink")
            raise SystemExit(0)
        if not stat.S_ISLNK(metadata.st_mode) and metadata.st_mode & 0o022:
            print("permissions")
            raise SystemExit(0)
        if not any(
            predicate(metadata.st_mode)
            for predicate in (stat.S_ISREG, stat.S_ISDIR, stat.S_ISLNK)
        ):
            print("type_invalid")
            raise SystemExit(0)
except (OSError, ValueError):
    print("unavailable")
    raise SystemExit(0)
print("ok")
PY
)" || helper_payload_integrity="unavailable"
    case "$helper_payload_integrity" in
      ok) ;;
      permissions) report FAIL helper_payload_permissions; helper_payload_safe=0 ;;
      ownership) report FAIL helper_payload_ownership; helper_payload_safe=0 ;;
      hardlink) report FAIL helper_payload_hardlink; helper_payload_safe=0 ;;
      type_invalid) report FAIL helper_payload_type_invalid; helper_payload_safe=0 ;;
      *) report FAIL helper_payload_integrity_unavailable; helper_payload_safe=0 ;;
    esac
  fi
fi
if (( helper_path_valid == 1 && helper_payload_safe == 1 )) && [[ -d "$helper_app" && ! -L "$helper_app" ]]; then
  helper_source_marker="$helper_app/Contents/Resources/.photos-indexer-source-fingerprint"
  current_helper_source_fingerprint=""
  if [[ ! -x /usr/bin/python3 ]]; then
    report FAIL helper_source_fingerprint_unavailable
  elif [[ ! -f "$helper_source_marker" || -L "$helper_source_marker" ]]; then
    report FAIL helper_source_fingerprint_missing
  else
    current_helper_source_fingerprint="$(
      unset PYTHONHOME PYTHONPATH
      /usr/bin/python3 "$project_root/packaging/helper_source_fingerprint.py" "$project_root" 2>/dev/null || true
    )"
    recorded_helper_source_fingerprint="$(<"$helper_source_marker")"
    if [[ ! "$current_helper_source_fingerprint" =~ '^[0-9a-f]{64}$' ]]; then
      report FAIL helper_source_fingerprint_unavailable
    elif [[ ! "$recorded_helper_source_fingerprint" =~ '^[0-9a-f]{64}$' \
      || "$recorded_helper_source_fingerprint" != "$current_helper_source_fingerprint" ]]; then
      report FAIL helper_source_fingerprint_stale
    else
      report PASS helper_source_fingerprint
    fi
  fi
fi
if (( helper_path_valid == 1 && helper_payload_safe == 1 )) && [[ -d "$helper_app" && ! -L "$helper_app" && -x "$helper" && ! -L "$helper" ]]; then
  report PASS helper
  if command -v lipo >/dev/null 2>&1; then
    helper_architectures="$(lipo -archs "$helper" 2>/dev/null || print unknown)"
    if [[ "$helper_architectures" == "arm64" ]]; then
      report PASS helper_architecture
      if helper_check_root="$(mktemp -d "${TMPDIR:-/tmp}/photos-indexer-preflight.XXXXXX")"; then
        record_helper_check_root_identity
        if helper_check_root_is_owned; then
          helper_runtime="$(
            env -i \
              PATH="/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin" \
              HOME="$helper_check_root" \
              TMPDIR="$helper_check_root" \
              "$helper" --self-check 2>/dev/null || print invalid
          )"
          if ! helper_check_root_is_owned; then
            helper_runtime="invalid"
          fi
        else
          helper_runtime="invalid"
        fi
      else
        helper_runtime="invalid"
      fi
      if [[ "$helper_runtime" == '{"status":"ok","runtime":"embedded","architecture":"arm64","protocol":"jsonl"}' ]]; then
        report PASS helper_runtime
      else
        report FAIL helper_runtime_invalid
      fi
    else
      report FAIL helper_wrong_architecture
    fi
  else
    report FAIL helper_architecture_uncheckable
  fi
elif (( helper_path_valid == 1 && helper_payload_safe == 1 )) && [[ -d "$helper_app" && ! -L "$helper_app" ]]; then
  report FAIL helper_binary_invalid
elif (( helper_path_valid == 1 && helper_payload_safe == 0 )); then
  :
else
  report FAIL helper_missing
fi

developer_identity="${DEVELOPER_ID_APPLICATION:-}"
if ! command -v security >/dev/null 2>&1; then
  report FAIL developer_id_missing
elif [[ -z "$developer_identity" ]]; then
  report FAIL developer_id_identity_missing
elif security find-identity -v -p codesigning 2>/dev/null | grep -Fq "\"$developer_identity\""; then
  report PASS developer_id
else
  report FAIL developer_id_identity_unavailable
fi

check_command codesign codesign
check_command spctl spctl
check_command hdiutil hdiutil
check_command xcrun xcrun

if command -v xcrun >/dev/null 2>&1 && xcrun --find notarytool >/dev/null 2>&1; then
  report PASS notarytool
else
  report FAIL notarytool_missing
fi

notary_profile="${APPLE_NOTARY_PROFILE:-}"
if [[ -z "$notary_profile" ]]; then
  report FAIL notary_profile_missing
elif command -v xcrun >/dev/null 2>&1 \
  && xcrun --find notarytool >/dev/null 2>&1 \
  && xcrun notarytool history --keychain-profile "$notary_profile" >/dev/null 2>&1; then
  report PASS notary_profile
else
  report FAIL notary_profile_unavailable
fi

resolved="${SPARKLE_LOCK_PATH:-$project_root/app/Package.resolved}"
if [[ -f "$resolved" && ! -L "$resolved" ]] \
  && /usr/bin/python3 - "$resolved" <<'PY'
import json
import os
import sys

path = sys.argv[1]
canonical_location = "https://github.com/sparkle-project/sparkle.git"
expected_version = "2.9.2"
expected_revision = "6276ba2b404829d139c45ff98427cf90e2efc59b"

try:
    if os.path.islink(path) or os.path.getsize(path) > 1024 * 1024:
        raise ValueError("lockfile is not a regular bounded file")
    with open(path, "rb") as stream:
        document = json.load(stream)
except (OSError, ValueError, TypeError):
    raise SystemExit(1)

pins = document.get("pins") if isinstance(document, dict) else None
if pins is None and isinstance(document, dict):
    legacy = document.get("object")
    pins = legacy.get("pins") if isinstance(legacy, dict) else None
if not isinstance(pins, list):
    raise SystemExit(1)

canonical_count = 0
matching_count = 0
invalid_origin = False
invalid_identity = False
for pin in pins:
    if not isinstance(pin, dict):
        continue
    identity = str(pin.get("identity", "")).casefold()
    location = str(pin.get("location", "")).casefold().rstrip("/")
    if identity != "sparkle":
        if location == canonical_location:
            invalid_identity = True
        continue
    if location != canonical_location:
        invalid_origin = True
        continue
    canonical_count += 1
    state = pin.get("state")
    if (
        isinstance(state, dict)
        and state.get("version") == expected_version
        and state.get("revision") == expected_revision
    ):
        matching_count += 1

if invalid_origin or invalid_identity or canonical_count != 1 or matching_count != 1:
    raise SystemExit(1)
PY
then
  report PASS sparkle_lock
else
  report FAIL sparkle_lock_invalid
fi

sparkle_feed="${SPARKLE_FEED_URL:-}"
sparkle_public_key="${SPARKLE_PUBLIC_ED_KEY:-}"
if [[ -z "$sparkle_feed" || -z "$sparkle_public_key" ]]; then
  report FAIL sparkle_update_configuration_missing
elif /usr/bin/python3 <<'PY'
import base64
import binascii
import os
from urllib.parse import urlsplit

feed = os.environ.get("SPARKLE_FEED_URL", "")
public_key = os.environ.get("SPARKLE_PUBLIC_ED_KEY", "")
try:
    parsed = urlsplit(feed)
except ValueError:
    raise SystemExit(1)
hostname = parsed.hostname or ""
placeholder_domains = ("example.invalid", "example.org", "example.com", "example.net")
hostname = hostname.casefold().rstrip(".")
try:
    port = parsed.port
except ValueError:
    raise SystemExit(1)
if (
    parsed.scheme != "https"
    or not parsed.netloc
    or not hostname
    or parsed.username is not None
    or parsed.password is not None
    or "@" in feed
    or (port is not None and not 1 <= port <= 65535)
    or any(hostname == domain or hostname.endswith(f".{domain}") for domain in placeholder_domains)
    or any(character in feed for character in (';', '"', "'", "\\"))
    or any(character.isspace() for character in feed + public_key)
    or "REPLACE_WITH" in public_key
):
    raise SystemExit(1)
try:
    decoded_key = base64.b64decode(public_key, validate=True)
except (ValueError, binascii.Error):
    raise SystemExit(1)
if len(decoded_key) != 32 or base64.b64encode(decoded_key).decode("ascii") != public_key:
    raise SystemExit(1)
PY
then
  report PASS sparkle_update_configuration
else
  report FAIL sparkle_update_configuration_invalid
fi

cleanup_helper_check
if (( cleanup_failed == 1 )) || { [[ -n "$helper_check_root" ]] && [[ -e "$helper_check_root" || -L "$helper_check_root" ]]; }; then
  report FAIL helper_check_cleanup_failed
else
  trap - EXIT INT TERM
fi

if (( failed == 0 )); then
  if (( json_mode == 1 )); then
    /usr/bin/python3 - "$failed" "run_build_python_helper" "run_build_python_helper" "${json_checks[@]}" -- "${json_hints[@]}" <<'PY'
import json
import sys

failed = int(sys.argv[1])
next_action = sys.argv[2]
next_action_action = sys.argv[3]
separator = sys.argv.index("--", 4)
checks = []
for item in sys.argv[4:separator]:
    state, code = item.split("|", 1)
    checks.append({"state": state, "code": code.upper().replace(".", "_")})
hints = []
for item in sys.argv[separator + 1:]:
    code, action = item.split("|", 1)
    hints.append({"code": code.upper().replace(".", "_"), "action": action})
section_codes = {
    "toolchain": {
        "DARWIN", "DARWIN_REQUIRED", "ARM64", "ARM64_REQUIRED", "XCODEBUILD", "XCODEBUILD_MISSING",
        "XCODE_SELECT", "XCODE_SELECT_MISSING", "SWIFT", "SWIFT_MISSING", "PLUTIL", "PLUTIL_MISSING", "SIPS", "SIPS_MISSING",
        "LIPO", "LIPO_MISSING", "FILE", "FILE_MISSING", "READLINK", "READLINK_MISSING",
        "XCODE_TOOLCHAIN", "XCODE_TOOLCHAIN_UNAVAILABLE", "XCODE_COMMAND_LINE_TOOLS_ACTIVE",
        "SWIFT_TOOLCHAIN", "SWIFT_TOOLCHAIN_UNAVAILABLE",
        "XCODE_FULL_INSTALLATION_MISSING", "XCODE_FULL_INSTALLATION_NOT_SELECTED",
        "PLISTBUDDY", "PLISTBUDDY_MISSING", "PYTHON3_12", "PYTHON3_12_ARCHITECTURE", "PYINSTALLER",
        "SYSTEM_PYTHON3", "SYSTEM_PYTHON3_MISSING",
        "PYTHON3_12_MISSING", "PYTHON3_12_WRONG_VERSION", "PYTHON3_12_WRONG_ARCHITECTURE", "PYINSTALLER_MISSING",
        "RELEASE_METADATA", "RELEASE_METADATA_MISSING", "RELEASE_METADATA_INVALID",
        "HELPER", "HELPER_ARCHITECTURE", "HELPER_RUNTIME", "HELPER_MISSING",
        "HELPER_PATH_INVALID", "HELPER_PAYLOAD_SYMLINK", "HELPER_PAYLOAD_SCAN_FAILED",
        "HELPER_PAYLOAD_PERMISSIONS", "HELPER_PAYLOAD_OWNERSHIP", "HELPER_PAYLOAD_HARDLINK",
        "HELPER_PAYLOAD_TYPE_INVALID", "HELPER_PAYLOAD_INTEGRITY_UNAVAILABLE",
        "HELPER_BINARY_INVALID", "HELPER_WRONG_ARCHITECTURE", "HELPER_ARCHITECTURE_UNCHECKABLE",
        "HELPER_RUNTIME_INVALID", "HELPER_CHECK_CLEANUP_FAILED",
        "HELPER_SOURCE_FINGERPRINT", "HELPER_SOURCE_FINGERPRINT_MISSING",
        "HELPER_SOURCE_FINGERPRINT_STALE", "HELPER_SOURCE_FINGERPRINT_UNAVAILABLE",
    },
    "signing": {"DEVELOPER_ID", "DEVELOPER_ID_MISSING", "DEVELOPER_ID_IDENTITY_MISSING", "DEVELOPER_ID_IDENTITY_UNAVAILABLE", "CODESIGN", "CODESIGN_MISSING", "SPCTL", "SPCTL_MISSING"},
    "notarization": {"HDIUTIL", "HDIUTIL_MISSING", "XCRUN", "XCRUN_MISSING", "NOTARYTOOL", "NOTARYTOOL_MISSING", "NOTARY_PROFILE", "NOTARY_PROFILE_MISSING", "NOTARY_PROFILE_UNAVAILABLE", "SPARKLE_LOCK", "SPARKLE_LOCK_INVALID", "SPARKLE_UPDATE_CONFIGURATION", "SPARKLE_UPDATE_CONFIGURATION_MISSING", "SPARKLE_UPDATE_CONFIGURATION_INVALID"},
}
diagnostic_sections = []
for section_id, codes in section_codes.items():
    failed_codes = [check["code"] for check in checks if check["code"] in codes and check["state"] != "PASS"]
    diagnostic_sections.append({
        "id": section_id,
        "state": "blocked" if failed_codes else "ready",
        "failed_codes": failed_codes,
    })
diagnostic_sections.append({
    "id": "runtime",
    "state": "not_checked",
    "failed_codes": ["PHOTOS_TCC_RUNTIME_TEST_REQUIRED", "AUTOMATION_TCC_RUNTIME_TEST_REQUIRED"],
    "next_action": "run_signed_smoke_test",
})
print(json.dumps({
    "schema_version": 2,
    "tool": "photos-local-keyword-indexer",
    "status": "blocked" if failed else "ready",
    # ``status`` is intentionally scoped to build/release gates. Runtime TCC
    # is always reported separately and cannot be inferred from a ready build.
    "status_scope": "release_gates",
    "runtime_status": "not_checked",
    "checks": checks,
    "diagnostic_sections": diagnostic_sections,
    # Release preflight intentionally cannot establish the user's TCC state.
    # Photos authorization is user/library-specific and Apple Events has no
    # passive public check. Keep those runtime gates explicit so support tools
    # never mistake a build-ready report for a permission-ready installation.
    "runtime_checks": [
        {
            "surface": "photos",
            "state": "NOT_CHECKED",
            "code": "PHOTOS_TCC_RUNTIME_TEST_REQUIRED",
            "action": "run_signed_smoke_test",
        },
        {
            "surface": "automation",
            "state": "NOT_CHECKED",
            "code": "AUTOMATION_TCC_RUNTIME_TEST_REQUIRED",
            "action": "run_signed_smoke_test",
        },
    ],
    "hints": hints,
    "next_action": next_action,
    "next_action_action": next_action_action,
}, ensure_ascii=False, separators=(",", ":")))
PY
  else
    print -- "release_preflight:NEXT:run_build_python_helper"
    print -- "release_preflight:NEXT_ACTION:run_build_python_helper"
    print -- "release_preflight:READY:release_gates_available_runtime_not_checked"
  fi
  exit 0
fi
if (( json_mode == 1 )); then
  /usr/bin/python3 - "$failed" "$next_code" "$next_action_action" "${json_checks[@]}" -- "${json_hints[@]}" <<'PY'
import json
import sys

failed = int(sys.argv[1])
next_action = sys.argv[2] or "review_release_preflight"
next_action_action = sys.argv[3] or "review_release_preflight"
separator = sys.argv.index("--", 4)
checks = []
for item in sys.argv[4:separator]:
    state, code = item.split("|", 1)
    checks.append({"state": state, "code": code.upper().replace(".", "_")})
hints = []
for item in sys.argv[separator + 1:]:
    code, action = item.split("|", 1)
    hints.append({"code": code.upper().replace(".", "_"), "action": action})
section_codes = {
    "toolchain": {
        "DARWIN", "DARWIN_REQUIRED", "ARM64", "ARM64_REQUIRED", "XCODEBUILD", "XCODEBUILD_MISSING",
        "XCODE_SELECT", "XCODE_SELECT_MISSING", "SWIFT", "SWIFT_MISSING", "PLUTIL", "PLUTIL_MISSING", "SIPS", "SIPS_MISSING",
        "LIPO", "LIPO_MISSING", "FILE", "FILE_MISSING", "READLINK", "READLINK_MISSING",
        "XCODE_TOOLCHAIN", "XCODE_TOOLCHAIN_UNAVAILABLE", "XCODE_COMMAND_LINE_TOOLS_ACTIVE",
        "SWIFT_TOOLCHAIN", "SWIFT_TOOLCHAIN_UNAVAILABLE",
        "XCODE_FULL_INSTALLATION_MISSING", "XCODE_FULL_INSTALLATION_NOT_SELECTED",
        "PLISTBUDDY", "PLISTBUDDY_MISSING", "PYTHON3_12", "PYTHON3_12_ARCHITECTURE", "PYINSTALLER",
        "SYSTEM_PYTHON3", "SYSTEM_PYTHON3_MISSING",
        "PYTHON3_12_MISSING", "PYTHON3_12_WRONG_VERSION", "PYTHON3_12_WRONG_ARCHITECTURE", "PYINSTALLER_MISSING",
        "RELEASE_METADATA", "RELEASE_METADATA_MISSING", "RELEASE_METADATA_INVALID",
        "HELPER", "HELPER_ARCHITECTURE", "HELPER_RUNTIME", "HELPER_MISSING",
        "HELPER_PATH_INVALID", "HELPER_PAYLOAD_SYMLINK", "HELPER_PAYLOAD_SCAN_FAILED",
        "HELPER_PAYLOAD_PERMISSIONS", "HELPER_PAYLOAD_OWNERSHIP", "HELPER_PAYLOAD_HARDLINK",
        "HELPER_PAYLOAD_TYPE_INVALID", "HELPER_PAYLOAD_INTEGRITY_UNAVAILABLE",
        "HELPER_BINARY_INVALID", "HELPER_WRONG_ARCHITECTURE", "HELPER_ARCHITECTURE_UNCHECKABLE",
        "HELPER_RUNTIME_INVALID", "HELPER_CHECK_CLEANUP_FAILED",
        "HELPER_SOURCE_FINGERPRINT", "HELPER_SOURCE_FINGERPRINT_MISSING",
        "HELPER_SOURCE_FINGERPRINT_STALE", "HELPER_SOURCE_FINGERPRINT_UNAVAILABLE",
    },
    "signing": {"DEVELOPER_ID", "DEVELOPER_ID_MISSING", "DEVELOPER_ID_IDENTITY_MISSING", "DEVELOPER_ID_IDENTITY_UNAVAILABLE", "CODESIGN", "CODESIGN_MISSING", "SPCTL", "SPCTL_MISSING"},
    "notarization": {"HDIUTIL", "HDIUTIL_MISSING", "XCRUN", "XCRUN_MISSING", "NOTARYTOOL", "NOTARYTOOL_MISSING", "NOTARY_PROFILE", "NOTARY_PROFILE_MISSING", "NOTARY_PROFILE_UNAVAILABLE", "SPARKLE_LOCK", "SPARKLE_LOCK_INVALID", "SPARKLE_UPDATE_CONFIGURATION", "SPARKLE_UPDATE_CONFIGURATION_MISSING", "SPARKLE_UPDATE_CONFIGURATION_INVALID"},
}
diagnostic_sections = []
for section_id, codes in section_codes.items():
    failed_codes = [check["code"] for check in checks if check["code"] in codes and check["state"] != "PASS"]
    diagnostic_sections.append({
        "id": section_id,
        "state": "blocked" if failed_codes else "ready",
        "failed_codes": failed_codes,
    })
diagnostic_sections.append({
    "id": "runtime",
    "state": "not_checked",
    "failed_codes": ["PHOTOS_TCC_RUNTIME_TEST_REQUIRED", "AUTOMATION_TCC_RUNTIME_TEST_REQUIRED"],
    "next_action": "run_signed_smoke_test",
})
print(json.dumps({
    "schema_version": 2,
    "tool": "photos-local-keyword-indexer",
    "status": "blocked" if failed else "ready",
    # Keep the machine contract explicit: release gates and runtime TCC are
    # separate observations with different authorization boundaries.
    "status_scope": "release_gates",
    "runtime_status": "not_checked",
    "checks": checks,
    "diagnostic_sections": diagnostic_sections,
    # Runtime TCC is deliberately separate from release prerequisites. The
    # signed smoke test is the only supported release-time confirmation.
    "runtime_checks": [
        {
            "surface": "photos",
            "state": "NOT_CHECKED",
            "code": "PHOTOS_TCC_RUNTIME_TEST_REQUIRED",
            "action": "run_signed_smoke_test",
        },
        {
            "surface": "automation",
            "state": "NOT_CHECKED",
            "code": "AUTOMATION_TCC_RUNTIME_TEST_REQUIRED",
            "action": "run_signed_smoke_test",
        },
    ],
    "hints": hints,
    "next_action": next_action,
    "next_action_action": next_action_action,
}, ensure_ascii=False, separators=(",", ":")))
PY
else
  print -- "release_preflight:NEXT:$next_code"
  print -- "release_preflight:NEXT_ACTION:$next_action_action"
  print -u2 -- "release_preflight:BLOCKED:resolve_failed_gates"
fi
exit 1
