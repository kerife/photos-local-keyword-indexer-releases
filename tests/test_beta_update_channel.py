import os
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_development_build_rejects_update_configuration_before_build():
    result = subprocess.run(
        ["/bin/zsh", str(ROOT / "packaging/build_swift_app.sh")],
        env={**os.environ, "RELEASE_BUILD": "0", "SPARKLE_FEED_URL": "https://updates.example.org/feed.xml"},
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "build_swift_app:FAIL:development_updates_disabled" in result.stderr


@pytest.mark.parametrize("missing", ["LICENSE", "docs/privacy.md", "docs/support.md", "docs/release/third-party-notices.md"])
def test_build_blocks_before_compilation_when_public_document_is_missing(tmp_path, missing):
    script = tmp_path / "packaging/build_swift_app.sh"
    script.parent.mkdir()
    script.write_bytes((ROOT / "packaging/build_swift_app.sh").read_bytes())
    for name in ("LICENSE", "docs/privacy.md", "docs/support.md", "docs/release/third-party-notices.md"):
        if name != missing:
            target = tmp_path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("public document\n")
    result = subprocess.run(
        ["/bin/zsh", str(script)],
        env={**os.environ, "RELEASE_BUILD": "0", "SPARKLE_FEED_URL": "", "SPARKLE_PUBLIC_ED_KEY": ""},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "build_swift_app:FAIL:distribution_document_missing" in result.stderr
