# Shared Sparkle framework validation, selection, and copied-bundle preparation
# for the app build.
#
# The caller must pass the SwiftPM build root and Package.resolved explicitly.
# Keeping selection in its own function makes it testable without invoking
# SwiftPM, signing, or copying anything into an app bundle. The arm64 helper at
# the end operates only on the explicit framework copy supplied by its caller.

validate_sparkle_lock() {
  local package_resolved="${1:-}"
  local expected_version="${2:-2.9.2}"
  local expected_revision="${3:-}"

  [[ -n "$package_resolved" && -n "$expected_version" && -n "$expected_revision" ]] || {
    print -u2 -- "Sparkle lock validator requires Package.resolved, expected version, and expected revision."
    return 2
  }
  [[ -f "$package_resolved" && ! -L "$package_resolved" ]] || {
    print -u2 -- "Package.resolved is missing or is a symlink."
    return 2
  }

  # Verify the exact resolved package and revision before looking at any
  # framework path. This accepts both Package.resolved schemas emitted by
  # SwiftPM 5/6.
  if ! /usr/bin/python3 - "$package_resolved" "$expected_version" "$expected_revision" <<'PY'
import json
import os
import sys

path, expected, expected_revision = sys.argv[1:]
try:
    oversized = os.path.getsize(path) > 1024 * 1024
except (OSError, ValueError):
    print("Package.resolved cannot be read.", file=sys.stderr)
    raise SystemExit(1)
if oversized:
    print("Package.resolved must be a regular bounded file.", file=sys.stderr)
    raise SystemExit(1)
try:
    with open(path, "rb") as stream:
        document = json.load(stream)
except (OSError, ValueError):
    print("Package.resolved cannot be read.", file=sys.stderr)
    raise SystemExit(1)

if isinstance(document, dict):
    pins = document.get("pins")
    if pins is None:
        legacy = document.get("object")
        pins = legacy.get("pins") if isinstance(legacy, dict) else None
else:
    pins = None
if not isinstance(pins, list):
    print("Package.resolved has no pins list.", file=sys.stderr)
    raise SystemExit(1)

matches = []
invalid_origin = False
invalid_identity = False
canonical_sparkle_pins = 0
canonical_location = "https://github.com/sparkle-project/sparkle.git"
for pin in pins:
    if not isinstance(pin, dict):
        continue
    identity = str(pin.get("identity", "")).casefold()
    location = str(pin.get("location", "")).casefold()
    if identity != "sparkle":
        if location.rstrip("/") == canonical_location:
            invalid_identity = True
        continue
    if location.rstrip("/") != canonical_location:
        invalid_origin = True
        continue
    canonical_sparkle_pins += 1
    state = pin.get("state", {})
    if (
        isinstance(state, dict)
        and state.get("version") == expected
        and state.get("revision") == expected_revision
    ):
        matches.append(pin)

if invalid_origin:
    print(
        f"Sparkle Package.resolved pin has an unexpected origin; expected https://github.com/sparkle-project/Sparkle.git.",
        file=sys.stderr,
    )
    raise SystemExit(1)
if invalid_identity:
    print(
        "Sparkle Package.resolved pin has an unexpected identity; expected sparkle.",
        file=sys.stderr,
    )
    raise SystemExit(1)
if canonical_sparkle_pins != 1:
    print(
        f"Expected exactly one canonical Sparkle pin; found {canonical_sparkle_pins}.",
        file=sys.stderr,
    )
    raise SystemExit(1)
if len(matches) != 1:
    print(
        f"Expected exactly one Sparkle pin at version {expected} and revision {expected_revision}; found {len(matches)}.",
        file=sys.stderr,
    )
    raise SystemExit(1)
PY
  then
    return 2
  fi
}

locate_sparkle_framework() {
  local build_root="${1:-}"
  local package_resolved="${2:-}"
  local expected_version="${3:-2.9.2}"
  local expected_revision="${4:-}"

  [[ -n "$build_root" && -n "$package_resolved" && -n "$expected_version" && -n "$expected_revision" ]] || {
    print -u2 -- "Sparkle locator requires build root, Package.resolved, expected version, and expected revision."
    return 2
  }
  [[ -d "$build_root" && ! -L "$build_root" ]] || {
    print -u2 -- "Sparkle build root is missing or is a symlink."
    return 2
  }
  validate_sparkle_lock "$package_resolved" "$expected_version" "$expected_revision" || return $?

  local candidates=()
  local candidate
  while IFS= read -r candidate; do
    [[ -n "$candidate" && ! -L "$candidate" ]] || continue
    # Read the framework's own version rather than trusting its directory name.
    if /usr/bin/python3 - "$candidate" "$expected_version" <<'PY'
import plistlib
import sys
from pathlib import Path

framework, expected = sys.argv[1:]
root = Path(framework)
try:
    resolved_root = root.resolve(strict=True)
except (OSError, RuntimeError):
    raise SystemExit(1)
info_paths = (
    root / "Versions" / "A" / "Resources" / "Info.plist",
    root / "Versions" / "Current" / "Resources" / "Info.plist",
    root / "Contents" / "Info.plist",
)
for info_path in info_paths:
    try:
        resolved_info = info_path.resolve(strict=True)
        resolved_info.relative_to(resolved_root)
        if not resolved_info.is_file():
            continue
        with resolved_info.open("rb") as stream:
            plist = plistlib.load(stream)
    except (OSError, RuntimeError, ValueError, plistlib.InvalidFileException):
        continue
    if isinstance(plist, dict) and str(plist.get("CFBundleShortVersionString", "")) == expected:
        raise SystemExit(0)
raise SystemExit(1)
PY
    then
      candidates+=("$candidate")
    fi
  done < <(find "$build_root" -type d -name 'Sparkle.framework' -print 2>/dev/null | sort)

  if (( ${#candidates[@]} == 0 )); then
    print -u2 -- "No Sparkle.framework matching resolved version $expected_version was found."
    return 1
  fi
  local preferred_framework="$build_root/arm64-apple-macosx/release/Sparkle.framework"
  for candidate in "${candidates[@]}"; do
    if [[ "$candidate" == "$preferred_framework" ]]; then
      print -r -- "$candidate"
      return 0
    fi
  done
  if (( ${#candidates[@]} != 1 )); then
    print -u2 -- "Ambiguous Sparkle.framework selection: ${#candidates[@]} matching frameworks found."
    return 3
  fi

  print -r -- "${candidates[1]}"
}

# Sparkle distributes one universal macOS framework.  The product is arm64
# only, so reduce Mach-O files in the copied app framework without touching the
# SwiftPM artifact cache.  Validate the complete tree before replacing any
# file, then replace each universal file atomically in its original directory.
thin_sparkle_framework_arm64() {
  local framework="${1:-}"
  local lipo_bin="${2:-}"
  local file_bin="${3:-}"

  [[ -n "$framework" && -d "$framework" && ! -L "$framework" ]] || {
    print -u2 -- "sparkle_framework:FAIL:path_invalid"
    return 2
  }
  [[ -n "$lipo_bin" && -x "$lipo_bin" && ! -d "$lipo_bin" ]] || {
    print -u2 -- "sparkle_framework:FAIL:lipo_unavailable"
    return 2
  }
  [[ -n "$file_bin" && -x "$file_bin" && ! -d "$file_bin" ]] || {
    print -u2 -- "sparkle_framework:FAIL:file_unavailable"
    return 2
  }

  local candidate description architectures
  local macho_count=0
  local -a universal_candidates=()
  while IFS= read -r -d "" candidate; do
    [[ -f "$candidate" && ! -L "$candidate" ]] || continue
    description="$("$file_bin" -b "$candidate" 2>/dev/null)" || {
      print -u2 -- "sparkle_framework:FAIL:inspection"
      return 1
    }
    [[ "$description" == *"Mach-O"* ]] || continue
    (( macho_count += 1 ))
    architectures="$("$lipo_bin" -archs "$candidate" 2>/dev/null)" || {
      print -u2 -- "sparkle_framework:FAIL:inspection"
      return 1
    }
    case "$architectures" in
      arm64) ;;
      "arm64 x86_64"|"x86_64 arm64") universal_candidates+=("$candidate") ;;
      *)
        print -u2 -- "sparkle_framework:FAIL:architecture"
        return 1
        ;;
    esac
  done < <(/usr/bin/find "$framework" -type f -print0)

  if (( macho_count == 0 )); then
    print -u2 -- "sparkle_framework:FAIL:macho_missing"
    return 1
  fi

  local temporary original_mode thinned_architectures
  for candidate in "${universal_candidates[@]}"; do
    temporary="$(mktemp "${candidate:h}/.sparkle-arm64.XXXXXX")" || {
      print -u2 -- "sparkle_framework:FAIL:temporary_output"
      return 1
    }
    original_mode="$(/usr/bin/stat -f '%Lp' "$candidate" 2>/dev/null)" || {
      /bin/rm -f -- "$temporary"
      print -u2 -- "sparkle_framework:FAIL:metadata"
      return 1
    }
    if ! "$lipo_bin" "$candidate" -thin arm64 -output "$temporary" >/dev/null 2>&1; then
      /bin/rm -f -- "$temporary"
      print -u2 -- "sparkle_framework:FAIL:thin"
      return 1
    fi
    thinned_architectures="$("$lipo_bin" -archs "$temporary" 2>/dev/null)" || true
    if [[ "$thinned_architectures" != "arm64" ]]; then
      /bin/rm -f -- "$temporary"
      print -u2 -- "sparkle_framework:FAIL:thin_verification"
      return 1
    fi
    if ! /bin/chmod "$original_mode" "$temporary" || ! /bin/mv -f -- "$temporary" "$candidate"; then
      /bin/rm -f -- "$temporary"
      print -u2 -- "sparkle_framework:FAIL:replacement"
      return 1
    fi
    if [[ "$("$lipo_bin" -archs "$candidate" 2>/dev/null)" != "arm64" ]]; then
      print -u2 -- "sparkle_framework:FAIL:thin_verification"
      return 1
    fi
  done
}
