from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "packaging" / "native_dependency_inventory.py"


def load_inventory():
    spec = importlib.util.spec_from_file_location("native_dependency_inventory", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class NativeDependencyInventoryTests(unittest.TestCase):
    def make_homebrew_fixture(self, root: Path) -> tuple[Path, dict[str, Path]]:
        cellar = root / "Cellar"
        opt = root / "opt"
        libraries: dict[str, Path] = {}
        for formula, version, license_name, library_names in (
            ("mpdecimal", "4.0.1", "BSD-2-Clause", ["libmpdec.4.dylib"]),
            ("xz", "5.8.3", "0BSD AND GPL-2.0-or-later", ["liblzma.5.dylib"]),
            ("openssl@3", "3.6.3", "Apache-2.0", ["libcrypto.3.dylib", "libssl.3.dylib"]),
        ):
            prefix = cellar / formula / version
            (prefix / "lib").mkdir(parents=True)
            (prefix / ".brew").mkdir()
            (prefix / ".brew" / f"{formula}.rb").write_text(
                f'class Fixture < Formula>\n  license "{license_name}"\nend\n', encoding="utf-8"
            )
            (prefix / "LICENSE").write_text(f"{formula} license\n", encoding="utf-8")
            opt.mkdir(exist_ok=True)
            (opt / formula).symlink_to(prefix, target_is_directory=True)
            for library_name in library_names:
                path = opt / formula / "lib" / library_name
                path.write_bytes(b"native")
                libraries[library_name] = path
        toc = root / "Analysis-00.toc"
        toc.write_text(repr(tuple((name, str(path), "BINARY") for name, path in libraries.items())), encoding="utf-8")
        return toc, libraries

    def test_derives_known_homebrew_dylibs_with_exact_versions_and_package_notices(self) -> None:
        inventory = load_inventory()
        with tempfile.TemporaryDirectory() as temporary:
            toc, _ = self.make_homebrew_fixture(Path(temporary))

            resolved = inventory.resolve_native_dependencies(toc)

            self.assertEqual(
                resolved.rows,
                [
                    {"name": "mpdecimal", "version": "4.0.1", "license": "BSD-2-Clause", "notice_files": []},
                    {"name": "openssl@3", "version": "3.6.3", "license": "Apache-2.0", "notice_files": []},
                    {"name": "xz", "version": "5.8.3", "license": "0BSD AND GPL-2.0-or-later", "notice_files": []},
                ],
            )
            self.assertEqual(set(resolved.source_notices), {"mpdecimal", "openssl@3", "xz"})
            self.assertTrue(all(paths and paths[0].name == "LICENSE" for paths in resolved.source_notices.values()))
            self.assertNotIn(str(Path(temporary)), inventory.canonical_inventory_json(resolved.rows))

    def test_rejects_a_bundled_native_dylib_without_an_audited_formula_mapping(self) -> None:
        inventory = load_inventory()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            toc, _ = self.make_homebrew_fixture(root)
            entries = eval(toc.read_text(encoding="utf-8"), {"__builtins__": {}})
            unknown = root / "opt" / "unknown" / "lib" / "libunknown.1.dylib"
            unknown.parent.mkdir(parents=True)
            unknown.write_bytes(b"native")
            toc.write_text(repr((*entries, ("libunknown.1.dylib", str(unknown), "BINARY"))), encoding="utf-8")

            with self.assertRaises(inventory.NativeInventoryError):
                inventory.resolve_native_dependencies(toc)

    def test_rejects_a_formula_when_no_package_scoped_notice_is_available(self) -> None:
        inventory = load_inventory()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            toc, _ = self.make_homebrew_fixture(root)
            (root / "Cellar" / "mpdecimal" / "4.0.1" / "LICENSE").unlink()

            with self.assertRaises(inventory.NativeInventoryError):
                inventory.resolve_native_dependencies(toc)

    def test_collects_minimum_macos_diagnostics_without_putting_paths_in_rows(self) -> None:
        inventory = load_inventory()
        with tempfile.TemporaryDirectory() as temporary:
            toc, libraries = self.make_homebrew_fixture(Path(temporary))
            resolved = inventory.resolve_native_dependencies(toc)

            minimums = inventory.minimum_macos_versions(
                libraries.values(),
                run=lambda command: "minos 14.0\nsdk 15.0\n" if command[-1].endswith("libmpdec.4.dylib") else "minos 26.0\nsdk 26.4\n",
            )

            self.assertEqual(minimums["libmpdec.4.dylib"], "14.0")
            self.assertEqual(minimums["libssl.3.dylib"], "26.0")
            self.assertTrue(all("/" not in key for key in minimums))
            self.assertEqual(len(resolved.rows), 3)

    def test_rejects_pbs_runtime_when_its_pristine_digest_does_not_match(self) -> None:
        inventory = load_inventory()
        notice_root = PROJECT_ROOT / "packaging" / "licenses" / "pbs-20260901"
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / "libpython3.12.dylib"
            runtime.write_bytes(b"fixture")

            with self.assertRaises(inventory.NativeInventoryError):
                inventory.resolve_pbs_static_dependencies(
                    notice_root / "PYTHON.static-dependencies.json", notice_root, runtime
                )


if __name__ == "__main__":
    unittest.main()
