from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "packaging" / "write_helper_dependency_inventory.py"


def load_inventory_writer():
    spec = importlib.util.spec_from_file_location("write_helper_dependency_inventory", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HelperDependencyInventoryTests(unittest.TestCase):
    def test_frozen_runtime_always_includes_pyinstaller_bootloader_notice(self) -> None:
        writer = load_inventory_writer()
        rows = []
        writer.add_bootloader_dependency(rows, {
            "pyinstaller": {"version": "6.22.2", "license": "GPL-2.0 with exception", "notice_files": ["COPYING.txt"]}
        })
        self.assertEqual(rows[0]["name"], "pyinstaller")
        self.assertEqual(rows[0]["version"], "6.22.2")

    def test_inventory_uses_analysis_membership_not_the_build_environment_alone(self) -> None:
        writer = load_inventory_writer()
        distributions = {
            "photoscript": {"version": "0.5.3", "license": "MIT", "notice_files": ["LICENSE"]},
            "pyinstaller": {"version": "6.0", "license": "GPL", "notice_files": ["COPYING"]},
        }

        inventory = writer.build_inventory(
            {"photoscript", "httpx"},
            distributions,
            {"photoscript": "photoscript", "PyInstaller": "pyinstaller"},
        )

        self.assertEqual(inventory["schema_version"], 1)
        self.assertEqual(inventory["membership_source"], "pyinstaller-analysis-toc-v1")
        self.assertEqual(inventory["dependencies"], [
            {"name": "photoscript", "version": "0.5.3", "license": "MIT", "notice_files": ["LICENSE"]}
        ])

    def test_inventory_is_canonical_json(self) -> None:
        writer = load_inventory_writer()
        inventory = {
            "schema_version": 1,
            "membership_source": "pyinstaller-analysis-toc-v1",
            "dependencies": [{"name": "httpx", "version": "0.1", "license": "BSD", "notice_files": []}],
        }
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "inventory.json"
            writer.write_inventory(output, inventory)

            self.assertEqual(output.read_text(encoding="utf-8"), json.dumps(inventory, sort_keys=True, separators=(",", ":")) + "\n")

    def test_notice_copy_preserves_text_under_a_package_scoped_path(self) -> None:
        writer = load_inventory_writer()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "LICENSE"
            source.write_text("license text\n", encoding="utf-8")
            destination = root / "notices"

            copied = writer.copy_notice_texts({"photoscript": [source]}, destination)

            self.assertEqual(copied, {"photoscript": ["photoscript/LICENSE"]})
            self.assertEqual((destination / "photoscript/LICENSE").read_text(encoding="utf-8"), "license text\n")

    def test_missing_notice_never_reuses_another_distribution_with_the_same_license(self) -> None:
        writer = load_inventory_writer()
        rows = [
            {"name": "photoscript", "license": "MIT"},
            {"name": "unrelated-mit-package", "license": "MIT"},
        ]

        with self.assertRaises(ValueError):
            writer.require_package_scoped_notices(rows, {"photoscript": [Path("/tmp/LICENSE")], "unrelated-mit-package": []})


if __name__ == "__main__":
    unittest.main()
