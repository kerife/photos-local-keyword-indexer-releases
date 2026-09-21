#!/bin/zsh
set -euo pipefail

project_root="${0:A:h:h}"
app_path="${1:-}"
identity="${DEVELOPER_ID_APPLICATION:-}"
app_entitlements="${ENTITLEMENTS_FILE:-$project_root/packaging/entitlements.plist}"
helper_entitlements="${HELPER_ENTITLEMENTS_FILE:-$project_root/packaging/helper-entitlements.plist}"

if [[ -z "$app_path" || "$app_path" != /* || "${app_path:t}" != *.app || -L "$app_path" || ! -d "$app_path" ]]; then
  print -u2 -- "sign_app:FAIL:app_bundle_path_invalid"
  print -u2 -- "sign_app:HINT:app_bundle_path_invalid:provide_a_complete_app_bundle_under_build"
  exit 2
fi
app_path="${app_path:a}"
source "$project_root/packaging/build_path_guard.zsh"
validate_build_path "$app_path" "$project_root/build" "app bundle"
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
if [[ -z "$identity" ]]; then
  print -u2 -- "sign_app:FAIL:developer_id_identity_missing"
  print -u2 -- "sign_app:HINT:developer_id_identity_missing:install_or_select_a_developer_id_application_identity"
  exit 2
fi
for command_name in codesign file find readlink; do
  command -v "$command_name" >/dev/null 2>&1 || {
    print -u2 -- "Required signing tool is unavailable: $command_name"
    exit 2
  }
done
for command_name in plutil lipo spctl; do
  command -v "$command_name" >/dev/null 2>&1 || {
    print -u2 -- "Required release verification tool is unavailable: $command_name"
    exit 1
  }
done
[[ -f "$app_entitlements" && -f "$helper_entitlements" ]] || {
  print -u2 -- "App and helper entitlement files are required."
  exit 2
}

contents="$app_path/Contents"
main="$contents/MacOS/PhotosLocalKeywordIndexer"
info="$contents/Info.plist"
helper_app="$contents/Helpers/PhotosIndexerWorker.app"
helper="$helper_app/Contents/MacOS/PhotosIndexerWorker"
helper_info="$helper_app/Contents/Info.plist"
legacy_helper="$contents/Helpers/PhotosIndexerWorker/PhotosIndexerWorker"
framework="$contents/Frameworks/Sparkle.framework"
[[ ! -e "$legacy_helper" && ! -L "$legacy_helper" \
  && -x "$main" && -f "$info" && -x "$helper" && -f "$helper_info" && -d "$framework" ]] || {
  print -u2 -- "App bundle is missing its main executable, helper, or Sparkle framework."
  exit 2
}
for bundle_component in "$contents" "$main" "$info" "$helper_app" "$helper_app/Contents" "$helper_app/Contents/MacOS" "$helper" "$helper_info" "$framework" "$app_entitlements" "$helper_entitlements"; do
  [[ ! -L "$bundle_component" ]] || {
    print -u2 -- "sign_app:FAIL:bundle_symlink"
    print -u2 -- "sign_app:HINT:bundle_symlink:rebuild_bundle_without_symlinked_core_nodes_then_retry"
    exit 2
  }
done

# codesign may rewrite Mach-O files and their code signatures in place. Refuse
# bundle files that are hardlinked to another artifact, or owned by a different
# user, before any signing command can mutate shared/untrusted content.
if [[ ! -x /usr/bin/python3 ]]; then
  print -u2 -- "sign_app:FAIL:bundle_integrity_check_unavailable"
  print -u2 -- "sign_app:HINT:bundle_integrity_check_unavailable:install_macos_system_python_then_retry"
  exit 1
fi
bundle_hardlink_check="$(/usr/bin/python3 - "$app_path" <<'PY'
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
        if metadata.st_mode & 0o022:
            print("permissions")
            raise SystemExit(0)
print("ok")
PY
)" || bundle_hardlink_check="unavailable"
case "$bundle_hardlink_check" in
  ok) ;;
  hardlink)
    print -u2 -- "sign_app:FAIL:bundle_hardlink"
    print -u2 -- "sign_app:HINT:bundle_hardlink:copy_bundle_to_unique_files_then_retry"
    exit 2
    ;;
  ownership)
    print -u2 -- "sign_app:FAIL:bundle_ownership"
    print -u2 -- "sign_app:HINT:bundle_ownership:rebuild_bundle_as_current_user_then_retry"
    exit 2
    ;;
  permissions)
    print -u2 -- "sign_app:FAIL:bundle_permissions"
    print -u2 -- "sign_app:HINT:bundle_permissions:remove_group_world_write_then_retry"
    exit 2
    ;;
  *)
    print -u2 -- "sign_app:FAIL:bundle_integrity_check_unavailable"
    print -u2 -- "sign_app:HINT:bundle_integrity_check_unavailable:install_macos_system_python_then_retry"
    exit 1
    ;;
esac

sign_flags=(--force --options runtime --timestamp --sign "$identity")

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
        print -u2 -- "sign_app:FAIL:bundle_symlink_escape"
        print -u2 -- "sign_app:HINT:bundle_symlink_escape:rebuild_bundle_without_external_symlinks_then_retry"
        return 1
        ;;
    esac
  done < <(find "$root" -type l -print0)
}

is_macho() {
  local candidate="$1"
  [[ -f "$candidate" && ! -L "$candidate" ]] || return 1
  file -b "$candidate" | grep -q "Mach-O"
}

sign_macho() {
  local candidate="$1"
  if codesign -d --entitlements :- "$candidate" >/dev/null 2>&1; then
    codesign "${sign_flags[@]}" --preserve-metadata=identifier,entitlements "$candidate"
  else
    codesign "${sign_flags[@]}" "$candidate"
  fi
}

sign_tree_inner_first() {
  local root="$1"
  local candidate
  local -a targets=()
  while IFS= read -r candidate; do
    if is_macho "$candidate"; then
      targets+=("$candidate")
    fi
  done < <(find "$root" -type f -print)

  local ordered
  ordered="$(printf '%s\n' "${targets[@]}" | awk -F/ '{print NF "\t" $0}' | sort -rn | cut -f2-)"
  while IFS= read -r candidate; do
    [[ -n "$candidate" ]] || continue
    sign_macho "$candidate"
  done <<< "$ordered"
}

sign_bundle() {
  local bundle="$1"
  if codesign -d --entitlements :- "$bundle" >/dev/null 2>&1; then
    codesign "${sign_flags[@]}" --preserve-metadata=identifier,entitlements "$bundle"
  else
    codesign "${sign_flags[@]}" "$bundle"
  fi
}

sign_nested_bundles() {
  local root="$1"
  local candidate
  local -a bundles=()
  while IFS= read -r candidate; do
    [[ "$candidate" != "$root" ]] && bundles+=("$candidate")
  done < <(find "$root" -type d \( -name '*.app' -o -name '*.xpc' -o -name '*.framework' -o -name '*.bundle' -o -name '*.appex' \) -print)

  local ordered
  ordered="$(printf '%s\n' "${bundles[@]}" | awk -F/ '{print NF "\t" $0}' | sort -rn | cut -f2-)"
  while IFS= read -r candidate; do
    [[ -n "$candidate" ]] || continue
    sign_bundle "$candidate"
  done <<< "$ordered"
}

# PyInstaller and Sparkle may contain nested dylibs, extensions, XPC services,
# and updater apps. Sign every Mach-O leaf before its containing code object.
verify_symlink_targets "$contents"
verify_symlink_targets "$helper_app"
verify_symlink_targets "$framework"
# A valid signature must bind the current Python worker, not merely a healthy
# frozen runtime copied by an older app build. The marker is already covered by
# the bundle ownership/hardlink/permission and symlink checks above.
helper_source_marker="$helper_app/Contents/Resources/.photos-indexer-source-fingerprint"
current_helper_source_fingerprint="$(
  unset PYTHONHOME PYTHONPATH
  /usr/bin/python3 "$project_root/packaging/helper_source_fingerprint.py" "$project_root" 2>/dev/null || true
)"
if [[ ! "$current_helper_source_fingerprint" =~ '^[0-9a-f]{64}$' ]]; then
  print -u2 -- "sign_app:FAIL:helper_source_fingerprint_unavailable"
  print -u2 -- "sign_app:HINT:helper_source_fingerprint_unavailable:verify_helper_source_inputs_then_retry"
  exit 2
fi
if [[ ! -f "$helper_source_marker" || -L "$helper_source_marker" ]]; then
  print -u2 -- "sign_app:FAIL:helper_source_fingerprint_missing"
  print -u2 -- "sign_app:HINT:helper_source_fingerprint_missing:rebuild_the_helper_and_app"
  exit 2
fi
recorded_helper_source_fingerprint="$(<"$helper_source_marker")"
if [[ ! "$recorded_helper_source_fingerprint" =~ '^[0-9a-f]{64}$' \
  || "$recorded_helper_source_fingerprint" != "$current_helper_source_fingerprint" ]]; then
  print -u2 -- "sign_app:FAIL:helper_source_fingerprint_stale"
  print -u2 -- "sign_app:HINT:helper_source_fingerprint_stale:rebuild_the_helper_and_app"
  exit 2
fi
if ! /usr/bin/python3 - "$info" "$helper_info" <<'PY'
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
  print -u2 -- "sign_app:FAIL:helper_bundle_version_invalid"
  print -u2 -- "sign_app:HINT:helper_bundle_version_invalid:rebuild_the_helper_and_app"
  exit 2
fi
if ! app_bundle_is_owned; then
  print -u2 -- "sign_app:FAIL:app_bundle_identity_changed"
  print -u2 -- "sign_app:HINT:app_bundle_identity_changed:rebuild_the_app_then_retry"
  exit 1
fi
sign_tree_inner_first "$helper_app"
codesign "${sign_flags[@]}" --identifier "com.photoslocalkeywordindexer.worker" "$helper"
sign_nested_bundles "$helper_app"
codesign "${sign_flags[@]}" --identifier "com.photoslocalkeywordindexer.worker" --entitlements "$helper_entitlements" \
  "$helper_app"

sign_tree_inner_first "$framework"
sign_nested_bundles "$framework"
codesign "${sign_flags[@]}" "$framework"

codesign "${sign_flags[@]}" --identifier "com.photoslocalkeywordindexer.app" "$main"
codesign "${sign_flags[@]}" --identifier "com.photoslocalkeywordindexer.app" --entitlements "$app_entitlements" "$app_path"

"$project_root/packaging/verify_release.sh" "$app_path"
if ! app_bundle_is_owned; then
  print -u2 -- "sign_app:FAIL:app_bundle_identity_changed"
  print -u2 -- "sign_app:HINT:app_bundle_identity_changed:rebuild_the_app_then_retry"
  exit 1
fi
print -- "release_signature:READY_FOR_DMG"
