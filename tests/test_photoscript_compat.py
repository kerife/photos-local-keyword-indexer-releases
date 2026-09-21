from __future__ import annotations

from importlib.metadata import distribution
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.skipif(sys.platform != "darwin", reason="AppleScript is macOS-only")
def test_keyword_setter_does_not_repeat_when_photos_reorders_values() -> None:
    from photos_indexer.photoscript_compat import compatible_photoscript_source

    source = Path(distribution("photoscript").locate_file(
        "photoscript/photoscript.applescript"
    )).read_text(encoding="utf-8")
    patched = compatible_photoscript_source(source)
    handler = patched.split("on photoSetKeywords(", 1)[1].split("end photoSetKeywords", 1)[0]
    handler = "on photoSetKeywords(" + handler + "end photoSetKeywords"
    # Replace only the remote Photos boundary; execute the actual handler's
    # loop and comparison with Photos-like reordered read-back.
    handler = handler.replace("photosLibraryWaitForPhotos(WAIT_FOR_PHOTOS)", "")
    handler = handler.replace('tell application "/System/Applications/Photos.app"', "tell me")
    handler = handler.replace("set keywords of media item id (id_) to keyword_list", "my writeKeywords(keyword_list)")
    handler = handler.replace("keywords of media item id (id_)", "my readKeywords()")
    program = '''property MAX_RETRY : 5
property writes : 0
on writeKeywords(values_)
    set writes to writes + 1
end writeKeywords
on readKeywords()
    return {"animal", "playa"}
end readKeywords
''' + handler + '\nphotoSetKeywords("fixture", {"playa", "animal"})\nreturn writes\n'
    result = subprocess.run(["/usr/bin/osascript", "-"], input=program,
                            capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "1"


def test_pinned_photoscript_bridge_uses_foundation_clock_on_macos_26() -> None:
    from photos_indexer import photoscript_compat
    from photos_indexer.photoscript_compat import compatible_photoscript_source

    source_path = distribution("photoscript").locate_file(
        "photoscript/photoscript.applescript"
    )
    original = Path(source_path).read_text(encoding="utf-8")

    patched = compatible_photoscript_source(original)

    assert patched != original
    assert "(time of (current date))" not in patched
    assert patched.count(
        "(current application's NSDate's new()'s timeIntervalSince1970())"
    ) == 2
    assert 'application "/System/Applications/Photos.app"' in patched
    assert 'with parameters {folderName}' not in patched
    restored = (
        patched
        .replace(
            "(current application's NSDate's new()'s timeIntervalSince1970())",
            "(time of (current date))",
        )
        .replace(
            'application "/System/Applications/Photos.app"',
            'application "Photos"',
        )
        .replace(photoscript_compat._SINGLE_KEYWORD_SETTER,
                 photoscript_compat._LEGACY_KEYWORD_SETTER)
        .replace(
            "run script theScript with folderName",
            "run script theScript with parameters {folderName}",
        )
    )
    assert restored == original


@pytest.mark.skipif(sys.platform != "darwin", reason="AppleScriptObjC is macOS-only")
def test_foundation_clock_expression_executes_on_the_current_host() -> None:
    from photos_indexer import photoscript_compat

    result = subprocess.run(
        [
            "/usr/bin/osascript",
            "-e",
            'use framework "Foundation"',
            "-e",
            f"return {photoscript_compat._FOUNDATION_CLOCK}",
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0


def test_unknown_photoscript_bridge_is_not_rewritten() -> None:
    from photos_indexer.photoscript_compat import compatible_photoscript_source

    source = 'use framework "Foundation"\nset x to (time of (current date))\n'

    assert compatible_photoscript_source(source) == source


def test_applescript_constructor_is_restored_after_success_and_failure() -> None:
    import applescript

    from photos_indexer.photoscript_compat import patched_applescript_constructor

    original = applescript.AppleScript
    with patched_applescript_constructor():
        assert applescript.AppleScript is not original
    assert applescript.AppleScript is original

    with pytest.raises(RuntimeError, match="sentinel"):
        with patched_applescript_constructor():
            assert applescript.AppleScript is not original
            raise RuntimeError("sentinel")
    assert applescript.AppleScript is original
