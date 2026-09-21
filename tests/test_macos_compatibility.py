from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "packaging" / "verify_macos_compatibility.py"


def load_verifier():
    spec = importlib.util.spec_from_file_location("verify_macos_compatibility", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MacOSCompatibilityTests(unittest.TestCase):
    def test_parses_arm64_minos_and_rejects_newer_than_macos14(self) -> None:
        verifier = load_verifier()
        self.assertEqual(verifier.arm64_minos("platform MACOS\n    minos 11.0\n"), (11, 0))
        self.assertTrue(verifier.is_supported_minos((14, 0)))
        self.assertFalse(verifier.is_supported_minos((14, 7)))
        self.assertFalse(verifier.is_supported_minos((15, 0)))

    def test_non_macho_regular_files_are_not_inspected_as_binaries(self) -> None:
        verifier = load_verifier()
        self.assertFalse(verifier.is_macho_description("ASCII text"))
        self.assertTrue(verifier.is_macho_description("Mach-O 64-bit executable arm64"))


if __name__ == "__main__":
    unittest.main()
