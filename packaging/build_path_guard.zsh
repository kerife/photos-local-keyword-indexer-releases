# Side-effect-free path validation shared by the macOS bundle build.
#
# The path may not escape the lexical or physical build root, and no existing
# path component may be a symlink.  This is intentionally separate from the
# build script so the destructive target check can be tested without invoking
# SwiftPM, PyInstaller, or codesign.

validate_build_path() {
  local path="${1:-}"
  local root="${2:-}"
  local label="${3:-build path}"

  [[ -n "$path" && -n "$root" ]] || {
    print -u2 -- "${label} validation requires a path and a build root."
    return 2
  }

  # :a makes both inputs absolute and normalizes dot segments without
  # resolving symlinks; resolving them here would hide the condition we must
  # reject.
  path="${path:a}"
  root="${root:a}"

  case "$path" in
    "$root"|"$root"/*) ;;
    *)
      print -u2 -- "${label} is outside the permitted build root."
      return 1
      ;;
  esac

  # Inspect every existing component from the candidate back to the root.
  # This catches a symlinked root, a symlinked parent, and an existing target
  # before any caller can create or remove files below it.
  local cursor="$path"
  while [[ "$cursor" != "/" ]]; do
    if [[ -L "$cursor" ]]; then
      print -u2 -- "${label} contains a symlinked component."
      return 1
    fi
    [[ "$cursor" == "$root" ]] && break
    cursor="${cursor:h}"
  done
  [[ "$cursor" == "$root" ]] || {
    print -u2 -- "${label} is outside the permitted build root."
    return 1
  }

  # Resolve the nearest existing ancestors with pwd -P.  This remains valid
  # when the final destination has not been created yet.
  local root_existing="$root"
  while [[ ! -e "$root_existing" && "$root_existing" != "/" ]]; do
    root_existing="${root_existing:h}"
  done
  local path_existing="$path"
  # A caller may validate an existing file (for example, a DMG passed to the
  # notarization step).  Resolve its directory ancestor while retaining the
  # symlink checks above for the file itself.
  if [[ -e "$path_existing" && ! -d "$path_existing" ]]; then
    path_existing="${path_existing:h}"
  fi
  while [[ ! -e "$path_existing" && "$path_existing" != "/" ]]; do
    path_existing="${path_existing:h}"
  done
  [[ -d "$root_existing" && -d "$path_existing" ]] || {
    print -u2 -- "${label} has no valid directory ancestor."
    return 1
  }

  local root_real path_real
  root_real="$(cd "$root_existing" && pwd -P)${root#$root_existing}"
  path_real="$(cd "$path_existing" && pwd -P)${path#$path_existing}"
  case "$path_real" in
    "$root_real"|"$root_real"/*) ;;
    *)
      print -u2 -- "${label} resolves outside the permitted build root."
      return 1
      ;;
  esac
}
