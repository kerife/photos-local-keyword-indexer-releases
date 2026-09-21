#!/bin/zsh
set -euo pipefail

# The lock and its owner file coordinate a long-running notarization. Do not
# let a permissive caller umask make either node writable by another user.
umask 077

dmg_path="${1:-}"
notary_profile="${APPLE_NOTARY_PROFILE:-}"
script_root="${0:A:h}"
project_root="${script_root:h}"
mount_dir=""
mount_dir_created_identity=""
mounted=0
notary_tmp=""
notarize_lock=""
notarize_lock_owner=""
notarize_lock_acquired=0
notarize_lock_created_identity=""
notary_tmp_created_identity=""
evidence_written=0
notarize_completed=0
evidence_path=""
evidence_created_identity=""
release_evidence_fingerprint=""
notarized_dmg_fingerprint=""
finalized_dmg_fingerprint=""
cleanup_failed=0

record_mount_dir_identity() {
  if [[ -d "$mount_dir" && ! -L "$mount_dir" ]]; then
    mount_dir_created_identity="$(stat -f '%d:%i' -- "$mount_dir" 2>/dev/null || true)"
  fi
}

mount_dir_is_owned() {
  local current_identity
  [[ -n "$mount_dir_created_identity" && -d "$mount_dir" && ! -L "$mount_dir" ]] || return 1
  current_identity="$(stat -f '%d:%i' -- "$mount_dir" 2>/dev/null || true)"
  [[ -n "$current_identity" && "$current_identity" == "$mount_dir_created_identity" ]]
}

record_evidence_identity() {
  if [[ -f "$evidence_path" && ! -L "$evidence_path" ]]; then
    evidence_created_identity="$(stat -f '%d:%i' -- "$evidence_path" 2>/dev/null || true)"
  fi
}

evidence_is_owned() {
  local current_identity
  [[ -n "$evidence_created_identity" && -f "$evidence_path" && ! -L "$evidence_path" ]] || return 1
  current_identity="$(stat -f '%d:%i' -- "$evidence_path" 2>/dev/null || true)"
  [[ -n "$current_identity" && "$current_identity" == "$evidence_created_identity" ]]
}

evidence_fingerprint() {
  /usr/bin/python3 - "$evidence_path" <<'PY'
import hashlib
import os
import stat
import sys

def stable_metadata(metadata):
    return (
        metadata.st_dev,
        metadata.st_ino,
        stat.S_IMODE(metadata.st_mode),
        metadata.st_nlink,
        metadata.st_uid,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )

try:
    descriptor = os.open(sys.argv[1], os.O_RDONLY | os.O_NOFOLLOW)
    metadata_before = os.fstat(descriptor)
    if not stat.S_ISREG(metadata_before.st_mode) or metadata_before.st_nlink != 1:
        raise ValueError
    digest = hashlib.sha256()
    for chunk in iter(lambda: os.read(descriptor, 1024 * 1024), b""):
        digest.update(chunk)
    metadata_after = os.fstat(descriptor)
    path_metadata = os.stat(sys.argv[1], follow_symlinks=False)
    if (
        stable_metadata(metadata_before) != stable_metadata(metadata_after)
        or stable_metadata(metadata_after) != stable_metadata(path_metadata)
    ):
        raise ValueError
except (OSError, ValueError):
    raise SystemExit(1)
finally:
    if "descriptor" in locals():
        os.close(descriptor)

print(
    f"{metadata_after.st_dev}:{metadata_after.st_ino}:{stat.S_IMODE(metadata_after.st_mode):o}:"
    f"{metadata_after.st_size}:{digest.hexdigest()}"
)
PY
}

record_release_evidence_fingerprint() {
  release_evidence_fingerprint="$(evidence_fingerprint)" || return 1
  [[ "$release_evidence_fingerprint" =~ '^[0-9]+:[0-9]+:[0-7]+:[0-9]+:[0-9a-f]{64}$' ]]
}

evidence_matches_release_fingerprint() {
  local current_fingerprint
  [[ -n "$release_evidence_fingerprint" ]] || return 1
  current_fingerprint="$(evidence_fingerprint)" || return 1
  [[ "$current_fingerprint" == "$release_evidence_fingerprint" ]]
}

record_notarize_lock_identity() {
  if [[ -d "$notarize_lock" && ! -L "$notarize_lock" ]]; then
    notarize_lock_created_identity="$(stat -f '%d:%i' -- "$notarize_lock" 2>/dev/null || true)"
  fi
}

notarize_lock_is_owned() {
  local current_identity
  [[ -n "$notarize_lock_created_identity" && -d "$notarize_lock" && ! -L "$notarize_lock" ]] || return 1
  current_identity="$(stat -f '%d:%i' -- "$notarize_lock" 2>/dev/null || true)"
  [[ -n "$current_identity" && "$current_identity" == "$notarize_lock_created_identity" ]]
}

record_notary_tmp_identity() {
  if [[ -d "$notary_tmp" && ! -L "$notary_tmp" ]]; then
    notary_tmp_created_identity="$(stat -f '%d:%i' -- "$notary_tmp" 2>/dev/null || true)"
  fi
}

notary_tmp_is_owned() {
  local current_identity
  [[ -n "$notary_tmp_created_identity" && -d "$notary_tmp" && ! -L "$notary_tmp" ]] || return 1
  current_identity="$(stat -f '%d:%i' -- "$notary_tmp" 2>/dev/null || true)"
  [[ -n "$current_identity" && "$current_identity" == "$notary_tmp_created_identity" ]]
}

# A notarization submission is intentionally long-running.  Keep a compact,
# path-free fingerprint of the exact regular DMG that was submitted so a
# concurrent replacement or modification cannot be stapled under the result
# for a different artifact.
dmg_fingerprint() {
  /usr/bin/python3 - "$dmg_path" <<'PY'
import hashlib
import os
import stat
import sys

def stable_metadata(metadata):
    return (
        metadata.st_dev,
        metadata.st_ino,
        stat.S_IMODE(metadata.st_mode),
        metadata.st_nlink,
        metadata.st_uid,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )

try:
    descriptor = os.open(sys.argv[1], os.O_RDONLY | os.O_NOFOLLOW)
    metadata_before = os.fstat(descriptor)
    if not stat.S_ISREG(metadata_before.st_mode) or metadata_before.st_nlink != 1:
        raise ValueError
    digest = hashlib.sha256()
    for chunk in iter(lambda: os.read(descriptor, 1024 * 1024), b""):
        digest.update(chunk)
    metadata_after = os.fstat(descriptor)
    path_metadata = os.stat(sys.argv[1], follow_symlinks=False)
    if (
        stable_metadata(metadata_before) != stable_metadata(metadata_after)
        or stable_metadata(metadata_after) != stable_metadata(path_metadata)
    ):
        raise ValueError
except (OSError, ValueError):
    raise SystemExit(1)
finally:
    if "descriptor" in locals():
        os.close(descriptor)

print(
    f"{metadata_after.st_dev}:{metadata_after.st_ino}:{stat.S_IMODE(metadata_after.st_mode):o}:"
    f"{metadata_after.st_size}:{digest.hexdigest()}"
)
PY
}

record_notarized_dmg_fingerprint() {
  notarized_dmg_fingerprint="$(dmg_fingerprint)" || return 1
  [[ "$notarized_dmg_fingerprint" =~ '^[0-9]+:[0-9]+:[0-7]+:[0-9]+:[0-9a-f]{64}$' ]]
}

dmg_matches_notarized_fingerprint() {
  local current_fingerprint
  [[ -n "$notarized_dmg_fingerprint" ]] || return 1
  current_fingerprint="$(dmg_fingerprint)" || return 1
  [[ "$current_fingerprint" == "$notarized_dmg_fingerprint" ]]
}

record_finalized_dmg_fingerprint() {
  finalized_dmg_fingerprint="$(dmg_fingerprint)" || return 1
  [[ "$finalized_dmg_fingerprint" =~ '^[0-9]+:[0-9]+:[0-7]+:[0-9]+:[0-9a-f]{64}$' ]]
}

dmg_matches_finalized_fingerprint() {
  local current_fingerprint
  [[ -n "$finalized_dmg_fingerprint" ]] || return 1
  current_fingerprint="$(dmg_fingerprint)" || return 1
  [[ "$current_fingerprint" == "$finalized_dmg_fingerprint" ]]
}

cleanup() {
  cleanup_failed=0
  if (( mounted == 1 )) && mount_dir_is_owned; then
    hdiutil detach "$mount_dir" >/dev/null 2>&1 || cleanup_failed=1
  fi
  if mount_dir_is_owned; then
    rmdir "$mount_dir" >/dev/null 2>&1 || cleanup_failed=1
  fi
  if notary_tmp_is_owned; then
    rm -rf -- "$notary_tmp" >/dev/null 2>&1 || cleanup_failed=1
  fi
  if (( evidence_written == 1 && notarize_completed == 0 )) && evidence_is_owned; then
    rm -f -- "$evidence_path" >/dev/null 2>&1 || cleanup_failed=1
  fi
  if (( notarize_lock_acquired == 1 )) && notarize_lock_is_owned; then
    if [[ -n "$notarize_lock_owner" && -f "$notarize_lock_owner" && ! -L "$notarize_lock_owner" ]]; then
      rm -f -- "$notarize_lock_owner" >/dev/null 2>&1 || cleanup_failed=1
    fi
    rmdir "$notarize_lock" >/dev/null 2>&1 || cleanup_failed=1
  fi
  return 0
}
trap cleanup EXIT
trap 'cleanup; exit 130' INT TERM

if [[ -z "$dmg_path" || "$dmg_path" != /* || -L "$dmg_path" || ! -f "$dmg_path" ]]; then
  print -u2 -- "notarize:FAIL:dmg_path_invalid"
  print -u2 -- "notarize:HINT:dmg_path_invalid:provide_a_release_dmg_under_dist"
  exit 2
fi
dmg_path="${dmg_path:a}"
source "$script_root/build_path_guard.zsh"
validate_build_path "$dmg_path" "$project_root/dist" "DMG path"
if [[ "${dmg_path:e}" != "dmg" ]]; then
  print -u2 -- "notarize:FAIL:dmg_path_invalid"
  print -u2 -- "notarize:HINT:dmg_path_invalid:provide_a_release_dmg_under_dist"
  exit 2
fi
if [[ "${dmg_path:t}" =~ '^PhotosLocalKeywordIndexer-[0-9]+\.[0-9]+\.[0-9]+-[1-9][0-9]*-dev-arm64\.dmg$' ]]; then
  print -u2 -- "notarize:FAIL:development_dmg_not_releasable"
  print -u2 -- "notarize:HINT:development_dmg_not_releasable:run_release_preflight_then_build_release"
  exit 2
fi
if [[ ! "${dmg_path:t}" =~ '^PhotosLocalKeywordIndexer-[0-9]+\.[0-9]+\.[0-9]+-[1-9][0-9]*\.dmg$' ]]; then
  print -u2 -- "notarize:FAIL:dmg_filename_invalid"
  print -u2 -- "notarize:HINT:dmg_filename_invalid:rebuild_with_version_and_build_filename"
  exit 2
fi
if [[ -z "$notary_profile" ]]; then
  print -u2 -- "notarize:FAIL:notary_profile_missing"
  print -u2 -- "notarize:HINT:notary_profile_missing:configure_with_xcrun_notarytool_store_credentials_then_retry"
  exit 2
fi
if [[ ! -s "$dmg_path" ]]; then
  print -u2 -- "notarize:FAIL:dmg_empty_artifact"
  print -u2 -- "notarize:HINT:dmg_empty_artifact:rebuild_the_release_dmg_then_retry"
  exit 2
fi

for command_name in hdiutil codesign spctl xcrun; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    print -u2 -- "notarize:FAIL:${command_name}_missing"
    print -u2 -- "notarize:HINT:${command_name}_missing:install_xcode_command_line_tools_then_retry"
    exit 1
  fi
done
if ! xcrun --find notarytool >/dev/null 2>&1; then
  print -u2 -- "notarize:FAIL:notarytool_missing"
  print -u2 -- "notarize:HINT:notarytool_missing:install_xcode_command_line_tools_then_retry"
  exit 1
fi

# stapler mutates the input DMG. Refuse hardlinks so a release operation cannot
# mutate another artifact that happens to share this inode under a different
# name. This check is deliberately before image inspection, mounting, or any
# notarization command.
if [[ ! -x /usr/bin/python3 ]]; then
  print -u2 -- "notarize:FAIL:dmg_hardlink_check_unavailable"
  print -u2 -- "notarize:HINT:dmg_hardlink_check_unavailable:install_macos_system_python_then_retry"
  exit 1
fi
if ! hardlink_count="$(/usr/bin/python3 - "$dmg_path" <<'PY'
import os
import sys

try:
    count = os.stat(sys.argv[1], follow_symlinks=False).st_nlink
except (OSError, ValueError):
    raise SystemExit(1)
print(count)
PY
)" || [[ ! "$hardlink_count" =~ '^[0-9]+$' ]]; then
  print -u2 -- "notarize:FAIL:dmg_hardlink_check_unavailable"
  print -u2 -- "notarize:HINT:dmg_hardlink_check_unavailable:install_macos_system_python_then_retry"
  exit 1
fi
if [[ "$hardlink_count" != "1" ]]; then
  print -u2 -- "notarize:FAIL:dmg_hardlink"
  print -u2 -- "notarize:HINT:dmg_hardlink:copy_the_dmg_to_a_unique_file_then_retry"
  exit 2
fi

# The submission-to-stapling interval can be long. A group/world-writable
# artifact could be altered by another local user after its fingerprint is
# recorded, so reject mutable or foreign-owned DMGs before acquiring a lock,
# inspecting the image, or contacting notarytool.
if ! dmg_integrity="$(/usr/bin/python3 - "$dmg_path" "$project_root/dist" <<'PY'
import os
from pathlib import Path
import stat
import sys

try:
    artifact = Path(sys.argv[1])
    release_root = Path(sys.argv[2])
    metadata = os.stat(artifact, follow_symlinks=False)
except (OSError, ValueError):
    print("unavailable")
    raise SystemExit(0)

if not stat.S_ISREG(metadata.st_mode):
    print("invalid")
elif metadata.st_uid != os.getuid():
    print("ownership")
elif metadata.st_mode & 0o022:
    print("permissions")
else:
    try:
        current = artifact.parent
        current.relative_to(release_root)
        while True:
            parent_metadata = os.stat(current, follow_symlinks=False)
            if not stat.S_ISDIR(parent_metadata.st_mode):
                print("parent_invalid")
                break
            if parent_metadata.st_uid != os.getuid():
                print("parent_ownership")
                break
            if parent_metadata.st_mode & 0o022:
                print("parent_permissions")
                break
            if current == release_root:
                print("ok")
                break
            current = current.parent
    except (OSError, ValueError):
        print("unavailable")
PY
)"; then
  dmg_integrity="unavailable"
fi
case "$dmg_integrity" in
  ok) ;;
  ownership)
    print -u2 -- "notarize:FAIL:dmg_ownership"
    print -u2 -- "notarize:HINT:dmg_ownership:rebuild_the_dmg_as_the_current_user_then_retry"
    exit 2
    ;;
  permissions)
    print -u2 -- "notarize:FAIL:dmg_permissions"
    print -u2 -- "notarize:HINT:dmg_permissions:remove_group_world_write_then_retry"
    exit 2
    ;;
  parent_ownership)
    print -u2 -- "notarize:FAIL:dmg_parent_ownership"
    print -u2 -- "notarize:HINT:dmg_parent_ownership:rebuild_the_dmg_in_a_private_release_directory"
    exit 2
    ;;
  parent_permissions)
    print -u2 -- "notarize:FAIL:dmg_parent_permissions"
    print -u2 -- "notarize:HINT:dmg_parent_permissions:remove_group_world_write_from_release_directories"
    exit 2
    ;;
  parent_invalid)
    print -u2 -- "notarize:FAIL:dmg_parent_invalid"
    print -u2 -- "notarize:HINT:dmg_parent_invalid:rebuild_the_dmg_in_a_private_release_directory"
    exit 2
    ;;
  invalid)
    print -u2 -- "notarize:FAIL:dmg_type_invalid"
    print -u2 -- "notarize:HINT:dmg_type_invalid:rebuild_the_release_dmg_then_retry"
    exit 2
    ;;
  *)
    print -u2 -- "notarize:FAIL:dmg_integrity_check_unavailable"
    print -u2 -- "notarize:HINT:dmg_integrity_check_unavailable:install_macos_system_python_then_retry"
    exit 1
    ;;
esac

evidence_path="${dmg_path:r}.release-evidence.json"
if [[ -e "$evidence_path" || -L "$evidence_path" ]]; then
  print -u2 -- "notarize:FAIL:evidence_exists"
  print -u2 -- "notarize:HINT:evidence_exists:review_or_remove_existing_evidence_then_retry"
  exit 2
fi

notarize_lock="${dmg_path}.notarize.lock"
notarize_lock_owner="$notarize_lock/owner"
if ! mkdir "$notarize_lock" 2>/dev/null; then
  if [[ -L "$notarize_lock" ]]; then
    print -u2 -- "notarize:FAIL:lock_invalid"
    print -u2 -- "notarize:HINT:lock_invalid:remove_the_lock_symlink_and_retry"
    exit 2
  fi
  owner_pid=""
  if [[ -f "$notarize_lock_owner" && ! -L "$notarize_lock_owner" ]]; then
    owner_pid="$(<"$notarize_lock_owner")"
    if [[ "$owner_pid" =~ '^[1-9][0-9]*$' ]] && kill -0 "$owner_pid" 2>/dev/null; then
      print -u2 -- "notarize:FAIL:already_running"
      print -u2 -- "notarize:HINT:already_running:wait_for_the_other_notarization_then_retry"
      exit 2
    fi
    print -u2 -- "notarize:FAIL:stale_lock"
    print -u2 -- "notarize:HINT:stale_lock:verify_no_notarization_then_remove_lock"
    exit 2
  fi
  print -u2 -- "notarize:FAIL:already_running"
  print -u2 -- "notarize:HINT:already_running:wait_for_the_other_notarization_then_retry"
  exit 2
fi
notarize_lock_acquired=1
record_notarize_lock_identity
if ! (
  set -o noclobber
  print -r -- "$$" > "$notarize_lock_owner"
) 2>/dev/null; then
  print -u2 -- "notarize:FAIL:lock_owner_write"
  print -u2 -- "notarize:HINT:lock_owner_write:remove_the_lock_and_retry"
  exit 2
fi
if [[ -e "$evidence_path" || -L "$evidence_path" ]]; then
  print -u2 -- "notarize:FAIL:evidence_exists"
  print -u2 -- "notarize:HINT:evidence_exists:review_or_remove_existing_evidence_then_retry"
  exit 2
fi

if ! hdiutil imageinfo "$dmg_path" >/dev/null 2>&1; then
  print -u2 -- "notarize:FAIL:dmg_image_invalid"
  print -u2 -- "notarize:HINT:dmg_image_invalid:rebuild_and_verify_the_dmg_then_retry"
  exit 2
fi
# Validate the mounted contents before any notarization submission. A malformed
# DMG must fail locally rather than consume a notarytool upload and only fail
# after stapling.
"$script_root/verify_dmg_layout.sh" "$dmg_path" >/dev/null
if ! codesign --verify --verbose=2 --strict "$dmg_path" >/dev/null 2>&1; then
  print -u2 -- "notarize:FAIL:dmg_signature_invalid"
  print -u2 -- "notarize:HINT:dmg_signature_invalid:rebuild_and_sign_the_dmg_then_retry"
  exit 1
fi

verify_mounted_helper_source_fingerprint() {
  local app_path="$1"
  local marker current_fingerprint recorded_fingerprint legacy_helper
  marker="$app_path/Contents/Helpers/PhotosIndexerWorker.app/Contents/Resources/.photos-indexer-source-fingerprint"
  legacy_helper="$app_path/Contents/Helpers/PhotosIndexerWorker/PhotosIndexerWorker"
  if [[ -e "$legacy_helper" || -L "$legacy_helper" ]]; then
    print -u2 -- "notarize:FAIL:helper_source_fingerprint_missing"
    print -u2 -- "notarize:HINT:helper_source_fingerprint_missing:rebuild_the_helper_app_and_dmg"
    return 1
  fi
  if [[ ! -x /usr/bin/python3 ]]; then
    print -u2 -- "notarize:FAIL:helper_source_fingerprint_unavailable"
    print -u2 -- "notarize:HINT:helper_source_fingerprint_unavailable:run_on_a_macos_release_host_with_system_python"
    return 1
  fi
  current_fingerprint="$(
    unset PYTHONHOME PYTHONPATH
    /usr/bin/python3 "$project_root/packaging/helper_source_fingerprint.py" "$project_root" 2>/dev/null || true
  )"
  if [[ ! "$current_fingerprint" =~ '^[0-9a-f]{64}$' ]]; then
    print -u2 -- "notarize:FAIL:helper_source_fingerprint_unavailable"
    print -u2 -- "notarize:HINT:helper_source_fingerprint_unavailable:verify_helper_source_inputs_then_retry"
    return 1
  fi
  if [[ ! -f "$marker" || -L "$marker" ]]; then
    print -u2 -- "notarize:FAIL:helper_source_fingerprint_missing"
    print -u2 -- "notarize:HINT:helper_source_fingerprint_missing:rebuild_the_helper_app_and_dmg"
    return 1
  fi
  recorded_fingerprint="$(<"$marker")"
  if [[ ! "$recorded_fingerprint" =~ '^[0-9a-f]{64}$' \
    || "$recorded_fingerprint" != "$current_fingerprint" ]]; then
    print -u2 -- "notarize:FAIL:helper_source_fingerprint_stale"
    print -u2 -- "notarize:HINT:helper_source_fingerprint_stale:rebuild_the_helper_app_and_dmg"
    return 1
  fi
}

verify_mounted_helper_release_metadata() {
  local app_path="$1"
  local app_info="$app_path/Contents/Info.plist"
  local helper_info="$app_path/Contents/Helpers/PhotosIndexerWorker.app/Contents/Info.plist"
  if [[ ! -f "$app_info" || -L "$app_info" || ! -f "$helper_info" || -L "$helper_info" ]]; then
    print -u2 -- "notarize:FAIL:mounted_helper_release_metadata_invalid"
    print -u2 -- "notarize:HINT:mounted_helper_release_metadata_invalid:rebuild_and_sign_the_app_then_retry"
    return 1
  fi
  if ! /usr/bin/python3 - "$app_info" "$helper_info" <<'PY'
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
    print -u2 -- "notarize:FAIL:mounted_helper_release_metadata_invalid"
    print -u2 -- "notarize:HINT:mounted_helper_release_metadata_invalid:rebuild_and_sign_the_app_then_retry"
    return 1
  fi
}

verify_mounted_app() {
  local top_level_count applications_link
  local -a apps
  top_level_count="$(find "$mount_dir" -mindepth 1 -maxdepth 1 -print | wc -l | tr -d '[:space:]')"
  applications_link="$mount_dir/Applications"
  if [[ "$top_level_count" != "2" || ! -L "$applications_link" || "$(readlink "$applications_link")" != "/Applications" ]]; then
    print -u2 -- "DMG root must contain only the app and Applications alias."
    return 1
  fi
  apps=("$mount_dir"/*.app(N))
  if (( ${#apps[@]} != 1 )); then
    print -u2 -- "DMG must contain exactly one app bundle."
    return 1
  fi
  mounted_app="${apps[1]}"
  verify_mounted_helper_release_metadata "$mounted_app"
  verify_mounted_helper_source_fingerprint "$mounted_app"
  "$script_root/verify_embedded_helper.sh" "$mounted_app" >/dev/null
  "$script_root/verify_release.sh" "$mounted_app" >/dev/null
  if ! spctl --assess --type execute --verbose=4 "$mounted_app" >/dev/null 2>&1; then
    print -u2 -- "notarize:FAIL:mounted_app_assessment_invalid"
    print -u2 -- "notarize:HINT:mounted_app_assessment_invalid:rebuild_and_sign_the_app_then_retry"
    return 1
  fi
}

# Inspect the signed app inside the DMG before uploading it. The layout gate
# checks only presence; these checks reject an incomplete or incoherent bundle
# locally and avoid spending a notarization submission on a bad artifact.
mount_dir="$(mktemp -d "${TMPDIR:-/tmp}/photos-indexer-dmg.XXXXXX")"
record_mount_dir_identity
if ! mount_dir_is_owned; then
  print -u2 -- "notarize:FAIL:dmg_mount_directory_invalid"
  print -u2 -- "notarize:HINT:dmg_mount_directory_invalid:recreate_the_private_mount_directory_then_retry"
  exit 1
fi
if ! hdiutil attach -readonly -nobrowse -mountpoint "$mount_dir" "$dmg_path" >/dev/null 2>&1; then
  print -u2 -- "notarize:FAIL:dmg_mount_failed"
  print -u2 -- "notarize:HINT:dmg_mount_failed:verify_the_dmg_and_retry"
  exit 1
fi
mounted=1
verify_mounted_app
if ! mount_dir_is_owned; then
  print -u2 -- "notarize:FAIL:dmg_mount_changed"
  print -u2 -- "notarize:HINT:dmg_mount_changed:detach_the_original_mount_manually_then_retry"
  exit 1
fi
if ! hdiutil detach "$mount_dir" >/dev/null 2>&1; then
  print -u2 -- "notarize:FAIL:dmg_unmount_failed"
  print -u2 -- "notarize:HINT:dmg_unmount_failed:detach_the_dmg_mount_and_retry"
  exit 1
fi
mounted=0
if [[ -e "$mount_dir" || -L "$mount_dir" ]]; then
  if ! mount_dir_is_owned; then
    print -u2 -- "notarize:FAIL:dmg_mount_changed"
    print -u2 -- "notarize:HINT:dmg_mount_changed:remove_the_replaced_mount_directory_then_retry"
    exit 1
  fi
  if ! rmdir "$mount_dir" >/dev/null 2>&1; then
    print -u2 -- "notarize:FAIL:dmg_mount_cleanup_failed"
    print -u2 -- "notarize:HINT:dmg_mount_cleanup_failed:remove_the_private_mount_directory_then_retry"
    exit 1
  fi
fi
mount_dir=""
mount_dir_created_identity=""

notary_tmp="$(mktemp -d "${TMPDIR:-/tmp}/photos-indexer-notary.XXXXXX")"
record_notary_tmp_identity
if ! notary_tmp_is_owned; then
  print -u2 -- "notarize:FAIL:notary_temp_invalid"
  print -u2 -- "notarize:HINT:notary_temp_invalid:recreate_the_private_notary_temp_then_retry"
  exit 1
fi
notary_result="$notary_tmp/result.json"
if ! record_notarized_dmg_fingerprint; then
  print -u2 -- "notarize:FAIL:dmg_fingerprint_unavailable"
  print -u2 -- "notarize:HINT:dmg_fingerprint_unavailable:rebuild_and_verify_the_dmg_then_retry"
  exit 1
fi
if ! (
  # The result file lives under a freshly-created private directory.  Keep the
  # redirection create-only nonetheless: if a concurrent process swaps the
  # pathname for a symlink before this command starts, zsh must fail closed
  # instead of following it to an unrelated file.
  set -o noclobber
  xcrun notarytool submit "$dmg_path" --keychain-profile "$notary_profile" --wait \
    --output-format json > "$notary_result" 2>/dev/null
); then
  print -u2 -- "notarize:FAIL:notary_submission_failed"
  print -u2 -- "notarize:HINT:notary_submission_failed:check_notary_profile_and_retry"
  exit 1
fi
if ! notary_tmp_is_owned; then
  print -u2 -- "notarize:FAIL:notary_temp_invalid"
  print -u2 -- "notarize:HINT:notary_temp_invalid:recreate_the_private_notary_temp_then_retry"
  exit 1
fi
if ! /usr/bin/python3 - "$notary_result" 2>/dev/null <<'PY'
import json
import re
import sys

def strict_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result

try:
    with open(sys.argv[1], "r", encoding="utf-8") as stream:
        result = json.load(stream, object_pairs_hook=strict_json_object)
except (OSError, ValueError):
    raise SystemExit(1)
if not isinstance(result, dict) or result.get("status") != "Accepted":
    raise SystemExit(1)
submission_id = result.get("id")
if not isinstance(submission_id, str) or not re.fullmatch(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
    submission_id,
):
    raise SystemExit(1)
PY
then
  print -u2 -- "notarize:FAIL:notary_result_invalid"
  print -u2 -- "notarize:HINT:notary_result_invalid:retry_notarization_before_writing_evidence"
  exit 1
fi
if ! dmg_matches_notarized_fingerprint; then
  print -u2 -- "notarize:FAIL:dmg_changed_after_submission"
  print -u2 -- "notarize:HINT:dmg_changed_after_submission:rebuild_and_resubmit_the_dmg"
  exit 1
fi
if ! xcrun stapler staple "$dmg_path" >/dev/null 2>&1; then
  print -u2 -- "notarize:FAIL:staple_failed"
  print -u2 -- "notarize:HINT:staple_failed:retry_stapling_the_accepted_dmg"
  exit 1
fi
if ! xcrun stapler validate "$dmg_path" >/dev/null 2>&1; then
  print -u2 -- "notarize:FAIL:staple_validation_failed"
  print -u2 -- "notarize:HINT:staple_validation_failed:retry_stapling_the_accepted_dmg"
  exit 1
fi
if ! spctl --assess --type open --context context:primary-signature --verbose=4 "$dmg_path" >/dev/null 2>&1; then
  print -u2 -- "notarize:FAIL:dmg_assessment_invalid"
  print -u2 -- "notarize:HINT:dmg_assessment_invalid:rebuild_and_staple_the_dmg_then_retry"
  exit 1
fi
if ! record_finalized_dmg_fingerprint; then
  print -u2 -- "notarize:FAIL:dmg_fingerprint_unavailable"
  print -u2 -- "notarize:HINT:dmg_fingerprint_unavailable:rebuild_and_verify_the_dmg_then_retry"
  exit 1
fi
mount_dir="$(mktemp -d "${TMPDIR:-/tmp}/photos-indexer-dmg.XXXXXX")"
record_mount_dir_identity
if ! mount_dir_is_owned; then
  print -u2 -- "notarize:FAIL:dmg_mount_directory_invalid"
  print -u2 -- "notarize:HINT:dmg_mount_directory_invalid:recreate_the_private_mount_directory_then_retry"
  exit 1
fi
if ! hdiutil attach -readonly -nobrowse -mountpoint "$mount_dir" "$dmg_path" >/dev/null 2>&1; then
  print -u2 -- "notarize:FAIL:dmg_mount_failed"
  print -u2 -- "notarize:HINT:dmg_mount_failed:verify_the_dmg_and_retry"
  exit 1
fi
mounted=1
verify_mounted_app
"$script_root/write_release_evidence.sh" "$dmg_path" "$mounted_app" \
  "$notary_result" "$evidence_path" >/dev/null
evidence_written=1
record_evidence_identity
if ! record_release_evidence_fingerprint; then
  print -u2 -- "notarize:FAIL:release_evidence_fingerprint_unavailable"
  print -u2 -- "notarize:HINT:release_evidence_fingerprint_unavailable:retry_notarization_before_distribution"
  exit 1
fi
if ! mount_dir_is_owned; then
  print -u2 -- "notarize:FAIL:dmg_mount_changed"
  print -u2 -- "notarize:HINT:dmg_mount_changed:detach_the_original_mount_manually_then_retry"
  exit 1
fi
if ! hdiutil detach "$mount_dir" >/dev/null 2>&1; then
  if (( evidence_written == 1 )) && evidence_is_owned; then
    rm -f -- "$evidence_path" >/dev/null 2>&1 || cleanup_failed=1
  fi
  print -u2 -- "notarize:FAIL:dmg_unmount_failed"
  print -u2 -- "notarize:HINT:dmg_unmount_failed:detach_the_dmg_mount_and_retry"
  exit 1
fi
mounted=0
if ! dmg_matches_finalized_fingerprint; then
  print -u2 -- "notarize:FAIL:dmg_changed_before_distribution"
  print -u2 -- "notarize:HINT:dmg_changed_before_distribution:rebuild_and_resubmit_the_dmg"
  exit 1
fi
if ! evidence_matches_release_fingerprint; then
  print -u2 -- "notarize:FAIL:release_evidence_changed_before_distribution"
  print -u2 -- "notarize:HINT:release_evidence_changed_before_distribution:retry_notarization_before_distribution"
  exit 1
fi
notarize_completed=1
cleanup
if (( cleanup_failed == 1 )); then
  print -u2 -- "notarize:FAIL:final_cleanup_failed"
  print -u2 -- "notarize:HINT:final_cleanup_failed:remove_stale_private_release_state_then_retry"
  exit 1
fi
trap - EXIT
print -- "release_evidence:RECORDED"
print -- "Notarization completed, the ticket was stapled, and release evidence was written."
print -- "notarize:READY_FOR_DISTRIBUTION"
