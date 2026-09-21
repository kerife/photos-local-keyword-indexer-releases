from __future__ import annotations

import os
from pathlib import Path
import plistlib
import stat
import subprocess
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VERIFIER = PROJECT_ROOT / "packaging" / "verify_embedded_helper.sh"


class NestedHelperIntegrationTests(unittest.TestCase):
    def _fake_tool_environment(self, root: Path) -> dict[str, str]:
        fake_bin = root / "bin"
        fake_bin.mkdir()
        (fake_bin / "lipo").write_text(
            "#!/bin/zsh\n[[ \"$1\" == \"-archs\" ]] && print -- arm64\n",
            encoding="utf-8",
        )
        (fake_bin / "file").write_text(
            "#!/bin/zsh\n"
            "case \"${@: -1}\" in\n"
            "  */Contents/MacOS/PhotosIndexerWorker|*.dylib) print -- 'Mach-O 64-bit arm64' ;;\n"
            "  *) print -- data ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        for command in fake_bin.iterdir():
            command.chmod(0o755)
        environment = os.environ.copy()
        environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
        return environment

    def _fingerprint(self) -> str:
        return subprocess.check_output(
            [
                "/usr/bin/python3",
                str(PROJECT_ROOT / "packaging" / "helper_source_fingerprint.py"),
                str(PROJECT_ROOT),
            ],
            text=True,
        ).strip()

    def _make_nested_app(self, root: Path) -> tuple[Path, Path]:
        app = root / "PhotosLocalKeywordIndexer.app"
        helper_app = app / "Contents/Helpers/PhotosIndexerWorker.app"
        executable = helper_app / "Contents/MacOS/PhotosIndexerWorker"
        resources = helper_app / "Contents/Resources"
        frameworks = helper_app / "Contents/Frameworks"
        executable.parent.mkdir(parents=True)
        resources.mkdir()
        frameworks.mkdir()
        (helper_app / "Contents/Info.plist").write_bytes(
            plistlib.dumps(
                {
                    "CFBundleExecutable": "PhotosIndexerWorker",
                    "CFBundleIdentifier": "com.photoslocalkeywordindexer.worker",
                    "CFBundlePackageType": "APPL",
                    "CFBundleShortVersionString": "0.1.0",
                    "CFBundleVersion": "1",
                    "LSUIElement": True,
                    "NSPhotoLibraryUsageDescription": "Fotos",
                    "NSPhotoLibraryAddUsageDescription": "Fotos",
                    "NSAppleEventsUsageDescription": "Fotos",
                }
            )
        )
        executable.write_text(
            "#!/bin/zsh\n"
            "[[ \"$1\" == \"--self-check\" ]] || exit 1\n"
            "print -- '{\"status\":\"ok\",\"runtime\":\"embedded\","
            "\"architecture\":\"arm64\",\"protocol\":\"jsonl\"}'\n",
            encoding="utf-8",
        )
        executable.chmod(0o755)
        marker = resources / ".photos-indexer-source-fingerprint"
        marker.write_text(f"{self._fingerprint()}\n", encoding="utf-8")
        marker.chmod(0o600)
        (frameworks / "runtime.dat").write_text("runtime", encoding="utf-8")
        return app, helper_app

    def _run(self, app: Path, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/zsh", str(VERIFIER), str(app)],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )

    def test_verifier_accepts_the_native_nested_helper_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app, _ = self._make_nested_app(root)

            result = self._run(app, self._fake_tool_environment(root))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("embedded_helper:PASS:bundle_identity", result.stdout)
        self.assertIn("embedded_helper:PASS:fingerprint", result.stdout)
        self.assertIn("embedded_helper:READY", result.stdout)

    def test_verifier_rejects_the_historical_flat_helper_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / "PhotosLocalKeywordIndexer.app"
            helper = app / "Contents/Helpers/PhotosIndexerWorker/PhotosIndexerWorker"
            helper.parent.mkdir(parents=True)
            helper.write_text(
                "#!/bin/zsh\n"
                "print -- '{\"status\":\"ok\",\"runtime\":\"embedded\","
                "\"architecture\":\"arm64\",\"protocol\":\"jsonl\"}'\n",
                encoding="utf-8",
            )
            helper.chmod(0o755)

            result = self._run(app, self._fake_tool_environment(root))

        self.assertEqual(result.returncode, 1)
        self.assertIn("embedded_helper:FAIL:helper_bundle_invalid", result.stderr)
        self.assertNotIn("embedded_helper:READY", result.stdout)

    def test_verifier_rejects_invalid_helper_bundle_identity(self) -> None:
        invalid_values = {
            "CFBundleIdentifier": "com.example.worker",
            "CFBundlePackageType": "BNDL",
            "CFBundleExecutable": "OtherWorker",
            "LSUIElement": False,
        }
        for key, value in invalid_values.items():
            with self.subTest(key=key), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                app, helper_app = self._make_nested_app(root)
                plist_path = helper_app / "Contents/Info.plist"
                info = plistlib.loads(plist_path.read_bytes())
                info[key] = value
                plist_path.write_bytes(plistlib.dumps(info))

                result = self._run(app, self._fake_tool_environment(root))

            self.assertEqual(result.returncode, 1)
            self.assertIn("embedded_helper:FAIL:bundle_identity", result.stderr)
            self.assertNotIn("embedded_helper:READY", result.stdout)

    def test_verifier_rejects_hardlinked_helper_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app, helper_app = self._make_nested_app(root)
            runtime = helper_app / "Contents/Frameworks/runtime.dat"
            os.link(runtime, helper_app / "Contents/Resources/runtime-alias.dat")

            result = self._run(app, self._fake_tool_environment(root))

        self.assertEqual(result.returncode, 1)
        self.assertIn("embedded_helper:FAIL:payload_hardlink", result.stderr)
        self.assertNotIn("embedded_helper:READY", result.stdout)

    def test_verifier_rejects_group_or_world_writable_bundle_components(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app, helper_app = self._make_nested_app(root)
            resources = helper_app / "Contents/Resources"
            resources.chmod(stat.S_IMODE(resources.stat().st_mode) | 0o020)

            result = self._run(app, self._fake_tool_environment(root))

        self.assertEqual(result.returncode, 1)
        self.assertIn("embedded_helper:FAIL:payload_permissions", result.stderr)
        self.assertNotIn("embedded_helper:READY", result.stdout)

    def test_verifier_requires_a_private_fingerprint_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app, helper_app = self._make_nested_app(root)
            marker = helper_app / "Contents/Resources/.photos-indexer-source-fingerprint"
            marker.chmod(0o644)

            result = self._run(app, self._fake_tool_environment(root))

        self.assertEqual(result.returncode, 1)
        self.assertIn("embedded_helper:FAIL:fingerprint", result.stderr)
        self.assertNotIn("embedded_helper:READY", result.stdout)

    def test_verifier_accepts_only_symlinks_resolving_inside_the_helper_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app, helper_app = self._make_nested_app(root)
            resources = helper_app / "Contents/Resources"
            internal_link = resources / "runtime-link.dat"
            internal_link.symlink_to("../Frameworks/runtime.dat")
            environment = self._fake_tool_environment(root)

            accepted = self._run(app, environment)

            internal_link.unlink()
            outside = root / "outside.dat"
            outside.write_text("outside", encoding="utf-8")
            internal_link.symlink_to(outside)
            rejected = self._run(app, environment)

        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertEqual(rejected.returncode, 1)
        self.assertIn("embedded_helper:FAIL:payload_symlink", rejected.stderr)
        self.assertNotIn("embedded_helper:READY", rejected.stdout)

    def test_verifier_rejects_non_arm64_nested_macho(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app, helper_app = self._make_nested_app(root)
            (helper_app / "Contents/Frameworks/native.dylib").write_bytes(b"native")
            environment = self._fake_tool_environment(root)
            fake_lipo = root / "bin/lipo"
            fake_lipo.write_text(
                "#!/bin/zsh\n"
                "[[ \"$2\" == *native.dylib ]] && print -- x86_64 || print -- arm64\n",
                encoding="utf-8",
            )
            fake_lipo.chmod(0o755)

            result = self._run(app, environment)

        self.assertEqual(result.returncode, 1)
        self.assertIn("embedded_helper:FAIL:nested_architecture", result.stderr)
        self.assertNotIn("embedded_helper:READY", result.stdout)

    def test_verifier_rejects_symlinked_native_bundle_core_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app, helper_app = self._make_nested_app(root)
            resources = helper_app / "Contents/Resources"
            real_resources = helper_app / "Contents/RealResources"
            resources.rename(real_resources)
            resources.symlink_to("RealResources", target_is_directory=True)

            result = self._run(app, self._fake_tool_environment(root))

        self.assertEqual(result.returncode, 1)
        self.assertIn("embedded_helper:FAIL:bundle_path_invalid", result.stderr)
        self.assertNotIn("embedded_helper:READY", result.stdout)


if __name__ == "__main__":
    unittest.main()
