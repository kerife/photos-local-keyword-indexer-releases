#!/bin/zsh
set -euo pipefail

# Release artifacts must never inherit a caller's permissive umask. Keep the
# DMG readable for installation while preventing group/other mutation; private
# staging and newly-created output directories are tightened separately below.
umask 022

project_root="${0:A:h:h}"
app_path="${1:-}"
output_root_input="${2:-$project_root/dist}"
release_build="${RELEASE_BUILD:-1}"
verify_dmg_layout="${VERIFY_DMG_LAYOUT:-1}"
if [[ -z "$app_path" || "$app_path" != /* || -L "$app_path" ]]; then
  print -u2 -- "App bundle path must not be a symlink."
  exit 2
fi
app_path="${app_path:a}"

source "$project_root/packaging/build_path_guard.zsh"
if ! validate_build_path "$app_path" "$project_root/build" "app bundle" >/dev/null 2>&1; then
  print -u2 -- "build_dmg:FAIL:app_bundle_path_invalid"
  print -u2 -- "build_dmg:HINT:app_bundle_path_invalid:provide_a_complete_app_bundle_under_build"
  exit 2
fi
if ! output_path_validation_error="$(validate_build_path "$output_root_input" "$project_root/dist" "output directory" 2>&1)"; then
  if [[ "$output_path_validation_error" == *"contains a symlinked component"* ]]; then
    print -u2 -- "build_dmg:FAIL:output_directory_symlink_escape"
    print -u2 -- "build_dmg:HINT:output_directory_symlink_escape:provide_a_private_output_directory_under_dist"
  else
    print -u2 -- "build_dmg:FAIL:output_directory_invalid"
    print -u2 -- "build_dmg:HINT:output_directory_invalid:provide_a_private_output_directory_under_dist"
  fi
  exit 2
fi
output_root="${output_root_input:A}"

if [[ ! -d "$app_path" || "${app_path:e}" != "app" ]]; then
  print -u2 -- "build_dmg:FAIL:app_bundle_path_invalid"
  print -u2 -- "build_dmg:HINT:app_bundle_path_invalid:provide_a_complete_app_bundle_under_build"
  exit 2
fi
if [[ "${app_path:t}" != "PhotosLocalKeywordIndexer.app" ]]; then
  print -u2 -- "App bundle must be named PhotosLocalKeywordIndexer.app."
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
if [[ "$release_build" != "0" && "$release_build" != "1" ]]; then
  print -u2 -- "build_dmg:FAIL:release_build_invalid"
  print -u2 -- "build_dmg:HINT:release_build_invalid:use_RELEASE_BUILD_0_or_1"
  exit 2
fi
if [[ "$verify_dmg_layout" != "0" && "$verify_dmg_layout" != "1" ]]; then
  print -u2 -- "build_dmg:FAIL:verify_dmg_layout_invalid"
  print -u2 -- "build_dmg:HINT:verify_dmg_layout_invalid:use_VERIFY_DMG_LAYOUT_0_or_1"
  exit 2
fi
if [[ "$release_build" == "1" && "$verify_dmg_layout" != "1" ]]; then
  print -u2 -- "build_dmg:FAIL:dmg_layout_verification_required"
  print -u2 -- "build_dmg:HINT:dmg_layout_verification_required:run_with_VERIFY_DMG_LAYOUT_1"
  exit 2
fi
if [[ "$release_build" == "1" && -z "${DEVELOPER_ID_APPLICATION:-}" ]]; then
  print -u2 -- "build_dmg:FAIL:developer_id_identity_missing"
  print -u2 -- "build_dmg:HINT:developer_id_identity_missing:install_or_select_a_developer_id_application_identity"
  exit 2
fi
if ! command -v hdiutil >/dev/null 2>&1; then
  print -u2 -- "build_dmg:FAIL:hdiutil_missing"
  print -u2 -- "build_dmg:HINT:hdiutil_missing:run_on_macos_release_host_with_hdiutil"
  exit 2
fi
for command_name in ditto ln; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    print -u2 -- "build_dmg:FAIL:${command_name}_missing"
    print -u2 -- "build_dmg:HINT:${command_name}_missing:run_on_macos_release_host_with_build_tools"
    exit 2
  fi
done
if ! command -v stat >/dev/null 2>&1; then
  print -u2 -- "build_dmg:FAIL:stat_missing"
  print -u2 -- "build_dmg:HINT:stat_missing:run_on_macos_release_host_with_build_tools"
  exit 2
fi
if [[ ! -x /usr/libexec/PlistBuddy ]]; then
  print -u2 -- "build_dmg:FAIL:plistbuddy_missing"
  print -u2 -- "build_dmg:HINT:plistbuddy_missing:run_on_macos_release_host_with_plistbuddy"
  exit 2
fi
if [[ "$release_build" == "1" ]]; then
  for command_name in codesign spctl; do
    if ! command -v "$command_name" >/dev/null 2>&1; then
      print -u2 -- "build_dmg:FAIL:${command_name}_missing"
      print -u2 -- "build_dmg:HINT:${command_name}_missing:run_on_macos_release_host_with_signing_tools"
      exit 2
    fi
  done
fi
if [[ ! -f "$app_path/Contents/Info.plist" || ! -x "$app_path/Contents/MacOS/PhotosLocalKeywordIndexer" ]]; then
  print -u2 -- "build_dmg:FAIL:app_bundle_incomplete"
  print -u2 -- "build_dmg:HINT:app_bundle_incomplete:rebuild_the_app_before_creating_a_dmg"
  exit 2
fi
app_version="$(/usr/libexec/PlistBuddy -c "Print :CFBundleShortVersionString" "$app_path/Contents/Info.plist" 2>/dev/null || true)"
build_number="$(/usr/libexec/PlistBuddy -c "Print :CFBundleVersion" "$app_path/Contents/Info.plist" 2>/dev/null || true)"
if [[ ! "$app_version" =~ '^[0-9]+\.[0-9]+\.[0-9]+$' || ! "$build_number" =~ '^[1-9][0-9]*$' ]]; then
  print -u2 -- "build_dmg:FAIL:app_version_invalid"
  print -u2 -- "build_dmg:HINT:app_version_invalid:fix_app_version_and_rebuild"
  exit 2
fi
record_app_bundle_identity

# A release DMG must contain the exact helper produced by the current helper
# build. Without this comparison, an older app bundle could be signed and
# distributed after the helper was rebuilt, leaving the shipped runtime out of
# sync with the source-side release checks.
if [[ "$release_build" == "1" ]]; then
  source_helper_app="$project_root/build/python-helper/dist/PhotosIndexerWorker.app"
  app_helper_app="$app_path/Contents/Helpers/PhotosIndexerWorker.app"
  source_helper="$source_helper_app/Contents/MacOS/PhotosIndexerWorker"
  app_helper="$app_helper_app/Contents/MacOS/PhotosIndexerWorker"
  if ! validate_build_path "$source_helper_app" "$project_root/build" "helper source" >/dev/null 2>&1 \
    || [[ ! -d "$source_helper_app" || -L "$source_helper_app" \
      || ! -d "$app_helper_app" || -L "$app_helper_app" \
      || ! -f "$source_helper" || -L "$source_helper" \
      || ! -f "$app_helper" || -L "$app_helper" ]]; then
    print -u2 -- "build_dmg:FAIL:embedded_helper_stale"
    print -u2 -- "build_dmg:HINT:embedded_helper_stale:rebuild_the_app_with_the_current_helper"
    exit 2
  fi
  source_fingerprint_marker="$source_helper_app/Contents/Resources/.photos-indexer-source-fingerprint"
  app_fingerprint_marker="$app_helper_app/Contents/Resources/.photos-indexer-source-fingerprint"
  if [[ ! -x /usr/bin/python3 ]]; then
    print -u2 -- "build_dmg:FAIL:helper_source_fingerprint_unavailable"
    print -u2 -- "build_dmg:HINT:helper_source_fingerprint_unavailable:run_on_a_macos_release_host_with_system_python"
    exit 2
  fi
  current_helper_source_fingerprint="$(
    unset PYTHONHOME PYTHONPATH
    /usr/bin/python3 "$project_root/packaging/helper_source_fingerprint.py" "$project_root" 2>/dev/null || true
  )"
  if [[ ! "$current_helper_source_fingerprint" =~ '^[0-9a-f]{64}$' \
    || ! -f "$source_fingerprint_marker" || -L "$source_fingerprint_marker" \
    || ! -f "$app_fingerprint_marker" || -L "$app_fingerprint_marker" ]]; then
    print -u2 -- "build_dmg:FAIL:helper_source_fingerprint_missing"
    print -u2 -- "build_dmg:HINT:helper_source_fingerprint_missing:rebuild_the_helper_and_app"
    exit 2
  fi
  source_helper_source_fingerprint="$(<"$source_fingerprint_marker")"
  app_helper_source_fingerprint="$(<"$app_fingerprint_marker")"
  if [[ ! "$source_helper_source_fingerprint" =~ '^[0-9a-f]{64}$' \
    || "$source_helper_source_fingerprint" != "$current_helper_source_fingerprint" \
    || "$app_helper_source_fingerprint" != "$source_helper_source_fingerprint" ]]; then
    print -u2 -- "build_dmg:FAIL:helper_source_fingerprint_stale"
    print -u2 -- "build_dmg:HINT:helper_source_fingerprint_stale:rebuild_the_helper_and_app"
    exit 2
  fi
  if ! cmp -s "$source_helper" "$app_helper"; then
    print -u2 -- "build_dmg:FAIL:embedded_helper_stale"
    print -u2 -- "build_dmg:HINT:embedded_helper_stale:rebuild_the_app_with_the_current_helper"
    exit 2
  fi
  helper_payload_check=""
  if [[ ! -x /usr/bin/python3 ]]; then
    print -u2 -- "build_dmg:FAIL:embedded_helper_payload_uncheckable"
    print -u2 -- "build_dmg:HINT:embedded_helper_payload_uncheckable:run_on_a_macos_release_host_with_system_python"
    exit 2
  fi
  if ! helper_payload_check="$(/usr/bin/python3 - "$source_helper_app" "$app_helper_app" <<'PY'
import hashlib
import os
import stat
import sys


def snapshot(root):
    entries = {}
    for directory, directories, files in os.walk(root, followlinks=False):
        names = sorted(directories + files)
        for name in names:
            path = os.path.join(directory, name)
            relative = os.path.relpath(path, root)
            metadata = os.lstat(path)
            mode = stat.S_IMODE(metadata.st_mode)
            if stat.S_ISLNK(metadata.st_mode):
                entries[relative] = ("link", os.readlink(path))
            elif stat.S_ISREG(metadata.st_mode):
                digest = hashlib.sha256()
                with open(path, "rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                entries[relative] = ("file", mode, metadata.st_size, digest.digest())
            elif stat.S_ISDIR(metadata.st_mode):
                entries[relative] = ("directory", mode)
            else:
                raise ValueError
    return entries


try:
    if snapshot(sys.argv[1]) != snapshot(sys.argv[2]):
        raise SystemExit(1)
except (OSError, ValueError, IndexError):
    raise SystemExit(1)
print("ok")
PY
)" || helper_payload_check="invalid"
  if [[ "$helper_payload_check" != "ok" ]]; then
    print -u2 -- "build_dmg:FAIL:embedded_helper_payload_stale"
    print -u2 -- "build_dmg:HINT:embedded_helper_payload_stale:rebuild_the_app_with_the_current_helper"
    exit 2
  fi
fi

# Validate the output target and reserve the artifact name before signing the
# input app. A bad destination or an existing artifact must not mutate the
# app as a side effect of a release attempt that will fail anyway.
case "$output_root" in
  "$project_root"/dist|"$project_root"/dist/*) ;;
  *)
    print -u2 -- "build_dmg:FAIL:output_directory_outside_release_root"
    exit 2
    ;;
esac

output_root_preexisting=0
output_root_created_identity=""
record_output_root_identity() {
  if [[ -d "$output_root" && ! -L "$output_root" ]]; then
    output_root_created_identity="$(stat -f '%d:%i' -- "$output_root" 2>/dev/null || true)"
  fi
}
output_root_is_owned() {
  local current_identity
  [[ -n "$output_root_created_identity" && -d "$output_root" && ! -L "$output_root" ]] || return 1
  current_identity="$(stat -f '%d:%i' -- "$output_root" 2>/dev/null || true)"
  [[ -n "$current_identity" && "$current_identity" == "$output_root_created_identity" ]]
}
[[ -e "$output_root" ]] && output_root_preexisting=1
mkdir -p "$output_root"
if (( output_root_preexisting == 0 )); then
  chmod 700 "$output_root"
  record_output_root_identity
fi
physical_output_root="$(cd "$output_root" && pwd -P)"
physical_dist_root="$(cd "$project_root/dist" && pwd -P)"
case "$physical_output_root" in
  "$physical_dist_root"|"$physical_dist_root"/*) ;;
  *)
    print -u2 -- "build_dmg:FAIL:output_directory_symlink_escape"
    exit 2
    ;;
esac

# hdiutil and codesign create and mutate the release artifact inside this
# directory.  Every release-owned component from dist down to the selected
# destination must therefore remain owned by this user and immutable to group
# and other users; otherwise another local account could replace the reserved
# artifact between validation and signing.
output_directory_candidate="$physical_output_root"
while true; do
  output_directory_owner="$(stat -f '%u' -- "$output_directory_candidate" 2>/dev/null || true)"
  output_directory_mode="$(stat -f '%Lp' -- "$output_directory_candidate" 2>/dev/null || true)"
  if [[ ! "$output_directory_owner" =~ '^[0-9]+$' || ! "$output_directory_mode" =~ '^[0-7]{3,4}$' ]]; then
    print -u2 -- "build_dmg:FAIL:output_directory_integrity_unavailable"
    print -u2 -- "build_dmg:HINT:output_directory_integrity_unavailable:recreate_a_private_release_output_directory"
    exit 2
  fi
  if [[ "$output_directory_owner" != "$EUID" ]]; then
    print -u2 -- "build_dmg:FAIL:output_directory_ownership"
    print -u2 -- "build_dmg:HINT:output_directory_ownership:recreate_the_release_output_as_the_current_user"
    exit 2
  fi
  if (( (8#$output_directory_mode & 8#22) != 0 )); then
    print -u2 -- "build_dmg:FAIL:output_directory_permissions"
    print -u2 -- "build_dmg:HINT:output_directory_permissions:remove_group_world_write_then_retry"
    exit 2
  fi
  [[ "$output_directory_candidate" == "$physical_dist_root" ]] && break
  output_directory_candidate="${output_directory_candidate:h}"
done
app_name="${app_path:t:r}"
dmg_stem="$app_name-$app_version-$build_number"
if [[ "$release_build" == "0" ]]; then
  dmg_stem="$dmg_stem-dev-arm64"
fi
dmg_path="$physical_output_root/$dmg_stem.dmg"
if [[ -e "$dmg_path" || -L "$dmg_path" ]]; then
  print -u2 -- "Refusing to overwrite an existing DMG."
  exit 2
fi

staging_dir=""
staging_created_identity=""
preserve_dmg=0
dmg_created_identity=""
reservation_path="$dmg_path.reservation"
reservation_acquired=0
reservation_created_identity=""
cleanup_failed=0

record_dmg_identity() {
  if [[ -f "$dmg_path" && ! -L "$dmg_path" ]]; then
    dmg_created_identity="$(stat -f '%d:%i' -- "$dmg_path" 2>/dev/null || true)"
  fi
}

dmg_is_owned() {
  local current_identity
  [[ -n "$dmg_created_identity" && -f "$dmg_path" && ! -L "$dmg_path" ]] || return 1
  current_identity="$(stat -f '%d:%i' -- "$dmg_path" 2>/dev/null || true)"
  [[ -n "$current_identity" && "$current_identity" == "$dmg_created_identity" ]]
}

record_reservation_identity() {
  if [[ -d "$reservation_path" && ! -L "$reservation_path" ]]; then
    reservation_created_identity="$(stat -f '%d:%i' -- "$reservation_path" 2>/dev/null || true)"
  fi
}

reservation_is_owned() {
  local current_identity
  [[ -n "$reservation_created_identity" && -d "$reservation_path" && ! -L "$reservation_path" ]] || return 1
  current_identity="$(stat -f '%d:%i' -- "$reservation_path" 2>/dev/null || true)"
  [[ -n "$current_identity" && "$current_identity" == "$reservation_created_identity" ]]
}

record_staging_identity() {
  if [[ -d "$staging_dir" && ! -L "$staging_dir" ]]; then
    staging_created_identity="$(stat -f '%d:%i' -- "$staging_dir" 2>/dev/null || true)"
  fi
}

staging_is_owned() {
  local current_identity
  [[ -n "$staging_created_identity" && -d "$staging_dir" && ! -L "$staging_dir" ]] || return 1
  current_identity="$(stat -f '%d:%i' -- "$staging_dir" 2>/dev/null || true)"
  [[ -n "$current_identity" && "$current_identity" == "$staging_created_identity" ]]
}

cleanup_build_artifacts() {
  cleanup_failed=0
  # hdiutil consumes the staging directory. If a concurrent process replaces
  # it while the command is running, remove only the directory created by
  # this build; never recursively delete the replacement.
  if staging_is_owned; then
    rm -rf -- "$staging_dir" >/dev/null 2>&1 || cleanup_failed=1
  fi
  # hdiutil, image validation, or codesign may fail after creating a partial
  # artifact.  Remove only the newly-selected regular file; never follow a
  # replacement symlink or remove a directory supplied by another process.
  if (( preserve_dmg == 0 )) && dmg_is_owned; then
    rm -f -- "$dmg_path" >/dev/null 2>&1 || cleanup_failed=1
  fi
  if (( reservation_acquired == 1 )) && reservation_is_owned; then
    rmdir -- "$reservation_path" >/dev/null 2>&1 || cleanup_failed=1
  fi
  # If setup created a new destination but the reservation/build failed, remove
  # only that now-empty directory. Never remove a caller-owned destination.
  if (( preserve_dmg == 0 && output_root_preexisting == 0 )) && output_root_is_owned; then
    rmdir -- "$output_root" >/dev/null 2>&1 || cleanup_failed=1
  fi
  return 0
}
trap cleanup_build_artifacts EXIT
trap 'cleanup_build_artifacts; exit 130' INT TERM

# An existence check alone is racy: two release processes could both pass it
# and then sign/create the same artifact.  mkdir is atomic, so the owner keeps
# an exclusive reservation for the complete build and releases it on exit.
if ! mkdir "$reservation_path" 2>/dev/null; then
  print -u2 -- "Another build is already producing this DMG; retry after it finishes."
  exit 2
fi
reservation_acquired=1
record_reservation_identity
if [[ -e "$dmg_path" || -L "$dmg_path" ]]; then
  print -u2 -- "Refusing to overwrite an existing DMG."
  exit 2
fi

if [[ "$release_build" == "1" ]]; then
  "$project_root/packaging/sign_app.sh" "$app_path"
  "$project_root/packaging/verify_release.sh" "$app_path"
  if ! spctl --assess --type execute --verbose=4 "$app_path" >/dev/null 2>&1; then
    print -u2 -- "build_dmg:FAIL:app_assessment_invalid"
    print -u2 -- "build_dmg:HINT:app_assessment_invalid:rebuild_and_sign_the_app_then_retry"
    exit 1
  fi
fi

staging_dir="$(mktemp -d "$output_root/.dmg-staging.XXXXXX")"
record_staging_identity
if ! staging_is_owned; then
  print -u2 -- "build_dmg:FAIL:staging_directory_invalid"
  print -u2 -- "build_dmg:HINT:staging_directory_invalid:retry_the_dmg_build"
  exit 1
fi
if ! app_bundle_is_owned; then
  print -u2 -- "build_dmg:FAIL:app_bundle_identity_changed"
  print -u2 -- "build_dmg:HINT:app_bundle_identity_changed:rebuild_the_app_then_retry"
  exit 1
fi
if ! ditto "$app_path" "$staging_dir/$app_name.app" >/dev/null 2>&1; then
  print -u2 -- "build_dmg:FAIL:app_staging_failed"
  print -u2 -- "build_dmg:HINT:app_staging_failed:retry_the_dmg_build"
  exit 1
fi
if ! app_bundle_is_owned; then
  print -u2 -- "build_dmg:FAIL:app_bundle_identity_changed"
  print -u2 -- "build_dmg:HINT:app_bundle_identity_changed:rebuild_the_app_then_retry"
  exit 1
fi
if ! staging_is_owned; then
  print -u2 -- "build_dmg:FAIL:staging_directory_invalid"
  print -u2 -- "build_dmg:HINT:staging_directory_invalid:retry_the_dmg_build"
  exit 1
fi
if ! ln -s /Applications "$staging_dir/Applications" >/dev/null 2>&1; then
  print -u2 -- "build_dmg:FAIL:applications_alias_failed"
  print -u2 -- "build_dmg:HINT:applications_alias_failed:retry_the_dmg_build"
  exit 1
fi

if ! hdiutil create \
  -volname "$app_name" \
  -srcfolder "$staging_dir" \
  -format UDZO \
  "$dmg_path" >/dev/null 2>&1; then
  # hdiutil can leave a partial file even when it exits nonzero. Record its
  # identity before cleanup so a later replacement at the same pathname is
  # never removed by this process.
  record_dmg_identity
  print -u2 -- "build_dmg:FAIL:dmg_creation_failed"
  print -u2 -- "build_dmg:HINT:dmg_creation_failed:retry_the_dmg_build"
  exit 1
fi
record_dmg_identity

if [[ ! -s "$dmg_path" ]]; then
  print -u2 -- "build_dmg:FAIL:dmg_empty_artifact"
  print -u2 -- "build_dmg:HINT:dmg_empty_artifact:retry_the_dmg_build"
  exit 1
fi

if ! hdiutil imageinfo "$dmg_path" >/dev/null 2>&1; then
  print -u2 -- "build_dmg:FAIL:dmg_image_invalid"
  print -u2 -- "build_dmg:HINT:dmg_image_invalid:retry_the_dmg_build"
  exit 1
fi

if [[ "$verify_dmg_layout" == "1" ]]; then
  "$project_root/packaging/verify_dmg_layout.sh" "$dmg_path"
fi

if [[ "$release_build" == "1" ]]; then
  if ! codesign --force --sign "$DEVELOPER_ID_APPLICATION" --timestamp "$dmg_path" >/dev/null 2>&1; then
    print -u2 -- "build_dmg:FAIL:dmg_signing_failed"
    print -u2 -- "build_dmg:HINT:dmg_signing_failed:check_developer_id_and_retry"
    exit 1
  fi
  if ! codesign --verify --verbose=2 --strict "$dmg_path" >/dev/null 2>&1; then
    print -u2 -- "build_dmg:FAIL:dmg_signature_invalid"
    print -u2 -- "build_dmg:HINT:dmg_signature_invalid:rebuild_and_sign_the_dmg_then_retry"
    exit 1
  fi
fi

preserve_dmg=1
cleanup_build_artifacts
if (( cleanup_failed == 1 )); then
  print -u2 -- "build_dmg:FAIL:final_cleanup_failed"
  print -u2 -- "build_dmg:HINT:final_cleanup_failed:remove_stale_private_build_state_then_retry"
  exit 1
fi
trap - EXIT INT TERM
if [[ "$release_build" == "1" ]]; then
  print -- "release_dmg:READY_FOR_NOTARIZATION"
else
  print -- "release_dmg:HINT:development_build:not_for_official_distribution_run_verify_public_beta_before_any_authorized_publication"
  print -- "release_dmg:READY_DEV"
fi
