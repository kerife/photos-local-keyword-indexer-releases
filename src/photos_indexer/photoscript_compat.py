from __future__ import annotations

import hashlib
import importlib
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from contextlib import contextmanager
from importlib.metadata import version
from types import ModuleType
from typing import Iterator


_PHOTOSCRIPT_VERSION = "0.5.3"
_PHOTOSCRIPT_SOURCE_SHA256 = (
    "99dec560ac7f49813e9ce012977737a716ef341de296a4b57144103f33791995"
)
_LEGACY_CLOCK = "(time of (current date))"
_FOUNDATION_CLOCK = "(current application's NSDate's new()'s timeIntervalSince1970())"
_LEGACY_PHOTOS_APP = 'application "Photos"'
_ABSOLUTE_PHOTOS_APP = 'application "/System/Applications/Photos.app"'
_LEGACY_SCRIPT_PARAMETERS = "with parameters {folderName}"
_COMPATIBLE_SCRIPT_PARAMETERS = "with folderName"
_IMPORT_LOCK = threading.RLock()
_LEGACY_KEYWORD_SETTER = '''on photoSetKeywords(id_, keyword_list)
	(* set keywords of photo *)
	photosLibraryWaitForPhotos(WAIT_FOR_PHOTOS)
	set count_ to 0
	repeat while count_ < MAX_RETRY
		tell application "Photos"
			set keywords of media item id (id_) to keyword_list
			if keywords of media item id (id_) = keyword_list then
				return keyword_list
			end if
		end tell
		set count_ to count_ + 1
	end repeat
end photoSetKeywords'''
_SINGLE_KEYWORD_SETTER = '''on photoSetKeywords(id_, keyword_list)
	photosLibraryWaitForPhotos(WAIT_FOR_PHOTOS)
	tell application "Photos"
		set keywords of media item id (id_) to keyword_list
	end tell
end photoSetKeywords'''


def compatible_photoscript_source(source: str) -> str:
    """Apply the audited macOS 26 fixes only to pinned PhotoScript 0.5.3."""
    if hashlib.sha256(source.encode("utf-8")).hexdigest() != _PHOTOSCRIPT_SOURCE_SHA256:
        return source
    if source.count(_LEGACY_CLOCK) != 2:
        raise RuntimeError("PhotoScript bridge does not match the audited source")
    if source.count(_LEGACY_KEYWORD_SETTER) != 1:
        raise RuntimeError("PhotoScript keyword setter does not match the audited source")
    return (
        source
        # Photos may reorder keywords. The adapter owns exact multiset
        # read-back; PhotoScript's ordered comparison otherwise writes five
        # times even after a successful mutation.
        .replace(_LEGACY_KEYWORD_SETTER, _SINGLE_KEYWORD_SETTER)
        .replace(_LEGACY_CLOCK, _FOUNDATION_CLOCK)
        .replace(_LEGACY_PHOTOS_APP, _ABSOLUTE_PHOTOS_APP)
        .replace(_LEGACY_SCRIPT_PARAMETERS, _COMPATIBLE_SCRIPT_PARAMETERS)
    )


@contextmanager
def patched_applescript_constructor() -> Iterator[None]:
    """Patch only bridge compilation and always restore the global constructor."""
    with _IMPORT_LOCK:
        applescript = importlib.import_module("applescript")
        original = applescript.AppleScript

        class CompatibleAppleScript(original):  # type: ignore[misc, valid-type]
            def __init__(self, source: str | None = None, path: str | None = None) -> None:
                if source is not None:
                    source = compatible_photoscript_source(source)
                    # NSAppleScript's in-memory compiler still asks Photos for
                    # terminology on macOS 26 and returns -600 when Photos is
                    # not running. Compile the audited source with osacompile,
                    # then load the resulting script without launching Photos.
                    with tempfile.TemporaryDirectory(prefix="photoscript-", dir="/tmp") as directory:
                        source_path = Path(directory) / "photoscript.applescript"
                        compiled_path = Path(directory) / "photoscript.scpt"
                        source_path.write_text(source, encoding="utf-8")
                        result = subprocess.run(
                            ["/usr/bin/osacompile", "-o", str(compiled_path), str(source_path)],
                            capture_output=True,
                            check=False,
                            timeout=30,
                        )
                        if result.returncode != 0:
                            raise RuntimeError("PhotoScript bridge compilation failed")
                        super().__init__(path=str(compiled_path))
                    return
                super().__init__(source=source, path=path)

        applescript.AppleScript = CompatibleAppleScript
        try:
            yield
        finally:
            applescript.AppleScript = original


def load_photoscript() -> ModuleType:
    """Import PhotoScript with the narrow 0.5.3 bridge compatibility shim."""
    with _IMPORT_LOCK:
        loaded = sys.modules.get("photoscript")
        if isinstance(loaded, ModuleType):
            return loaded
        if version("photoscript") != _PHOTOSCRIPT_VERSION:
            return importlib.import_module("photoscript")

        applescript = importlib.import_module("applescript")
        original = applescript.AppleScript
        with patched_applescript_constructor():
            module = importlib.import_module("photoscript")
            loader = importlib.import_module("photoscript.script_loader")

        # PhotoScript also imports the constructor into these module globals.
        # Restore those references so only the audited bridge compilation is
        # affected by the shim.
        module.AppleScript = original
        loader.AppleScript = original
        return module
