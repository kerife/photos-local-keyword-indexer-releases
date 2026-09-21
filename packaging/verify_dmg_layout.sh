#!/bin/zsh
# Read-only local smoke gate for a freshly built DMG.  It never installs,
# signs, notarizes, or changes the mounted image.
set -euo pipefail

project_root="${0:A:h:h}"
json_mode=0
if [[ "${1:-}" == "--json" ]]; then
  json_mode=1
  shift
fi
dmg_path="${1:-}"
typeset -a checks=()

emit_json() {
  local result_status="$1"
  local next_action="$2"
  /usr/bin/python3 - "$result_status" "$next_action" "${checks[@]}" <<'PY'
import json
import sys

status = sys.argv[1]
next_action = sys.argv[2]
checks = []
for item in sys.argv[3:]:
    state, code = item.split("|", 1)
    checks.append({"state": state, "code": code})
print(json.dumps({
    "schema_version": 1,
    "tool": "photos-local-keyword-indexer",
    "status": "ready" if status == "READY" else "blocked",
    "checks": checks,
    "next_action": next_action,
}, separators=(",", ":")))
PY
}

report() {
  checks+=("$1|$2")
}

fail() {
  local code="$1"
  local exit_code="$2"
  local message="$3"
  local next_action="${4:-review_dmg_layout}"
  if (( json_mode == 1 )); then
    report FAIL "$code"
    emit_json BLOCKED "$next_action"
  else
    print -u2 -- "$message"
  fi
  exit "$exit_code"
}

if [[ -z "$dmg_path" || "$dmg_path" != /* || -L "$dmg_path" || ! -f "$dmg_path" ]]; then
  if (( json_mode == 1 )); then
    fail invalid_arguments 2 "" provide_an_absolute_dmg_under_dist
  fi
  print -u2 -- "dmg_layout:FAIL:invalid_arguments"
  print -u2 -- "dmg_layout:HINT:invalid_arguments:provide_an_absolute_dmg_under_dist"
  exit 2
fi
report PASS dmg_path

source "$project_root/packaging/build_path_guard.zsh"
if (( json_mode == 1 )); then
  validate_build_path "$dmg_path" "$project_root/dist" "DMG path" 2>/dev/null || fail dmg_path_invalid 2 "DMG path is invalid."
else
  validate_build_path "$dmg_path" "$project_root/dist" "DMG path" || fail dmg_path_invalid 2 "DMG path is invalid."
fi
dmg_path="${dmg_path:a}"
dmg_created_identity=""
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
record_dmg_identity
dmg_name="${dmg_path:t}"
if [[ ! "$dmg_name" =~ '^PhotosLocalKeywordIndexer-([0-9]+\.[0-9]+\.[0-9]+)-([1-9][0-9]*)(-dev-arm64)?\.dmg$' ]]; then
  fail dmg_filename_invalid 2 "DMG filename must be PhotosLocalKeywordIndexer-<version>-<build>[-dev-arm64].dmg."
fi
report PASS dmg_filename
expected_version="${match[1]}"
expected_build="${match[2]}"
development_build=0
[[ -n "${match[3]:-}" ]] && development_build=1
command -v hdiutil >/dev/null 2>&1 || fail hdiutil_missing 1 "hdiutil is required to inspect the DMG."
report PASS hdiutil
command -v /usr/libexec/PlistBuddy >/dev/null 2>&1 || fail plistbuddy_missing 1 "PlistBuddy is required to inspect the DMG app metadata."
report PASS plistbuddy

if ! mount_point="$(mktemp -d "${TMPDIR:-/tmp}/photos-indexer-dmg.XXXXXX")"; then
  fail temporary_mount_failed 1 "Could not create a temporary mount point."
fi
mount_point_created_identity=""
record_mount_point_identity() {
  if [[ -d "$mount_point" && ! -L "$mount_point" ]]; then
    mount_point_created_identity="$(stat -f '%d:%i' -- "$mount_point" 2>/dev/null || true)"
  fi
}
mount_point_is_owned() {
  local current_identity
  [[ -n "$mount_point_created_identity" && -d "$mount_point" && ! -L "$mount_point" ]] || return 1
  current_identity="$(stat -f '%d:%i' -- "$mount_point" 2>/dev/null || true)"
  [[ -n "$current_identity" && "$current_identity" == "$mount_point_created_identity" ]]
}
record_mount_point_identity
mounted=0
mounted_device=""
detached=0
cleanup_failed=0
cleanup() {
  if (( mounted == 1 )); then
    # Mounting replaces the mountpoint's device/inode, so its pre-attach
    # identity cannot be used while the image is mounted.  Detach the exact
    # device reported by hdiutil instead; the original directory identity is
    # still checked before removing it after detach.
    if [[ -z "$mounted_device" ]]; then
      print -u2 -- "dmg_layout:WARN:mount_cleanup_failed"
      cleanup_failed=1
      return
    fi
    if hdiutil detach "$mounted_device" >/dev/null 2>&1; then
      mounted=0
      detached=1
    else
      print -u2 -- "dmg_layout:WARN:mount_cleanup_failed"
      cleanup_failed=1
      return
    fi
  else
    detached=1
  fi
  if (( detached == 1 )) && mount_point_is_owned; then
    rm -rf -- "$mount_point" >/dev/null 2>&1 || cleanup_failed=1
  fi
}
trap cleanup EXIT
trap 'cleanup; exit 130' INT TERM

if ! mount_point_is_owned; then
  fail temporary_mount_invalid 1 "Temporary mount point identity changed."
fi
if ! dmg_is_owned; then
  fail dmg_identity_changed 1 "DMG identity changed during verification."
fi
attach_output=""
if ! attach_output="$(hdiutil attach -nobrowse -readonly -mountpoint "$mount_point" "$dmg_path" 2>/dev/null)"; then
  fail dmg_mount_failed 1 "DMG could not be mounted read-only."
fi
mounted_device="$(print -r -- "$attach_output" | awk '$1 ~ /^\/dev\/disk[0-9]+s?[0-9]*$/ {print $1; exit}')"
if [[ -z "$mounted_device" ]]; then
  # Test doubles may not print hdiutil's device table.  Keep the historical
  # path fallback for that case; real hdiutil always reports a /dev/disk node.
  mounted_device="$mount_point"
fi
mounted=1
report PASS dmg_mount_readonly

app_path="$mount_point/PhotosLocalKeywordIndexer.app"
applications_link="$mount_point/Applications"
top_level_count="$(find "$mount_point" -mindepth 1 -maxdepth 1 -print | wc -l | tr -d ' ')"
if [[ "$top_level_count" != "2" || ! -d "$app_path" || -L "$app_path" || ! -L "$applications_link" ]]; then
  fail dmg_layout_invalid 1 "DMG root must contain only the app and Applications alias."
fi
alias_target="$(readlink "$applications_link" 2>/dev/null || print invalid)"
[[ "$alias_target" == "/Applications" ]] || fail applications_alias_invalid 1 "Applications alias must target /Applications."
report PASS dmg_layout

info="$app_path/Contents/Info.plist"
main="$app_path/Contents/MacOS/PhotosLocalKeywordIndexer"
helper_app="$app_path/Contents/Helpers/PhotosIndexerWorker.app"
helper_info="$helper_app/Contents/Info.plist"
helper="$helper_app/Contents/MacOS/PhotosIndexerWorker"
icon="$app_path/Contents/Resources/AppIcon.icns"
sparkle="$app_path/Contents/Frameworks/Sparkle.framework"
[[ -f "$info" && ! -L "$info" && -x "$main" && ! -L "$main" \
  && -d "$helper_app" && ! -L "$helper_app" \
  && -f "$helper_info" && ! -L "$helper_info" \
  && -x "$helper" && ! -L "$helper" \
  && -f "$icon" && ! -L "$icon" && -d "$sparkle" && ! -L "$sparkle" ]] || {
  fail dmg_app_incomplete 1 "DMG app is missing its metadata, icon, main executable, embedded helper, or Sparkle framework."
}
report PASS dmg_app_complete
if ! "$project_root/packaging/verify_embedded_helper.sh" "$app_path" >/dev/null 2>&1; then
  fail dmg_helper_invalid 1 "DMG embedded helper verification failed."
fi
report PASS dmg_helper_runtime
actual_version="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$info" 2>/dev/null || print invalid)"
actual_build="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' "$info" 2>/dev/null || print invalid)"
if [[ "$actual_version" != "$expected_version" || "$actual_build" != "$expected_build" ]]; then
  fail dmg_metadata_mismatch 1 "DMG app version/build do not match its filename."
fi
report PASS dmg_metadata

if ! dmg_is_owned; then
  fail dmg_identity_changed 1 "DMG identity changed during verification."
fi
cleanup
if (( cleanup_failed == 1 || mounted == 1 )) || [[ -e "$mount_point" || -L "$mount_point" ]]; then
  if (( json_mode == 1 )); then
    fail mount_cleanup_failed 1 "" detach_the_original_mount_then_retry
  fi
  print -u2 -- "dmg_layout:FAIL:mount_cleanup_failed"
  print -u2 -- "dmg_layout:HINT:mount_cleanup_failed:detach_the_original_mount_then_retry"
  exit 1
fi
trap - EXIT INT TERM
if (( json_mode == 1 )); then
  if (( development_build == 1 )); then
    emit_json READY run_release_preflight_then_build_release
  else
    emit_json READY none
  fi
elif (( development_build == 1 )); then
  print -- "dmg_layout:HINT:development_build:not_for_official_distribution_run_verify_public_beta_before_any_authorized_publication"
  print -- "dmg_layout:READY_DEV"
else
  print -- "dmg_layout:READY"
fi
