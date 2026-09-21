#!/bin/zsh
set -euo pipefail

app_path="${1:-}"
if [[ -z "$app_path" || "$app_path" != /* || "${app_path:t}" != *.app || -L "$app_path" || ! -d "$app_path" ]]; then
  print -u2 -- "verify_release:FAIL:app_bundle_path_invalid"
  print -u2 -- "verify_release:HINT:app_bundle_path_invalid:provide_a_real_app_bundle"
  exit 2
fi
app_bundle_created_identity=""
record_app_bundle_identity() {
  if [[ -d "$app_path" && ! -L "$app_path" ]]; then
    app_bundle_created_identity="$(stat -f '%d:%i' -- "$app_path" 2>/dev/null || true)"
  fi
}
app_bundle_is_owned() {
  local current_identity
  [[ -n "$app_bundle_created_identity" && -d "$app_path" && ! -L "$app_path" ]] || return 1
  current_identity="$(stat -f '%d:%i' -- "$app_path" 2>/dev/null || true)"
  [[ -n "$current_identity" && "$current_identity" == "$app_bundle_created_identity" ]]
}
record_app_bundle_identity
for command_name in plutil lipo codesign spctl; do
  command -v "$command_name" >/dev/null 2>&1 || {
    print -u2 -- "Required release verification tool is unavailable: $command_name"
    exit 1
  }
done
contents="$app_path/Contents"
framework="$contents/Frameworks/Sparkle.framework"
main="$contents/MacOS/PhotosLocalKeywordIndexer"
helper_app="$contents/Helpers/PhotosIndexerWorker.app"
helper="$helper_app/Contents/MacOS/PhotosIndexerWorker"
legacy_helper="$contents/Helpers/PhotosIndexerWorker/PhotosIndexerWorker"
helper_info="$helper_app/Contents/Info.plist"
info="$contents/Info.plist"
icon="$contents/Resources/AppIcon.icns"
entitlements="${ENTITLEMENTS_FILE:-${0:A:h}/entitlements.plist}"
helper_entitlements="${HELPER_ENTITLEMENTS_FILE:-${0:A:h}/helper-entitlements.plist}"
plist_reader="/usr/bin/python3"
command -v "$plist_reader" >/dev/null 2>&1 || { print -u2 -- "A Python 3 plist reader is required."; exit 1; }
command -v file >/dev/null 2>&1 || { print -u2 -- "The file utility is required."; exit 1; }
command -v readlink >/dev/null 2>&1 || { print -u2 -- "The readlink utility is required."; exit 1; }
[[ ! -e "$legacy_helper" && ! -L "$legacy_helper" \
  && -x "$main" && -x "$helper" && -f "$helper_info" && -f "$info" && -f "$entitlements" && -f "$helper_entitlements" ]] || {
  print -u2 -- "Release bundle is missing its executable, helper, Info.plist, or entitlement policy."
  exit 1
}
for directory in "$contents" "$contents/MacOS" "$contents/Helpers" "$helper_app" "$helper_app/Contents" "$helper_app/Contents/MacOS" "$helper_app/Contents/Resources" "$contents/Frameworks" "$framework"; do
  [[ -d "$directory" && ! -L "$directory" ]] || {
    print -u2 -- "Bundle directories must not be symlinks or missing."
    exit 1
  }
done
[[ ! -L "$contents" && ! -L "$framework" && ! -L "$main" && ! -L "$helper_app" && ! -L "$helper" && ! -L "$helper_info" && ! -L "$info" && ! -L "$entitlements" && ! -L "$helper_entitlements" ]] || {
  print -u2 -- "Core bundle nodes must not be symlinks."
  exit 1
}
[[ -d "$framework" ]] || {
  print -u2 -- "Release bundle must contain Sparkle.framework."
  exit 1
}

verify_symlink_targets() {
  local root="$1"
  local root_real="${root:A}"
  local candidate raw target
  while IFS= read -r -d "" candidate; do
    raw="$(readlink "$candidate")"
    if [[ "$raw" == /* ]]; then
      target="$raw"
    else
      target="${candidate:h}/$raw"
    fi
    target="${target:A}"
    case "$target" in
      "$root_real"/*) ;;
      *)
        print -u2 -- "verify_release:FAIL:bundle_symlink"
        return 1
        ;;
    esac
  done < <(find "$root" -type l -print0)
}

verify_symlink_targets "$contents"
verify_symlink_targets "$helper_app"
verify_symlink_targets "$framework"
if [[ ! -x "$plist_reader" ]]; then
  print -u2 -- "verify_release:FAIL:bundle_integrity_check_unavailable"
  print -u2 -- "verify_release:HINT:bundle_integrity_check_unavailable:install_macos_system_python_then_retry"
  exit 1
fi
bundle_integrity_check="$("$plist_reader" - "$contents" <<'PY'
import os
import sys

root = sys.argv[1]
for directory, directories, files in os.walk(root, followlinks=False):
    directories[:] = [name for name in directories if not os.path.islink(os.path.join(directory, name))]
    candidates = [directory]
    candidates.extend(os.path.join(directory, name) for name in directories)
    candidates.extend(os.path.join(directory, name) for name in files)
    for candidate in candidates:
        try:
            metadata = os.stat(candidate, follow_symlinks=False)
        except (OSError, ValueError):
            print("unavailable")
            raise SystemExit(0)
        if os.path.isfile(candidate) and metadata.st_nlink != 1:
            print("hardlink")
            raise SystemExit(0)
        if metadata.st_uid != os.getuid():
            print("ownership")
            raise SystemExit(0)
print("ok")
PY
)" || bundle_integrity_check="unavailable"
case "$bundle_integrity_check" in
  ok) ;;
  hardlink)
    print -u2 -- "verify_release:FAIL:bundle_hardlink"
    print -u2 -- "verify_release:HINT:bundle_hardlink:copy_bundle_to_unique_files_then_retry"
    exit 1
    ;;
  ownership)
    print -u2 -- "verify_release:FAIL:bundle_ownership"
    print -u2 -- "verify_release:HINT:bundle_ownership:rebuild_bundle_as_current_user_then_retry"
    exit 1
    ;;
  *)
    print -u2 -- "verify_release:FAIL:bundle_integrity_check_unavailable"
    print -u2 -- "verify_release:HINT:bundle_integrity_check_unavailable:install_macos_system_python_then_retry"
    exit 1
    ;;
esac
[[ -f "$icon" && ! -L "$icon" ]] || {
  print -u2 -- "Release bundle is missing its regular AppIcon.icns resource."
  exit 1
}
verify_bundle_permissions() {
  local root="$1"
  local writable
  writable="$(find "$root" \( -type f -o -type d \) \( -perm -002 -o -perm -020 \) -print -quit)"
  [[ -z "$writable" ]] || {
    print -u2 -- "Release bundle contains group/world-writable content."
    return 1
  }
}

verify_bundle_permissions "$contents"
if ! plutil -lint "$info" >/dev/null 2>&1; then
  print -u2 -- "verify_release:FAIL:info_plist_invalid"
  exit 1
fi
if ! plutil -lint "$helper_info" >/dev/null 2>&1; then
  print -u2 -- "verify_release:FAIL:helper_info_plist_invalid"
  exit 1
fi
if ! "$plist_reader" - "$helper_info" <<'PY'
import plistlib
import sys

with open(sys.argv[1], "rb") as handle:
    values = plistlib.load(handle)
expected = {
    "CFBundleIdentifier": "com.photoslocalkeywordindexer.worker",
    "CFBundleExecutable": "PhotosIndexerWorker",
    "CFBundlePackageType": "APPL",
    "LSUIElement": True,
}
if not isinstance(values, dict) or any(values.get(key) != value for key, value in expected.items()):
    raise SystemExit("helper bundle identity is invalid")
for key in (
    "NSPhotoLibraryUsageDescription",
    "NSPhotoLibraryAddUsageDescription",
    "NSAppleEventsUsageDescription",
):
    if not isinstance(values.get(key), str) or not values[key].strip():
        raise SystemExit("helper privacy declaration is invalid")
PY
then
  print -u2 -- "verify_release:FAIL:helper_bundle_identity_invalid"
  exit 1
fi
if ! "$plist_reader" - "$info" "$helper_info" <<'PY'
import plistlib
import sys

with open(sys.argv[1], "rb") as handle:
    app_info = plistlib.load(handle)
with open(sys.argv[2], "rb") as handle:
    helper_info = plistlib.load(handle)
if not isinstance(app_info, dict) or not isinstance(helper_info, dict):
    raise SystemExit("plist root must be a dictionary")
if helper_info.get("CFBundleShortVersionString") != app_info.get("CFBundleShortVersionString"):
    raise SystemExit("helper bundle version is invalid")
if helper_info.get("CFBundleVersion") != app_info.get("CFBundleVersion"):
    raise SystemExit("helper bundle build is invalid")
PY
then
  print -u2 -- "verify_release:FAIL:helper_bundle_version_invalid"
  exit 1
fi
if ! plutil -lint "$entitlements" >/dev/null 2>&1; then
  print -u2 -- "verify_release:FAIL:entitlements_plist_invalid"
  exit 1
fi
validate_sparkle_framework() {
  local bundle="$1"
  local plist="$bundle/Versions/Current/Resources/Info.plist"
  [[ -f "$plist" && ! -L "$plist" ]] || {
    print -u2 -- "Sparkle framework metadata is missing."
    return 1
  }
  "$plist_reader" - "$plist" <<'PY'
import plistlib
import sys

with open(sys.argv[1], "rb") as handle:
    values = plistlib.load(handle)
if not isinstance(values, dict):
    raise SystemExit("plist root must be a dictionary")
expected = {
    "CFBundleIdentifier": "org.sparkle-project.Sparkle",
    "CFBundleShortVersionString": "2.9.2",
}
if any(values.get(key) != value for key, value in expected.items()):
    raise SystemExit("Sparkle framework identity or version is invalid")
PY
}

validate_sparkle_framework "$framework"
sparkle_binary="$contents/Frameworks/Sparkle.framework/Versions/Current/Sparkle"
[[ -x "$sparkle_binary" ]] || { print -u2 -- "Sparkle executable is missing."; exit 1; }
framework_real="${framework:A}"
sparkle_binary="${sparkle_binary:A}"
case "$sparkle_binary" in
  "$framework_real"/*) ;;
  *) print -u2 -- "Sparkle executable escapes the app bundle."; exit 1 ;;
esac
[[ "$(lipo -archs "$main")" == "arm64" && "$(lipo -archs "$helper")" == "arm64" && "$(lipo -archs "$sparkle_binary")" == "arm64" ]] || {
  print -u2 -- "Release executables must be arm64-only."
  exit 1
}

verify_arm64_tree() {
  local root="$1"
  local candidate architectures
  while IFS= read -r candidate; do
    if [[ -f "$candidate" && ! -L "$candidate" ]] && file -b "$candidate" | grep -q "Mach-O"; then
      architectures="$(lipo -archs "$candidate")"
      [[ "$architectures" == "arm64" ]] || {
        print -u2 -- "verify_release:FAIL:nested_architecture"
        return 1
      }
    fi
  done < <(find "$root" -type f -print)
}

verify_arm64_tree "$helper_app"
verify_arm64_tree "$framework"

entitlement_tmp="$(mktemp -d "${TMPDIR:-/tmp}/photos-indexer-entitlements.XXXXXX")"
entitlement_tmp_created_identity=""
record_entitlement_tmp_identity() {
  if [[ -d "$entitlement_tmp" && ! -L "$entitlement_tmp" ]]; then
    entitlement_tmp_created_identity="$(stat -f '%d:%i' -- "$entitlement_tmp" 2>/dev/null || true)"
  fi
}
entitlement_tmp_is_owned() {
  local current_identity
  [[ -n "$entitlement_tmp_created_identity" && -d "$entitlement_tmp" && ! -L "$entitlement_tmp" ]] || return 1
  current_identity="$(stat -f '%d:%i' -- "$entitlement_tmp" 2>/dev/null || true)"
  [[ -n "$current_identity" && "$current_identity" == "$entitlement_tmp_created_identity" ]]
}
record_entitlement_tmp_identity
cleanup_failed=0
cleanup_entitlements() {
  if entitlement_tmp_is_owned; then
    rm -rf -- "$entitlement_tmp" >/dev/null 2>&1 || cleanup_failed=1
  fi
}
trap cleanup_entitlements EXIT
trap 'cleanup_entitlements; exit 130' INT TERM

verify_no_debug_entitlements() {
  local target="$1"
  local signed_plist="$entitlement_tmp/debug-check.plist"
  if ! entitlement_tmp_is_owned; then
    print -u2 -- "verify_release:FAIL:entitlement_temp_invalid"
    return 1
  fi
  rm -f -- "$signed_plist"
  if ! codesign -d --entitlements :- "$target" > "$signed_plist" 2>/dev/null; then
    return 0
  fi
  if ! entitlement_tmp_is_owned; then
    print -u2 -- "verify_release:FAIL:entitlement_temp_invalid"
    return 1
  fi
  [[ -s "$signed_plist" ]] || return 0
  if ! "$plist_reader" - "$signed_plist" <<'PY'
import plistlib
import sys

with open(sys.argv[1], "rb") as handle:
    values = plistlib.load(handle)
if not isinstance(values, dict):
    raise SystemExit("plist root must be a dictionary")
if values.get("com.apple.security.get-task-allow") is True:
    raise SystemExit("debug entitlement is enabled")
PY
  then
    print -u2 -- "verify_release:FAIL:debug_entitlement"
    return 1
  fi
}

verify_code() {
  local target="$1"
  local expected_identifier="${2:-}"
  if ! codesign --verify --strict --verbose=2 "$target" >/dev/null 2>&1; then
    print -u2 -- "verify_release:FAIL:code_signature_invalid"
    return 1
  fi
  local details="$(codesign -dvv "$target" 2>&1)"
  if [[ "$details" != *"Authority=Developer ID Application:"* ]]; then
    print -u2 -- "verify_release:FAIL:code_signature_identity_invalid"
    print -u2 -- "verify_release:HINT:code_signature_identity_invalid:sign_with_developer_id_application"
    return 1
  fi
  if [[ "$details" != *"flags=0x10000(runtime)"* && "$details" != *"flags=0x10000(runtime,"* ]]; then
    print -u2 -- "verify_release:FAIL:code_signature_runtime_invalid"
    print -u2 -- "verify_release:HINT:code_signature_runtime_invalid:sign_with_hardened_runtime"
    return 1
  fi
  local identifier="$(print -r -- "$details" | sed -n 's/^Identifier=//p')"
  if [[ -n "$expected_identifier" && "$identifier" != "$expected_identifier" ]]; then
    print -u2 -- "verify_release:FAIL:code_signature_identifier_invalid"
    print -u2 -- "verify_release:HINT:code_signature_identifier_invalid:rebuild_with_expected_bundle_identifier"
    return 1
  fi
  local team_id="$(print -r -- "$details" | sed -n 's/^TeamIdentifier=//p')"
  if [[ -z "$team_id" ]]; then
    print -u2 -- "verify_release:FAIL:code_signature_team_invalid"
    print -u2 -- "verify_release:HINT:code_signature_team_invalid:sign_nested_code_with_same_developer_id_team"
    return 1
  fi
  verify_no_debug_entitlements "$target"
  print -r -- "$team_id"
}

verify_macho_tree() {
  local root="$1"
  local candidate
  local -a ids=()
  while IFS= read -r candidate; do
    if [[ -f "$candidate" && ! -L "$candidate" ]] && file -b "$candidate" | grep -q "Mach-O"; then
      ids+=("$(verify_code "$candidate")")
    fi
  done < <(find "$root" -type f -print)
  print -rl -- "${ids[@]}"
}

verify_bundle_tree() {
  local root="$1"
  local candidate
  local -a ids=()
  while IFS= read -r candidate; do
    [[ "$candidate" != "$root" ]] || continue
    ids+=("$(verify_code "$candidate")")
  done < <(find "$root" -type d \( -name '*.app' -o -name '*.xpc' -o -name '*.framework' -o -name '*.bundle' -o -name '*.appex' \) -print)
  print -rl -- "${ids[@]}"
}

if ! app_bundle_is_owned; then
  print -u2 -- "verify_release:FAIL:app_bundle_identity_changed"
  exit 1
fi
team_ids=("$(verify_code "$helper" "com.photoslocalkeywordindexer.worker")" "$(verify_code "$helper_app" "com.photoslocalkeywordindexer.worker")" "$(verify_code "$sparkle_binary" "org.sparkle-project.Sparkle")" "$(verify_code "$framework" "org.sparkle-project.Sparkle")" "$(verify_code "$main" "com.photoslocalkeywordindexer.app")" "$(verify_code "$app_path" "com.photoslocalkeywordindexer.app")")
nested_ids="$(verify_macho_tree "$helper_app")"
[[ -n "$nested_ids" ]] && team_ids+=("${(@f)nested_ids}")
nested_ids="$(verify_macho_tree "$framework")"
[[ -n "$nested_ids" ]] && team_ids+=("${(@f)nested_ids}")
nested_ids="$(verify_macho_tree "$contents")"
[[ -n "$nested_ids" ]] && team_ids+=("${(@f)nested_ids}")
nested_ids="$(verify_bundle_tree "$helper_app")"
[[ -n "$nested_ids" ]] && team_ids+=("${(@f)nested_ids}")
nested_ids="$(verify_bundle_tree "$framework")"
[[ -n "$nested_ids" ]] && team_ids+=("${(@f)nested_ids}")
for team_id in $team_ids; do
  if [[ -z "$team_id" || "$team_id" != "$team_ids[1]" ]]; then
    print -u2 -- "verify_release:FAIL:code_signature_team_mismatch"
    print -u2 -- "verify_release:HINT:code_signature_team_mismatch:sign_all_nested_code_with_same_developer_id_team"
    exit 1
  fi
done
validate_entitlements() {
  local plist="$1"
  "$plist_reader" - "$plist" <<'PY'
import plistlib
import sys

path = sys.argv[1]
with open(path, "rb") as handle:
    values = plistlib.load(handle)
if not isinstance(values, dict):
    raise SystemExit("plist root must be a dictionary")
allowed = {
    "com.apple.security.automation.apple-events",
    "com.apple.security.personal-information.photos-library",
    "com.apple.application-identifier",
    "com.apple.developer.team-identifier",
}
required = {
    "com.apple.security.automation.apple-events",
    "com.apple.security.personal-information.photos-library",
}
unknown = set(values) - allowed
missing = {key for key in required if values.get(key) is not True}
if unknown or missing:
    raise SystemExit("unapproved or disabled entitlements")
PY
}

validate_bundle_identity() {
  local plist="$1"
  "$plist_reader" - "$plist" <<'PY'
import plistlib
import sys

path = sys.argv[1]
with open(path, "rb") as handle:
    values = plistlib.load(handle)
if not isinstance(values, dict):
    raise SystemExit("plist root must be a dictionary")
expected = {
    "CFBundleIdentifier": "com.photoslocalkeywordindexer.app",
    "CFBundleExecutable": "PhotosLocalKeywordIndexer",
    "CFBundlePackageType": "APPL",
    "CFBundleIconFile": "AppIcon.icns",
    "LSMinimumSystemVersion": "14.0",
}
if any(values.get(key) != value for key, value in expected.items()):
    raise SystemExit("bundle identity or minimum system version is invalid")
PY
}

validate_release_version() {
  local plist="$1"
  "$plist_reader" - "$plist" <<'PY'
import plistlib
import re
import sys

with open(sys.argv[1], "rb") as handle:
    values = plistlib.load(handle)
if not isinstance(values, dict):
    raise SystemExit("plist root must be a dictionary")
version = values.get("CFBundleShortVersionString")
build = values.get("CFBundleVersion")
if not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
    raise SystemExit("release version must be three numeric components")
if not isinstance(build, str) or not re.fullmatch(r"[1-9][0-9]*", build):
    raise SystemExit("release build number must be a positive integer")
PY
}

validate_privacy_usage_descriptions() {
  local plist="$1"
  if ! "$plist_reader" - "$plist" 2>/dev/null <<'PY'
import plistlib
import unicodedata
import sys

with open(sys.argv[1], "rb") as handle:
    values = plistlib.load(handle)
if not isinstance(values, dict):
    raise SystemExit(1)
for key in (
    "NSPhotoLibraryUsageDescription",
    "NSAppleEventsUsageDescription",
):
    value = values.get(key)
    if (
        not isinstance(value, str)
        or not 8 <= len(value.strip()) <= 500
        or any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in value)
    ):
        raise SystemExit(1)
PY
  then
    print -u2 -- "verify_release:FAIL:privacy_usage_description_invalid"
    print -u2 -- "verify_release:HINT:privacy_usage_description_invalid:configure_meaningful_photos_and_automation_privacy_text"
    print -u2 -- "Release privacy usage description is missing or unsafe."
    return 1
  fi
}

validate_sparkle_configuration() {
  local plist="$1"
  local configuration_status
  configuration_status="$("$plist_reader" - "$plist" <<'PY'
import base64
import binascii
import plistlib
import sys
from urllib.parse import urlsplit

path = sys.argv[1]
with open(path, "rb") as handle:
    values = plistlib.load(handle)
if not isinstance(values, dict):
    raise SystemExit("plist root must be a dictionary")
feed = values.get("SUFeedURL")
public_key = values.get("SUPublicEDKey")
automatic_checks = values.get("SUEnableAutomaticChecks")
if feed is None and public_key is None:
    print("missing")
    raise SystemExit(0)
if not isinstance(feed, str) or not isinstance(public_key, str):
    raise SystemExit("Sparkle feed and public key must be configured together")
try:
    parsed = urlsplit(feed)
except ValueError:
    raise SystemExit("Sparkle feed is invalid")
try:
    port = parsed.port
except ValueError:
    raise SystemExit("Sparkle feed is invalid")
placeholder_domains = ("example.invalid", "example.org", "example.com", "example.net")
hostname = (parsed.hostname or "").casefold().rstrip(".")
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
):
    raise SystemExit("Sparkle feed must be an HTTPS URL without credentials")
if any(character.isspace() for character in feed + public_key) or "REPLACE_WITH" in public_key:
    raise SystemExit("Sparkle feed configuration contains unsafe whitespace or a placeholder key")
if not public_key:
    raise SystemExit("Sparkle public key is empty")
try:
    decoded_key = base64.b64decode(public_key, validate=True)
except (ValueError, binascii.Error):
    raise SystemExit("Sparkle public key is not valid base64")
if len(decoded_key) != 32 or base64.b64encode(decoded_key).decode("ascii") != public_key:
    raise SystemExit("Sparkle public key must be the canonical 32-byte Ed25519 key")
if automatic_checks is not True:
    raise SystemExit("Sparkle automatic checks must be enabled in a release bundle")
print("valid")
PY
  )" || configuration_status="invalid"
  case "$configuration_status" in
    valid) ;;
    missing)
      print -u2 -- "verify_release:FAIL:sparkle_update_configuration_missing"
      return 1
      ;;
    *)
      print -u2 -- "verify_release:FAIL:sparkle_update_configuration_invalid"
      return 1
      ;;
  esac
}

validate_bundle_identity "$info"
validate_release_version "$info"
validate_privacy_usage_descriptions "$info"
validate_sparkle_configuration "$info"
validate_entitlements "$entitlements"
validate_entitlements "$helper_entitlements"
signed_entitlements="$entitlement_tmp/signed.plist"
signed_helper_entitlements="$entitlement_tmp/signed-helper.plist"
if ! entitlement_tmp_is_owned; then
  print -u2 -- "verify_release:FAIL:entitlement_temp_invalid"
  exit 1
fi
codesign -d --entitlements :- "$app_path" > "$signed_entitlements" 2>/dev/null
codesign -d --entitlements :- "$helper" > "$signed_helper_entitlements" 2>/dev/null
if ! entitlement_tmp_is_owned; then
  print -u2 -- "verify_release:FAIL:entitlement_temp_invalid"
  exit 1
fi
validate_entitlements "$signed_entitlements"
validate_entitlements "$signed_helper_entitlements"
if ! spctl --assess --type execute --verbose=4 "$app_path" >/dev/null 2>&1; then
  print -u2 -- "verify_release:FAIL:gatekeeper_assessment_invalid"
  exit 1
fi
if ! app_bundle_is_owned; then
  print -u2 -- "verify_release:FAIL:app_bundle_identity_changed"
  exit 1
fi
cleanup_entitlements
if (( cleanup_failed == 1 )) || [[ -e "$entitlement_tmp" || -L "$entitlement_tmp" ]]; then
  print -u2 -- "verify_release:FAIL:final_cleanup_failed"
  print -u2 -- "verify_release:HINT:final_cleanup_failed:remove_stale_private_verification_state_then_retry"
  exit 1
fi
trap - EXIT INT TERM
print -- "Release verification passed."
