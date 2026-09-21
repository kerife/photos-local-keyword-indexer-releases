#!/bin/zsh
# Generate the deterministic app icon without network or checked-in binaries.
set -euo pipefail

project_root="${0:A:h:h}"
source "$project_root/packaging/build_path_guard.zsh"
output_path="${1:-}"
if [[ -z "$output_path" || "$output_path" != /* || "${output_path:e}" != "icns" ]]; then
  print -u2 -- "build_app_icon:FAIL:output_path_invalid"
  print -u2 -- "build_app_icon:HINT:output_path_invalid:provide_a_new_absolute_icns_path_under_build"
  exit 2
fi
validate_build_path "$output_path" "$project_root/build" "icon output"
if [[ -e "$output_path" || -L "$output_path" ]]; then
  print -u2 -- "Refusing to overwrite an existing app icon."
  exit 2
fi
command -v /usr/bin/python3 >/dev/null 2>&1 || {
  print -u2 -- "Python 3 is required to generate the app icon."
  exit 1
}
command -v /usr/bin/sips >/dev/null 2>&1 || {
  print -u2 -- "sips is required to generate the app icon."
  exit 1
}

icon_tmp_root="$(mktemp -d "${output_path:h}/.photos-indexer-icon.XXXXXX")"
iconset_dir="$icon_tmp_root/AppIcon.iconset"
icon_output_tmp="$icon_tmp_root/AppIcon.icns"
icon_tmp_root_created_identity=""
record_icon_tmp_root_identity() {
  if [[ -d "$icon_tmp_root" && ! -L "$icon_tmp_root" ]]; then
    icon_tmp_root_created_identity="$(stat -f '%d:%i' -- "$icon_tmp_root" 2>/dev/null || true)"
  fi
}
icon_tmp_root_is_owned() {
  local current_identity
  [[ -n "$icon_tmp_root_created_identity" && -d "$icon_tmp_root" && ! -L "$icon_tmp_root" ]] || return 1
  current_identity="$(stat -f '%d:%i' -- "$icon_tmp_root" 2>/dev/null || true)"
  [[ -n "$current_identity" && "$current_identity" == "$icon_tmp_root_created_identity" ]]
}
record_icon_tmp_root_identity
cleanup() {
  if icon_tmp_root_is_owned; then
    if [[ -n "${icon_output_tmp:-}" && -f "$icon_output_tmp" && ! -L "$icon_output_tmp" ]]; then
      rm -f -- "$icon_output_tmp"
    fi
    rm -rf -- "$icon_tmp_root"
  fi
}
trap cleanup EXIT
trap 'cleanup; exit 130' INT TERM
if ! icon_tmp_root_is_owned; then
  print -u2 -- "build_app_icon:FAIL:temporary_output_invalid"
  exit 1
fi
mkdir "$iconset_dir"

/usr/bin/python3 - "$iconset_dir" <<'PY'
from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path


def png(path: Path, size: int) -> None:
    pixels = bytearray()
    for y in range(size):
        pixels.append(0)
        for x in range(size):
            nx, ny = (x + 0.5) / size, (y + 0.5) / size
            # Rounded navy square on a transparent canvas.
            edge = min(nx, 1 - nx, ny, 1 - ny)
            inside = edge >= 0.08 or ((nx - 0.08) ** 2 + (ny - 0.08) ** 2 >= 0.08 ** 2 and
                                     (nx - 0.92) ** 2 + (ny - 0.08) ** 2 >= 0.08 ** 2 and
                                     (nx - 0.08) ** 2 + (ny - 0.92) ** 2 >= 0.08 ** 2 and
                                     (nx - 0.92) ** 2 + (ny - 0.92) ** 2 >= 0.08 ** 2)
            r, g, b, a = (18, 48, 91, 255) if inside else (0, 0, 0, 0)
            # White magnifier: a visible, concrete local-search mark.
            dx, dy = nx - 0.46, ny - 0.43
            distance = (dx * dx + dy * dy) ** 0.5
            if 0.15 <= distance <= 0.20:
                r, g, b, a = 232, 245, 255, 255
            if distance < 0.15:
                r, g, b, a = 36, 112, 193, 255
            if 0.58 <= nx <= 0.83 and 0.59 <= ny <= 0.84 and abs((ny - 0.59) - (nx - 0.58)) < 0.07:
                r, g, b, a = 232, 245, 255, 255
            # Three keyword lines.
            if 0.19 <= nx <= 0.36 and any(0.22 <= ny <= 0.25 for _ in [0]):
                r, g, b, a = 255, 190, 74, 255
            if 0.19 <= nx <= 0.32 and 0.30 <= ny <= 0.33:
                r, g, b, a = 255, 190, 74, 255
            if 0.19 <= nx <= 0.28 and 0.38 <= ny <= 0.41:
                r, g, b, a = 255, 190, 74, 255
            pixels.extend((r, g, b, a))

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)

    data = b"\x89PNG\r\n\x1a\n"
    data += chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
    data += chunk(b"IDAT", zlib.compress(bytes(pixels), 9))
    data += chunk(b"IEND", b"")
    path.write_bytes(data)


root = Path(sys.argv[1])
for base in (16, 32, 128, 256, 512):
    png(root / f"icon_{base}x{base}.png", base)
    png(root / f"icon_{base}x{base}@2x.png", base * 2)
PY

/usr/bin/sips -s format icns "$iconset_dir/icon_512x512.png" --out "$icon_output_tmp" >/dev/null
[[ -s "$icon_output_tmp" && ! -L "$icon_output_tmp" ]] || {
  print -u2 -- "sips did not produce a regular app icon."
  exit 1
}
if ! icon_tmp_root_is_owned; then
  print -u2 -- "build_app_icon:FAIL:temporary_output_invalid"
  exit 1
fi
mv "$icon_output_tmp" "$output_path"
[[ -s "$output_path" && ! -L "$output_path" ]] || {
  print -u2 -- "Could not publish a regular app icon."
  exit 1
}
