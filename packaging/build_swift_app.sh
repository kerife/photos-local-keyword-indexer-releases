#!/bin/zsh
set -euo pipefail

project_root="${0:A:h:h}"
app_root="${BUILD_ROOT:-$project_root/build/app}"
app_root="${app_root:a}"
package_root="$project_root/app"
app_bundle="$app_root/PhotosLocalKeywordIndexer.app"
release_build="${RELEASE_BUILD:-0}"
verify_embedded_helper="${VERIFY_EMBEDDED_HELPER:-1}"
feed_url="${SPARKLE_FEED_URL:-}"
public_key="${SPARKLE_PUBLIC_ED_KEY:-}"
app_version="${APP_VERSION:-}"
build_number="${BUILD_NUMBER:-}"
local_code_sign_identity="${LOCAL_CODE_SIGN_IDENTITY:-}"
development_sign_identity="${local_code_sign_identity:--}"

if [[ "$release_build" == "0" && ( -n "$feed_url" || -n "$public_key" ) ]]; then
  print -u2 -- "build_swift_app:FAIL:development_updates_disabled"
  exit 2
fi

for beta_document in LICENSE docs/privacy.md docs/support.md docs/release/third-party-notices.md; do
  if [[ ! -f "$project_root/$beta_document" || -L "$project_root/$beta_document" ]]; then
    print -u2 -- "build_swift_app:FAIL:distribution_document_missing"
    exit 2
  fi
done

case "$app_root" in
  "$project_root"/build|"$project_root"/build/*) ;;
  *) print -u2 -- "build_swift_app:FAIL:build_root_invalid"; exit 2 ;;
esac

source "$project_root/packaging/build_path_guard.zsh"
validate_build_path "$app_root" "$project_root/build" "BUILD_ROOT"
validate_build_path "$app_bundle" "$project_root/build" "app bundle"

if [[ "$(uname -m)" != "arm64" ]]; then
  print -u2 -- "The distributable app is arm64-only."; exit 2
fi
if ! command -v /usr/bin/sips >/dev/null 2>&1; then
  print -u2 -- "sips is required before compiling the app."; exit 2
fi
if ! command -v /usr/bin/python3 >/dev/null 2>&1; then
  print -u2 -- "Python 3 is required before compiling the app."; exit 2
fi
# The build later removes a stale app bundle recursively. Refuse a destination
# tree that another local account can replace between validation and cleanup.
# Check every existing component from the target back through the build root;
# uncreated descendants inherit the nearest validated parent.
build_root_integrity="$(/usr/bin/python3 - "$app_bundle" "$project_root/build" <<'PY'
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
    print -u2 -- "build_swift_app:FAIL:build_root_ownership"
    print -u2 -- "build_swift_app:HINT:build_root_ownership:recreate_the_build_root_as_the_current_user"
    exit 2
    ;;
  permissions)
    print -u2 -- "build_swift_app:FAIL:build_root_permissions"
    print -u2 -- "build_swift_app:HINT:build_root_permissions:remove_group_world_write_then_retry"
    exit 2
    ;;
  invalid)
    print -u2 -- "build_swift_app:FAIL:build_root_invalid"
    print -u2 -- "build_swift_app:HINT:build_root_invalid:recreate_a_private_build_directory"
    exit 2
    ;;
  *)
    print -u2 -- "build_swift_app:FAIL:build_root_integrity_unavailable"
    print -u2 -- "build_swift_app:HINT:build_root_integrity_unavailable:recreate_a_private_build_directory"
    exit 1
    ;;
esac
if [[ "$release_build" != "0" && "$release_build" != "1" ]]; then
  print -u2 -- "RELEASE_BUILD must be 0 or 1."; exit 2
fi
if [[ "$verify_embedded_helper" != "0" && "$verify_embedded_helper" != "1" ]]; then
  print -u2 -- "VERIFY_EMBEDDED_HELPER must be 0 or 1."; exit 2
fi
if [[ "$release_build" == "1" || -n "$feed_url" || -n "$public_key" ]]; then
  [[ -n "$feed_url" && -n "$public_key" ]] || {
    print -u2 -- "Release update configuration is incomplete."; exit 2
  }
  [[ "${feed_url:l}" == https://* ]] || { print -u2 -- "Release update feed must use HTTPS."; exit 2; }
  [[ "$feed_url" != *[[:space:]]* ]] || { print -u2 -- "Release update feed must not contain whitespace."; exit 2; }
  [[ "$feed_url" != *"@"* ]] || { print -u2 -- "Release update feed must not contain credentials."; exit 2; }
  # The value is passed inside a PlistBuddy command below.  Reject syntax
  # delimiters and malformed HTTPS URLs before that command can interpret
  # caller-controlled text as another plist operation.
  if /usr/bin/python3 - "$feed_url" <<'PY'
from urllib.parse import urlsplit
import sys

feed = sys.argv[1]
placeholder_domains = ("example.invalid", "example.org", "example.com", "example.net")
try:
    parsed = urlsplit(feed)
except ValueError:
    raise SystemExit(1)
try:
    port = parsed.port
except ValueError:
    raise SystemExit(1)
if (
    parsed.scheme != "https"
    or not parsed.netloc
    or parsed.hostname is None
    or (port is not None and not 1 <= port <= 65535)
    or any(character in feed for character in (';', '"', "'", "\\"))
):
    raise SystemExit(1)
hostname = parsed.hostname.casefold().rstrip(".")
if any(hostname == domain or hostname.endswith(f".{domain}") for domain in placeholder_domains):
    raise SystemExit(2)
PY
  then
    :
  else
    feed_status=$?
    if (( feed_status == 2 )); then
      print -u2 -- "Release update feed must not use a placeholder host."
      exit 2
    fi
    print -u2 -- "build_swift_app:FAIL:sparkle_update_configuration_invalid"
    exit 2
  fi
  [[ "$public_key" != *[[:space:]]* ]] || { print -u2 -- "Release update key must not contain whitespace."; exit 2; }
  [[ "$public_key" != *REPLACE_WITH* ]] || { print -u2 -- "Release update key is invalid."; exit 2; }
  if ! /usr/bin/python3 - "$public_key" <<'PY'
import base64
import binascii
import sys

public_key = sys.argv[1]
try:
    decoded_key = base64.b64decode(public_key, validate=True)
except (ValueError, binascii.Error):
    raise SystemExit(1)
if len(decoded_key) != 32 or base64.b64encode(decoded_key).decode("ascii") != public_key:
    raise SystemExit(1)
PY
  then
    print -u2 -- "build_swift_app:FAIL:sparkle_update_configuration_invalid"
    exit 2
  fi
  release_build="1"
fi
if [[ "$release_build" == "1" && "$verify_embedded_helper" != "1" ]]; then
  print -u2 -- "build_swift_app:FAIL:embedded_helper_verification_required"
  exit 2
fi
if [[ "$release_build" == "1" && -n "$local_code_sign_identity" ]]; then
  print -u2 -- "build_swift_app:FAIL:local_code_sign_identity_not_allowed_for_release"
  print -u2 -- "build_swift_app:HINT:local_code_sign_identity_not_allowed_for_release:use_developer_id_application_for_release"
  exit 2
fi
if [[ "$release_build" == "1" ]]; then
  [[ "$app_version" =~ '^[0-9]+\.[0-9]+\.[0-9]+$' && "$build_number" =~ '^[1-9][0-9]*$' ]] || {
    print -u2 -- "APP_VERSION and BUILD_NUMBER are required for release builds."; exit 2
  }
  project_version="$(sed -n 's/^version = "\([^"]*\)"/\1/p' "$project_root/pyproject.toml" | head -1)"
  [[ -n "$project_version" && "$app_version" == "$project_version" ]] || {
    print -u2 -- "APP_VERSION must match pyproject.toml version."; exit 2
  }
fi
if [[ "$release_build" == "1" && ! -x /usr/libexec/PlistBuddy ]]; then
  print -u2 -- "build_swift_app:FAIL:plistbuddy_missing"
  print -u2 -- "build_swift_app:HINT:plistbuddy_missing:run_on_macos_release_host_with_plistbuddy"
  exit 2
fi

if ! command -v xcodebuild >/dev/null 2>&1; then
  print -u2 -- "Full Xcode is required to build the app; install it and select its developer directory."; exit 2
fi
developer_dir="${DEVELOPER_DIR:-}"
selected_developer_dir="${DEVELOPER_DIR:-}"
if [[ -z "$selected_developer_dir" ]]; then
  selected_developer_dir="$(xcode-select -p 2>/dev/null || true)"
fi
if [[ "$selected_developer_dir" == "/Library/Developer/CommandLineTools" ]]; then
  if [[ -n "$developer_dir" ]]; then
    print -u2 -- "DEVELOPER_DIR points to Command Line Tools; select a full Xcode developer directory."; exit 2
  fi
  print -u2 -- "Full Xcode must be selected instead of Command Line Tools."; exit 2
fi
if ! xcodebuild -version >/dev/null 2>&1; then
  print -u2 -- "Selected Xcode toolchain is unavailable; select a valid Xcode developer directory."; exit 2
fi
if ! command -v xcrun >/dev/null 2>&1; then
  print -u2 -- "xcrun is required to resolve Swift from the selected Xcode toolchain."; exit 2
fi
if [[ -n "$selected_developer_dir" ]]; then
  swift_bin="$(DEVELOPER_DIR="$selected_developer_dir" xcrun --find swift 2>/dev/null || true)"
else
  swift_bin="$(xcrun --find swift 2>/dev/null || true)"
fi
if [[ -z "$swift_bin" || ! -x "$swift_bin" ]]; then
  print -u2 -- "Selected Xcode toolchain does not provide an executable Swift compiler."; exit 2
fi
if ! command -v lipo >/dev/null 2>&1; then
  print -u2 -- "lipo is required to validate the arm64 app executable."; exit 2
fi
if ! command -v file >/dev/null 2>&1; then
  print -u2 -- "file is required to inspect the embedded Sparkle framework."; exit 2
fi
if ! command -v codesign >/dev/null 2>&1; then
  print -u2 -- "codesign is required to reseal the embedded Sparkle framework."; exit 2
fi
if [[ -n "$local_code_sign_identity" ]]; then
  if ! command -v security >/dev/null 2>&1; then
    print -u2 -- "build_swift_app:FAIL:local_code_sign_identity_check_unavailable"
    exit 2
  fi
  local_identity_listing="$(security find-identity -v -p codesigning 2>/dev/null || true)"
  if [[ "$local_identity_listing" != *"$local_code_sign_identity"* ]]; then
    print -u2 -- "build_swift_app:FAIL:local_code_sign_identity_unavailable"
    print -u2 -- "build_swift_app:HINT:local_code_sign_identity_unavailable:install_or_select_a_local_codesigning_identity"
    exit 2
  fi
  unset local_identity_listing
fi
package_lock="$package_root/Package.resolved"
if [[ -L "$package_lock" || ! -f "$package_lock" ]]; then
  print -u2 -- "build_swift_app:FAIL:package_lock_invalid"; exit 2
fi
source "$project_root/packaging/sparkle_framework.zsh"
if ! validate_sparkle_lock "$package_lock" "2.9.2" "6276ba2b404829d139c45ff98427cf90e2efc59b"; then
  print -u2 -- "build_swift_app:FAIL:sparkle_lock_invalid"; exit 2
fi

helper="$project_root/build/python-helper/dist/PhotosIndexerWorker.app"
if ! validate_build_path "$helper" "$project_root/build" "helper source"; then
  print -u2 -- "build_swift_app:FAIL:helper_path_invalid"
  exit 1
fi
helper_info="$helper/Contents/Info.plist"
helper_binary="$helper/Contents/MacOS/PhotosIndexerWorker"
helper_resources="$helper/Contents/Resources"
if [[ -L "$helper" || ! -d "$helper" \
  || ! -f "$helper_info" || -L "$helper_info" \
  || ! -d "$helper_resources" || -L "$helper_resources" \
  || -L "$helper_binary" ]]; then
  print -u2 -- "build_swift_app:FAIL:helper_path_invalid"; exit 1
fi
if [[ ! -x "$helper_binary" ]]; then
  print -u2 -- "Bundled helper is required before building the app."; exit 1
fi
if ! /usr/bin/python3 - "$helper_info" <<'PY'
import plistlib
import sys

try:
    with open(sys.argv[1], "rb") as stream:
        info = plistlib.load(stream)
    expected = {
        "CFBundleExecutable": "PhotosIndexerWorker",
        "CFBundleIdentifier": "com.photoslocalkeywordindexer.worker",
        "CFBundlePackageType": "APPL",
        "LSUIElement": True,
    }
    if any(info.get(key) != value for key, value in expected.items()):
        raise ValueError
except (OSError, ValueError, plistlib.InvalidFileException):
    raise SystemExit(1)
PY
then
  print -u2 -- "build_swift_app:FAIL:helper_bundle_identity"
  exit 1
fi

helper_source_marker="$helper_resources/.photos-indexer-source-fingerprint"
current_helper_source_fingerprint="$(
  unset PYTHONHOME PYTHONPATH
  /usr/bin/python3 "$project_root/packaging/helper_source_fingerprint.py" "$project_root" 2>/dev/null || true
)"
recorded_helper_source_fingerprint=""
helper_source_fingerprint_state="ok"
if [[ ! "$current_helper_source_fingerprint" =~ '^[0-9a-f]{64}$' ]]; then
  helper_source_fingerprint_state="unavailable"
elif [[ ! -f "$helper_source_marker" || -L "$helper_source_marker" ]]; then
  helper_source_fingerprint_state="missing"
else
  recorded_helper_source_fingerprint="$(<"$helper_source_marker")"
  if [[ ! "$recorded_helper_source_fingerprint" =~ '^[0-9a-f]{64}$' \
    || "$recorded_helper_source_fingerprint" != "$current_helper_source_fingerprint" ]]; then
    helper_source_fingerprint_state="stale"
  fi
fi
if [[ "$helper_source_fingerprint_state" != "ok" ]]; then
  if [[ "$release_build" == "1" ]]; then
    case "$helper_source_fingerprint_state" in
      missing) print -u2 -- "build_swift_app:FAIL:helper_source_fingerprint_missing" ;;
      stale) print -u2 -- "build_swift_app:FAIL:helper_source_fingerprint_stale" ;;
      *) print -u2 -- "build_swift_app:FAIL:helper_source_fingerprint_unavailable" ;;
    esac
    print -u2 -- "build_swift_app:HINT:helper_source_fingerprint:rebuild_the_python_helper"
    exit 2
  fi
  print -u2 -- "build_swift_app:WARN:helper_source_fingerprint_${helper_source_fingerprint_state}"
fi

# Remove any prior bundle only after its path has passed the build-root guard.
# A failed SwiftPM invocation must not leave an apparently usable app from a
# previous build for a later DMG step.
build_succeeded=0
app_bundle_created_identity=""
record_app_bundle_identity() {
  if [[ -d "$app_bundle" && ! -L "$app_bundle" ]]; then
    app_bundle_created_identity="$(stat -f '%d:%i' -- "$app_bundle" 2>/dev/null || true)"
  fi
}
app_bundle_is_owned() {
  local current_identity
  [[ -n "$app_bundle_created_identity" && -d "$app_bundle" && ! -L "$app_bundle" ]] || return 1
  current_identity="$(stat -f '%d:%i' -- "$app_bundle" 2>/dev/null || true)"
  [[ -n "$current_identity" && "$current_identity" == "$app_bundle_created_identity" ]]
}
cleanup_app_bundle() {
  if (( build_succeeded == 0 )) && app_bundle_is_owned; then
    rm -rf -- "$app_bundle"
  fi
}
trap cleanup_app_bundle EXIT
trap 'cleanup_app_bundle; exit 130' INT TERM
rm -rf -- "$app_bundle"

# Use the checked-in Package.resolved exactly; a missing cache must fail
# locally instead of silently resolving a different dependency revision.
"$swift_bin" build --package-path "$package_root" --disable-automatic-resolution --disable-sandbox -c release --arch arm64 \
  -Xlinker -rpath -Xlinker '@loader_path/../Frameworks'
binary="$package_root/.build/arm64-apple-macosx/release/PhotosLocalKeywordIndexer"
if [[ ! -x "$binary" ]]; then
  binary="$package_root/.build/release/PhotosLocalKeywordIndexer"
fi
if [[ ! -x "$binary" ]]; then
  print -u2 -- "SwiftPM did not produce PhotosLocalKeywordIndexer."; exit 1
fi
if [[ -L "$binary" ]]; then
  print -u2 -- "build_swift_app:FAIL:binary_path_invalid"; exit 1
fi
binary_architectures="$(lipo -archs "$binary" 2>/dev/null || print unknown)"
if [[ "$binary_architectures" != "arm64" ]]; then
  print -u2 -- "build_swift_app:FAIL:binary_architecture_not_arm64"; exit 1
fi

if ! mkdir -p "$app_bundle/Contents/MacOS" "$app_bundle/Contents/Resources" \
  "$app_bundle/Contents/Helpers" "$app_bundle/Contents/Frameworks"; then
  record_app_bundle_identity
  print -u2 -- "build_swift_app:FAIL:app_bundle_directory_creation"
  exit 1
fi
record_app_bundle_identity
if ! app_bundle_is_owned; then
  print -u2 -- "build_swift_app:FAIL:app_bundle_identity_changed"
  exit 1
fi
ditto "$binary" "$app_bundle/Contents/MacOS/PhotosLocalKeywordIndexer"
ditto "$project_root/packaging/AppInfo.plist" "$app_bundle/Contents/Info.plist"
mkdir -p "$app_bundle/Contents/Resources/Documentation/docs/release"
ditto "$project_root/LICENSE" "$app_bundle/Contents/Resources/Documentation/LICENSE"
ditto "$project_root/docs/privacy.md" "$app_bundle/Contents/Resources/Documentation/docs/privacy.md"
ditto "$project_root/docs/support.md" "$app_bundle/Contents/Resources/Documentation/docs/support.md"
ditto "$project_root/docs/release/third-party-notices.md" "$app_bundle/Contents/Resources/Documentation/docs/release/third-party-notices.md"
/usr/libexec/PlistBuddy -c "Set :PhotosLocalKeywordIndexerBuildChannel development" "$app_bundle/Contents/Info.plist"
if [[ "$release_build" == "1" ]]; then
  /usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $app_version" "$app_bundle/Contents/Info.plist"
  /usr/libexec/PlistBuddy -c "Set :CFBundleVersion $build_number" "$app_bundle/Contents/Info.plist"
  /usr/libexec/PlistBuddy -c "Set :PhotosLocalKeywordIndexerBuildChannel release" "$app_bundle/Contents/Info.plist"
fi
# AppInfo.plist declares CFBundleIconFile; generate that resource before the
# bundle is copied into a DMG or passed to the signing/verifier chain.
"$project_root/packaging/build_app_icon.sh" "$app_bundle/Contents/Resources/AppIcon.icns"

# SwiftPM does not turn a binary framework dependency into an app bundle
# automatically. Keep the framework inside Contents/Frameworks so the
# executable is self-contained before nested signing.
sparkle_framework=""
sparkle_locator_status=0
sparkle_framework="$(locate_sparkle_framework "$package_root/.build" "$package_root/Package.resolved" "2.9.2" "6276ba2b404829d139c45ff98427cf90e2efc59b")" || sparkle_locator_status=$?
if (( sparkle_locator_status == 0 )); then
  embedded_sparkle="$app_bundle/Contents/Frameworks/Sparkle.framework"
  sparkle_license="$package_root/.build/artifacts/sparkle/Sparkle/LICENSE"
  [[ -f "$sparkle_license" && ! -L "$sparkle_license" ]] || {
    print -u2 -- "build_swift_app:FAIL:sparkle_license_missing"; exit 1
  }
  ditto "$sparkle_license" "$app_bundle/Contents/Resources/Documentation/Sparkle-LICENSE"
  ditto --rsrc --extattr "$sparkle_framework" "$embedded_sparkle"
  if ! thin_sparkle_framework_arm64 \
    "$embedded_sparkle" "$(command -v lipo)" "$(command -v file)"; then
    print -u2 -- "build_swift_app:FAIL:sparkle_architecture"
    exit 1
  fi
elif (( sparkle_locator_status == 3 )); then
  print -u2 -- "Sparkle.framework selection is ambiguous; refusing to build an unsafe app bundle."
  exit 1
elif (( sparkle_locator_status == 2 )); then
  print -u2 -- "Sparkle.framework resolution metadata is invalid; refusing to build an unpinned app bundle."
  exit 2
else
  print -u2 -- "Sparkle.framework is required for every app build."; exit 1
fi

# Release builds provide the signed Sparkle feed settings explicitly. Local
# builds remain update-disabled instead of shipping placeholder values.
if [[ "$release_build" == "1" ]]; then
  /usr/libexec/PlistBuddy -c "Add :SUFeedURL string $feed_url" "$app_bundle/Contents/Info.plist"
  /usr/libexec/PlistBuddy -c "Add :SUPublicEDKey string $public_key" "$app_bundle/Contents/Info.plist"
  /usr/libexec/PlistBuddy -c "Add :SUEnableAutomaticChecks bool true" "$app_bundle/Contents/Info.plist"
fi

ditto "$helper" "$app_bundle/Contents/Helpers/PhotosIndexerWorker.app"

# Verify the copy that the native app will actually launch. Checking only the
# source artifact above is insufficient: an interrupted or symlinked copy must
# fail closed before this script announces an app bundle.
bundled_helper_root="$app_bundle/Contents/Helpers/PhotosIndexerWorker.app"
bundled_helper="$bundled_helper_root/Contents/MacOS/PhotosIndexerWorker"
if [[ ! -d "$bundled_helper_root" || -L "$bundled_helper_root" ]]; then
  print -u2 -- "App bundle copy did not produce an invocable embedded helper directory."
  exit 1
fi
if [[ ! -x "$bundled_helper" || -L "$bundled_helper" ]]; then
  print -u2 -- "App bundle copy did not produce an invocable embedded helper."
  exit 1
fi
if [[ "$verify_embedded_helper" == "1" ]]; then
  "$project_root/packaging/verify_embedded_helper.sh" "$app_bundle"
fi

development_sign_bundle_tree() {
  local bundle_root="$1"
  local bundle_entitlements="${2:-}"
  local candidate payload_kind

  while IFS= read -r -d $'\0' candidate; do
    payload_kind="$(file -b "$candidate" 2>/dev/null || true)"
    if [[ "$payload_kind" == *"Mach-O"* ]]; then
      codesign --force --sign "$development_sign_identity" "$candidate" >/dev/null
    fi
  done < <(find "$bundle_root" -type f -print0)

  while IFS= read -r -d $'\0' candidate; do
    [[ "$candidate" != "$bundle_root" ]] || continue
    codesign --force --sign "$development_sign_identity" "$candidate" >/dev/null
    codesign --verify --strict --verbose=2 "$candidate" >/dev/null
  done < <(find "$bundle_root" -depth -type d \( -name '*.app' -o -name '*.framework' -o -name '*.xpc' -o -name '*.bundle' \) -print0)

  if [[ -n "$bundle_entitlements" ]]; then
    codesign --force --sign "$development_sign_identity" --entitlements "$bundle_entitlements" "$bundle_root" >/dev/null
  else
    codesign --force --sign "$development_sign_identity" "$bundle_root" >/dev/null
  fi
  codesign --verify --strict --verbose=2 "$bundle_root" >/dev/null
}

if [[ "$release_build" == "0" ]]; then
  development_sign_bundle_tree "$bundled_helper_root" "$project_root/packaging/helper-entitlements.plist"
  development_sign_bundle_tree "$embedded_sparkle"
  codesign --force --sign "$development_sign_identity" "$app_bundle/Contents/MacOS/PhotosLocalKeywordIndexer" >/dev/null
  codesign --verify --strict --verbose=2 "$app_bundle/Contents/MacOS/PhotosLocalKeywordIndexer" >/dev/null
  codesign --force --sign "$development_sign_identity" --entitlements "$project_root/packaging/entitlements.plist" "$app_bundle" >/dev/null
  codesign --verify --strict --verbose=2 "$app_bundle" >/dev/null
  codesign --verify --deep --strict --verbose=2 "$app_bundle" >/dev/null
fi

if ! app_bundle_is_owned; then
  print -u2 -- "build_swift_app:FAIL:app_bundle_identity_changed"
  exit 1
fi
if [[ "$release_build" == "1" ]]; then
  build_succeeded=1
  trap - EXIT INT TERM
  print -- "release_app:READY_FOR_SIGNING_AND_DMG"
else
  build_succeeded=1
  trap - EXIT INT TERM
  print -- "release_app:HINT:development_build:not_for_official_distribution_run_verify_public_beta_before_any_authorized_publication"
  print -- "release_app:HINT:local_launch:use_finder_or_terminal_outside_sandboxed_agent"
  if [[ -n "$local_code_sign_identity" ]]; then
    print -- "release_app:HINT:development_signature:stable_local_identity_not_for_distribution"
  else
    print -- "release_app:HINT:development_signature:ad_hoc_permissions_may_reset_after_rebuild"
  fi
  print -- "release_app:READY_DEV"
fi
