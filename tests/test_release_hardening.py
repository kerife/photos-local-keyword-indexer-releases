from __future__ import annotations

import json
import os
import plistlib
import re
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SPARKLE_REVISION = "6276ba2b404829d139c45ff98427cf90e2efc59b"
PROJECT_VERSION = re.search(
    r'^version = "([^"]+)"$',
    (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"),
    re.MULTILINE,
).group(1)


class ReleaseHardeningTests(unittest.TestCase):
    @staticmethod
    def _current_helper_source_fingerprint() -> str:
        return subprocess.run(
            [
                "/usr/bin/python3",
                str(PROJECT_ROOT / "packaging" / "helper_source_fingerprint.py"),
                str(PROJECT_ROOT),
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def _create_embedded_helper_bundle(
        self,
        bundle_root: Path,
        *,
        helper_script: str,
    ) -> tuple[Path, Path]:
        helper = bundle_root / "Contents" / "MacOS" / "PhotosIndexerWorker"
        helper_info = bundle_root / "Contents" / "Info.plist"
        helper_resources = bundle_root / "Contents" / "Resources"
        helper_frameworks = bundle_root / "Contents" / "Frameworks"
        helper.parent.mkdir(parents=True)
        helper_resources.mkdir(parents=True)
        helper_frameworks.mkdir(parents=True)
        helper.write_text(helper_script, encoding="utf-8")
        helper.chmod(0o755)
        with helper_info.open("wb") as stream:
            plistlib.dump(
                    {
                        "CFBundleExecutable": "PhotosIndexerWorker",
                        "CFBundleIdentifier": "com.photoslocalkeywordindexer.worker",
                        "CFBundlePackageType": "APPL",
                        "CFBundleShortVersionString": "1.2.3",
                        "CFBundleVersion": "45",
                        "LSUIElement": True,
                        "NSPhotoLibraryUsageDescription": "Fotos",
                        "NSPhotoLibraryAddUsageDescription": "Fotos",
                        "NSAppleEventsUsageDescription": "Fotos",
                    },
                stream,
            )
        helper_marker = helper_resources / ".photos-indexer-source-fingerprint"
        helper_marker.write_text(self._current_helper_source_fingerprint(), encoding="utf-8")
        helper_marker.chmod(0o600)
        return bundle_root, helper

    def test_release_scripts_use_only_the_nested_helper_app_topology(self) -> None:
        sign_source = (PROJECT_ROOT / "packaging" / "sign_app.sh").read_text(encoding="utf-8")
        verify_source = (PROJECT_ROOT / "packaging" / "verify_release.sh").read_text(encoding="utf-8")
        notarize_source = (PROJECT_ROOT / "packaging" / "notarize.sh").read_text(encoding="utf-8")

        nested_app = 'Contents/Helpers/PhotosIndexerWorker.app'
        nested_marker = f'{nested_app}/Contents/Resources/.photos-indexer-source-fingerprint'
        self.assertIn('helper_app="$contents/Helpers/PhotosIndexerWorker.app"', sign_source)
        self.assertIn('helper="$helper_app/Contents/MacOS/PhotosIndexerWorker"', sign_source)
        self.assertIn('helper_source_marker="$helper_app/Contents/Resources/.photos-indexer-source-fingerprint"', sign_source)
        self.assertIn('helper_app="$contents/Helpers/PhotosIndexerWorker.app"', verify_source)
        self.assertIn('helper="$helper_app/Contents/MacOS/PhotosIndexerWorker"', verify_source)
        self.assertIn(nested_marker, notarize_source)

        helper_bundle_sign = sign_source.index('--entitlements "$helper_entitlements"')
        sparkle_sign = sign_source.index('codesign "${sign_flags[@]}" "$framework"')
        outer_sign = sign_source.index(
            'codesign "${sign_flags[@]}" --identifier "com.photoslocalkeywordindexer.app" --entitlements "$app_entitlements" "$app_path"'
        )
        self.assertLess(helper_bundle_sign, sparkle_sign)
        self.assertLess(sparkle_sign, outer_sign)

    def _run_python_helper_adversarial_bundle(self, case: str) -> dict[str, object]:
        """Run the real build gate around a controlled fake PyInstaller result."""
        build_dir = PROJECT_ROOT / "build"
        build_dir.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build_dir) as tmp:
            root = Path(tmp)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            build_root = root / "helper-build"
            helper_app = build_root / "dist/PhotosIndexerWorker.app"
            external_app = root / "external/PhotosIndexerWorker.app"
            external_payload = root / "external-payload"
            external_payload.write_bytes(b"shared")
            execution_marker = root / "helper-executed"
            helper_plist = root / "HelperInfo.plist"
            helper_plist.write_bytes(plistlib.dumps({
                "CFBundleExecutable": "PhotosIndexerWorker",
                "CFBundleIdentifier": "com.photoslocalkeywordindexer.worker",
                "CFBundlePackageType": "APPL",
                "CFBundleShortVersionString": PROJECT_VERSION,
                "CFBundleVersion": "1",
                "LSUIElement": True,
                "NSPhotoLibraryUsageDescription": "Fotos",
                "NSPhotoLibraryAddUsageDescription": "Fotos",
                "NSAppleEventsUsageDescription": "Fotos",
            }))
            fake_uname = fake_bin / "uname"
            fake_uname.write_text('#!/bin/zsh\n[[ "$1" == "-m" ]] && print -- arm64\n', encoding="utf-8")
            fake_uname.chmod(0o755)
            fake_lipo = fake_bin / "lipo"
            fake_lipo.write_text('#!/bin/zsh\n[[ "$1" == "-archs" ]] && print -- arm64\n', encoding="utf-8")
            fake_lipo.chmod(0o755)
            fake_python = fake_bin / "python3.12"
            fake_python.write_text(
                '''#!/bin/zsh
if [[ "$1" == "-c" && "$2" == *"sys.version_info"* ]]; then print -- 3.12; exit 0; fi
if [[ "$1" == "-c" && "$2" == *"platform.machine"* ]]; then print -- arm64; exit 0; fi
if [[ "$1" == "-c" && "$2" == *"importlib.metadata"* ]]; then exit 0; fi
if [[ "$1" == "-c" && "$2" == "import PyInstaller" ]]; then exit 0; fi
if [[ "$1" == "-c" && "$2" == *"PhotoScriptBridge.preflight_compatibility()"* ]]; then exit 0; fi
if [[ "$1" == "-m" && "$2" == "PyInstaller" ]]; then
  shift 2
  dist_path=""
  while (( $# > 0 )); do
    if [[ "$1" == "--distpath" ]]; then
      dist_path="$2"
      shift 2
    else
      shift
    fi
  done
  helper_app="$dist_path/PhotosIndexerWorker.app"
  /bin/rm -rf -- "$helper_app"
  if [[ "$FAKE_CASE" == "partial" ]]; then
    /bin/mkdir -p -- "$helper_app/Contents"
    print -- partial > "$helper_app/Contents/partial"
    exit 1
  fi
  target_app="$helper_app"
  if [[ "$FAKE_CASE" == "root_symlink" ]]; then
    target_app="$EXTERNAL_APP"
  fi
  /bin/mkdir -p -- "$target_app/Contents/MacOS" "$target_app/Contents/Frameworks" "$target_app/Contents/Resources"
  /bin/cp -- "$HELPER_PLIST" "$target_app/Contents/Info.plist"
  if [[ "$FAKE_CASE" == "standalone_runtime" ]]; then
    print -- runtime > "$target_app/Contents/Frameworks/libpython3.12.dylib"
  else
    print -- runtime > "$target_app/Contents/Frameworks/Python"
  fi
  {
    print -- '#!/bin/zsh'
    print -- ': > "'"$EXECUTION_MARKER"'"'
    print -- 'print -- '\''{"status":"ok","runtime":"embedded","architecture":"arm64","protocol":"jsonl"}'\'''
  } > "$target_app/Contents/MacOS/PhotosIndexerWorker"
  /bin/chmod 755 "$target_app/Contents/MacOS/PhotosIndexerWorker"
  case "$FAKE_CASE" in
    root_symlink) /bin/ln -s -- "$target_app" "$helper_app" ;;
    missing_executable) /bin/rm -- "$helper_app/Contents/MacOS/PhotosIndexerWorker" ;;
    hardlink) /bin/ln -- "$EXTERNAL_PAYLOAD" "$helper_app/Contents/Frameworks/shared" ;;
    unsafe_permissions) /bin/chmod 666 "$helper_app/Contents/Frameworks/Python" ;;
  esac
  exit 0
fi
exit 1
''',
                encoding="utf-8",
            )
            fake_python.chmod(0o755)
            environment = os.environ.copy()
            environment.update({
                "PATH": f"{fake_bin}:{environment['PATH']}",
                "PYTHON_BIN": str(fake_python),
                "BUILD_ROOT": str(build_root),
                "VERIFY_HELPER": "1",
                "FAKE_CASE": case,
                "EXTERNAL_APP": str(external_app),
                "EXTERNAL_PAYLOAD": str(external_payload),
                "EXECUTION_MARKER": str(execution_marker),
                "HELPER_PLIST": str(helper_plist),
            })
            result = subprocess.run(
                ["/bin/zsh", str(PROJECT_ROOT / "packaging/build_python_helper.sh")],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            return {
                "returncode": result.returncode,
                "stderr": result.stderr,
                "helper_exists": helper_app.exists() or helper_app.is_symlink(),
                "external_app_exists": external_app.is_dir(),
                "external_fingerprint_exists": (
                    external_app / "Contents/Resources/.photos-indexer-source-fingerprint"
                ).exists(),
                "external_payload_exists": external_payload.is_file(),
                "executed": execution_marker.exists(),
            }

    def test_python_helper_accepts_standalone_libpython_runtime_before_later_gates(self) -> None:
        """A legitimate standalone runtime must pass the topology gate unchanged."""
        result = self._run_python_helper_adversarial_bundle("standalone_runtime")

        self.assertEqual(result["returncode"], 1)
        self.assertIn("dependency_inventory_source_missing", result["stderr"])
        self.assertNotIn("PyInstaller did not produce the expected helper", result["stderr"])

    def test_helper_source_fingerprint_is_deterministic_and_content_sensitive(self) -> None:
        script = PROJECT_ROOT / "packaging" / "helper_source_fingerprint.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for relative in (
                "src/photos_indexer/service.py",
                "src/photos_indexer/ipc.py",
                "packaging/worker_entry.py",
                "packaging/PhotosIndexerWorker.spec",
                "pyproject.toml",
            ):
                destination = root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes((PROJECT_ROOT / relative).read_bytes())

            first = subprocess.run(
                ["/usr/bin/python3", str(script), str(root)],
                check=False,
                capture_output=True,
                text=True,
            )
            second = subprocess.run(
                ["/usr/bin/python3", str(script), str(root)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(first.stdout, second.stdout)
            self.assertRegex(first.stdout.strip(), r"^[0-9a-f]{64}$")

            with (root / "src/photos_indexer/service.py").open("ab") as stream:
                stream.write(b"\n# fingerprint mutation\n")
            changed = subprocess.run(
                ["/usr/bin/python3", str(script), str(root)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(changed.returncode, 0, changed.stderr)
            self.assertNotEqual(first.stdout, changed.stdout)

    def test_helper_source_fingerprint_covers_nested_python_modules(self) -> None:
        """A new package module must invalidate an existing frozen helper."""
        script = PROJECT_ROOT / "packaging" / "helper_source_fingerprint.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for relative in (
                "src/photos_indexer/service.py",
                "packaging/worker_entry.py",
                "packaging/PhotosIndexerWorker.spec",
                "pyproject.toml",
            ):
                destination = root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes((PROJECT_ROOT / relative).read_bytes())

            nested = root / "src/photos_indexer/places/context.py"
            nested.parent.mkdir(parents=True)
            nested.write_text("PLACE_CONTEXT = True\n", encoding="utf-8")
            first = subprocess.run(
                ["/usr/bin/python3", str(script), str(root)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(first.returncode, 0, first.stderr)

            nested.write_text("PLACE_CONTEXT = False\n", encoding="utf-8")
            changed = subprocess.run(
                ["/usr/bin/python3", str(script), str(root)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(changed.returncode, 0, changed.stderr)
            self.assertNotEqual(first.stdout, changed.stdout)

    def test_release_pipeline_requires_the_helper_source_fingerprint(self) -> None:
        expected = {
            "build_python_helper.sh": "helper_source_fingerprint",
            "build_swift_app.sh": "helper_source_fingerprint_stale",
            "build_dmg.sh": "helper_source_fingerprint_stale",
            "release_preflight.sh": "helper_source_fingerprint_stale",
            "sign_app.sh": "helper_source_fingerprint_stale",
            "notarize.sh": "helper_source_fingerprint_stale",
        }
        for name, marker in expected.items():
            source = (PROJECT_ROOT / "packaging" / name).read_text(encoding="utf-8")
            self.assertIn(marker, source, name)

    def test_release_verification_requires_meaningful_photos_and_automation_privacy_descriptions(
        self,
    ) -> None:
        source = (PROJECT_ROOT / "packaging" / "verify_release.sh").read_text(encoding="utf-8")

        self.assertIn("validate_privacy_usage_descriptions", source)
        self.assertIn('"NSPhotoLibraryUsageDescription"', source)
        self.assertIn('"NSAppleEventsUsageDescription"', source)
        self.assertIn("privacy usage description is missing or unsafe", source)
        self.assertLess(
            source.index("validate_privacy_usage_descriptions"),
            source.index("validate_sparkle_configuration"),
        )

    def test_release_success_messages_do_not_echo_local_artifact_paths(self) -> None:
        expected = {
            "build_python_helper.sh": "release_helper:READY_FOR_APP_BUNDLE",
            "build_swift_app.sh": "release_app:READY_FOR_SIGNING_AND_DMG",
            "build_dmg.sh": "release_dmg:READY_FOR_NOTARIZATION",
            "sign_app.sh": "release_signature:READY_FOR_DMG",
        }
        forbidden = (
            'print -- "Built private helper: $helper"',
            'print -- "Built release candidate app bundle: $app_bundle"',
            'print -- "Built local development app bundle (updates disabled; not a release): $app_bundle"',
            'print -- "Created release candidate DMG: $dmg_path"',
            'print -- "Created local development DMG (not a release): $dmg_path"',
            'print -- "Release app signing and verification passed: $app_path"',
        )
        for name, marker in expected.items():
            source = (PROJECT_ROOT / "packaging" / name).read_text(encoding="utf-8")
            self.assertIn(marker, source)
        for source_text in (
            (PROJECT_ROOT / "packaging" / name).read_text(encoding="utf-8")
            for name in expected
        ):
            for line in forbidden:
                self.assertNotIn(line, source_text)

    def test_development_artifact_markers_are_explicitly_not_for_distribution(self) -> None:
        expected = {
            "build_swift_app.sh": (
                "release_app:HINT:development_build:not_for_official_distribution_run_verify_public_beta_before_any_authorized_publication"
            ),
            "build_dmg.sh": (
                "release_dmg:HINT:development_build:not_for_official_distribution_run_verify_public_beta_before_any_authorized_publication"
            ),
        }
        for name, marker in expected.items():
            source = (PROJECT_ROOT / "packaging" / name).read_text(encoding="utf-8")
            self.assertIn(marker, source)
            self.assertLess(source.index(marker), source.index("READY_DEV"))

    def test_development_build_can_use_an_explicit_stable_local_signing_identity(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "build_swift_app.sh").read_text(encoding="utf-8")
        cleanup = source.index('rm -rf -- "$app_bundle"')

        self.assertIn('local_code_sign_identity="${LOCAL_CODE_SIGN_IDENTITY:-}"', source)
        self.assertIn('development_sign_identity="${local_code_sign_identity:--}"', source)
        self.assertIn("build_swift_app:FAIL:local_code_sign_identity_unavailable", source)
        self.assertIn('security find-identity -v -p codesigning', source)
        self.assertLess(source.index('security find-identity -v -p codesigning'), cleanup)
        self.assertIn('codesign --force --sign "$development_sign_identity"', source)
        self.assertNotIn('--timestamp --sign "$development_sign_identity"', source)
        self.assertIn(
            "release_app:HINT:development_signature:stable_local_identity_not_for_distribution",
            source,
        )
        self.assertIn(
            "release_app:HINT:development_signature:ad_hoc_permissions_may_reset_after_rebuild",
            source,
        )

    def test_release_build_rejects_the_local_only_signing_identity(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "build_swift_app.sh").read_text(encoding="utf-8")
        cleanup = source.index('rm -rf -- "$app_bundle"')
        guard = 'if [[ "$release_build" == "1" && -n "$local_code_sign_identity" ]]; then'

        self.assertIn(guard, source)
        self.assertIn("build_swift_app:FAIL:local_code_sign_identity_not_allowed_for_release", source)
        self.assertLess(source.index(guard), cleanup)

    def test_build_channel_is_explicitly_written_for_development_and_release(self) -> None:
        plist = (PROJECT_ROOT / "packaging" / "AppInfo.plist").read_text(encoding="utf-8")
        source = (PROJECT_ROOT / "packaging" / "build_swift_app.sh").read_text(encoding="utf-8")
        self.assertIn("PhotosLocalKeywordIndexerBuildChannel", plist)
        self.assertIn('Set :PhotosLocalKeywordIndexerBuildChannel development', source)
        self.assertIn('Set :PhotosLocalKeywordIndexerBuildChannel release', source)
        self.assertLess(source.index("Set :PhotosLocalKeywordIndexerBuildChannel development"), source.index("release_app:READY_DEV"))

    def test_development_app_warns_against_launching_from_a_sandboxed_agent(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "build_swift_app.sh").read_text(encoding="utf-8")
        documentation = (PROJECT_ROOT / "docs" / "release.md").read_text(encoding="utf-8")
        hint = "release_app:HINT:local_launch:use_finder_or_terminal_outside_sandboxed_agent"

        self.assertIn(hint, source)
        self.assertLess(source.index(hint), source.index("release_app:READY_DEV"))
        hint_line = next(line for line in source.splitlines() if hint in line)
        self.assertNotIn("$app_bundle", hint_line)
        self.assertIn("_RegisterApplication", documentation)
        self.assertIn("LaunchServices", documentation)
        self.assertIn("ASN", documentation)
        self.assertIn("Finder o Terminal.app", documentation)

    def test_release_build_treats_the_https_scheme_case_insensitively(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "build_swift_app.sh").read_text(encoding="utf-8")

        self.assertIn('[[ "${feed_url:l}" == https://* ]]', source)
        self.assertNotIn('[[ "$feed_url" == https://* ]]', source)
        self.assertIn('parsed.scheme != "https"', source)
        self.assertIn('parsed.hostname is None', source)
        self.assertIn("any(character in feed for character in (';', '\"', \"'\", \"\\\\\"))", source)

    def test_embedded_helper_diagnostic_is_read_only_and_checks_the_frozen_runtime(self) -> None:
        script_path = PROJECT_ROOT / "packaging" / "verify_embedded_helper.sh"
        self.assertTrue(script_path.is_file())
        script = script_path.read_text(encoding="utf-8")

        self.assertIn('"$helper" --self-check', script)
        self.assertIn("env -i", script)
        self.assertIn('lipo -archs "$helper"', script)
        self.assertIn("embedded_helper:PASS:photos_tcc:NOT_CHECKED", script)
        self.assertIn("embedded_helper:PASS:automation_tcc:NOT_CHECKED", script)
        self.assertNotIn("tccutil", script)
        self.assertNotIn("osascript", script)
        self.assertNotIn("curl", script)
        self.assertNotIn("ollama", script.lower())

    def test_embedded_helper_rejects_invalid_argument_count_without_leaking_paths(self) -> None:
        script = PROJECT_ROOT / "packaging" / "verify_embedded_helper.sh"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "lipo-invoked"
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_lipo = fake_bin / "lipo"
            fake_lipo.write_text(f"#!/bin/zsh\n: > '{marker}'\nexit 0\n", encoding="utf-8")
            fake_lipo.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
            argument_sets = ((), (str(root / "App.app"), "unexpected"))

            for arguments in argument_sets:
                with self.subTest(arguments=arguments):
                    result = subprocess.run(
                        ["/bin/zsh", str(script), *arguments],
                        check=False,
                        capture_output=True,
                        text=True,
                        env=environment,
                    )

                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(
                        result.stderr,
                        "embedded_helper:FAIL:invalid_arguments\n"
                        "embedded_helper:HINT:invalid_arguments:provide_one_app_bundle_path\n",
                    )
                    self.assertNotIn(str(PROJECT_ROOT), result.stderr)
                    self.assertNotIn(str(root), result.stderr)
                    self.assertFalse(marker.exists())

    def test_embedded_helper_diagnostic_accepts_only_arm64_frozen_self_check(self) -> None:
        script = PROJECT_ROOT / "packaging" / "verify_embedded_helper.sh"
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "build") as tmp:
            root = Path(tmp)
            app = root / "PhotosLocalKeywordIndexer.app"
            _, helper = self._create_embedded_helper_bundle(
                app / "Contents" / "Helpers" / "PhotosIndexerWorker.app",
                helper_script=
                '#!/bin/zsh\n'
                'if [[ "$1" == "--self-check" ]]; then\n'
                '  print -- \'{"status":"ok","runtime":"embedded","architecture":"arm64","protocol":"jsonl"}\'\n'
                '  exit 0\n'
                'fi\n'
                'exit 1\n',
            )
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_lipo = fake_bin / "lipo"
            fake_lipo.write_text(
                '#!/bin/zsh\n[[ "$1" == "-archs" ]] && print -- arm64\n',
                encoding="utf-8",
            )
            fake_lipo.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
            result = subprocess.run(
                ["zsh", str(script), str(app)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("embedded_helper:PASS:architecture", result.stdout)
            self.assertIn("embedded_helper:PASS:runtime", result.stdout)
            self.assertIn("embedded_helper:PASS:photos_tcc:NOT_CHECKED", result.stdout)
            self.assertIn("embedded_helper:PASS:automation_tcc:NOT_CHECKED", result.stdout)

    def test_embedded_helper_diagnostic_rejects_non_arm64_nested_code(self) -> None:
        script = PROJECT_ROOT / "packaging" / "verify_embedded_helper.sh"
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "build") as tmp:
            root = Path(tmp)
            app = root / "PhotosLocalKeywordIndexer.app"
            helper_app, _ = self._create_embedded_helper_bundle(
                app / "Contents" / "Helpers" / "PhotosIndexerWorker.app",
                helper_script=
                '#!/bin/zsh\nprint -- \'{"status":"ok","runtime":"embedded","architecture":"arm64","protocol":"jsonl"}\'\n',
            )
            (helper_app / "Contents" / "Frameworks" / "nested.dylib").write_bytes(b"nested code")
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_file = fake_bin / "file"
            fake_file.write_text(
                '#!/bin/zsh\n[[ "${@: -1}" == *"nested.dylib" ]] && print -- "Mach-O 64-bit dynamically linked shared library" || print -- "script text"\n',
                encoding="utf-8",
            )
            fake_file.chmod(0o755)
            fake_lipo = fake_bin / "lipo"
            fake_lipo.write_text(
                '#!/bin/zsh\n[[ "$2" == *"nested.dylib" ]] && print -- x86_64 || print -- arm64\n',
                encoding="utf-8",
            )
            fake_lipo.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{fake_bin}:{environment['PATH']}"

            result = subprocess.run(
                ["zsh", str(script), str(app)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn("embedded_helper:FAIL:nested_architecture", result.stderr)
        self.assertNotIn("nested.dylib", result.stderr)
        self.assertNotIn("embedded_helper:READY", result.stdout)

    def test_embedded_helper_diagnostic_fails_closed_when_file_cannot_inspect_payload(self) -> None:
        script = PROJECT_ROOT / "packaging" / "verify_embedded_helper.sh"
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "build") as tmp:
            root = Path(tmp)
            app = root / "PhotosLocalKeywordIndexer.app"
            helper_app, _ = self._create_embedded_helper_bundle(
                app / "Contents" / "Helpers" / "PhotosIndexerWorker.app",
                helper_script=
                '#!/bin/zsh\nprint -- \'{"status":"ok","runtime":"embedded","architecture":"arm64","protocol":"jsonl"}\'\n',
            )
            (helper_app / "Contents" / "Resources" / "uninspectable.bin").write_bytes(b"payload")
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_file = fake_bin / "file"
            fake_file.write_text(
                '#!/bin/zsh\n[[ "${@: -1}" == *"uninspectable.bin" ]] && exit 1\nprint -- "script text"\n',
                encoding="utf-8",
            )
            fake_file.chmod(0o755)
            fake_lipo = fake_bin / "lipo"
            fake_lipo.write_text('#!/bin/zsh\nprint -- arm64\n', encoding="utf-8")
            fake_lipo.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{fake_bin}:{environment['PATH']}"

            result = subprocess.run(
                ["zsh", str(script), str(app)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn("embedded_helper:FAIL:payload_architecture_scan_failed", result.stderr)
        self.assertNotIn("uninspectable.bin", result.stderr)
        self.assertNotIn("embedded_helper:READY", result.stdout)

    def test_embedded_helper_diagnostic_fails_closed_when_architecture_scan_is_incomplete(self) -> None:
        script = PROJECT_ROOT / "packaging" / "verify_embedded_helper.sh"
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "build") as tmp:
            root = Path(tmp)
            app = root / "PhotosLocalKeywordIndexer.app"
            _, helper = self._create_embedded_helper_bundle(
                app / "Contents" / "Helpers" / "PhotosIndexerWorker.app",
                helper_script=
                '#!/bin/zsh\nprint -- \'{"status":"ok","runtime":"embedded","architecture":"arm64","protocol":"jsonl"}\'\n',
            )
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_file = fake_bin / "file"
            fake_file.write_text('#!/bin/zsh\nprint -- "script text"\n', encoding="utf-8")
            fake_file.chmod(0o755)
            fake_lipo = fake_bin / "lipo"
            fake_lipo.write_text('#!/bin/zsh\nprint -- arm64\n', encoding="utf-8")
            fake_lipo.chmod(0o755)
            fake_find = fake_bin / "find"
            fake_find.write_text(
                '#!/bin/zsh\n'
                'if [[ "$*" == *"-type f"* ]]; then\n'
                '  print -- "$1/PhotosIndexerWorker"\n'
                '  exit 1\n'
                'fi\n'
                'exec /usr/bin/find "$@"\n',
                encoding="utf-8",
            )
            fake_find.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{fake_bin}:{environment['PATH']}"

            result = subprocess.run(
                ["zsh", str(script), str(app)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn("embedded_helper:FAIL:payload_architecture_scan_failed", result.stderr)
        self.assertNotIn("embedded_helper:READY", result.stdout)

    def test_embedded_helper_diagnostic_rejects_symlinked_bundle_ancestors(self) -> None:
        script = PROJECT_ROOT / "packaging" / "verify_embedded_helper.sh"
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "build") as tmp:
            root = Path(tmp)
            app = root / "PhotosLocalKeywordIndexer.app"
            contents = app / "Contents"
            contents.mkdir(parents=True)
            external_helpers = root / "external-helpers"
            self._create_embedded_helper_bundle(
                external_helpers / "PhotosIndexerWorker.app",
                helper_script="#!/bin/zsh\nexit 0\n",
            )
            (contents / "Helpers").symlink_to(external_helpers, target_is_directory=True)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_lipo = fake_bin / "lipo"
            fake_lipo.write_text(
                '#!/bin/zsh\n[[ "$1" == "-archs" ]] && print -- arm64\n',
                encoding="utf-8",
            )
            fake_lipo.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
            result = subprocess.run(
                ["zsh", str(script), str(app)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("embedded_helper:FAIL:bundle_path_invalid", result.stderr)
        self.assertNotIn("embedded_helper:READY", result.stdout)

    def test_embedded_helper_diagnostic_rejects_symlinked_payload_entries(self) -> None:
        script = PROJECT_ROOT / "packaging" / "verify_embedded_helper.sh"
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "build") as tmp:
            root = Path(tmp)
            app = root / "PhotosLocalKeywordIndexer.app"
            helper_app, _ = self._create_embedded_helper_bundle(
                app / "Contents" / "Helpers" / "PhotosIndexerWorker.app",
                helper_script=
                '#!/bin/zsh\n'
                '[[ "$1" == "--self-check" ]] || exit 1\n'
                'print -- \'{"status":"ok","runtime":"embedded","architecture":"arm64","protocol":"jsonl"}\'\n',
            )
            external = root / "external-payload"
            external.write_text("outside", encoding="utf-8")
            (helper_app / "Contents" / "Frameworks" / "libpython.dylib").symlink_to(external)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_lipo = fake_bin / "lipo"
            fake_lipo.write_text(
                '#!/bin/zsh\n[[ "$1" == "-archs" ]] && print -- arm64\n',
                encoding="utf-8",
            )
            fake_lipo.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
            result = subprocess.run(
                ["zsh", str(script), str(app)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("embedded_helper:FAIL:payload_symlink", result.stderr)
        self.assertNotIn("embedded_helper:READY", result.stdout)

    def test_embedded_helper_diagnostic_fails_closed_when_payload_scan_is_incomplete(self) -> None:
        script = PROJECT_ROOT / "packaging" / "verify_embedded_helper.sh"
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "build") as tmp:
            root = Path(tmp)
            app = root / "PhotosLocalKeywordIndexer.app"
            helper_app, _ = self._create_embedded_helper_bundle(
                app / "Contents" / "Helpers" / "PhotosIndexerWorker.app",
                helper_script="#!/bin/zsh\nexit 0\n",
            )
            unreadable = helper_app / "Contents" / "Resources" / "unreadable-payload"
            unreadable.mkdir()
            unreadable.chmod(0o000)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_lipo = fake_bin / "lipo"
            fake_lipo.write_text(
                '#!/bin/zsh\n[[ "$1" == "-archs" ]] && print -- arm64\n',
                encoding="utf-8",
            )
            fake_lipo.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
            try:
                result = subprocess.run(
                    ["zsh", str(script), str(app)],
                    check=False,
                    capture_output=True,
                    text=True,
                    env=environment,
                )
            finally:
                unreadable.chmod(0o700)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("embedded_helper:FAIL:payload_scan_failed", result.stderr)
        self.assertNotIn(str(root), result.stderr)
        self.assertNotIn("embedded_helper:READY", result.stdout)

    def test_build_swift_runs_the_embedded_helper_diagnostic_after_copy(self) -> None:
        script = (PROJECT_ROOT / "packaging" / "build_swift_app.sh").read_text(encoding="utf-8")
        self.assertIn('VERIFY_EMBEDDED_HELPER', script)
        self.assertIn('verify_embedded_helper.sh" "$app_bundle"', script)
        self.assertLess(
            script.index('ditto "$helper" "$app_bundle/Contents/Helpers/PhotosIndexerWorker.app"'),
            script.index('verify_embedded_helper.sh" "$app_bundle"'),
        )

    def test_build_swift_embeds_the_declared_app_icon(self) -> None:
        script = (PROJECT_ROOT / "packaging" / "build_swift_app.sh").read_text(encoding="utf-8")
        self.assertIn('build_app_icon.sh', script)
        self.assertIn('CFBundleIconFile', script)
        self.assertIn('Contents/Resources/AppIcon.icns', script)

    def test_notarize_redacts_dmg_signature_verification_errors(self) -> None:
        script = (PROJECT_ROOT / "packaging" / "notarize.sh").read_text(encoding="utf-8")

        self.assertIn('if ! codesign --verify --verbose=2 --strict "$dmg_path" >/dev/null 2>&1; then', script)
        self.assertIn("notarize:FAIL:dmg_signature_invalid", script)
        self.assertIn("notarize:HINT:dmg_signature_invalid:rebuild_and_sign_the_dmg_then_retry", script)
        self.assertNotIn('codesign --verify --verbose=2 --strict "$dmg_path"\n', script)

    def test_notarize_missing_profile_has_a_stable_actionable_diagnostic(self) -> None:
        script = PROJECT_ROOT / "packaging" / "notarize.sh"
        (PROJECT_ROOT / "dist").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "dist") as tmp:
            dmg = Path(tmp) / "PhotosLocalKeywordIndexer-1.2.3-45.dmg"
            dmg.write_bytes(b"notarization fixture")
            environment = os.environ.copy()
            environment.pop("APPLE_NOTARY_PROFILE", None)

            result = subprocess.run(
                ["/bin/zsh", str(script), str(dmg)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(
            result.stderr,
            "notarize:FAIL:notary_profile_missing\n"
            "notarize:HINT:notary_profile_missing:configure_with_xcrun_notarytool_store_credentials_then_retry\n",
        )
        self.assertNotIn(str(dmg), result.stderr)

    def test_sign_app_missing_identity_has_a_stable_actionable_diagnostic(self) -> None:
        script = PROJECT_ROOT / "packaging" / "sign_app.sh"
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "build") as tmp:
            app = Path(tmp) / "PhotosLocalKeywordIndexer.app"
            app.mkdir()
            environment = os.environ.copy()
            environment.pop("DEVELOPER_ID_APPLICATION", None)

            result = subprocess.run(
                ["/bin/zsh", str(script), str(app)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(
            result.stderr,
            "sign_app:FAIL:developer_id_identity_missing\n"
            "sign_app:HINT:developer_id_identity_missing:install_or_select_a_developer_id_application_identity\n",
        )
        self.assertNotIn(str(app), result.stderr)

    def test_notarize_redacts_spctl_verification_errors(self) -> None:
        script = (PROJECT_ROOT / "packaging" / "notarize.sh").read_text(encoding="utf-8")

        self.assertIn(
            'if ! spctl --assess --type execute --verbose=4 "$mounted_app" >/dev/null 2>&1; then',
            script,
        )
        self.assertIn("notarize:FAIL:mounted_app_assessment_invalid", script)
        self.assertIn(
            'if ! spctl --assess --type open --context context:primary-signature --verbose=4 "$dmg_path" >/dev/null 2>&1; then',
            script,
        )
        self.assertIn("notarize:FAIL:dmg_assessment_invalid", script)

    def test_notarize_redacts_notary_submission_errors(self) -> None:
        script = (PROJECT_ROOT / "packaging" / "notarize.sh").read_text(encoding="utf-8")

        self.assertIn(
            'if ! (\n'
            '  # The result file lives under a freshly-created private directory.  Keep the\n'
            '  # redirection create-only nonetheless: if a concurrent process swaps the\n'
            '  # pathname for a symlink before this command starts, zsh must fail closed\n'
            '  # instead of following it to an unrelated file.\n'
            '  set -o noclobber\n'
            '  xcrun notarytool submit "$dmg_path" --keychain-profile "$notary_profile" --wait \\\n'
            '    --output-format json > "$notary_result" 2>/dev/null\n'
            '); then',
            script,
        )
        self.assertIn("notarize:FAIL:notary_submission_failed", script)
        self.assertIn(
            "notarize:HINT:notary_submission_failed:check_notary_profile_and_retry",
            script,
        )

    def test_notarize_opens_notary_result_create_only(self) -> None:
        """A raced result path must not be followed by shell redirection."""
        script = (PROJECT_ROOT / "packaging" / "notarize.sh").read_text(encoding="utf-8")

        submission = 'xcrun notarytool submit "$dmg_path" --keychain-profile "$notary_profile" --wait'
        self.assertIn(submission, script)
        start = script.index('notary_tmp="$(mktemp -d')
        end = script.index('print -u2 -- "notarize:FAIL:notary_submission_failed"', start)
        submission_block = script[start:end]
        self.assertIn("if ! (\n", submission_block)
        self.assertLess(submission_block.index("set -o noclobber"), submission_block.index(submission))
        self.assertIn('> "$notary_result" 2>/dev/null', submission_block)

    def test_notarize_redacts_stapler_errors_before_writing_evidence(self) -> None:
        script = (PROJECT_ROOT / "packaging" / "notarize.sh").read_text(encoding="utf-8")

        self.assertIn(
            'if ! xcrun stapler staple "$dmg_path" >/dev/null 2>&1; then',
            script,
        )
        self.assertIn("notarize:FAIL:staple_failed", script)
        self.assertIn(
            'if ! xcrun stapler validate "$dmg_path" >/dev/null 2>&1; then',
            script,
        )
        self.assertIn("notarize:FAIL:staple_validation_failed", script)

    def test_notarize_sanitizes_malformed_notary_results(self) -> None:
        script = (PROJECT_ROOT / "packaging" / "notarize.sh").read_text(encoding="utf-8")

        self.assertIn(
            'if ! /usr/bin/python3 - "$notary_result" 2>/dev/null <<\'PY\'',
            script,
        )
        self.assertIn("except (OSError, ValueError):", script)
        self.assertIn("notarize:FAIL:notary_result_invalid", script)

    def test_notarize_requires_owned_result_directory_before_submission(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "notarize.sh").read_text(encoding="utf-8")
        guard = (
            "if ! notary_tmp_is_owned; then\n"
            '  print -u2 -- "notarize:FAIL:notary_temp_invalid"\n'
            '  print -u2 -- "notarize:HINT:notary_temp_invalid:recreate_the_private_notary_temp_then_retry"\n'
            "  exit 1\n"
            "fi\n"
        )

        record = source.index("record_notary_tmp_identity\n")
        identity_guard = source.index(guard, record)
        submission = source.index("xcrun notarytool submit", record)
        self.assertLess(record, identity_guard)
        self.assertLess(identity_guard, submission)

    def test_notarize_rechecks_result_directory_after_submission_before_reading(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "notarize.sh").read_text(encoding="utf-8")
        guard = (
            "if ! notary_tmp_is_owned; then\n"
            '  print -u2 -- "notarize:FAIL:notary_temp_invalid"\n'
            '  print -u2 -- "notarize:HINT:notary_temp_invalid:recreate_the_private_notary_temp_then_retry"\n'
            "  exit 1\n"
            "fi\n"
        )

        submission = source.index("xcrun notarytool submit")
        result_guard = source.index(guard, submission)
        result_read = source.index('/usr/bin/python3 - "$notary_result"', submission)
        self.assertLess(submission, result_guard)
        self.assertLess(result_guard, result_read)

    def test_notarize_requires_an_accepted_submission_id_before_stapling(self) -> None:
        """Never mutate a DMG when its accepted receipt cannot be published."""
        script = (PROJECT_ROOT / "packaging" / "notarize.sh").read_text(encoding="utf-8")

        receipt_validation = 'submission_id = result.get("id")'
        self.assertIn(receipt_validation, script)
        self.assertIn("re.fullmatch", script)
        self.assertLess(script.index(receipt_validation), script.index('xcrun stapler staple "$dmg_path"'))

    def test_notarize_sanitizes_mount_lifecycle_errors(self) -> None:
        script = (PROJECT_ROOT / "packaging" / "notarize.sh").read_text(encoding="utf-8")

        self.assertIn(
            'if ! hdiutil attach -readonly -nobrowse -mountpoint "$mount_dir" "$dmg_path" >/dev/null 2>&1; then',
            script,
        )
        self.assertIn("notarize:FAIL:dmg_mount_failed", script)
        self.assertIn(
            'if ! hdiutil detach "$mount_dir" >/dev/null 2>&1; then',
            script,
        )
        self.assertIn("notarize:FAIL:dmg_unmount_failed", script)

    def test_notarize_finishes_preflight_mount_cleanup_before_submitting(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "notarize.sh").read_text(encoding="utf-8")

        first_detach = source.index('if ! hdiutil detach "$mount_dir"')
        cleanup_failure = source.index("notarize:FAIL:dmg_mount_cleanup_failed", first_detach)
        cleanup_hint = source.index(
            "notarize:HINT:dmg_mount_cleanup_failed:remove_the_private_mount_directory_then_retry",
            cleanup_failure,
        )
        clear_identity = source.index('mount_dir_created_identity=""', first_detach)
        submission = source.index("xcrun notarytool submit")

        self.assertLess(first_detach, cleanup_failure)
        self.assertLess(cleanup_failure, cleanup_hint)
        self.assertLess(cleanup_hint, clear_identity)
        self.assertLess(clear_identity, submission)

    def test_notarize_cleanup_only_removes_the_mount_directory_it_created(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "notarize.sh").read_text(encoding="utf-8")

        self.assertIn('mount_dir_created_identity=""', source)
        self.assertIn("record_mount_dir_identity()", source)
        self.assertIn("mount_dir_is_owned()", source)
        self.assertIn("stat -f '%d:%i' -- \"$mount_dir\"", source)
        self.assertEqual(source.count("record_mount_dir_identity\n"), 2)
        self.assertIn(
            'if mount_dir_is_owned; then\n'
            '    rmdir "$mount_dir" >/dev/null 2>&1 || cleanup_failed=1\n'
            "  fi",
            source,
        )
        first_mount = source.index('mount_dir="$(mktemp -d')
        first_record = source.index("record_mount_dir_identity\n", first_mount)
        first_attach = source.index("hdiutil attach", first_mount)
        second_mount = source.index('mount_dir="$(mktemp -d', first_mount + 1)
        second_record = source.index("record_mount_dir_identity\n", second_mount)
        second_attach = source.index("hdiutil attach", second_mount)
        self.assertLess(first_record, first_attach)
        self.assertLess(second_record, second_attach)

    def test_notarize_rechecks_mount_identity_before_each_explicit_detach(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "notarize.sh").read_text(encoding="utf-8")
        guard = (
            "if ! mount_dir_is_owned; then\n"
            '  print -u2 -- "notarize:FAIL:dmg_mount_changed"\n'
            '  print -u2 -- "notarize:HINT:dmg_mount_changed:detach_the_original_mount_manually_then_retry"\n'
            "  exit 1\n"
            "fi\n"
        )

        self.assertEqual(source.count(guard), 2)
        first_guard = source.index(guard)
        first_detach = source.index('if ! hdiutil detach "$mount_dir"', first_guard)
        second_guard = source.index(guard, first_guard + len(guard))
        second_detach = source.index('if ! hdiutil detach "$mount_dir"', second_guard)
        self.assertLess(first_guard, first_detach)
        self.assertLess(second_guard, second_detach)

    def test_notarize_requires_owned_mount_directory_before_each_attach(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "notarize.sh").read_text(encoding="utf-8")
        guard = (
            "if ! mount_dir_is_owned; then\n"
            '  print -u2 -- "notarize:FAIL:dmg_mount_directory_invalid"\n'
            '  print -u2 -- "notarize:HINT:dmg_mount_directory_invalid:recreate_the_private_mount_directory_then_retry"\n'
            "  exit 1\n"
            "fi\n"
        )

        self.assertEqual(source.count(guard), 2)
        first_record = source.index("record_mount_dir_identity\n")
        first_guard = source.index(guard, first_record)
        first_attach = source.index("if ! hdiutil attach", first_record)
        second_record = source.index("record_mount_dir_identity\n", first_record + 1)
        second_guard = source.index(guard, second_record)
        second_attach = source.index("if ! hdiutil attach", second_record)
        self.assertLess(first_record, first_guard)
        self.assertLess(first_guard, first_attach)
        self.assertLess(second_record, second_guard)
        self.assertLess(second_guard, second_attach)

    def test_notarize_removes_evidence_created_by_a_run_when_detach_fails(self) -> None:
        script = (PROJECT_ROOT / "packaging" / "notarize.sh").read_text(encoding="utf-8")

        self.assertIn("evidence_written=0", script)
        self.assertIn("evidence_written=1", script)
        cleanup = (
            'if (( evidence_written == 1 )) && evidence_is_owned; then\n'
            '    rm -f -- "$evidence_path" >/dev/null 2>&1 || cleanup_failed=1\n'
            '  fi'
        )
        self.assertIn(cleanup, script)
        assignment_index = script.index("evidence_written=1")
        cleanup_index = script.index(cleanup, assignment_index)
        self.assertLess(assignment_index, cleanup_index)
        self.assertLess(cleanup_index, script.rindex("notarize:FAIL:dmg_unmount_failed"))

    def test_notarize_cleans_evidence_on_interrupt_before_success(self) -> None:
        script = (PROJECT_ROOT / "packaging" / "notarize.sh").read_text(encoding="utf-8")

        self.assertIn("notarize_completed=0", script)
        self.assertIn("notarize_completed=1", script)
        cleanup = (
            'if (( evidence_written == 1 && notarize_completed == 0 )) && evidence_is_owned; then\n'
            '    rm -f -- "$evidence_path" >/dev/null 2>&1 || cleanup_failed=1\n'
            '  fi'
        )
        self.assertIn(cleanup, script)
        self.assertLess(script.index(cleanup), script.index("notarize_completed=1"))

    def test_notarize_marks_distribution_ready_only_after_evidence_and_detach(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "notarize.sh").read_text(encoding="utf-8")
        marker = 'print -- "notarize:READY_FOR_DISTRIBUTION"'

        self.assertIn(marker, source)
        self.assertLess(source.index('"$script_root/write_release_evidence.sh"'), source.index(marker))
        self.assertLess(source.rindex('hdiutil detach "$mount_dir"'), source.index(marker))
        self.assertLess(source.index("notarize_completed=1"), source.index(marker))

    def test_notarize_rechecks_the_stapled_dmg_before_distribution_ready(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "notarize.sh").read_text(encoding="utf-8")
        staple_validation = 'xcrun stapler validate "$dmg_path"'
        final_fingerprint = "if ! record_finalized_dmg_fingerprint; then"
        final_recheck = "if ! dmg_matches_finalized_fingerprint; then"
        evidence = '"$script_root/write_release_evidence.sh"'
        marker = 'print -- "notarize:READY_FOR_DISTRIBUTION"'

        self.assertIn(final_fingerprint, source)
        self.assertIn(final_recheck, source)
        self.assertIn("notarize:FAIL:dmg_changed_before_distribution", source)
        self.assertLess(source.index(staple_validation), source.index(final_fingerprint))
        self.assertLess(source.index(final_fingerprint), source.index(evidence))
        self.assertLess(source.index(evidence), source.index(final_recheck))
        self.assertLess(source.index(final_recheck), source.index(marker))

    def test_notarize_rechecks_release_evidence_before_distribution_ready(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "notarize.sh").read_text(encoding="utf-8")
        evidence = '"$script_root/write_release_evidence.sh"'
        record = "if ! record_release_evidence_fingerprint; then"
        recheck = "if ! evidence_matches_release_fingerprint; then"
        detach = 'hdiutil detach "$mount_dir"'
        marker = 'print -- "notarize:READY_FOR_DISTRIBUTION"'

        self.assertIn("notarize:FAIL:release_evidence_changed_before_distribution", source)
        self.assertLess(source.index(evidence), source.index(record))
        self.assertLess(source.index(record), source.rindex(detach))
        self.assertLess(source.rindex(detach), source.index(recheck))
        self.assertLess(source.index(recheck), source.index(marker))

    def test_notarize_does_not_report_recorded_evidence_before_final_rechecks(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "notarize.sh").read_text(encoding="utf-8")
        evidence_call_end = '"$notary_result" "$evidence_path" >/dev/null'
        final_recheck = "if ! evidence_matches_release_fingerprint; then"
        recorded = 'print -- "release_evidence:RECORDED"'
        ready = 'print -- "notarize:READY_FOR_DISTRIBUTION"'

        self.assertIn(evidence_call_end, source)
        self.assertIn(recorded, source)
        self.assertLess(source.index(final_recheck), source.index(recorded))
        self.assertLess(source.index(recorded), source.index(ready))

    def test_notarize_marks_completion_only_after_final_rechecks(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "notarize.sh").read_text(encoding="utf-8")
        dmg_recheck = "if ! dmg_matches_finalized_fingerprint; then"
        evidence_recheck = "if ! evidence_matches_release_fingerprint; then"
        completed = "notarize_completed=1"
        recorded = 'print -- "release_evidence:RECORDED"'

        self.assertLess(source.index(dmg_recheck), source.index(completed))
        self.assertLess(source.index(evidence_recheck), source.index(completed))
        self.assertLess(source.index(completed), source.index(recorded))

    def test_notarize_fingerprints_are_bound_to_stable_file_descriptors(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "notarize.sh").read_text(encoding="utf-8")
        descriptor_open = "descriptor = os.open(sys.argv[1], os.O_RDONLY | os.O_NOFOLLOW)"
        descriptor_stat = "os.fstat(descriptor)"
        path_stat = "path_metadata = os.stat(sys.argv[1], follow_symlinks=False)"

        self.assertEqual(source.count(descriptor_open), 2)
        self.assertEqual(source.count(descriptor_stat), 4)
        self.assertEqual(source.count(path_stat), 2)
        self.assertEqual(
            source.count("stable_metadata(metadata_before) != stable_metadata(metadata_after)"),
            2,
        )
        self.assertEqual(
            source.count("stable_metadata(metadata_after) != stable_metadata(path_metadata)"),
            2,
        )

    def test_notarize_finishes_cleanup_before_terminal_markers(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "notarize.sh").read_text(encoding="utf-8")
        completed = "notarize_completed=1"
        cleanup = "\ncleanup\n"
        cleanup_guard = "if (( cleanup_failed == 1 )); then"
        disable_trap = "trap - EXIT"
        recorded = 'print -- "release_evidence:RECORDED"'

        self.assertIn("notarize:FAIL:final_cleanup_failed", source)
        self.assertLess(source.index(completed), source.index(cleanup))
        self.assertLess(source.index(cleanup), source.index(cleanup_guard))
        self.assertLess(source.index(cleanup_guard), source.index(disable_trap))
        self.assertLess(source.index(disable_trap), source.index(recorded))

    def test_build_dmg_finishes_cleanup_before_terminal_markers(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "build_dmg.sh").read_text(encoding="utf-8")
        preserve = "preserve_dmg=1"
        cleanup = "\ncleanup_build_artifacts\n"
        cleanup_guard = "if (( cleanup_failed == 1 )); then"
        disable_traps = "trap - EXIT INT TERM"
        ready = 'print -- "release_dmg:READY_FOR_NOTARIZATION"'

        self.assertIn("build_dmg:FAIL:final_cleanup_failed", source)
        self.assertLess(source.index(preserve), source.index(cleanup))
        self.assertLess(source.index(cleanup), source.index(cleanup_guard))
        self.assertLess(source.index(cleanup_guard), source.index(disable_traps))
        self.assertLess(source.index(disable_traps), source.index(ready))

    def test_sign_app_marker_is_scoped_to_the_dmg_stage(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "sign_app.sh").read_text(encoding="utf-8")

        self.assertIn('print -- "release_signature:READY_FOR_DMG"', source)
        self.assertNotIn('print -- "release_signature:READY"', source)

    def test_helper_marker_is_scoped_to_the_app_bundle_stage(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "build_python_helper.sh").read_text(encoding="utf-8")

        self.assertIn('print -- "release_helper:READY_FOR_APP_BUNDLE"', source)
        self.assertNotIn('print -- "release_helper:READY"', source)

    def test_python_helper_finishes_cleanup_before_ready_for_app_bundle(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "build_python_helper.sh").read_text(encoding="utf-8")
        cleanup = "\n  cleanup_helper_check\n"
        cleanup_guard = 'if [[ -e "$isolation_root" || -L "$isolation_root" ]]; then'
        disable_traps = "trap - EXIT INT TERM"
        ready = 'print -- "release_helper:READY_FOR_APP_BUNDLE"'

        self.assertIn("build_python_helper:FAIL:final_cleanup_failed", source)
        self.assertLess(source.index(cleanup), source.index(cleanup_guard))
        self.assertLess(source.index(cleanup_guard), source.index(disable_traps, source.index(cleanup_guard)))
        self.assertLess(source.index(disable_traps, source.index(cleanup_guard)), source.index(ready))

    def test_python_helper_clears_signal_cleanup_before_every_ready_path(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "build_python_helper.sh").read_text(encoding="utf-8")
        unverified_branch = "else\n  trap - EXIT INT TERM\nfi"
        ready = 'print -- "release_helper:READY_FOR_APP_BUNDLE"'

        self.assertIn(unverified_branch, source)
        self.assertLess(source.index(unverified_branch), source.index(ready))

    def test_embedded_helper_finishes_cleanup_before_ready(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "verify_embedded_helper.sh").read_text(encoding="utf-8")
        cleanup = "\ncleanup_diagnostic\n"
        cleanup_guard = "if (( cleanup_failed == 1 )) || [[ -e \"$diagnostic_root\" || -L \"$diagnostic_root\" ]]; then"
        disable_traps = "trap - EXIT INT TERM"
        ready = 'print -- "embedded_helper:READY"'

        self.assertIn("embedded_helper:FAIL:final_cleanup_failed", source)
        self.assertLess(source.index(cleanup), source.index(cleanup_guard))
        self.assertLess(source.index(cleanup_guard), source.index(disable_traps))
        self.assertLess(source.index(disable_traps), source.index(ready))

    def test_dmg_layout_finishes_unmount_cleanup_before_ready(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "verify_dmg_layout.sh").read_text(encoding="utf-8")
        cleanup = "\ncleanup\n"
        cleanup_guard = "if (( cleanup_failed == 1 || mounted == 1 )) || [[ -e \"$mount_point\" || -L \"$mount_point\" ]]; then"
        disable_traps = "trap - EXIT INT TERM"
        ready_branch = "if (( json_mode == 1 )); then"

        self.assertIn("dmg_layout:FAIL:mount_cleanup_failed", source)
        self.assertLess(source.index(cleanup), source.index(cleanup_guard))
        self.assertLess(source.index(cleanup_guard), source.index(disable_traps))
        self.assertLess(source.index(disable_traps), source.index(ready_branch, source.index(disable_traps)))

    def test_release_preflight_finishes_helper_cleanup_before_ready(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "release_preflight.sh").read_text(encoding="utf-8")
        cleanup = "\ncleanup_helper_check\n"
        cleanup_guard = "if (( cleanup_failed == 1 )) || { [[ -n \"$helper_check_root\" ]] && [[ -e \"$helper_check_root\" || -L \"$helper_check_root\" ]]; }; then"
        cleanup_failure = (
            f"{cleanup_guard}\n"
            "  report FAIL helper_check_cleanup_failed\n"
            "else\n"
            "  trap - EXIT INT TERM\n"
            "fi"
        )
        readiness = "if (( failed == 0 )); then"

        self.assertIn(cleanup_failure, source)
        self.assertLess(source.index(cleanup), source.index(cleanup_guard))
        self.assertLess(source.index(cleanup_guard), source.index(readiness))

    def test_notarize_cleanup_does_not_remove_a_replaced_evidence_file(self) -> None:
        script = (PROJECT_ROOT / "packaging" / "notarize.sh").read_text(encoding="utf-8")

        self.assertIn('evidence_created_identity=""', script)
        self.assertIn("record_evidence_identity()", script)
        self.assertIn("evidence_is_owned()", script)
        self.assertIn("stat -f '%d:%i' -- \"$evidence_path\"", script)
        cleanup = (
            'if (( evidence_written == 1 && notarize_completed == 0 )) && evidence_is_owned; then\n'
            '    rm -f -- "$evidence_path" >/dev/null 2>&1 || cleanup_failed=1\n'
            '  fi'
        )
        self.assertIn(cleanup, script)
        self.assertLess(script.index("record_evidence_identity()"), script.index("notarize_completed=1"))

    def test_notarize_cleanup_does_not_remove_a_replaced_lock(self) -> None:
        script = (PROJECT_ROOT / "packaging" / "notarize.sh").read_text(encoding="utf-8")

        self.assertIn('notarize_lock_created_identity=""', script)
        self.assertIn("record_notarize_lock_identity()", script)
        self.assertIn("notarize_lock_is_owned()", script)
        self.assertIn('stat -f \'%d:%i\' -- "$notarize_lock"', script)
        cleanup = (
            'if (( notarize_lock_acquired == 1 )) && notarize_lock_is_owned; then\n'
            '    if [[ -n "$notarize_lock_owner" && -f "$notarize_lock_owner" && ! -L "$notarize_lock_owner" ]]; then'
        )
        self.assertIn(cleanup, script)
        self.assertLess(script.index("record_notarize_lock_identity()"), script.index("notarize_completed=1"))

    def test_notarize_cleanup_does_not_remove_a_replaced_private_temp_directory(self) -> None:
        """Recursive cleanup must be limited to the temp directory this run created."""
        script = (PROJECT_ROOT / "packaging" / "notarize.sh").read_text(encoding="utf-8")

        self.assertIn('notary_tmp_created_identity=""', script)
        self.assertIn("record_notary_tmp_identity()", script)
        self.assertIn("notary_tmp_is_owned()", script)
        self.assertIn('stat -f \'%d:%i\' -- "$notary_tmp"', script)
        cleanup = (
            'if notary_tmp_is_owned; then\n'
            '    rm -rf -- "$notary_tmp" >/dev/null 2>&1 || cleanup_failed=1\n'
            '  fi'
        )
        self.assertIn(cleanup, script)
        self.assertLess(script.index("record_notary_tmp_identity()"), script.index("notarize_completed=1"))

    def test_build_swift_checks_icon_tool_before_compiling(self) -> None:
        script = (PROJECT_ROOT / "packaging" / "build_swift_app.sh").read_text(encoding="utf-8")
        self.assertIn('command -v /usr/bin/sips', script)
        self.assertIn('sips is required before compiling the app.', script)
        self.assertIn('command -v /usr/bin/python3', script)
        self.assertIn('Python 3 is required before compiling the app.', script)
        self.assertLess(script.index('command -v /usr/bin/sips'), script.index('"$swift_bin" build --package-path'))
        self.assertLess(script.index('command -v /usr/bin/python3'), script.index('"$swift_bin" build --package-path'))

    def test_build_swift_checks_release_plistbuddy_before_compiling(self) -> None:
        script = (PROJECT_ROOT / "packaging" / "build_swift_app.sh").read_text(encoding="utf-8")
        self.assertIn('if [[ "$release_build" == "1" && ! -x /usr/libexec/PlistBuddy ]]; then', script)
        self.assertIn("build_swift_app:FAIL:plistbuddy_missing", script)
        self.assertLess(
            script.index('if [[ "$release_build" == "1" && ! -x /usr/libexec/PlistBuddy ]]; then'),
            script.index('"$swift_bin" build --package-path'),
        )

    def test_build_swift_outside_root_error_does_not_echo_checkout_path(self) -> None:
        script = PROJECT_ROOT / "packaging" / "build_swift_app.sh"
        environment = os.environ.copy()
        environment["BUILD_ROOT"] = "/private/tmp/photos-indexer-outside-build"

        result = subprocess.run(
            ["/bin/zsh", str(script)],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "build_swift_app:FAIL:build_root_invalid\n")
        self.assertNotIn(str(PROJECT_ROOT), result.stderr)

    def test_build_swift_rejects_a_shared_writable_build_root_before_mutation(self) -> None:
        script = PROJECT_ROOT / "packaging" / "build_swift_app.sh"
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "build") as tmp:
            build_root = Path(tmp)
            app = build_root / "PhotosLocalKeywordIndexer.app"
            stale = app / "stale-marker"
            app.mkdir()
            stale.write_text("preserve", encoding="utf-8")
            build_root.chmod(0o777)
            environment = os.environ.copy()
            environment["BUILD_ROOT"] = str(build_root)

            try:
                result = subprocess.run(
                    ["/bin/zsh", str(script)],
                    check=False,
                    capture_output=True,
                    text=True,
                    env=environment,
                )
            finally:
                build_root.chmod(0o700)

            self.assertEqual(result.returncode, 2)
            self.assertIn("build_swift_app:FAIL:build_root_permissions", result.stderr)
            self.assertTrue(stale.exists(), "unsafe build roots must fail before stale-app cleanup")

    def test_python_helper_architecture_error_does_not_echo_interpreter_path(self) -> None:
        script = (PROJECT_ROOT / "packaging" / "build_python_helper.sh").read_text(encoding="utf-8")
        self.assertNotIn("Expected arm64 Python 3.12 at $python_bin", script)
        self.assertIn("Expected arm64 Python 3.12; found $python_architecture.", script)

    def test_python_helper_dependency_error_does_not_echo_interpreter_path(self) -> None:
        script = (PROJECT_ROOT / "packaging" / "build_python_helper.sh").read_text(encoding="utf-8")
        self.assertNotIn("Install build dependencies first: $python_bin", script)
        self.assertIn("Install build dependencies first with Python 3.12", script)

    def test_python_helper_checks_pinned_runtime_provenance_before_pyinstaller(self) -> None:
        script = (PROJECT_ROOT / "packaging" / "build_python_helper.sh").read_text(encoding="utf-8")
        pyinstaller = '"$python_bin" -m PyInstaller'

        self.assertIn("from importlib.metadata import version", script)
        self.assertIn('"photoscript": "0.5.3"', script)
        self.assertIn('"pyobjc-framework-Photos": "12.2.2"', script)
        self.assertIn("build_python_helper:FAIL:python_runtime_provenance_invalid", script)
        self.assertLess(script.index("python_runtime_provenance_invalid"), script.index(pyinstaller))

    def test_python_helper_preflights_the_project_photoscript_compatibility_loader(self) -> None:
        script = (PROJECT_ROOT / "packaging" / "build_python_helper.sh").read_text(encoding="utf-8")

        self.assertIn("PhotoScriptBridge.preflight_compatibility()", script)
        self.assertNotIn("import photoscript, AppKit", script)

    def test_python_helper_does_not_pass_makespec_options_with_the_checked_in_spec(self) -> None:
        script = (PROJECT_ROOT / "packaging" / "build_python_helper.sh").read_text(encoding="utf-8")
        invocation = script[
            script.index('"$python_bin" -m PyInstaller') : script.index('\n\nhelper=')
        ]

        self.assertIn('"$project_root/packaging/PhotosIndexerWorker.spec"', invocation)
        self.assertNotIn("--specpath", invocation)

    def test_helper_spec_resolves_the_project_from_its_packaging_directory(self) -> None:
        spec = (PROJECT_ROOT / "packaging" / "PhotosIndexerWorker.spec").read_text(encoding="utf-8")

        self.assertIn("project_root = Path(SPECPATH).resolve().parent\n", spec)
        self.assertNotIn("Path(SPECPATH).resolve().parent.parent", spec)

    def test_helper_spec_bundles_photoscript_apple_script_data(self) -> None:
        spec = (PROJECT_ROOT / "packaging" / "PhotosIndexerWorker.spec").read_text(encoding="utf-8")

        self.assertIn("collect_data_files", spec)
        self.assertIn('datas = collect_data_files("photoscript")', spec)
        self.assertIn("datas=datas,", spec)

    def test_helper_spec_does_not_import_photoscript_to_discover_submodules(self) -> None:
        spec = (PROJECT_ROOT / "packaging" / "PhotosIndexerWorker.spec").read_text(encoding="utf-8")

        self.assertNotIn('collect_submodules("photoscript")', spec)
        self.assertIn('"photoscript.script_loader"', spec)

    def test_icon_generator_does_not_leave_a_partial_output_after_sips_failure(self) -> None:
        script = (PROJECT_ROOT / "packaging" / "build_app_icon.sh").read_text(encoding="utf-8")

        self.assertIn('icon_output_tmp="$icon_tmp_root/AppIcon.icns"', script)
        self.assertIn('--out "$icon_output_tmp"', script)
        self.assertIn('mv "$icon_output_tmp" "$output_path"', script)
        self.assertIn('rm -f -- "$icon_output_tmp"', script)

    def test_icon_generator_rejects_invalid_output_paths_without_leaking_them(self) -> None:
        script = PROJECT_ROOT / "packaging" / "build_app_icon.sh"
        build_root = PROJECT_ROOT / "build"
        build_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build_root) as tmp:
            root = Path(tmp)
            candidates = ("AppIcon.icns", str(root / "AppIcon.png"))

            for candidate in candidates:
                with self.subTest(candidate=candidate):
                    result = subprocess.run(
                        ["/bin/zsh", str(script), candidate],
                        check=False,
                        capture_output=True,
                        text=True,
                        cwd=root,
                    )

                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(
                        result.stderr,
                        "build_app_icon:FAIL:output_path_invalid\n"
                        "build_app_icon:HINT:output_path_invalid:provide_a_new_absolute_icns_path_under_build\n",
                    )
                    self.assertNotIn(str(PROJECT_ROOT), result.stderr)
                    self.assertNotIn(str(root), result.stderr)
                    self.assertEqual(list(root.iterdir()), [])

    def test_icon_generator_places_publish_temp_next_to_destination_volume(self) -> None:
        script = (PROJECT_ROOT / "packaging" / "build_app_icon.sh").read_text(encoding="utf-8")

        self.assertIn('mktemp -d "${output_path:h}/.photos-indexer-icon.XXXXXX"', script)
        self.assertNotIn('mktemp -d "${TMPDIR:-/tmp}/photos-indexer-icon.', script)

    def test_icon_generator_guards_destination_path_before_creating_temp(self) -> None:
        script = (PROJECT_ROOT / "packaging" / "build_app_icon.sh").read_text(encoding="utf-8")

        guard = 'validate_build_path "$output_path" "$project_root/build" "icon output"'
        self.assertIn('source "$project_root/packaging/build_path_guard.zsh"', script)
        self.assertIn(guard, script)
        self.assertLess(script.index(guard), script.index('mktemp -d "${output_path:h}/.photos-indexer-icon.XXXXXX"'))

    def test_icon_generator_cleanup_tracks_temp_root_identity(self) -> None:
        script = (PROJECT_ROOT / "packaging" / "build_app_icon.sh").read_text(encoding="utf-8")

        self.assertIn('icon_tmp_root_created_identity=""', script)
        self.assertIn("record_icon_tmp_root_identity()", script)
        self.assertIn("icon_tmp_root_is_owned()", script)
        self.assertIn("icon_tmp_root_is_owned", script[script.index("cleanup()") :])

    def test_icon_generator_rechecks_temp_root_before_generation_and_publish(self) -> None:
        script = (PROJECT_ROOT / "packaging" / "build_app_icon.sh").read_text(encoding="utf-8")
        guard = (
            "if ! icon_tmp_root_is_owned; then\n"
            '  print -u2 -- "build_app_icon:FAIL:temporary_output_invalid"\n'
            "  exit 1\n"
            "fi\n"
        )

        record = script.index("record_icon_tmp_root_identity\n")
        generation_guard = script.index(guard, record)
        generation = script.index('mkdir "$iconset_dir"', record)
        publish_guard = script.index(guard, generation_guard + len(guard))
        publish = script.index('mv "$icon_output_tmp" "$output_path"', generation)
        self.assertLess(record, generation_guard)
        self.assertLess(generation_guard, generation)
        self.assertLess(generation, publish_guard)
        self.assertLess(publish_guard, publish)

    def test_icon_generator_cleanup_does_not_unlink_through_a_replaced_temp_root(self) -> None:
        script = (PROJECT_ROOT / "packaging" / "build_app_icon.sh").read_text(encoding="utf-8")

        guarded_cleanup = (
            "cleanup() {\n"
            "  if icon_tmp_root_is_owned; then\n"
            '    if [[ -n "${icon_output_tmp:-}" && -f "$icon_output_tmp" '
            '&& ! -L "$icon_output_tmp" ]]; then\n'
            '      rm -f -- "$icon_output_tmp"\n'
            "    fi\n"
            '    rm -rf -- "$icon_tmp_root"\n'
            "  fi\n"
            "}"
        )
        self.assertIn(guarded_cleanup, script)

    def test_icon_generator_cleans_temp_root_when_iconset_creation_fails(self) -> None:
        script = PROJECT_ROOT / "packaging" / "build_app_icon.sh"
        build_dir = PROJECT_ROOT / "build"
        build_dir.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build_dir) as tmp:
            root = Path(tmp)
            output = root / "AppIcon.icns"
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_mkdir = fake_bin / "mkdir"
            fake_mkdir.write_text(
                "#!/bin/zsh\n"
                "if [[ \"$1\" != \"-p\" ]]; then exit 1; fi\n"
                "exec /bin/mkdir \"$@\"\n",
                encoding="utf-8",
            )
            fake_mkdir.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{fake_bin}:{environment['PATH']}"

            result = subprocess.run(
                ["zsh", str(script), str(output)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(list(root.glob(".photos-indexer-icon.*")), [])
            self.assertFalse(output.exists())

    def test_build_swift_checks_full_xcode_toolchain_before_mutating_bundle(self) -> None:
        script = (PROJECT_ROOT / "packaging" / "build_swift_app.sh").read_text(encoding="utf-8")
        cleanup = script.index('rm -rf -- "$app_bundle"')
        self.assertIn('command -v xcodebuild', script)
        self.assertIn('xcodebuild -version', script)
        self.assertIn('xcode-select -p', script)
        self.assertLess(script.index('command -v xcodebuild'), cleanup)
        self.assertLess(script.index('xcodebuild -version'), cleanup)
        self.assertLess(script.index('xcode-select -p'), cleanup)

    def test_build_swift_honors_full_developer_dir_but_rejects_command_line_tools(self) -> None:
        script = (PROJECT_ROOT / "packaging" / "build_swift_app.sh").read_text(encoding="utf-8")

        self.assertIn('developer_dir="${DEVELOPER_DIR:-}"', script)
        self.assertIn('DEVELOPER_DIR points to Command Line Tools', script)
        self.assertIn('selected_developer_dir="${DEVELOPER_DIR:-}"', script)
        self.assertIn('selected_developer_dir="$(xcode-select -p', script)

    def test_build_swift_uses_swift_from_selected_xcode_toolchain(self) -> None:
        """A bare PATH swift can ignore DEVELOPER_DIR and build with CLT."""
        script = (PROJECT_ROOT / "packaging" / "build_swift_app.sh").read_text(encoding="utf-8")

        self.assertIn('command -v xcrun', script)
        self.assertIn('xcrun --find swift', script)
        self.assertIn('swift_bin=', script)
        self.assertIn('"$swift_bin" build --package-path', script)
        self.assertNotIn('swift build --package-path', script)
        self.assertLess(script.index('xcrun --find swift'), script.index('"$swift_bin" build --package-path'))

    def test_build_swift_does_not_require_a_bare_path_swift_shim(self) -> None:
        script = (PROJECT_ROOT / "packaging" / "build_swift_app.sh").read_text(encoding="utf-8")
        self.assertNotIn('if ! command -v swift >/dev/null 2>&1; then', script)
        self.assertIn('xcrun --find swift', script)

    def test_build_swift_disables_automatic_dependency_resolution(self) -> None:
        script = (PROJECT_ROOT / "packaging" / "build_swift_app.sh").read_text(encoding="utf-8")

        self.assertIn("--disable-automatic-resolution", script)
        self.assertLess(
            script.index('"$swift_bin" build --package-path'),
            script.index("--disable-automatic-resolution"),
        )
        self.assertLess(
            script.index("--disable-automatic-resolution"),
            script.index('binary="$package_root/.build/'),
        )

    def test_build_swift_validates_main_binary_architecture_before_copy(self) -> None:
        script = (PROJECT_ROOT / "packaging" / "build_swift_app.sh").read_text(encoding="utf-8")

        self.assertIn('binary_architectures="$(lipo -archs "$binary"', script)
        self.assertIn("build_swift_app:FAIL:binary_architecture_not_arm64", script)
        self.assertLess(
            script.index('binary_architectures="$(lipo -archs "$binary"'),
            script.index('mkdir -p "$app_bundle/Contents/MacOS"'),
        )

    def test_build_swift_validates_package_lock_before_swiftpm(self) -> None:
        script = (PROJECT_ROOT / "packaging" / "build_swift_app.sh").read_text(encoding="utf-8")

        self.assertIn('package_lock="$package_root/Package.resolved"', script)
        self.assertIn('[[ -L "$package_lock" || ! -f "$package_lock" ]]', script)
        self.assertIn("build_swift_app:FAIL:package_lock_invalid", script)
        self.assertLess(
            script.index('[[ -L "$package_lock" || ! -f "$package_lock" ]]'),
            script.index('"$swift_bin" build --package-path'),
        )

    def test_build_swift_removes_stale_app_before_compiling(self) -> None:
        script = (PROJECT_ROOT / "packaging" / "build_swift_app.sh").read_text(encoding="utf-8")

        cleanup = script.index('rm -rf -- "$app_bundle"')
        swift_build = script.index('"$swift_bin" build --package-path')

        self.assertLess(cleanup, swift_build)

    def test_build_swift_cleans_partial_app_after_failure_or_interrupt(self) -> None:
        script = (PROJECT_ROOT / "packaging" / "build_swift_app.sh").read_text(encoding="utf-8")

        cleanup_function = script.index("cleanup_app_bundle()")
        cleanup_trap = script.index("trap cleanup_app_bundle EXIT")
        swift_build = script.index('"$swift_bin" build --package-path')
        successful_teardown = script.index("trap - EXIT INT TERM")

        self.assertLess(cleanup_function, swift_build)
        self.assertLess(cleanup_trap, swift_build)
        self.assertGreater(successful_teardown, swift_build)

    def test_build_swift_cleanup_preserves_a_replacement_app_bundle(self) -> None:
        script = (PROJECT_ROOT / "packaging" / "build_swift_app.sh").read_text(encoding="utf-8")

        self.assertIn('app_bundle_created_identity=""', script)
        self.assertIn("record_app_bundle_identity()", script)
        self.assertIn("app_bundle_is_owned()", script)
        self.assertIn("if (( build_succeeded == 0 )) && app_bundle_is_owned; then", script)
        self.assertLess(
            script.index("record_app_bundle_identity"),
            script.index('rm -rf -- "$app_bundle"', script.index("cleanup_app_bundle()")),
        )

    def test_build_swift_rechecks_app_bundle_identity_before_population_and_ready(self) -> None:
        script = (PROJECT_ROOT / "packaging" / "build_swift_app.sh").read_text(encoding="utf-8")
        guard = (
            "if ! app_bundle_is_owned; then\n"
            '  print -u2 -- "build_swift_app:FAIL:app_bundle_identity_changed"\n'
            "  exit 1\n"
            "fi\n"
        )

        record = script.index("record_app_bundle_identity\n")
        population_guard = script.index(guard, record)
        population = script.index('ditto "$binary"', record)
        ready_guard = script.index(guard, population_guard + len(guard))
        ready = script.index("build_succeeded=1", population)
        self.assertLess(record, population_guard)
        self.assertLess(population_guard, population)
        self.assertLess(population, ready_guard)
        self.assertLess(ready_guard, ready)

    def test_build_flags_reject_invalid_verification_values_before_build_mutations(self) -> None:
        helper_script = (PROJECT_ROOT / "packaging" / "build_python_helper.sh").read_text(encoding="utf-8")
        swift_script = (PROJECT_ROOT / "packaging" / "build_swift_app.sh").read_text(encoding="utf-8")

        self.assertIn('verify_helper="${VERIFY_HELPER:-1}"', helper_script)
        self.assertIn('VERIFY_HELPER must be 0 or 1.', helper_script)
        self.assertIn('if [[ "$verify_helper" == "1" ]]', helper_script)
        self.assertLess(
            helper_script.index("VERIFY_HELPER must be 0 or 1."),
            helper_script.index("-m PyInstaller"),
        )

        self.assertIn('VERIFY_EMBEDDED_HELPER must be 0 or 1.', swift_script)
        self.assertLess(
            swift_script.index("VERIFY_EMBEDDED_HELPER must be 0 or 1."),
            swift_script.index('"$swift_bin" build --package-path'),
        )

    def test_python_helper_rejects_invalid_verify_flag_without_creating_build_output(self) -> None:
        script = PROJECT_ROOT / "packaging" / "build_python_helper.sh"
        build_dir = PROJECT_ROOT / "build"
        build_dir.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build_dir) as tmp:
            root = Path(tmp)
            output_root = root / "helper-build"
            environment = os.environ.copy()
            environment["VERIFY_HELPER"] = "unexpected"
            environment["BUILD_ROOT"] = str(output_root)

            result = subprocess.run(
                ["zsh", str(script)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("VERIFY_HELPER must be 0 or 1", result.stderr)
            self.assertFalse(output_root.exists())

    def test_python_helper_does_not_create_build_directories_before_preflight(self) -> None:
        script = PROJECT_ROOT / "packaging" / "build_python_helper.sh"
        build_dir = PROJECT_ROOT / "build"
        with tempfile.TemporaryDirectory(dir=build_dir) as tmp:
            root = Path(tmp)
            output_root = root / "helper-build"
            environment = os.environ.copy()
            environment.update(
                {
                    "BUILD_ROOT": str(output_root),
                    "PYTHON_BIN": str(root / "missing-python3.12"),
                }
            )

            result = subprocess.run(
                ["zsh", str(script)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("Python 3.12 is required", result.stderr)
            self.assertFalse(output_root.exists(), "preflight failure must not leave build directories")

    def test_python_helper_rechecks_isolation_root_before_running_self_check(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "build_python_helper.sh").read_text(encoding="utf-8")
        guard = (
            "if ! isolation_root_is_owned; then\n"
            '    print -u2 -- "build_python_helper:FAIL:helper_check_temp_invalid"\n'
            '    print -u2 -- "build_python_helper:HINT:helper_check_temp_invalid:retry_the_private_helper_check"\n'
            "    exit 1\n"
            "  fi\n"
        )

        record = source.index("record_isolation_root_identity\n")
        identity_guard = source.index(guard, record)
        self_check = source.index('cd "$isolation_root"', record)
        self.assertLess(record, identity_guard)
        self.assertLess(identity_guard, self_check)

    def test_python_helper_rechecks_isolation_root_after_running_self_check(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "build_python_helper.sh").read_text(encoding="utf-8")
        guard = (
            "if ! isolation_root_is_owned; then\n"
            '    print -u2 -- "build_python_helper:FAIL:helper_check_temp_invalid"\n'
            '    print -u2 -- "build_python_helper:HINT:helper_check_temp_invalid:retry_the_private_helper_check"\n'
            "    exit 1\n"
            "  fi\n"
        )

        self_check = source.index('cd "$isolation_root"')
        result_guard = source.index(guard, self_check)
        cleanup = source.index("cleanup_helper_check\n", self_check)
        self.assertLess(self_check, result_guard)
        self.assertLess(result_guard, cleanup)

    def test_python_helper_cleans_partial_directories_when_post_preflight_mkdir_fails(self) -> None:
        script = PROJECT_ROOT / "packaging" / "build_python_helper.sh"
        build_dir = PROJECT_ROOT / "build"
        with tempfile.TemporaryDirectory(dir=build_dir) as tmp:
            root = Path(tmp)
            output_root = root / "helper-build"
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_uname = fake_bin / "uname"
            fake_uname.write_text('#!/bin/zsh\n[[ "$1" == "-m" ]] && print -- arm64\n', encoding="utf-8")
            fake_uname.chmod(0o755)
            fake_lipo = fake_bin / "lipo"
            fake_lipo.write_text("#!/bin/zsh\nexit 0\n", encoding="utf-8")
            fake_lipo.chmod(0o755)
            fake_mkdir = fake_bin / "mkdir"
            fake_mkdir.write_text(
                "#!/bin/zsh\n"
                "if [[ \"$1\" == \"-p\" && $# == 4 ]]; then\n"
                "  /bin/mkdir -p \"$2\"\n"
                "  exit 1\n"
                "fi\n"
                "exec /bin/mkdir \"$@\"\n",
                encoding="utf-8",
            )
            fake_mkdir.chmod(0o755)
            fake_python = root / "python3.12"
            fake_python.write_text(
                "#!/bin/zsh\n"
                "if [[ \"$1\" == \"-c\" && \"$2\" == *\"sys.version_info\"* ]]; then print -- 3.12; exit 0; fi\n"
                "if [[ \"$1\" == \"-c\" && \"$2\" == *\"platform.machine\"* ]]; then print -- arm64; exit 0; fi\n"
                "exit 0\n",
                encoding="utf-8",
            )
            fake_python.chmod(0o755)
            environment = os.environ.copy()
            environment.update(
                {
                    "BUILD_ROOT": str(output_root),
                    "PYTHON_BIN": str(fake_python),
                    "PATH": f"{fake_bin}:{environment['PATH']}",
                }
            )

            result = subprocess.run(
                ["zsh", str(script)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(output_root.exists(), "partial mkdir output must be cleaned")

    def test_python_helper_removes_stale_output_only_before_pyinstaller(self) -> None:
        script = (PROJECT_ROOT / "packaging" / "build_python_helper.sh").read_text(encoding="utf-8")

        cleanup = script.index('rm -rf -- "$build_root/dist/PhotosIndexerWorker"')
        pyinstaller_invocation = script.index('"$python_bin" -m PyInstaller')

        self.assertLess(cleanup, pyinstaller_invocation)

    def test_python_helper_guards_work_and_spec_directories_against_symlinks(self) -> None:
        script = (PROJECT_ROOT / "packaging" / "build_python_helper.sh").read_text(encoding="utf-8")

        self.assertIn('for build_directory in "$build_root/work" "$build_root/spec"; do', script)
        self.assertIn("build_python_helper:FAIL:build_directory_symlink", script)
        self.assertIn("build_python_helper:FAIL:build_directory_not_directory", script)
        self.assertLess(
            script.index('for build_directory in "$build_root/work" "$build_root/spec"; do'),
            script.index('mkdir -p "$build_root/dist" "$build_root/work" "$build_root/spec"'),
        )

    def test_python_helper_preserves_stale_output_when_preflight_cannot_start(self) -> None:
        """A missing build interpreter must not destroy the last usable helper."""
        script = PROJECT_ROOT / "packaging" / "build_python_helper.sh"
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "build") as tmp:
            root = Path(tmp)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_uname = fake_bin / "uname"
            fake_uname.write_text(
                '#!/bin/zsh\n[[ "$1" == "-m" ]] && print -- arm64\n',
                encoding="utf-8",
            )
            fake_uname.chmod(0o755)
            stale_helper = (
                root
                / "helper-build/dist/PhotosIndexerWorker.app/Contents/MacOS/PhotosIndexerWorker"
            )
            stale_helper.parent.mkdir(parents=True)
            stale_helper.write_text("last known good helper", encoding="utf-8")
            stale_helper.chmod(0o755)
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{fake_bin}:/bin:/usr/bin",
                    "PYTHON_BIN": str(root / "missing-python3.12"),
                    "BUILD_ROOT": str(root / "helper-build"),
                }
            )

            result = subprocess.run(
                ["zsh", str(script)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("Python 3.12 is required", result.stderr)
            self.assertEqual(stale_helper.read_text(encoding="utf-8"), "last known good helper")

    def test_python_helper_preserves_stale_output_when_macos_runtime_imports_are_missing(self) -> None:
        """Missing PhotoScript/PyObjC imports must fail before stale cleanup or PyInstaller."""
        script = PROJECT_ROOT / "packaging" / "build_python_helper.sh"
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "build") as tmp:
            root = Path(tmp)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            stale_helper = (
                root
                / "helper-build/dist/PhotosIndexerWorker.app/Contents/MacOS/PhotosIndexerWorker"
            )
            stale_helper.parent.mkdir(parents=True)
            stale_helper.write_text("last known good helper", encoding="utf-8")
            stale_helper.chmod(0o755)
            marker = root / "pyinstaller-invoked"
            (fake_bin / "uname").write_text(
                '#!/bin/zsh\n[[ "$1" == "-m" ]] && print -- arm64\n', encoding="utf-8"
            )
            (fake_bin / "lipo").write_text(
                '#!/bin/zsh\n[[ "$1" == "-archs" ]] && print -- arm64\n', encoding="utf-8"
            )
            for tool in ("uname", "lipo"):
                (fake_bin / tool).chmod(0o755)
            fake_python = fake_bin / "python3.12"
            fake_python.write_text(
                f'''#!/bin/zsh
if [[ "$1" == "-c" && "$2" == *"sys.version_info"* ]]; then
  print -- "3.12"
  exit 0
fi
if [[ "$1" == "-c" && "$2" == *"platform.machine"* ]]; then
  print -- "arm64"
  exit 0
fi
if [[ "$1" == "-c" && "$2" == *"importlib.metadata"* ]]; then
  exit 0
fi
if [[ "$1" == "-c" && "$2" == "import PyInstaller" ]]; then
  exit 0
fi
if [[ "$1" == "-c" && "$2" == *"PhotoScriptBridge.preflight_compatibility()"* ]]; then
  print -u2 -- "PhotoScript AppleScript error -2741"
  exit 1
fi
if [[ "$1" == "-m" && "$2" == "PyInstaller" ]]; then
  : > "{marker}"
  exit 0
fi
exit 1
''',
                encoding="utf-8",
            )
            fake_python.chmod(0o755)
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{fake_bin}:/bin:/usr/bin",
                    "PYTHON_BIN": str(fake_python),
                    "BUILD_ROOT": str(root / "helper-build"),
                }
            )

            result = subprocess.run(
                ["zsh", str(script)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertEqual(stale_helper.read_text(encoding="utf-8"), "last known good helper")
            self.assertFalse(marker.exists(), "missing runtime imports must fail before PyInstaller")

        self.assertEqual(result.returncode, 2)
        self.assertEqual(
            result.stderr,
            "build_python_helper:FAIL:photoscript_applescript_unavailable\n"
            "build_python_helper:HINT:photoscript_applescript_unavailable:check_photoscript_and_macos_compatibility_before_retry\n"
            "build_python_helper:INFO:previous_helper_preserved_not_release_ready\n",
        )
        self.assertNotIn("-2741", result.stderr)

    def test_python_helper_classifies_applescript_bridge_abort_as_compatibility_failure(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "build_python_helper.sh").read_text(encoding="utf-8")

        self.assertIn("runtime_dependency_status=0", source)
        self.assertIn('runtime_dependency_status == 134', source)
        self.assertIn("hiservices-xpcservice", source)

    def test_python_helper_cleans_partial_output_after_post_build_failure(self) -> None:
        script = (PROJECT_ROOT / "packaging" / "build_python_helper.sh").read_text(encoding="utf-8")

        cleanup_function = script.index("cleanup_helper_output()")
        cleanup_trap = script.index("trap cleanup_helper_output EXIT")
        pyinstaller = script.index('"$python_bin" -m PyInstaller')
        successful_teardown = script.index("trap - EXIT INT TERM")

        self.assertLess(cleanup_function, pyinstaller)
        self.assertLess(cleanup_trap, pyinstaller)
        self.assertGreater(successful_teardown, pyinstaller)

    def test_python_helper_cleanup_removes_replaced_output_inside_owned_dist(self) -> None:
        """A failed build owns its exact output slot even when PyInstaller replaces its inode."""
        script = PROJECT_ROOT / "packaging" / "build_python_helper.sh"
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "build") as tmp:
            root = Path(tmp)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            build_root = root / "helper-build"
            helper_root = build_root / "dist" / "PhotosIndexerWorker.app"
            replacement = root / "replacement-marker"
            fake_uname = fake_bin / "uname"
            fake_uname.write_text(
                '#!/bin/zsh\n[[ "$1" == "-m" ]] && print -- arm64\n',
                encoding="utf-8",
            )
            fake_uname.chmod(0o755)
            fake_lipo = fake_bin / "lipo"
            fake_lipo.write_text(
                '#!/bin/zsh\n[[ "$1" == "-archs" ]] && print -- arm64\n',
                encoding="utf-8",
            )
            fake_lipo.chmod(0o755)
            fake_python = fake_bin / "python3.12"
            fake_python.write_text(
                f'''#!/bin/zsh
if [[ "$1" == "-c" && "$2" == *"sys.version_info"* ]]; then print -- 3.12; exit 0; fi
if [[ "$1" == "-c" && "$2" == *"platform.machine"* ]]; then print -- arm64; exit 0; fi
if [[ "$1" == "-c" && "$2" == *"importlib.metadata"* ]]; then exit 0; fi
if [[ "$1" == "-c" && "$2" == "import PyInstaller" ]]; then exit 0; fi
if [[ "$1" == "-c" && "$2" == *"PhotoScriptBridge.preflight_compatibility()"* ]]; then exit 0; fi
if [[ "$1" == "-m" && "$2" == "PyInstaller" ]]; then
  /bin/mkdir -p "{helper_root}"
  /bin/rm -rf "{helper_root}"
  /bin/mkdir -p "{helper_root}"
  print -- replacement > "{replacement}"
  exit 1
fi
exit 1
''',
                encoding="utf-8",
            )
            fake_python.chmod(0o755)
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{fake_bin}:/bin:/usr/bin",
                    "PYTHON_BIN": str(fake_python),
                    "BUILD_ROOT": str(build_root),
                    "VERIFY_HELPER": "0",
                }
            )

            result = subprocess.run(
                ["zsh", str(script)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(replacement.is_file())
            self.assertFalse(helper_root.exists(), "cleanup must remove the failed output slot")

    def test_python_helper_rejects_a_symlinked_output_binary(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "build_python_helper.sh").read_text(encoding="utf-8")

        self.assertIn('|| ! -x "$helper" \\\n  || -L "$helper" ]]; then', source)

    def test_python_helper_rejects_a_symlinked_dist_before_cleanup(self) -> None:
        script = PROJECT_ROOT / "packaging" / "build_python_helper.sh"
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "build") as tmp:
            root = Path(tmp)
            build_root = root / "helper-build"
            external = root / "external"
            stale_helper = external / "PhotosIndexerWorker" / "PhotosIndexerWorker"
            stale_helper.parent.mkdir(parents=True)
            stale_helper.write_text("must survive", encoding="utf-8")
            build_root.mkdir()
            (build_root / "dist").symlink_to(external, target_is_directory=True)

            environment = os.environ.copy()
            environment["BUILD_ROOT"] = str(build_root)

            result = subprocess.run(
                ["zsh", str(script)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(stale_helper.exists(), "a symlinked dist must not allow cleanup outside BUILD_ROOT")

    def test_python_helper_rejects_a_symlinked_build_root_before_preflight(self) -> None:
        script = PROJECT_ROOT / "packaging" / "build_python_helper.sh"
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "build") as tmp:
            root = Path(tmp)
            target = root / "real-helper-build"
            target.mkdir()
            build_root = root / "linked-helper-build"
            build_root.symlink_to(target, target_is_directory=True)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_uname = fake_bin / "uname"
            fake_uname.write_text(
                '#!/bin/zsh\n[[ "$1" == "-m" ]] && print -- arm64\n',
                encoding="utf-8",
            )
            fake_uname.chmod(0o755)
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{fake_bin}:/bin:/usr/bin",
                    "PYTHON_BIN": str(root / "missing-python3.12"),
                    "BUILD_ROOT": str(build_root),
                }
            )

            result = subprocess.run(
                ["zsh", str(script)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("symlink", result.stderr.lower())
            self.assertNotIn(str(build_root), result.stderr)
            self.assertFalse((target / "dist").exists(), "a symlinked BUILD_ROOT must not be created or reused")

    def test_python_helper_rejects_non_arm64_interpreter_before_pyinstaller(self) -> None:
        script = PROJECT_ROOT / "packaging" / "build_python_helper.sh"
        build_dir = PROJECT_ROOT / "build"
        build_dir.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build_dir) as tmp:
            root = Path(tmp)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            marker = root / "pyinstaller-invoked"
            fake_uname = fake_bin / "uname"
            fake_uname.write_text(
                "#!/bin/zsh\n[[ \"$1\" == \"-m\" ]] && print -- arm64\n",
                encoding="utf-8",
            )
            fake_uname.chmod(0o755)
            fake_python = fake_bin / "python3.12-x86_64"
            fake_python.write_text(
                """#!/bin/zsh
if [[ "$1" == "-c" && "$2" == *"sys.version_info"* ]]; then
  print -- "3.12"
  exit 0
fi
if [[ "$1" == "-c" && "$2" == *"platform.machine"* ]]; then
  print -- "x86_64"
  exit 0
fi
if [[ "$1" == "-c" && "$2" == *"importlib.metadata"* ]]; then
  exit 0
fi
if [[ "$1" == "-c" && "$2" == "import PyInstaller" ]]; then
  exit 0
fi
if [[ "$1" == "-m" && "$2" == "PyInstaller" ]]; then
  : > "$PYINSTALLER_MARKER"
  exit 0
fi
exit 1
""",
                encoding="utf-8",
            )
            fake_python.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
            environment["PYTHON_BIN"] = str(fake_python)
            environment["BUILD_ROOT"] = str(root / "helper-build")
            environment["PYINSTALLER_MARKER"] = str(marker)

            result = subprocess.run(
                ["zsh", str(script)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("arm64 Python 3.12", result.stderr)
            self.assertNotIn(str(fake_python), result.stderr)
            self.assertIn("PYTHON_BIN", result.stderr)
            self.assertFalse(marker.exists(), "an incompatible interpreter must fail before PyInstaller runs")

    def test_python_helper_rejects_missing_lipo_before_pyinstaller(self) -> None:
        script = PROJECT_ROOT / "packaging" / "build_python_helper.sh"
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "build") as tmp:
            root = Path(tmp)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            marker = root / "pyinstaller-invoked"
            stale_helper = (
                root
                / "helper-build/dist/PhotosIndexerWorker.app/Contents/MacOS/PhotosIndexerWorker"
            )
            stale_helper.parent.mkdir(parents=True)
            stale_helper.write_text("stale helper", encoding="utf-8")
            stale_helper.chmod(0o755)
            fake_uname = fake_bin / "uname"
            fake_uname.write_text(
                '#!/bin/zsh\n[[ "$1" == "-m" ]] && print -- arm64\n',
                encoding="utf-8",
            )
            fake_uname.chmod(0o755)
            fake_python = fake_bin / "python3.12"
            fake_python.write_text(
                f'''#!/bin/zsh
if [[ "$1" == "-c" && "$2" == *"sys.version_info"* ]]; then
  print -- "3.12"
  exit 0
fi
if [[ "$1" == "-c" && "$2" == *"platform.machine"* ]]; then
  print -- "arm64"
  exit 0
fi
if [[ "$1" == "-c" && "$2" == *"importlib.metadata"* ]]; then
  exit 0
fi
if [[ "$1" == "-c" && "$2" == "import PyInstaller" ]]; then
  exit 0
fi
if [[ "$1" == "-c" && "$2" == *"PhotoScriptBridge.preflight_compatibility()"* ]]; then
  exit 0
fi
if [[ "$1" == *"write_helper_dependency_inventory.py" ]]; then
  print -- '{{"dependencies":[{{"license":"MIT","name":"photoscript","notice_files":["LICENSE"],"version":"0.5.3"}}],"membership_source":"pyinstaller-analysis-toc-v1","schema_version":1}}' > "$3"
  exit 0
fi
if [[ "$1" == "-m" && "$2" == "PyInstaller" ]]; then
  : > "{marker}"
  exit 0
fi
exit 1
''',
                encoding="utf-8",
            )
            fake_python.chmod(0o755)
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{fake_bin}:/bin",
                    "PYTHON_BIN": str(fake_python),
                    "BUILD_ROOT": str(root / "helper-build"),
                    "VERIFY_HELPER": "1",
                }
            )

            result = subprocess.run(
                ["zsh", str(script)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertTrue(stale_helper.exists(), "missing lipo must preserve the last usable helper")

        self.assertEqual(result.returncode, 2)
        self.assertEqual(
            result.stderr,
            "build_python_helper:FAIL:lipo_missing\n"
            "build_python_helper:HINT:lipo_missing:install_xcode_command_line_tools_then_retry\n",
        )
        self.assertFalse(marker.exists(), "missing lipo must fail before PyInstaller runs")

    def test_python_helper_isolation_check_cannot_depend_on_the_build_environment(self) -> None:
        """The optional smoke check must exercise the packaged runtime in isolation.

        A helper can appear healthy while accidentally importing the source tree or
        a virtualenv through inherited Python environment variables.  The release
        build therefore needs an explicit clean-environment check from a directory
        outside the checkout; source-level assertions keep this gate testable on CI
        hosts that cannot execute a macOS PyInstaller artifact.
        """
        script = (PROJECT_ROOT / "packaging" / "build_python_helper.sh").read_text(encoding="utf-8")

        self.assertIn('isolation_root="$(mktemp -d', script)
        self.assertIn('cd "$isolation_root"', script)
        self.assertIn('env -i', script)
        self.assertIn('PYTHONPATH', script)
        self.assertIn('PYTHONHOME', script)
        self.assertIn('VIRTUAL_ENV', script)
        self.assertIn('VERIFY_HELPER', script)

    def test_python_helper_isolation_check_really_clears_python_environment(self) -> None:
        """Run the build gate with a fake artifact to prove env -i is effective."""
        script = PROJECT_ROOT / "packaging" / "build_python_helper.sh"
        build_dir = PROJECT_ROOT / "build"
        build_dir.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build_dir) as tmp:
            root = Path(tmp)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            helper_app = root / "helper-build" / "dist" / "PhotosIndexerWorker.app"
            marker = root / "helper-cwd"
            helper_plist = root / "HelperInfo.plist"
            helper_plist.write_bytes(plistlib.dumps({
                "CFBundleExecutable": "PhotosIndexerWorker",
                "CFBundleIdentifier": "com.photoslocalkeywordindexer.worker",
                "CFBundlePackageType": "APPL",
                "CFBundleShortVersionString": PROJECT_VERSION,
                "CFBundleVersion": "1",
                "LSUIElement": True,
                "NSPhotoLibraryUsageDescription": "Fotos",
                "NSPhotoLibraryAddUsageDescription": "Fotos",
                "NSAppleEventsUsageDescription": "Fotos",
            }))
            fake_uname = fake_bin / "uname"
            fake_uname.write_text("#!/bin/zsh\n[[ \"$1\" == \"-m\" ]] && print -- arm64\n", encoding="utf-8")
            fake_uname.chmod(0o755)
            fake_lipo = fake_bin / "lipo"
            fake_lipo.write_text("#!/bin/zsh\n[[ \"$1\" == \"-archs\" ]] && print -- arm64\n", encoding="utf-8")
            fake_lipo.chmod(0o755)
            fake_python = fake_bin / "python3.12"
            fake_python.write_text(
                '''#!/bin/zsh
if [[ "$1" == "-c" && "$2" == *"sys.version_info"* ]]; then
  print -- "3.12"
  exit 0
fi
if [[ "$1" == "-c" && "$2" == *"platform.machine"* ]]; then
  print -- "arm64"
  exit 0
fi
if [[ "$1" == "-c" && "$2" == *"importlib.metadata"* ]]; then
  exit 0
fi
if [[ "$1" == "-c" && "$2" == "import PyInstaller" ]]; then
  exit 0
fi
if [[ "$1" == "-c" && "$2" == *"PhotoScriptBridge.preflight_compatibility()"* ]]; then
  exit 0
fi
if [[ "$1" == *"write_helper_dependency_inventory.py" ]]; then
  print -- '{"dependencies":[{"license":"MIT","name":"photoscript","notice_files":["LICENSE"],"version":"0.5.3"}],"membership_source":"pyinstaller-analysis-toc-v1","schema_version":1}' > "$3"
  exit 0
fi
if [[ "$1" == "-m" && "$2" == "PyInstaller" ]]; then
  mkdir -p "$FAKE_HELPER_APP/Contents/MacOS"
  mkdir -p "$FAKE_HELPER_APP/Contents/Frameworks"
  mkdir -p "$FAKE_HELPER_APP/Contents/Resources"
  cp "$HELPER_PLIST" "$FAKE_HELPER_APP/Contents/Info.plist"
  print -- runtime > "$FAKE_HELPER_APP/Contents/Frameworks/Python"
  {
    print -- '#!/bin/zsh'
    print -- 'if [[ -n "${VIRTUAL_ENV+x}" || -n "${PYTHONPATH+x}" || -n "${PYTHONHOME+x}" ]]; then exit 11; fi'
    print -- 'print -- "$PWD" > "'"$HELPER_MARKER"'"'
    print -- 'print -- '\''{"status":"ok","runtime":"embedded","architecture":"arm64","protocol":"jsonl"}'\'''
  } > "$FAKE_HELPER_APP/Contents/MacOS/PhotosIndexerWorker"
  chmod 755 "$FAKE_HELPER_APP/Contents/MacOS/PhotosIndexerWorker"
  mkdir -p "$(dirname "$(dirname "$FAKE_HELPER_APP")")/work/PhotosIndexerWorker"
  print -- "('photoscript',)" > "$(dirname "$(dirname "$FAKE_HELPER_APP")")/work/PhotosIndexerWorker/Analysis-00.toc"
  exit 0
fi
exit 1
''',
                encoding="utf-8",
            )
            fake_python.chmod(0o755)

            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{fake_bin}:{environment['PATH']}",
                    "PYTHON_BIN": str(fake_python),
                    "BUILD_ROOT": str(root / "helper-build"),
                    "FAKE_HELPER_APP": str(helper_app),
                    "HELPER_PLIST": str(helper_plist),
                    "HELPER_MARKER": str(marker),
                    "VERIFY_HELPER": "1",
                    "VIRTUAL_ENV": str(root / "venv"),
                    "PYTHONPATH": str(PROJECT_ROOT / "src"),
                    "PYTHONHOME": str(root / "python-home"),
                }
            )

            result = subprocess.run(
                ["/bin/zsh", "-c", 'umask 000; exec /bin/zsh "$1"', "helper-umask-test", str(script)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(marker.is_file(), result.stderr)
            self.assertEqual(stat.S_IMODE(helper_app.stat().st_mode), 0o755)
            self.assertEqual(
                stat.S_IMODE(
                    (helper_app / "Contents/Resources/.photos-indexer-source-fingerprint").stat().st_mode
                ),
                0o600,
            )
            self.assertNotEqual(marker.read_text(encoding="utf-8").strip(), str(PROJECT_ROOT))
            self.assertNotEqual(marker.read_text(encoding="utf-8").strip(), str(root / "helper-build"))

    def test_python_helper_resolves_relative_python_bin_before_changing_directory(self) -> None:
        """A relative PYTHON_BIN must remain usable after the script enters the project root."""
        script = PROJECT_ROOT / "packaging" / "build_python_helper.sh"
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "build") as tmp:
            root = Path(tmp)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            helper_app = root / "helper-build" / "dist" / "PhotosIndexerWorker.app"
            helper_plist = root / "HelperInfo.plist"
            helper_plist.write_bytes(plistlib.dumps({
                "CFBundleExecutable": "PhotosIndexerWorker",
                "CFBundleIdentifier": "com.photoslocalkeywordindexer.worker",
                "CFBundlePackageType": "APPL",
                "CFBundleShortVersionString": PROJECT_VERSION,
                "CFBundleVersion": "1",
                "LSUIElement": True,
                "NSPhotoLibraryUsageDescription": "Fotos",
                "NSPhotoLibraryAddUsageDescription": "Fotos",
                "NSAppleEventsUsageDescription": "Fotos",
            }))
            fake_uname = fake_bin / "uname"
            fake_uname.write_text('#!/bin/zsh\n[[ "$1" == "-m" ]] && print -- arm64\n', encoding="utf-8")
            fake_uname.chmod(0o755)
            fake_lipo = fake_bin / "lipo"
            fake_lipo.write_text('#!/bin/zsh\n[[ "$1" == "-archs" ]] && print -- arm64\n', encoding="utf-8")
            fake_lipo.chmod(0o755)
            fake_python = fake_bin / "python3.12"
            fake_python.write_text(
                f'''#!/bin/zsh
if [[ "$1" == "-c" && "$2" == *"sys.version_info"* ]]; then
  print -- "3.12"
  exit 0
fi
if [[ "$1" == "-c" && "$2" == *"platform.machine"* ]]; then
  print -- "arm64"
  exit 0
fi
if [[ "$1" == "-c" && "$2" == *"importlib.metadata"* ]]; then
  exit 0
fi
if [[ "$1" == "-c" && "$2" == "import PyInstaller" ]]; then
  exit 0
fi
if [[ "$1" == "-c" && "$2" == *"PhotoScriptBridge.preflight_compatibility()"* ]]; then
  exit 0
fi
if [[ "$1" == *"write_helper_dependency_inventory.py" ]]; then
  print -- '{{"dependencies":[{{"license":"MIT","name":"photoscript","notice_files":["LICENSE"],"version":"0.5.3"}}],"membership_source":"pyinstaller-analysis-toc-v1","schema_version":1}}' > "$3"
  exit 0
fi
if [[ "$1" == "-m" && "$2" == "PyInstaller" ]]; then
  mkdir -p "{helper_app}/Contents/MacOS" "{helper_app}/Contents/Frameworks" "{helper_app}/Contents/Resources"
  cp "{helper_plist}" "{helper_app}/Contents/Info.plist"
  print -- runtime > "{helper_app}/Contents/Frameworks/Python"
  print -- '#!/bin/zsh' > "{helper_app}/Contents/MacOS/PhotosIndexerWorker"
  print -- 'print -- '\''{{"status":"ok","runtime":"embedded","architecture":"arm64","protocol":"jsonl"}}'\''' >> "{helper_app}/Contents/MacOS/PhotosIndexerWorker"
  chmod 755 "{helper_app}/Contents/MacOS/PhotosIndexerWorker"
  mkdir -p "{helper_app.parent.parent}/work/PhotosIndexerWorker"
  print -- "('photoscript',)" > "{helper_app.parent.parent}/work/PhotosIndexerWorker/Analysis-00.toc"
  exit 0
fi
exit 1
''',
                encoding="utf-8",
            )
            fake_python.chmod(0o755)
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{fake_bin}:/bin:/usr/bin",
                    "PYTHON_BIN": "./bin/python3.12",
                    "BUILD_ROOT": str(root / "helper-build"),
                    "VERIFY_HELPER": "1",
                }
            )

            result = subprocess.run(
                ["zsh", str(script)],
                check=False,
                capture_output=True,
                text=True,
                cwd=root,
                env=environment,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.strip().endswith("release_helper:READY_FOR_APP_BUNDLE"))

    def test_python_helper_preserves_virtualenv_python_symlink(self) -> None:
        """A venv executable symlink must keep its environment after absolutizing."""
        script = (PROJECT_ROOT / "packaging" / "build_python_helper.sh").read_text(encoding="utf-8")

        self.assertIn('python_bin="${python_bin:a}"', script)
        self.assertNotIn('python_bin="${python_bin:A}"', script)

    def test_python_helper_removes_the_legacy_flat_collect_after_a_successful_bundle_build(self) -> None:
        script = PROJECT_ROOT / "packaging" / "build_python_helper.sh"
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "build") as tmp:
            root = Path(tmp)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            helper_app = root / "helper-build" / "dist" / "PhotosIndexerWorker.app"
            legacy_helper = root / "helper-build" / "dist" / "PhotosIndexerWorker" / "PhotosIndexerWorker"
            helper_plist = root / "HelperInfo.plist"
            helper_plist.write_bytes(plistlib.dumps({
                "CFBundleExecutable": "PhotosIndexerWorker",
                "CFBundleIdentifier": "com.photoslocalkeywordindexer.worker",
                "CFBundlePackageType": "APPL",
                "CFBundleShortVersionString": PROJECT_VERSION,
                "CFBundleVersion": "1",
                "LSUIElement": True,
                "NSPhotoLibraryUsageDescription": "Fotos",
                "NSPhotoLibraryAddUsageDescription": "Fotos",
                "NSAppleEventsUsageDescription": "Fotos",
            }))
            fake_uname = fake_bin / "uname"
            fake_uname.write_text('#!/bin/zsh\n[[ "$1" == "-m" ]] && print -- arm64\n', encoding="utf-8")
            fake_uname.chmod(0o755)
            fake_lipo = fake_bin / "lipo"
            fake_lipo.write_text('#!/bin/zsh\n[[ "$1" == "-archs" ]] && print -- arm64\n', encoding="utf-8")
            fake_lipo.chmod(0o755)
            fake_python = fake_bin / "python3.12"
            fake_python.write_text(
                f'''#!/bin/zsh
if [[ "$1" == "-c" && "$2" == *"sys.version_info"* ]]; then
  print -- "3.12"
  exit 0
fi
if [[ "$1" == "-c" && "$2" == *"platform.machine"* ]]; then
  print -- "arm64"
  exit 0
fi
if [[ "$1" == "-c" && "$2" == *"importlib.metadata"* ]]; then
  exit 0
fi
if [[ "$1" == "-c" && "$2" == "import PyInstaller" ]]; then
  exit 0
fi
if [[ "$1" == "-c" && "$2" == *"PhotoScriptBridge.preflight_compatibility()"* ]]; then
  exit 0
fi
if [[ "$1" == *"write_helper_dependency_inventory.py" ]]; then
  print -- '{{"dependencies":[{{"license":"MIT","name":"photoscript","notice_files":["LICENSE"],"version":"0.5.3"}}],"membership_source":"pyinstaller-analysis-toc-v1","schema_version":1}}' > "$3"
  exit 0
fi
if [[ "$1" == "-m" && "$2" == "PyInstaller" ]]; then
  mkdir -p "{helper_app}/Contents/MacOS" "{helper_app}/Contents/Frameworks" "{helper_app}/Contents/Resources"
  cp "{helper_plist}" "{helper_app}/Contents/Info.plist"
  print -- runtime > "{helper_app}/Contents/Frameworks/Python"
  print -- '#!/bin/zsh' > "{helper_app}/Contents/MacOS/PhotosIndexerWorker"
  print -- 'print -- '\''{{"status":"ok","runtime":"embedded","architecture":"arm64","protocol":"jsonl"}}'\''' >> "{helper_app}/Contents/MacOS/PhotosIndexerWorker"
  chmod 755 "{helper_app}/Contents/MacOS/PhotosIndexerWorker"
  mkdir -p "{helper_app.parent.parent}/work/PhotosIndexerWorker"
  print -- "('photoscript',)" > "{helper_app.parent.parent}/work/PhotosIndexerWorker/Analysis-00.toc"
  mkdir -p "{legacy_helper.parent}"
  print -- stale > "{legacy_helper}"
  chmod 755 "{legacy_helper}"
  exit 0
fi
exit 1
''',
                encoding="utf-8",
            )
            fake_python.chmod(0o755)
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{fake_bin}:/bin:/usr/bin",
                    "PYTHON_BIN": str(fake_python),
                    "BUILD_ROOT": str(root / "helper-build"),
                    "VERIFY_HELPER": "1",
                }
            )

            result = subprocess.run(
                ["zsh", str(script)],
                check=False,
                capture_output=True,
                text=True,
                cwd=root,
                env=environment,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(
                legacy_helper.exists(),
                "legacy flat COLLECT output must be removed after a successful bundle build",
            )
            self.assertTrue(helper_app.exists())
            self.assertTrue(result.stdout.strip().endswith("release_helper:READY_FOR_APP_BUNDLE"))

    def test_build_python_helper_uses_the_native_pyinstaller_bundle(self) -> None:
        """The native BUNDLE output must be used directly without relocating its runtime."""
        script = (PROJECT_ROOT / "packaging" / "build_python_helper.sh").read_text(encoding="utf-8")

        self.assertIn('helper_app="$build_root/dist/PhotosIndexerWorker.app"', script)
        self.assertNotIn("materialize_helper_app.sh", script)

    def test_python_helper_rejects_a_root_symlink_before_writing_or_executing(self) -> None:
        result = self._run_python_helper_adversarial_bundle("root_symlink")

        self.assertNotEqual(result["returncode"], 0, result["stderr"])
        self.assertFalse(result["helper_exists"])
        self.assertTrue(result["external_app_exists"])
        self.assertFalse(result["external_fingerprint_exists"])
        self.assertFalse(result["executed"])

    def test_python_helper_removes_a_partial_bundle_after_pyinstaller_replaces_it(self) -> None:
        result = self._run_python_helper_adversarial_bundle("partial")

        self.assertNotEqual(result["returncode"], 0, result["stderr"])
        self.assertFalse(result["helper_exists"])

    def test_python_helper_removes_a_native_bundle_with_a_missing_executable(self) -> None:
        result = self._run_python_helper_adversarial_bundle("missing_executable")

        self.assertNotEqual(result["returncode"], 0, result["stderr"])
        self.assertFalse(result["helper_exists"])
        self.assertFalse(result["executed"])

    def test_python_helper_removes_a_native_bundle_with_a_hardlinked_payload(self) -> None:
        result = self._run_python_helper_adversarial_bundle("hardlink")

        self.assertNotEqual(result["returncode"], 0, result["stderr"])
        self.assertFalse(result["helper_exists"])
        self.assertTrue(result["external_payload_exists"])
        self.assertFalse(result["executed"])

    def test_python_helper_removes_a_native_bundle_with_unsafe_permissions(self) -> None:
        result = self._run_python_helper_adversarial_bundle("unsafe_permissions")

        self.assertNotEqual(result["returncode"], 0, result["stderr"])
        self.assertFalse(result["helper_exists"])
        self.assertFalse(result["executed"])

    def test_pyinstaller_spec_defines_the_native_nested_helper_topology(self) -> None:
        """PyInstaller must own the macOS Frameworks/Resources classification."""
        source = (PROJECT_ROOT / "packaging" / "PhotosIndexerWorker.spec").read_text(encoding="utf-8")

        self.assertIn("BUNDLE(", source)
        self.assertIn('name="PhotosIndexerWorker.app"', source)
        self.assertIn('bundle_identifier="com.photoslocalkeywordindexer.worker"', source)
        self.assertIn('"LSUIElement": True', source)
        self.assertIn('"NSPhotoLibraryUsageDescription"', source)
        self.assertIn('"NSPhotoLibraryAddUsageDescription"', source)
        self.assertIn('"NSAppleEventsUsageDescription"', source)

    def test_release_scripts_are_syntax_valid(self) -> None:
        for script in (
            "build_python_helper.sh",
            "build_swift_app.sh",
            "build_dmg.sh",
            "notarize.sh",
            "sign_app.sh",
            "verify_dmg_layout.sh",
        ):
            result = subprocess.run(
                ["zsh", "-n", str(PROJECT_ROOT / "packaging" / script)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_dmg_layout_verifier_is_a_read_only_install_smoke_gate(self) -> None:
        script = PROJECT_ROOT / "packaging" / "verify_dmg_layout.sh"
        source = script.read_text(encoding="utf-8")
        self.assertTrue(script.is_file())
        self.assertIn("hdiutil attach", source)
        self.assertIn("-readonly", source)
        self.assertIn("hdiutil detach", source)
        self.assertIn("--json", source)
        self.assertIn('"schema_version": 1', source)
        self.assertIn('"status"', source)
        self.assertIn("dmg_layout", source)
        self.assertIn("DMG root must contain only the app and Applications alias", source)
        self.assertIn("DMG app version/build do not match its filename", source)
        self.assertIn("Contents/Resources/AppIcon.icns", source)
        self.assertNotIn("codesign --sign", source)
        self.assertNotIn("/Applications/PhotosLocalKeywordIndexer.app", source)

    def test_dmg_layout_does_not_remove_mountpoint_when_detach_fails(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "verify_dmg_layout.sh").read_text(encoding="utf-8")
        self.assertIn("detached=0", source)
        self.assertIn('if hdiutil detach "$mounted_device" >/dev/null 2>&1; then', source)
        self.assertIn("dmg_layout:WARN:mount_cleanup_failed", source)
        self.assertIn("mount_point_is_owned()", source)
        self.assertIn('if (( detached == 1 )) && mount_point_is_owned; then', source)

    def test_dmg_layout_cleanup_only_removes_its_original_mountpoint(self) -> None:
        """A raced mountpoint replacement must not be recursively removed."""
        source = (PROJECT_ROOT / "packaging" / "verify_dmg_layout.sh").read_text(encoding="utf-8")
        self.assertIn('mount_point_created_identity=""', source)
        self.assertIn("record_mount_point_identity()", source)
        self.assertIn("mount_point_is_owned()", source)
        self.assertIn("stat -f '%d:%i' -- \"$mount_point\"", source)
        self.assertIn(
            '  if (( detached == 1 )) && mount_point_is_owned; then\n'
            '    rm -rf -- "$mount_point" >/dev/null 2>&1 || cleanup_failed=1\n'
            '  fi',
            source,
        )

    def test_dmg_layout_cleanup_does_not_detach_a_replaced_mountpoint(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "verify_dmg_layout.sh").read_text(encoding="utf-8")

        self.assertIn('if [[ -z "$mounted_device" ]]; then', source)
        self.assertIn('if hdiutil detach "$mounted_device" >/dev/null 2>&1; then', source)
        self.assertIn('if (( detached == 1 )) && mount_point_is_owned; then', source)

    def test_dmg_layout_tracks_the_attached_device_before_cleanup(self) -> None:
        """A mounted directory changes device/inode; detach the image device instead."""
        source = (PROJECT_ROOT / "packaging" / "verify_dmg_layout.sh").read_text(encoding="utf-8")
        self.assertIn('mounted_device=""', source)
        self.assertIn('mounted_device="$(print -r -- "$attach_output"', source)
        self.assertIn('hdiutil detach "$mounted_device"', source)

    def test_dmg_layout_requires_owned_mountpoint_before_attach(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "verify_dmg_layout.sh").read_text(encoding="utf-8")
        guard = (
            "if ! mount_point_is_owned; then\n"
            '  fail temporary_mount_invalid 1 "Temporary mount point identity changed."\n'
            "fi\n"
        )

        record = source.index("record_mount_point_identity\n")
        identity_guard = source.index(guard, record)
        attach = source.index('if ! attach_output="$(hdiutil attach', record)
        self.assertLess(record, identity_guard)
        self.assertLess(identity_guard, attach)

    def test_dmg_layout_rechecks_dmg_identity_before_attach_and_ready(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "verify_dmg_layout.sh").read_text(encoding="utf-8")
        guard = (
            "if ! dmg_is_owned; then\n"
            '  fail dmg_identity_changed 1 "DMG identity changed during verification."\n'
            "fi\n"
        )

        record = source.index("record_dmg_identity\n")
        attach_guard = source.index(guard, record)
        attach = source.index('if ! attach_output="$(hdiutil attach', record)
        ready_guard = source.index(guard, attach_guard + len(guard))
        ready = source.index("if (( json_mode == 1 )); then", attach)
        self.assertLess(record, attach_guard)
        self.assertLess(attach_guard, attach)
        self.assertLess(attach, ready_guard)
        self.assertLess(ready_guard, ready)

    def test_release_diagnostics_guard_private_temp_cleanup_with_identity(self) -> None:
        """Private diagnostic cleanup must not recursively remove a replacement."""
        expectations = {
            "build_python_helper.sh": ("isolation_root", "cleanup_helper_check"),
            "verify_embedded_helper.sh": ("diagnostic_root", "cleanup_diagnostic"),
            "release_preflight.sh": ("helper_check_root", "cleanup_helper_check"),
            "verify_release.sh": ("entitlement_tmp", "cleanup_entitlements"),
        }
        for name, (temp_name, cleanup_name) in expectations.items():
            source = (PROJECT_ROOT / "packaging" / name).read_text(encoding="utf-8")
            self.assertIn(f'{temp_name}_created_identity=""', source, name)
            self.assertIn(f"record_{temp_name}_identity()", source, name)
            self.assertIn(f"{temp_name}_is_owned()", source, name)
            cleanup_start = source.index(f"{cleanup_name}()")
            cleanup_end = source.index("\n  }", cleanup_start) if name == "build_python_helper.sh" else source.index("\n}", cleanup_start)
            cleanup = source[cleanup_start:cleanup_end]
            self.assertIn(f"if {temp_name}_is_owned; then", cleanup, name)
            self.assertIn(f'rm -rf -- "${temp_name}"', cleanup, name)
            self.assertLess(cleanup.index(f"{temp_name}_is_owned"), cleanup.index("rm -rf"), name)

    def test_release_preflight_rechecks_helper_temp_identity_before_self_check(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "release_preflight.sh").read_text(encoding="utf-8")

        record = source.index("record_helper_check_root_identity\n")
        identity_guard = source.index("if helper_check_root_is_owned; then", record)
        self_check = source.index('"$helper" --self-check', record)
        self.assertLess(record, identity_guard)
        self.assertLess(identity_guard, self_check)

    def test_release_preflight_rechecks_helper_temp_identity_after_self_check(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "release_preflight.sh").read_text(encoding="utf-8")
        guard = (
            "if ! helper_check_root_is_owned; then\n"
            '            helper_runtime="invalid"\n'
            "          fi\n"
        )

        self_check = source.index('"$helper" --self-check')
        result_guard = source.index(guard, self_check)
        result_check = source.index('if [[ "$helper_runtime" ==', self_check)
        self.assertLess(self_check, result_guard)
        self.assertLess(result_guard, result_check)

    def test_embedded_helper_rechecks_diagnostic_root_before_self_check(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "verify_embedded_helper.sh").read_text(
            encoding="utf-8"
        )
        guard = (
            "if ! diagnostic_root_is_owned; then\n"
            '  print -u2 -- "embedded_helper:FAIL:diagnostic_temp_invalid"\n'
            "  exit 1\n"
            "fi\n"
        )

        record = source.index("record_diagnostic_root_identity\n")
        identity_guard = source.index(guard, record)
        self_check = source.index('cd "$diagnostic_root"', record)
        self.assertLess(record, identity_guard)
        self.assertLess(identity_guard, self_check)

    def test_embedded_helper_rechecks_diagnostic_root_after_self_check(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "verify_embedded_helper.sh").read_text(
            encoding="utf-8"
        )
        guard = (
            "if ! diagnostic_root_is_owned; then\n"
            '  print -u2 -- "embedded_helper:FAIL:diagnostic_temp_invalid"\n'
            "  exit 1\n"
            "fi\n"
        )

        self_check = source.index('"$helper" --self-check')
        result_guard = source.index(guard, self_check)
        result_check = source.index('if [[ "$check_output" !=', self_check)
        self.assertLess(self_check, result_guard)
        self.assertLess(result_guard, result_check)

    def test_release_verifier_rechecks_entitlement_temp_identity_before_writing(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "verify_release.sh").read_text(encoding="utf-8")
        function = source[
            source.index("verify_no_debug_entitlements()") : source.index("\n}\n", source.index("verify_no_debug_entitlements()"))
        ]

        self.assertIn("if ! entitlement_tmp_is_owned; then", function)
        self.assertIn("verify_release:FAIL:entitlement_temp_invalid", function)
        self.assertLess(function.index("entitlement_tmp_is_owned"), function.index('rm -f -- "$signed_plist"'))
        self.assertLess(function.index("entitlement_tmp_is_owned"), function.index('> "$signed_plist"'))

    def test_release_verifier_rechecks_debug_entitlement_temp_after_extraction(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "verify_release.sh").read_text(encoding="utf-8")
        function = source[
            source.index("verify_no_debug_entitlements()") : source.index(
                "\n}\n", source.index("verify_no_debug_entitlements()")
            )
        ]
        guard = (
            "if ! entitlement_tmp_is_owned; then\n"
            '    print -u2 -- "verify_release:FAIL:entitlement_temp_invalid"\n'
            "    return 1\n"
            "  fi\n"
        )

        extraction = function.index('codesign -d --entitlements :- "$target"')
        result_guard = function.index(guard, extraction)
        result_read = function.index('[[ -s "$signed_plist" ]]', extraction)
        self.assertLess(extraction, result_guard)
        self.assertLess(result_guard, result_read)

    def test_release_verifier_guards_final_entitlement_outputs_before_write_and_read(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "verify_release.sh").read_text(encoding="utf-8")
        guard = (
            "if ! entitlement_tmp_is_owned; then\n"
            '  print -u2 -- "verify_release:FAIL:entitlement_temp_invalid"\n'
            "  exit 1\n"
            "fi\n"
        )

        outputs = source.index('signed_entitlements="$entitlement_tmp/signed.plist"')
        write_guard = source.index(guard, outputs)
        write = source.index('codesign -d --entitlements :- "$app_path"', outputs)
        read_guard = source.index(guard, write_guard + len(guard))
        read_result = source.index('validate_entitlements "$signed_entitlements"', write)
        self.assertLess(outputs, write_guard)
        self.assertLess(write_guard, write)
        self.assertLess(write, read_guard)
        self.assertLess(read_guard, read_result)

    def test_release_verifier_checks_its_required_macos_tools_before_bundle_reads(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "verify_release.sh").read_text(encoding="utf-8")
        self.assertIn('for command_name in plutil lipo codesign spctl; do', source)
        self.assertIn('Required release verification tool is unavailable: $command_name', source)

    def test_release_verifier_rechecks_bundle_identity_before_signatures_and_pass(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "verify_release.sh").read_text(encoding="utf-8")
        guard = (
            "if ! app_bundle_is_owned; then\n"
            '  print -u2 -- "verify_release:FAIL:app_bundle_identity_changed"\n'
            "  exit 1\n"
            "fi\n"
        )

        record = source.index("record_app_bundle_identity\n")
        signature_guard = source.index(guard, record)
        signatures = source.index('team_ids=("$(verify_code', record)
        pass_guard = source.index(guard, signature_guard + len(guard))
        passed = source.index('print -- "Release verification passed."', signatures)
        self.assertLess(record, signature_guard)
        self.assertLess(signature_guard, signatures)
        self.assertLess(signatures, pass_guard)
        self.assertLess(pass_guard, passed)

    def test_release_verifier_finishes_cleanup_before_pass_terminal(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "verify_release.sh").read_text(encoding="utf-8")
        cleanup = "\ncleanup_entitlements\n"
        cleanup_guard = "if (( cleanup_failed == 1 )) || [[ -e \"$entitlement_tmp\" || -L \"$entitlement_tmp\" ]]; then"
        disable_traps = "trap - EXIT INT TERM"
        passed = 'print -- "Release verification passed."'

        self.assertIn("verify_release:FAIL:final_cleanup_failed", source)
        self.assertLess(source.index(cleanup), source.index(cleanup_guard))
        self.assertLess(source.index(cleanup_guard), source.index(disable_traps))
        self.assertLess(source.index(disable_traps), source.index(passed))

    def test_release_scripts_contain_fail_closed_release_gates(self) -> None:
        helper_script = (PROJECT_ROOT / "packaging" / "build_python_helper.sh").read_text(encoding="utf-8")
        swift_script = (PROJECT_ROOT / "packaging" / "build_swift_app.sh").read_text(encoding="utf-8")
        update_service = (PROJECT_ROOT / "app" / "PhotosLocalKeywordIndexer" / "Services" / "UpdateService.swift").read_text(encoding="utf-8")
        dmg_script = (PROJECT_ROOT / "packaging" / "build_dmg.sh").read_text(encoding="utf-8")
        notary_script = (PROJECT_ROOT / "packaging" / "notarize.sh").read_text(encoding="utf-8")
        sign_script = (PROJECT_ROOT / "packaging" / "sign_app.sh").read_text(encoding="utf-8")
        verify_script = (PROJECT_ROOT / "packaging" / "verify_release.sh").read_text(encoding="utf-8")

        self.assertIn('rm -rf -- "$build_root/dist/PhotosIndexerWorker"', helper_script)
        self.assertLess(
            helper_script.index('rm -rf -- "$build_root/dist/PhotosIndexerWorker"'),
            helper_script.index('-m PyInstaller'),
        )
        self.assertIn('lipo -archs "$helper"', helper_script)
        self.assertIn('PhotosIndexerWorker must be arm64-only', helper_script)
        self.assertIn('URL(string: feed)', update_service)
        self.assertIn('feedURL.scheme?.lowercased() == "https"', update_service)
        self.assertIn('let host = feedURL.host', update_service)
        self.assertIn('!host.isEmpty', update_service)
        self.assertIn('feedURL.user == nil', update_service)
        self.assertIn('feedURL.password == nil', update_service)
        self.assertIn('!feed.contains("@")', update_service)
        self.assertIn('"example.invalid", "example.org", "example.com", "example.net"', update_service)
        self.assertIn('!Self.isPlaceholderHost(host)', update_service)
        self.assertIn('or not hostname', verify_script)
        self.assertIn("any(character in feed for character in (';', '\"', \"'\", \"\\\\\"))", verify_script)
        self.assertIn('or "@" in feed', verify_script)
        self.assertIn('if [[ -L "$helper" || ! -d "$helper" \\', swift_script)
        self.assertIn('bundled_helper_root="$app_bundle/Contents/Helpers/PhotosIndexerWorker.app"', swift_script)
        self.assertIn('App bundle copy did not produce an invocable embedded helper.', swift_script)
        self.assertIn('[[ ! -d "$bundled_helper_root" || -L "$bundled_helper_root"', swift_script)
        self.assertIn('[[ ! -x "$bundled_helper" || -L "$bundled_helper"', swift_script)
        self.assertIn('source "$project_root/packaging/sparkle_framework.zsh"', swift_script)
        self.assertIn('"$package_root/Package.resolved" "2.9.2"', swift_script)
        self.assertIn(f'"2.9.2" "{SPARKLE_REVISION}"', swift_script)
        self.assertIn('sparkle_locator_status == 3', swift_script)
        self.assertIn('sparkle_locator_status == 2', swift_script)
        self.assertIn('Sparkle.framework resolution metadata is invalid', swift_script)
        self.assertIn('Sparkle.framework is required for every app build.', swift_script)
        self.assertIn('[[ "$feed_url" != *[[:space:]]* ]]', swift_script)
        self.assertIn('[[ "$feed_url" != *"@"* ]]', swift_script)
        self.assertIn('placeholder_domains = ("example.invalid", "example.org", "example.com", "example.net")', swift_script)
        self.assertIn('[[ "$public_key" != *[[:space:]]* ]]', swift_script)
        self.assertNotIn('sparkle_framework="$candidate"', swift_script)
        self.assertIn("RELEASE_BUILD", swift_script)
        self.assertIn('source "$project_root/packaging/build_path_guard.zsh"', swift_script)
        self.assertIn('validate_build_path "$app_root" "$project_root/build" "BUILD_ROOT"', swift_script)
        self.assertIn('validate_build_path "$app_bundle" "$project_root/build" "app bundle"', swift_script)
        self.assertLess(
            swift_script.index('validate_build_path "$app_bundle"'),
            swift_script.index('rm -rf -- "$app_bundle"'),
        )
        self.assertIn("RELEASE_BUILD", dmg_script)
        self.assertIn('source "$project_root/packaging/build_path_guard.zsh"', dmg_script)
        self.assertIn('validate_build_path "$app_path" "$project_root/build" "app bundle"', dmg_script)
        self.assertIn('app_path="${app_path:a}"', dmg_script)
        self.assertLess(
            dmg_script.index('validate_build_path "$app_path"'),
            dmg_script.index('app_path="${app_path:A}"') if 'app_path="${app_path:A}"' in dmg_script else len(dmg_script),
        )
        self.assertIn('if [[ -e "$dmg_path" || -L "$dmg_path" ]]; then', dmg_script)
        self.assertIn('hdiutil imageinfo "$dmg_path"', dmg_script)
        self.assertIn('codesign --verify --verbose=2 --strict "$dmg_path"', dmg_script)
        self.assertIn('"$project_root/packaging/verify_release.sh" "$app_path"', dmg_script)
        self.assertLess(
            dmg_script.index('"$project_root/packaging/sign_app.sh" "$app_path"'),
            dmg_script.index('"$project_root/packaging/verify_release.sh" "$app_path"'),
        )
        self.assertIn("spctl --assess", dmg_script)
        self.assertIn("codesign --verify --verbose=2", notary_script)
        self.assertIn("xcrun stapler validate", notary_script)
        self.assertIn("spctl --assess", notary_script)
        self.assertIn("hdiutil attach", notary_script)
        self.assertIn("hdiutil detach", notary_script)
        self.assertIn('top_level_count=', notary_script)
        self.assertIn('readlink "$applications_link"', notary_script)
        self.assertIn('DMG root must contain only the app and Applications alias', notary_script)
        self.assertIn('verify_release.sh', notary_script)
        self.assertIn('"$script_root/verify_embedded_helper.sh" "$mounted_app"', notary_script)
        self.assertIn('verify_mounted_helper_release_metadata "$mounted_app"', notary_script)
        self.assertIn("notarize:FAIL:mounted_helper_release_metadata_invalid", notary_script)
        self.assertIn('source "$script_root/build_path_guard.zsh"', notary_script)
        self.assertIn('validate_build_path "$dmg_path" "$project_root/dist" "DMG path"', notary_script)
        self.assertIn('dmg_path="${dmg_path:a}"', notary_script)
        self.assertIn('plist_reader="/usr/bin/python3"', verify_script)
        self.assertNotIn("PLIST_READER", verify_script)
        self.assertIn('"$project_root/packaging/sign_app.sh" "$app_path"', dmg_script)
        self.assertIn('helper_entitlements', sign_script)
        self.assertIn('source "$project_root/packaging/build_path_guard.zsh"', sign_script)
        self.assertIn('validate_build_path "$app_path" "$project_root/build" "app bundle"', sign_script)
        self.assertIn('app_path="${app_path:a}"', sign_script)
        self.assertIn('--options runtime', sign_script)
        self.assertIn('--identifier "com.photoslocalkeywordindexer.worker"', sign_script)
        self.assertIn("sign_app:FAIL:helper_bundle_version_invalid", sign_script)
        self.assertNotIn("codesign --deep", sign_script)
        self.assertIn('sign_nested_bundles', sign_script)
        self.assertIn('sign_nested_bundles "$framework"', sign_script)
        self.assertIn('--preserve-metadata=identifier,entitlements', sign_script)
        self.assertIn('validate_entitlements "$signed_helper_entitlements"', verify_script)
        self.assertIn('validate_bundle_identity "$info"', verify_script)
        self.assertIn('validate_release_version "$info"', verify_script)
        self.assertIn('CFBundleShortVersionString', verify_script)
        self.assertIn('CFBundleVersion', verify_script)
        self.assertIn('com.photoslocalkeywordindexer.app', verify_script)
        self.assertIn('validate_sparkle_configuration "$info"', verify_script)
        self.assertGreaterEqual(verify_script.count('if not isinstance(values, dict):'), 5)
        self.assertIn('raise SystemExit("plist root must be a dictionary")', verify_script)
        self.assertIn('SUFeedURL', verify_script)
        self.assertIn('SUPublicEDKey', verify_script)
        self.assertIn('SUEnableAutomaticChecks', verify_script)
        self.assertIn('try:\n    parsed = urlsplit(feed)', verify_script)
        self.assertIn('except ValueError:\n    raise SystemExit("Sparkle feed is invalid")', verify_script)
        self.assertIn("parsed.port", verify_script)
        self.assertIn('placeholder_domains = ("example.invalid", "example.org", "example.com", "example.net")', verify_script)
        self.assertIn('validate_sparkle_framework "$framework"', verify_script)
        self.assertIn('org.sparkle-project.Sparkle', verify_script)
        self.assertIn('2.9.2', verify_script)
        self.assertIn('base64.b64decode', verify_script)
        self.assertIn('len(decoded_key) != 32', verify_script)
        self.assertIn('verify_code "$helper" "com.photoslocalkeywordindexer.worker"', verify_script)
        self.assertIn("verify_release:FAIL:helper_bundle_version_invalid", verify_script)
        self.assertIn('verify_code "$sparkle_binary" "org.sparkle-project.Sparkle"', verify_script)
        self.assertIn('verify_code "$main" "com.photoslocalkeywordindexer.app"', verify_script)
        self.assertIn('verify_code "$app_path" "com.photoslocalkeywordindexer.app"', verify_script)
        self.assertIn('verify_bundle_permissions "$contents"', verify_script)
        self.assertIn('-perm -002', verify_script)
        self.assertIn('-perm -020', verify_script)
        self.assertIn('verify_no_debug_entitlements "$target"', verify_script)
        self.assertIn('com.apple.security.get-task-allow', verify_script)
        self.assertIn('helper-entitlements.plist', verify_script)
        self.assertIn('verify_macho_tree', verify_script)
        self.assertIn('verify_macho_tree "$helper_app"', verify_script)
        self.assertIn('verify_macho_tree "$framework"', verify_script)
        self.assertIn('verify_macho_tree "$contents"', verify_script)
        self.assertIn('verify_bundle_tree "$framework"', verify_script)
        self.assertIn('verify_bundle_tree "$helper_app"', verify_script)
        self.assertIn('print -rl -- "${ids[@]}"', verify_script)
        self.assertIn('verify_symlink_targets', verify_script)
        self.assertIn('verify_symlink_targets', sign_script)
        self.assertIn('verify_symlink_targets "$contents"', verify_script)
        self.assertIn('verify_symlink_targets "$contents"', sign_script)

    def test_update_service_applies_automatic_check_preference_before_starting_sparkle(self) -> None:
        source = (
            PROJECT_ROOT / "app" / "PhotosLocalKeywordIndexer" / "Services" / "UpdateService.swift"
        ).read_text(encoding="utf-8")

        controller = "controller = SPUStandardUpdaterController("
        preference = "controller?.updater.automaticallyChecksForUpdates = automaticChecksEnabled"
        start = "controller?.startUpdater()"
        self.assertIn("startingUpdater: false", source)
        self.assertNotIn("startingUpdater: true", source)
        self.assertIn(start, source)
        self.assertLess(source.index(controller), source.index(preference))
        self.assertLess(source.index(preference), source.index(start))

    def test_update_service_rejects_the_pipeline_unsafe_feed_delimiters(self) -> None:
        source = (
            PROJECT_ROOT / "app" / "PhotosLocalKeywordIndexer" / "Services" / "UpdateService.swift"
        ).read_text(encoding="utf-8")
        characters = r'''private static let unsafeFeedCharacters = CharacterSet(charactersIn: ";\"'\\")'''
        guard = "feed.rangeOfCharacter(from: Self.unsafeFeedCharacters) == nil"

        self.assertIn(characters, source)
        self.assertIn(guard, source)
        self.assertLess(source.index(guard), source.index("controller = SPUStandardUpdaterController("))

    def test_update_service_rejects_whitespace_before_starting_sparkle(self) -> None:
        source = (
            PROJECT_ROOT / "app" / "PhotosLocalKeywordIndexer" / "Services" / "UpdateService.swift"
        ).read_text(encoding="utf-8")
        guard = r"!feed.contains(where: \.isWhitespace)"

        self.assertIn(guard, source)
        self.assertLess(source.index(guard), source.index("controller = SPUStandardUpdaterController("))

    def test_cleanup_scripts_handle_interrupts_after_mutation_starts(self) -> None:
        scripts = {
            "build_python_helper.sh": "cleanup_helper_output",
            "build_swift_app.sh": "cleanup_app_bundle",
            "build_dmg.sh": "cleanup_build_artifacts",
            "build_app_icon.sh": "cleanup",
            "notarize.sh": "cleanup",
            "verify_dmg_layout.sh": "cleanup",
            "verify_release.sh": "cleanup_entitlements",
        }
        for name, cleanup in scripts.items():
            source = (PROJECT_ROOT / "packaging" / name).read_text(encoding="utf-8")
            self.assertIn(
                f"trap '{cleanup}; exit 130' INT TERM",
                source,
                f"{name} must clean temporary or partial output on SIGINT/SIGTERM",
            )

    def test_verify_release_sanitizes_plist_lint_failures(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "verify_release.sh").read_text(encoding="utf-8")

        self.assertIn('if ! plutil -lint "$info" >/dev/null 2>&1; then', source)
        self.assertIn('verify_release:FAIL:info_plist_invalid', source)
        self.assertIn('if ! plutil -lint "$entitlements" >/dev/null 2>&1; then', source)
        self.assertIn('verify_release:FAIL:entitlements_plist_invalid', source)

    def test_build_dmg_validates_output_and_collision_before_signing(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "build_dmg.sh").read_text(encoding="utf-8")

        output_validation = source.index('case "$output_root" in')
        artifact_collision = source.index('if [[ -e "$dmg_path" || -L "$dmg_path" ]]; then')
        signing = source.index('"$project_root/packaging/sign_app.sh" "$app_path"')

        self.assertLess(output_validation, signing)
        self.assertLess(artifact_collision, signing)

    def test_release_dmg_requires_layout_verification(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "build_dmg.sh").read_text(encoding="utf-8")

        release_layout_gate = 'if [[ "$release_build" == "1" && "$verify_dmg_layout" != "1" ]]; then'

        self.assertIn(release_layout_gate, source)
        self.assertIn("build_dmg:FAIL:dmg_layout_verification_required", source)
        self.assertLess(source.index(release_layout_gate), source.index("hdiutil create"))

    def test_notarize_verifies_dmg_layout_before_submitting(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "notarize.sh").read_text(encoding="utf-8")

        layout_verification = source.index(
            '"$script_root/verify_dmg_layout.sh" "$dmg_path" >/dev/null'
        )
        notary_submission = source.index("xcrun notarytool submit")

        self.assertLess(layout_verification, notary_submission)

    def test_notarize_sanitizes_hdiutil_validation_errors(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "notarize.sh").read_text(encoding="utf-8")

        imageinfo_gate = source.index('if ! hdiutil imageinfo "$dmg_path" >/dev/null 2>&1; then')
        invalid_image_failure = source.index('notarize:FAIL:dmg_image_invalid')
        invalid_image_hint = source.index(
            'notarize:HINT:dmg_image_invalid:rebuild_and_verify_the_dmg_then_retry'
        )
        layout_verification = source.index('"$script_root/verify_dmg_layout.sh" "$dmg_path"')

        self.assertLess(imageinfo_gate, invalid_image_failure)
        self.assertLess(invalid_image_failure, invalid_image_hint)
        self.assertLess(invalid_image_hint, layout_verification)

    def test_notarize_verifies_mounted_app_before_submitting(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "notarize.sh").read_text(encoding="utf-8")

        helper_metadata_verification = source.index(
            'verify_mounted_helper_release_metadata "$mounted_app"'
        )
        helper_verification = source.index(
            '"$script_root/verify_embedded_helper.sh" "$mounted_app" >/dev/null'
        )
        mounted_app_verification = source.index(
            '"$script_root/verify_release.sh" "$mounted_app" >/dev/null'
        )
        notary_submission = source.index("xcrun notarytool submit")

        self.assertLess(helper_metadata_verification, helper_verification)
        self.assertLess(helper_verification, notary_submission)
        self.assertLess(mounted_app_verification, notary_submission)

    def test_notarize_rejects_a_stale_mounted_helper_before_submitting(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "notarize.sh").read_text(encoding="utf-8")

        fingerprint_gate = 'verify_mounted_helper_source_fingerprint "$mounted_app"'
        notary_submission = "xcrun notarytool submit"

        self.assertIn(fingerprint_gate, source)
        self.assertIn("notarize:FAIL:helper_source_fingerprint_stale", source)
        self.assertIn(".photos-indexer-source-fingerprint", source)
        self.assertLess(source.index(fingerprint_gate), source.index(notary_submission))

    def test_notarize_preflights_spctl_before_mounting_or_submitting(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "notarize.sh").read_text(encoding="utf-8")

        self.assertIn("for command_name in hdiutil codesign spctl xcrun; do", source)
        self.assertLess(source.index("spctl"), source.index('hdiutil imageinfo "$dmg_path"'))
        self.assertLess(source.index("spctl"), source.index("xcrun notarytool submit"))

    def test_notarize_rejects_hardlinked_dmg_before_inspecting_or_mutating_it(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "notarize.sh").read_text(encoding="utf-8")

        self.assertIn("dmg_hardlink", source)
        self.assertIn("os.stat", source)
        self.assertLess(source.index("dmg_hardlink"), source.index('hdiutil imageinfo "$dmg_path"'))

    def test_notarize_rechecks_the_uploaded_dmg_before_stapling(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "notarize.sh").read_text(encoding="utf-8")

        self.assertIn("record_notarized_dmg_fingerprint()", source)
        self.assertIn("notarized_dmg_fingerprint=", source)
        self.assertIn("dmg_matches_notarized_fingerprint()", source)
        self.assertIn("notarize:FAIL:dmg_changed_after_submission", source)
        self.assertLess(
            source.index("notarized_dmg_fingerprint="),
            source.index("xcrun notarytool submit"),
        )
        self.assertLess(
            source.index("dmg_matches_notarized_fingerprint()"),
            source.index("xcrun stapler staple"),
        )

    def test_notarize_serializes_mutation_and_cleans_its_lock(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "notarize.sh").read_text(encoding="utf-8")

        self.assertIn('notarize_lock="${dmg_path}.notarize.lock"', source)
        self.assertIn('if ! mkdir "$notarize_lock" 2>/dev/null; then', source)
        self.assertIn("notarize:FAIL:already_running", source)
        self.assertIn("notarize_lock_acquired=1", source)
        self.assertIn('rmdir "$notarize_lock"', source)
        self.assertLess(source.index('if ! mkdir "$notarize_lock" 2>/dev/null; then'), source.index('xcrun stapler staple'))

    def test_notarize_reports_stale_locks_without_deleting_an_active_lock(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "notarize.sh").read_text(encoding="utf-8")

        self.assertIn('notarize_lock_owner="$notarize_lock/owner"', source)
        self.assertIn("set -o noclobber", source)
        self.assertIn('print -r -- "$$" > "$notarize_lock_owner"', source)
        self.assertIn("notarize:FAIL:lock_owner_write", source)
        self.assertIn("notarize:FAIL:stale_lock", source)
        self.assertIn("notarize:HINT:stale_lock:verify_no_notarization_then_remove_lock", source)
        self.assertIn('kill -0 "$owner_pid"', source)
        self.assertIn('rm -f -- "$notarize_lock_owner"', source)

    def test_notarize_lock_is_private_under_a_permissive_caller_umask(self) -> None:
        script = PROJECT_ROOT / "packaging" / "notarize.sh"
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "dist") as tmp:
            root = Path(tmp)
            dmg = root / "PhotosLocalKeywordIndexer-1.2.3-45.dmg"
            dmg.write_bytes(b"notary lock permissions")
            fake_bin = root / "bin"
            fake_bin.mkdir()
            observed = root / "lock-modes"
            fake_hdiutil = fake_bin / "hdiutil"
            fake_hdiutil.write_text(
                "#!/bin/zsh\n"
                'print -- "$(stat -f \'%Lp\' -- "$DMG_PATH.notarize.lock"):'
                '$(stat -f \'%Lp\' -- "$DMG_PATH.notarize.lock/owner")" > "$LOCK_MODES"\n'
                "exit 1\n",
                encoding="utf-8",
            )
            fake_hdiutil.chmod(0o755)
            for command_name in ("codesign", "spctl"):
                command = fake_bin / command_name
                command.write_text("#!/bin/zsh\nexit 0\n", encoding="utf-8")
                command.chmod(0o755)
            fake_xcrun = fake_bin / "xcrun"
            fake_xcrun.write_text(
                '#!/bin/zsh\n[[ "$1" == "--find" && "$2" == "notarytool" ]] && exit 0\nexit 99\n',
                encoding="utf-8",
            )
            fake_xcrun.chmod(0o755)
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{fake_bin}:/bin:/usr/bin",
                    "APPLE_NOTARY_PROFILE": "local-profile",
                    "DMG_PATH": str(dmg),
                    "LOCK_MODES": str(observed),
                }
            )

            result = subprocess.run(
                [
                    "/bin/zsh",
                    "-c",
                    'umask 000; exec /bin/zsh "$1" "$2"',
                    "notarize-umask-test",
                    str(script),
                    str(dmg),
                ],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(observed.read_text(encoding="utf-8").strip(), "700:600")

    def test_notarize_early_cleanup_failure_does_not_echo_the_lock_path(self) -> None:
        script = PROJECT_ROOT / "packaging" / "notarize.sh"
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "dist") as tmp:
            root = Path(tmp)
            dmg = root / "PhotosLocalKeywordIndexer-1.2.3-45.dmg"
            dmg.write_bytes(b"invalid image")
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_hdiutil = fake_bin / "hdiutil"
            fake_hdiutil.write_text("#!/bin/zsh\nexit 1\n", encoding="utf-8")
            fake_hdiutil.chmod(0o755)
            fake_rm = fake_bin / "rm"
            fake_rm.write_text(
                "#!/bin/zsh\n"
                "print -u2 -- \"${@[-1]}\"\n"
                "exit 1\n",
                encoding="utf-8",
            )
            fake_rm.chmod(0o755)
            for command_name in ("codesign", "spctl"):
                command = fake_bin / command_name
                command.write_text("#!/bin/zsh\nexit 0\n", encoding="utf-8")
                command.chmod(0o755)
            fake_xcrun = fake_bin / "xcrun"
            fake_xcrun.write_text(
                '#!/bin/zsh\n[[ "$1" == "--find" && "$2" == "notarytool" ]] && exit 0\nexit 99\n',
                encoding="utf-8",
            )
            fake_xcrun.chmod(0o755)
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{fake_bin}:/bin:/usr/bin",
                    "APPLE_NOTARY_PROFILE": "local-profile",
                }
            )

            result = subprocess.run(
                ["/bin/zsh", str(script), str(dmg)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertEqual(result.returncode, 2)
            self.assertEqual(
                result.stderr,
                "notarize:FAIL:dmg_image_invalid\n"
                "notarize:HINT:dmg_image_invalid:rebuild_and_verify_the_dmg_then_retry\n",
            )
            self.assertNotIn(str(root), result.stdout + result.stderr)

    def test_notarize_hardlink_gate_preserves_the_shared_inode(self) -> None:
        script = PROJECT_ROOT / "packaging" / "notarize.sh"
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "dist") as tmp:
            root = Path(tmp)
            dmg = root / "PhotosLocalKeywordIndexer-1.2.3-45.dmg"
            external = root / "release-copy.dmg"
            external.write_bytes(b"same inode must not be stapled")
            os.link(external, dmg)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            marker = root / "validation-tool-invoked"
            for command_name in ("hdiutil", "codesign", "spctl"):
                command = fake_bin / command_name
                command.write_text(f"#!/bin/zsh\n: > '{marker}'\nexit 0\n", encoding="utf-8")
                command.chmod(0o755)
            fake_xcrun = fake_bin / "xcrun"
            fake_xcrun.write_text(
                "#!/bin/zsh\n"
                "if [[ \"$1\" == \"--find\" && \"$2\" == \"notarytool\" ]]; then exit 0; fi\n"
                "exit 99\n",
                encoding="utf-8",
            )
            fake_xcrun.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
            environment["APPLE_NOTARY_PROFILE"] = "local-profile"

            result = subprocess.run(
                ["/bin/zsh", str(script), str(dmg)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("notarize:FAIL:dmg_hardlink", result.stderr)
            self.assertFalse(marker.exists(), "a hardlinked artifact must fail before validation tools")
            self.assertEqual(dmg.stat().st_ino, external.stat().st_ino)
            self.assertEqual(dmg.read_bytes(), b"same inode must not be stapled")

    def test_notarize_rejects_group_writable_dmg_before_inspection_or_submission(self) -> None:
        """A mutable release artifact must not enter the notarization window."""
        script = PROJECT_ROOT / "packaging" / "notarize.sh"
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "dist") as tmp:
            root = Path(tmp)
            dmg = root / "PhotosLocalKeywordIndexer-1.2.3-45.dmg"
            dmg.write_bytes(b"release artifact must be immutable to other users")
            dmg.chmod(0o664)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            marker = root / "validation-tool-invoked"
            for command_name in ("hdiutil", "codesign", "spctl"):
                command = fake_bin / command_name
                command.write_text(
                    f"#!/bin/zsh\n: > '{marker}'\nexit 0\n", encoding="utf-8"
                )
                command.chmod(0o755)
            fake_xcrun = fake_bin / "xcrun"
            fake_xcrun.write_text(
                "#!/bin/zsh\n"
                'if [[ "$1" == "--find" && "$2" == "notarytool" ]]; then exit 0; fi\n'
                "exit 99\n",
                encoding="utf-8",
            )
            fake_xcrun.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = str(fake_bin)
            environment["APPLE_NOTARY_PROFILE"] = "local-profile"

            result = subprocess.run(
                ["/bin/zsh", str(script), str(dmg)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

        self.assertEqual(result.returncode, 2)
        self.assertIn("notarize:FAIL:dmg_permissions", result.stderr)
        self.assertFalse(marker.exists(), "a mutable DMG must fail before validation tools")

    def test_notarize_rejects_a_shared_writable_dmg_directory_before_inspection(self) -> None:
        """A local peer must not be able to replace the DMG during notarization."""
        script = PROJECT_ROOT / "packaging" / "notarize.sh"
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "dist") as tmp:
            root = Path(tmp)
            dmg = root / "PhotosLocalKeywordIndexer-1.2.3-45.dmg"
            dmg.write_bytes(b"release artifact in an unsafe directory")
            fake_bin = root / "bin"
            fake_bin.mkdir()
            marker = root / "validation-tool-invoked"
            for command_name in ("hdiutil", "codesign", "spctl"):
                command = fake_bin / command_name
                command.write_text(
                    f"#!/bin/zsh\n: > '{marker}'\nexit 0\n", encoding="utf-8"
                )
                command.chmod(0o755)
            fake_xcrun = fake_bin / "xcrun"
            fake_xcrun.write_text(
                "#!/bin/zsh\n"
                'if [[ "$1" == "--find" && "$2" == "notarytool" ]]; then exit 0; fi\n'
                "exit 99\n",
                encoding="utf-8",
            )
            fake_xcrun.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
            environment["APPLE_NOTARY_PROFILE"] = "local-profile"
            root.chmod(0o777)
            try:
                result = subprocess.run(
                    ["/bin/zsh", str(script), str(dmg)],
                    check=False,
                    capture_output=True,
                    text=True,
                    env=environment,
                )
            finally:
                root.chmod(0o700)

        self.assertEqual(result.returncode, 2)
        self.assertIn("notarize:FAIL:dmg_parent_permissions", result.stderr)
        self.assertFalse(marker.exists(), "an unsafe parent must fail before validation tools")

    def test_notarize_existing_evidence_error_is_path_free_and_preflight_only(self) -> None:
        script = PROJECT_ROOT / "packaging" / "notarize.sh"
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "dist") as tmp:
            root = Path(tmp)
            dmg = root / "PhotosLocalKeywordIndexer-1.2.3-45.dmg"
            dmg.write_bytes(b"valid artifact placeholder")
            evidence = root / "PhotosLocalKeywordIndexer-1.2.3-45.release-evidence.json"
            evidence.write_text("existing evidence", encoding="utf-8")
            fake_bin = root / "bin"
            fake_bin.mkdir()
            marker = root / "validation-tool-invoked"
            for command_name in ("hdiutil", "codesign", "spctl"):
                command = fake_bin / command_name
                command.write_text(f"#!/bin/zsh\n: > '{marker}'\nexit 0\n", encoding="utf-8")
                command.chmod(0o755)
            fake_xcrun = fake_bin / "xcrun"
            fake_xcrun.write_text(
                "#!/bin/zsh\n"
                "if [[ \"$1\" == \"--find\" && \"$2\" == \"notarytool\" ]]; then exit 0; fi\n"
                "exit 99\n",
                encoding="utf-8",
            )
            fake_xcrun.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = str(fake_bin)
            environment["APPLE_NOTARY_PROFILE"] = "local-profile"

            result = subprocess.run(
                ["/bin/zsh", str(script), str(dmg)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertEqual(result.returncode, 2)
            self.assertEqual(
                result.stderr,
                "notarize:FAIL:evidence_exists\n"
                "notarize:HINT:evidence_exists:review_or_remove_existing_evidence_then_retry\n",
            )
            self.assertNotIn(str(root), result.stdout + result.stderr)
            self.assertFalse(marker.exists(), "existing evidence must fail before DMG inspection")

    def test_sign_app_checks_bundle_ownership_and_hardlinks_before_codesign(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "sign_app.sh").read_text(encoding="utf-8")

        self.assertIn("bundle_hardlink_check", source)
        self.assertIn("os.stat(candidate, follow_symlinks=False)", source)
        self.assertIn("sign_app:FAIL:bundle_hardlink", source)
        self.assertIn("sign_app:FAIL:bundle_ownership", source)
        self.assertIn("metadata.st_mode & 0o022", source)
        self.assertIn("sign_app:FAIL:bundle_permissions", source)
        self.assertLess(source.index("bundle_hardlink_check"), source.index("sign_tree_inner_first"))

    def test_sign_app_rechecks_bundle_identity_before_signing_and_ready(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "sign_app.sh").read_text(encoding="utf-8")
        guard = (
            "if ! app_bundle_is_owned; then\n"
            '  print -u2 -- "sign_app:FAIL:app_bundle_identity_changed"\n'
            '  print -u2 -- "sign_app:HINT:app_bundle_identity_changed:rebuild_the_app_then_retry"\n'
            "  exit 1\n"
            "fi\n"
        )

        record = source.index("record_app_bundle_identity\n")
        signing_guard = source.index(guard, record)
        signing = source.index('sign_tree_inner_first "$helper_app"', record)
        ready_guard = source.index(guard, signing_guard + len(guard))
        ready = source.index('print -- "release_signature:READY_FOR_DMG"', signing)
        self.assertLess(record, signing_guard)
        self.assertLess(signing_guard, signing)
        self.assertLess(signing, ready_guard)
        self.assertLess(ready_guard, ready)

    def test_sign_app_rejects_a_missing_bundle_without_leaking_local_paths(self) -> None:
        script = PROJECT_ROOT / "packaging" / "sign_app.sh"
        build_root = PROJECT_ROOT / "build"
        build_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build_root) as tmp:
            root = Path(tmp)
            app = root / "PhotosLocalKeywordIndexer.app"
            fake_bin = root / "bin"
            fake_bin.mkdir()
            marker = root / "codesign-invoked"
            fake_codesign = fake_bin / "codesign"
            fake_codesign.write_text(f"#!/bin/zsh\n: > '{marker}'\nexit 0\n", encoding="utf-8")
            fake_codesign.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
            environment["DEVELOPER_ID_APPLICATION"] = "local-identity"

            result = subprocess.run(
                ["/bin/zsh", str(script), str(app)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertFalse(marker.exists(), "invalid input must fail before codesign")

        self.assertEqual(result.returncode, 2)
        self.assertEqual(
            result.stderr,
            "sign_app:FAIL:app_bundle_path_invalid\n"
            "sign_app:HINT:app_bundle_path_invalid:provide_a_complete_app_bundle_under_build\n",
        )
        self.assertNotIn(str(PROJECT_ROOT), result.stderr)
        self.assertNotIn(str(app), result.stderr)

    def test_sign_app_rejects_a_stale_helper_source_fingerprint_before_codesign(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "sign_app.sh").read_text(encoding="utf-8")

        stale_guard = "sign_app:FAIL:helper_source_fingerprint_stale"
        self.assertIn(stale_guard, source)
        self.assertIn(".photos-indexer-source-fingerprint", source)
        self.assertLess(source.index(stale_guard), source.index('sign_tree_inner_first "$helper_app"'))

    def test_sign_app_rejects_a_helper_bundle_with_mismatched_release_metadata_before_codesign(self) -> None:
        script = PROJECT_ROOT / "packaging" / "sign_app.sh"
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "build") as tmp:
            root = Path(tmp)
            app = root / "PhotosLocalKeywordIndexer.app"
            contents = app / "Contents"
            helper_app, _ = self._create_embedded_helper_bundle(
                contents / "Helpers" / "PhotosIndexerWorker.app",
                helper_script="#!/bin/zsh\nexit 0\n",
            )
            (contents / "MacOS").mkdir(parents=True)
            (contents / "Frameworks" / "Sparkle.framework").mkdir(parents=True)
            main = contents / "MacOS" / "PhotosLocalKeywordIndexer"
            main.write_bytes(b"main")
            main.chmod(0o755)
            with (contents / "Info.plist").open("wb") as stream:
                plistlib.dump(
                    {
                        "CFBundleIdentifier": "com.photoslocalkeywordindexer.app",
                        "CFBundleExecutable": "PhotosLocalKeywordIndexer",
                        "CFBundlePackageType": "APPL",
                        "CFBundleIconFile": "AppIcon.icns",
                        "LSMinimumSystemVersion": "14.0",
                        "CFBundleShortVersionString": "1.2.3",
                        "CFBundleVersion": "45",
                    },
                    stream,
                )
            helper_info = helper_app / "Contents" / "Info.plist"
            helper_values = plistlib.loads(helper_info.read_bytes())
            helper_values["CFBundleShortVersionString"] = "9.9.9"
            helper_values["CFBundleVersion"] = "999"
            helper_info.write_bytes(plistlib.dumps(helper_values))
            fake_bin = root / "bin"
            fake_bin.mkdir()
            marker = root / "codesign-invoked"
            for name, body in {
                "codesign": f"#!/bin/zsh\n: > '{marker}'\nexit 0\n",
                "file": '#!/bin/zsh\nprint -- "Mach-O 64-bit executable arm64"\n',
                "find": '#!/bin/zsh\n/usr/bin/find "$@"\n',
                "readlink": '#!/bin/zsh\n/bin/readlink "$@"\n',
                "plutil": '#!/bin/zsh\nexit 0\n',
                "lipo": '#!/bin/zsh\nprint -- arm64\n',
                "spctl": '#!/bin/zsh\nexit 0\n',
            }.items():
                tool = fake_bin / name
                tool.write_text(body, encoding="utf-8")
                tool.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{fake_bin}:/usr/bin:/bin:/usr/sbin:/sbin"
            environment["DEVELOPER_ID_APPLICATION"] = "local-identity"

            result = subprocess.run(
                ["/bin/zsh", str(script), str(app)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

        self.assertEqual(result.returncode, 2)
        self.assertIn("sign_app:FAIL:helper_bundle_version_invalid", result.stderr)
        self.assertFalse(marker.exists(), "mismatched helper metadata must fail before codesign")

    def test_sign_app_rejects_hardlinked_bundle_content_before_codesign(self) -> None:
        script = PROJECT_ROOT / "packaging" / "sign_app.sh"
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "build") as tmp:
            root = Path(tmp)
            app = root / "PhotosLocalKeywordIndexer.app"
            contents = app / "Contents"
            (contents / "MacOS").mkdir(parents=True)
            self._create_embedded_helper_bundle(
                contents / "Helpers" / "PhotosIndexerWorker.app",
                helper_script="#!/bin/zsh\nexit 0\n",
            )
            (contents / "Frameworks" / "Sparkle.framework").mkdir(parents=True)
            main = contents / "MacOS" / "PhotosLocalKeywordIndexer"
            external = root / "external-main"
            external.write_bytes(b"shared executable")
            os.link(external, main)
            main.chmod(0o755)
            with (contents / "Info.plist").open("wb") as stream:
                plistlib.dump(
                    {
                        "CFBundleIdentifier": "com.photoslocalkeywordindexer.app",
                        "CFBundleExecutable": "PhotosLocalKeywordIndexer",
                        "CFBundlePackageType": "APPL",
                        "CFBundleShortVersionString": "1.2.3",
                        "CFBundleVersion": "45",
                    },
                    stream,
                )
            fake_bin = root / "bin"
            fake_bin.mkdir()
            marker = root / "codesign-invoked"
            fake_codesign = fake_bin / "codesign"
            fake_codesign.write_text(f"#!/bin/zsh\n: > '{marker}'\nexit 0\n", encoding="utf-8")
            fake_codesign.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
            environment["DEVELOPER_ID_APPLICATION"] = "local-identity"

            result = subprocess.run(
                ["/bin/zsh", str(script), str(app)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("sign_app:FAIL:bundle_hardlink", result.stderr)
            self.assertFalse(marker.exists(), "hardlinked bundle content must fail before codesign")
            self.assertEqual(main.stat().st_ino, external.stat().st_ino)
            self.assertEqual(main.read_bytes(), b"shared executable")

    def test_sign_app_symlink_error_is_path_free_before_codesign(self) -> None:
        script = PROJECT_ROOT / "packaging" / "sign_app.sh"
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "build") as tmp:
            root = Path(tmp)
            app = root / "PhotosLocalKeywordIndexer.app"
            contents = app / "Contents"
            (contents / "MacOS").mkdir(parents=True)
            self._create_embedded_helper_bundle(
                contents / "Helpers" / "PhotosIndexerWorker.app",
                helper_script="#!/bin/zsh\nexit 0\n",
            )
            (contents / "Frameworks" / "Sparkle.framework").mkdir(parents=True)
            external = root / "external-main"
            external.write_bytes(b"symlinked executable")
            external.chmod(0o755)
            main = contents / "MacOS" / "PhotosLocalKeywordIndexer"
            main.symlink_to(external)
            with (contents / "Info.plist").open("wb") as stream:
                plistlib.dump(
                    {
                        "CFBundleIdentifier": "com.photoslocalkeywordindexer.app",
                        "CFBundleExecutable": "PhotosLocalKeywordIndexer",
                        "CFBundlePackageType": "APPL",
                        "CFBundleShortVersionString": "1.2.3",
                        "CFBundleVersion": "45",
                    },
                    stream,
                )
            fake_bin = root / "bin"
            fake_bin.mkdir()
            marker = root / "codesign-invoked"
            fake_codesign = fake_bin / "codesign"
            fake_codesign.write_text(f"#!/bin/zsh\n: > '{marker}'\nexit 0\n", encoding="utf-8")
            fake_codesign.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
            environment["DEVELOPER_ID_APPLICATION"] = "local-identity"

            result = subprocess.run(
                ["/bin/zsh", str(script), str(app)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("symlink", result.stderr.lower())
            self.assertNotIn(str(app), result.stderr)
            self.assertFalse(marker.exists(), "symlinked bundle content must fail before codesign")

    def test_sign_app_rejects_an_escaping_nested_symlink_without_echoing_its_path(self) -> None:
        script = PROJECT_ROOT / "packaging" / "sign_app.sh"
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "build") as tmp:
            root = Path(tmp)
            app = root / "PhotosLocalKeywordIndexer.app"
            contents = app / "Contents"
            (contents / "MacOS").mkdir(parents=True)
            helper_root, _ = self._create_embedded_helper_bundle(
                contents / "Helpers" / "PhotosIndexerWorker.app",
                helper_script="#!/bin/zsh\nexit 0\n",
            )
            (contents / "Frameworks" / "Sparkle.framework").mkdir(parents=True)
            main = contents / "MacOS" / "PhotosLocalKeywordIndexer"
            main.write_bytes(b"main")
            main.chmod(0o755)
            with (contents / "Info.plist").open("wb") as stream:
                plistlib.dump(
                    {
                        "CFBundleIdentifier": "com.photoslocalkeywordindexer.app",
                        "CFBundleExecutable": "PhotosLocalKeywordIndexer",
                        "CFBundlePackageType": "APPL",
                        "CFBundleShortVersionString": "1.2.3",
                        "CFBundleVersion": "45",
                    },
                    stream,
                )
            external = root / "external-payload"
            external.write_bytes(b"outside")
            (helper_root / "libpython.dylib").symlink_to(external)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            marker = root / "codesign-invoked"
            fake_codesign = fake_bin / "codesign"
            fake_codesign.write_text(f"#!/bin/zsh\n: > '{marker}'\nexit 0\n", encoding="utf-8")
            fake_codesign.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{fake_bin}:/usr/bin:/bin:/usr/sbin:/sbin"
            environment["DEVELOPER_ID_APPLICATION"] = "local-identity"

            result = subprocess.run(
                ["/bin/zsh", str(script), str(app)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("symlink", result.stderr.lower())
        self.assertNotIn(str(app), result.stderr)
        self.assertFalse(marker.exists(), "escaping symlink must fail before codesign")

    def test_sign_app_preflights_verify_tools_before_any_codesign(self) -> None:
        script = PROJECT_ROOT / "packaging" / "sign_app.sh"
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "build") as tmp:
            root = Path(tmp)
            app = root / "PhotosLocalKeywordIndexer.app"
            contents = app / "Contents"
            (contents / "MacOS").mkdir(parents=True)
            helper_root, _ = self._create_embedded_helper_bundle(
                contents / "Helpers" / "PhotosIndexerWorker.app",
                helper_script="#!/bin/zsh\nexit 0\n",
            )
            (contents / "Frameworks" / "Sparkle.framework").mkdir(parents=True)
            for executable in (
                contents / "MacOS" / "PhotosLocalKeywordIndexer",
                helper_root / "Contents" / "MacOS" / "PhotosIndexerWorker",
            ):
                executable.write_bytes(b"mach-o")
                executable.chmod(0o755)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            marker = root / "codesign-invoked"
            fake_codesign = fake_bin / "codesign"
            fake_codesign.write_text(
                "#!/bin/zsh\n"
                'if [[ "$1" == "-d" ]]; then exit 1; fi\n'
                ': > "$CODESIGN_MARKER"\n'
                "exit 0\n",
                encoding="utf-8",
            )
            fake_codesign.chmod(0o755)
            fake_file = fake_bin / "file"
            fake_file.write_text("#!/bin/zsh\nprint -- 'Mach-O 64-bit'\n", encoding="utf-8")
            fake_file.chmod(0o755)
            fake_lipo = fake_bin / "lipo"
            fake_lipo.write_text("#!/bin/zsh\nprint -- arm64\n", encoding="utf-8")
            fake_lipo.chmod(0o755)
            fake_plutil = fake_bin / "plutil"
            fake_plutil.write_text("#!/bin/zsh\nexit 0\n", encoding="utf-8")
            fake_plutil.chmod(0o755)
            for command_name in ("find", "readlink", "grep", "awk", "sort", "cut"):
                (fake_bin / command_name).symlink_to(f"/usr/bin/{command_name}")
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": str(fake_bin),
                    "DEVELOPER_ID_APPLICATION": "local-identity",
                    "CODESIGN_MARKER": str(marker),
                }
            )

            result = subprocess.run(
                ["/bin/zsh", str(script), str(app)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            codesign_invoked = marker.exists()

        self.assertEqual(result.returncode, 1)
        self.assertEqual(
            result.stderr,
            "Required release verification tool is unavailable: spctl\n",
        )
        self.assertFalse(codesign_invoked, "missing verify tools must fail before codesign")

    def test_sign_app_rejects_group_or_world_writable_bundle_content_before_codesign(self) -> None:
        script = PROJECT_ROOT / "packaging" / "sign_app.sh"
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "build") as tmp:
            root = Path(tmp)
            app = root / "PhotosLocalKeywordIndexer.app"
            contents = app / "Contents"
            (contents / "MacOS").mkdir(parents=True)
            self._create_embedded_helper_bundle(
                contents / "Helpers" / "PhotosIndexerWorker.app",
                helper_script="#!/bin/zsh\nexit 0\n",
            )
            (contents / "Frameworks" / "Sparkle.framework").mkdir(parents=True)
            main = contents / "MacOS" / "PhotosLocalKeywordIndexer"
            main.write_bytes(b"writable executable")
            main.chmod(0o775)
            with (contents / "Info.plist").open("wb") as stream:
                plistlib.dump(
                    {
                        "CFBundleIdentifier": "com.photoslocalkeywordindexer.app",
                        "CFBundleExecutable": "PhotosLocalKeywordIndexer",
                        "CFBundlePackageType": "APPL",
                        "CFBundleShortVersionString": "1.2.3",
                        "CFBundleVersion": "45",
                    },
                    stream,
                )
            fake_bin = root / "bin"
            fake_bin.mkdir()
            marker = root / "codesign-invoked"
            fake_codesign = fake_bin / "codesign"
            fake_codesign.write_text(f"#!/bin/zsh\n: > '{marker}'\nexit 0\n", encoding="utf-8")
            fake_codesign.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
            environment["DEVELOPER_ID_APPLICATION"] = "local-identity"

            result = subprocess.run(
                ["/bin/zsh", str(script), str(app)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("sign_app:FAIL:bundle_permissions", result.stderr)
            self.assertFalse(marker.exists(), "unsafe bundle permissions must fail before codesign")

    def test_sign_app_rejects_a_group_writable_app_root_before_codesign(self) -> None:
        script = PROJECT_ROOT / "packaging" / "sign_app.sh"
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "build") as tmp:
            root = Path(tmp)
            app = root / "PhotosLocalKeywordIndexer.app"
            contents = app / "Contents"
            (contents / "MacOS").mkdir(parents=True)
            self._create_embedded_helper_bundle(
                contents / "Helpers" / "PhotosIndexerWorker.app",
                helper_script="#!/bin/zsh\nexit 0\n",
            )
            (contents / "Frameworks" / "Sparkle.framework").mkdir(parents=True)
            main = contents / "MacOS" / "PhotosLocalKeywordIndexer"
            main.write_bytes(b"main")
            main.chmod(0o755)
            with (contents / "Info.plist").open("wb") as stream:
                plistlib.dump(
                    {
                        "CFBundleIdentifier": "com.photoslocalkeywordindexer.app",
                        "CFBundleExecutable": "PhotosLocalKeywordIndexer",
                        "CFBundlePackageType": "APPL",
                        "CFBundleShortVersionString": "1.2.3",
                        "CFBundleVersion": "45",
                    },
                    stream,
                )
            app.chmod(0o775)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            marker = root / "codesign-invoked"
            fake_codesign = fake_bin / "codesign"
            fake_codesign.write_text(
                f"#!/bin/zsh\n: > '{marker}'\nexit 0\n",
                encoding="utf-8",
            )
            fake_codesign.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
            environment["DEVELOPER_ID_APPLICATION"] = "local-identity"

            try:
                result = subprocess.run(
                    ["/bin/zsh", str(script), str(app)],
                    check=False,
                    capture_output=True,
                    text=True,
                    env=environment,
                )
            finally:
                app.chmod(0o755)

            self.assertEqual(result.returncode, 2)
            self.assertIn("sign_app:FAIL:bundle_permissions", result.stderr)
            self.assertFalse(marker.exists(), "unsafe app root must fail before codesign")

    def test_build_dmg_checks_hdiutil_before_signing(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "build_dmg.sh").read_text(encoding="utf-8")

        hdiutil_check = source.index('command -v hdiutil')
        signing = source.index('"$project_root/packaging/sign_app.sh" "$app_path"')

        self.assertLess(hdiutil_check, signing)

    def test_build_dmg_checks_copy_tools_before_signing(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "build_dmg.sh").read_text(encoding="utf-8")

        signing = source.index('"$project_root/packaging/sign_app.sh" "$app_path"')
        tool_preflight = "for command_name in ditto ln; do"
        self.assertIn(tool_preflight, source)
        self.assertLess(source.index(tool_preflight), signing)

    def test_build_dmg_missing_copy_tool_does_not_sign_or_reserve(self) -> None:
        script = PROJECT_ROOT / "packaging" / "build_dmg.sh"
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "build") as tmp:
            root = Path(tmp)
            app = root / "PhotosLocalKeywordIndexer.app"
            info = app / "Contents" / "Info.plist"
            main = app / "Contents" / "MacOS" / "PhotosLocalKeywordIndexer"
            main.parent.mkdir(parents=True)
            info.write_bytes(b"not reached")
            main.write_bytes(b"must not be signed")
            main.chmod(0o755)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_hdiutil = fake_bin / "hdiutil"
            fake_hdiutil.write_text("#!/bin/zsh\nexit 0\n", encoding="utf-8")
            fake_hdiutil.chmod(0o755)
            output_root = PROJECT_ROOT / "dist" / root.name
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": str(fake_bin),
                    "RELEASE_BUILD": "1",
                    "DEVELOPER_ID_APPLICATION": "local-identity",
                }
            )

            result = subprocess.run(
                ["/bin/zsh", str(script), str(app), str(output_root)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("ditto", result.stderr)
            self.assertEqual(main.read_bytes(), b"must not be signed")
            self.assertFalse(output_root.exists(), "preflight failure must not create an output directory")

    def test_notarize_rejects_a_misnamed_release_artifact_before_validation_tools(self) -> None:
        script = PROJECT_ROOT / "packaging" / "notarize.sh"
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "dist") as tmp:
            root = Path(tmp)
            dmg = root / "PhotosLocalKeywordIndexer-latest.dmg"
            dmg.write_bytes(b"not-a-real-disk-image")
            marker = root / "hdiutil-invoked"
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_hdiutil = fake_bin / "hdiutil"
            fake_hdiutil.write_text(
                "#!/bin/zsh\n"
                ": > \"$HDIUTIL_MARKER\"\n"
                "exit 0\n",
                encoding="utf-8",
            )
            fake_hdiutil.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
            environment["APPLE_NOTARY_PROFILE"] = "local-profile"
            environment["HDIUTIL_MARKER"] = str(marker)

            result = subprocess.run(
                ["zsh", str(script), str(dmg)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertEqual(result.returncode, 2)
            self.assertEqual(
                result.stderr,
                "notarize:FAIL:dmg_filename_invalid\n"
                "notarize:HINT:dmg_filename_invalid:rebuild_with_version_and_build_filename\n",
            )
            self.assertFalse(marker.exists(), "a misnamed artifact must fail before hdiutil or notarytool")

    def test_notarize_rejects_invalid_dmg_paths_without_leaking_them(self) -> None:
        script = PROJECT_ROOT / "packaging" / "notarize.sh"
        dist_root = PROJECT_ROOT / "dist"
        dist_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=dist_root) as tmp:
            root = Path(tmp)
            marker = root / "xcrun-invoked"
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_xcrun = fake_bin / "xcrun"
            fake_xcrun.write_text(f"#!/bin/zsh\n: > '{marker}'\nexit 0\n", encoding="utf-8")
            fake_xcrun.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = str(fake_bin)
            environment["APPLE_NOTARY_PROFILE"] = "local-profile"
            wrong_extension = root / "release.zip"
            wrong_extension.write_bytes(b"not-a-dmg")
            candidates = ("relative.dmg", str(root / "Missing.dmg"), str(wrong_extension))

            for candidate in candidates:
                with self.subTest(candidate=candidate):
                    result = subprocess.run(
                        ["/bin/zsh", str(script), candidate],
                        check=False,
                        capture_output=True,
                        text=True,
                        env=environment,
                    )

                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(
                        result.stderr,
                        "notarize:FAIL:dmg_path_invalid\n"
                        "notarize:HINT:dmg_path_invalid:provide_a_release_dmg_under_dist\n",
                    )
                    self.assertNotIn(str(PROJECT_ROOT), result.stderr)
                    self.assertNotIn(candidate, result.stderr)
                    self.assertFalse(marker.exists(), "invalid input must fail before xcrun")

    def test_notarize_gives_a_safe_action_for_a_development_dmg(self) -> None:
        script = PROJECT_ROOT / "packaging" / "notarize.sh"
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "dist") as tmp:
            dmg = Path(tmp) / "PhotosLocalKeywordIndexer-1.2.3-45-dev-arm64.dmg"
            dmg.write_bytes(b"development-only")

            result = subprocess.run(
                ["zsh", str(script), str(dmg)],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 2)
            self.assertEqual(
                result.stderr,
                "notarize:FAIL:development_dmg_not_releasable\n"
                "notarize:HINT:development_dmg_not_releasable:run_release_preflight_then_build_release\n",
            )

    def test_notarize_rejects_an_empty_release_artifact_before_validation_tools(self) -> None:
        script = PROJECT_ROOT / "packaging" / "notarize.sh"
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "dist") as tmp:
            root = Path(tmp)
            dmg = root / "PhotosLocalKeywordIndexer-1.2.3-45.dmg"
            dmg.touch()
            marker = root / "validation-tool-invoked"
            fake_bin = root / "bin"
            fake_bin.mkdir()
            for command_name in ("hdiutil", "codesign", "spctl", "xcrun"):
                command = fake_bin / command_name
                command.write_text(
                    f"#!/bin/zsh\n: > '{marker}'\nexit 0\n", encoding="utf-8"
                )
                command.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
            environment["APPLE_NOTARY_PROFILE"] = "local-profile"

            result = subprocess.run(
                ["/bin/zsh", str(script), str(dmg)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(
            result.stderr,
            "notarize:FAIL:dmg_empty_artifact\n"
            "notarize:HINT:dmg_empty_artifact:rebuild_the_release_dmg_then_retry\n",
        )
        self.assertFalse(marker.exists(), "an empty DMG must fail before validation tools")

    def test_notarize_reports_missing_xcrun_before_inspecting_the_dmg(self) -> None:
        script = PROJECT_ROOT / "packaging" / "notarize.sh"
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "dist") as tmp:
            root = Path(tmp)
            dmg = root / "PhotosLocalKeywordIndexer-1.2.3-45.dmg"
            dmg.write_bytes(b"not-a-real-disk-image")
            marker = root / "hdiutil-invoked"
            fake_bin = root / "bin"
            fake_bin.mkdir()
            for command_name in ("hdiutil", "codesign", "spctl"):
                command = fake_bin / command_name
                command.write_text(f"#!/bin/zsh\n: > '{marker}'\n", encoding="utf-8")
                command.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = str(fake_bin)
            environment["APPLE_NOTARY_PROFILE"] = "local-profile"

            result = subprocess.run(
                ["/bin/zsh", str(script), str(dmg)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

        self.assertEqual(result.returncode, 1)
        self.assertEqual(
            result.stderr,
            "notarize:FAIL:xcrun_missing\n"
            "notarize:HINT:xcrun_missing:install_xcode_command_line_tools_then_retry\n",
        )
        self.assertNotIn(str(root), result.stdout + result.stderr)
        self.assertFalse(marker.exists(), "missing xcrun must fail before inspecting or submitting the DMG")

    def test_release_build_requires_and_embeds_version_identity(self) -> None:
        swift_script = (PROJECT_ROOT / "packaging" / "build_swift_app.sh").read_text(encoding="utf-8")

        self.assertIn('app_version="${APP_VERSION:-}"', swift_script)
        self.assertIn('build_number="${BUILD_NUMBER:-}"', swift_script)
        self.assertIn("project_version=", swift_script)
        self.assertIn("APP_VERSION must match pyproject.toml version.", swift_script)
        self.assertIn("APP_VERSION and BUILD_NUMBER are required for release builds.", swift_script)
        self.assertIn("CFBundleShortVersionString", swift_script)
        self.assertIn("CFBundleVersion", swift_script)
        self.assertLess(
            swift_script.index("APP_VERSION and BUILD_NUMBER are required for release builds."),
            swift_script.index('"$swift_bin" build --package-path'),
        )
        self.assertLess(
            swift_script.index('ditto "$project_root/packaging/AppInfo.plist"'),
            swift_script.index("Set :CFBundleShortVersionString"),
        )

    def test_swift_build_rejects_symlinked_source_helper_before_copy(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "build_swift_app.sh").read_text(encoding="utf-8")

        helper_gate = 'if [[ -L "$helper" || ! -d "$helper" \\'
        helper_copy = 'ditto "$helper" "$app_bundle/Contents/Helpers/PhotosIndexerWorker.app"'

        self.assertIn(helper_gate, source)
        self.assertIn("build_swift_app:FAIL:helper_path_invalid", source)
        self.assertLess(source.index(helper_gate), source.index(helper_copy))

    def test_swift_build_rejects_symlinked_helper_ancestors_before_copy(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "build_swift_app.sh").read_text(encoding="utf-8")

        helper_path_guard = 'validate_build_path "$helper" "$project_root/build" "helper source"'
        helper_copy = 'ditto "$helper" "$app_bundle/Contents/Helpers/PhotosIndexerWorker.app"'

        self.assertIn('source "$project_root/packaging/build_path_guard.zsh"', source)
        self.assertIn(helper_path_guard, source)
        self.assertIn("build_swift_app:FAIL:helper_path_invalid", source)
        self.assertLess(source.index(helper_path_guard), source.index(helper_copy))

    def test_release_preflight_rejects_symlinked_helper_ancestors_before_self_check(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "release_preflight.sh").read_text(encoding="utf-8")

        helper_path_guard = 'validate_build_path "$helper_app" "$project_root/build" "helper source"'
        helper_self_check = '"$helper" --self-check'

        self.assertIn('source "$project_root/packaging/build_path_guard.zsh"', source)
        self.assertIn(helper_path_guard, source)
        self.assertIn("helper_path_invalid", source)
        self.assertLess(source.index(helper_path_guard), source.index(helper_self_check))

    def test_release_preflight_rejects_symlinked_helper_payload_before_self_check(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "release_preflight.sh").read_text(encoding="utf-8")

        payload_scan = 'find "$helper_app" -type l -print'
        helper_self_check = '"$helper" --self-check'

        self.assertIn(payload_scan, source)
        self.assertIn("helper_payload_symlink", source)
        self.assertLess(source.index(payload_scan), source.index(helper_self_check))

    def test_release_build_rejects_disabled_embedded_helper_verification_before_swift(self) -> None:
        script = PROJECT_ROOT / "packaging" / "build_swift_app.sh"
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "build") as tmp:
            root = Path(tmp)
            marker = root / "swift-invoked"
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_swift = fake_bin / "swift"
            fake_swift.write_text(f"#!/bin/zsh\n: > '{marker}'\n", encoding="utf-8")
            fake_swift.chmod(0o755)
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{fake_bin}:{environment['PATH']}",
                    "BUILD_ROOT": str(root / "app-build"),
                    "RELEASE_BUILD": "1",
                    "VERIFY_EMBEDDED_HELPER": "0",
                    "APP_VERSION": "0.1.0",
                    "BUILD_NUMBER": "1",
                    "SPARKLE_FEED_URL": "https://updates.photosindexer.app/appcast.xml",
                    "SPARKLE_PUBLIC_ED_KEY": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                }
            )

            result = subprocess.run(
                ["zsh", str(script)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

        self.assertEqual(result.returncode, 2)
        self.assertIn("build_swift_app:FAIL:embedded_helper_verification_required", result.stderr)
        self.assertFalse(marker.exists(), "a release must reject disabled helper verification before SwiftPM")

    def test_release_build_rejects_placeholder_feed_before_swift(self) -> None:
        script = PROJECT_ROOT / "packaging" / "build_swift_app.sh"
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "build") as tmp:
            root = Path(tmp)
            marker = root / "swift-invoked"
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_swift = fake_bin / "swift"
            fake_swift.write_text(f"#!/bin/zsh\n: > '{marker}'\n", encoding="utf-8")
            fake_swift.chmod(0o755)
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{fake_bin}:{environment['PATH']}",
                    "BUILD_ROOT": str(root / "app-build"),
                    "RELEASE_BUILD": "1",
                    "APP_VERSION": "0.1.0",
                    "BUILD_NUMBER": "1",
                    "SPARKLE_FEED_URL": "https://updates.example.invalid/appcast.xml",
                    "SPARKLE_PUBLIC_ED_KEY": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                }
            )

            result = subprocess.run(
                ["zsh", str(script)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

        self.assertEqual(result.returncode, 2)
        self.assertIn("placeholder host", result.stderr)
        self.assertFalse(marker.exists(), "an invalid release feed must fail before SwiftPM")

    def test_release_build_rejects_reserved_example_feed_before_swift(self) -> None:
        script = PROJECT_ROOT / "packaging" / "build_swift_app.sh"
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "build") as tmp:
            root = Path(tmp)
            marker = root / "swift-invoked"
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_swift = fake_bin / "swift"
            fake_swift.write_text(f"#!/bin/zsh\n: > '{marker}'\n", encoding="utf-8")
            fake_swift.chmod(0o755)
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{fake_bin}:{environment['PATH']}",
                    "BUILD_ROOT": str(root / "app-build"),
                    "RELEASE_BUILD": "1",
                    "APP_VERSION": "0.1.0",
                    "BUILD_NUMBER": "1",
                    "SPARKLE_FEED_URL": "https://updates.example.org/appcast.xml",
                    "SPARKLE_PUBLIC_ED_KEY": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                }
            )

            result = subprocess.run(
                ["zsh", str(script)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

        self.assertEqual(result.returncode, 2)
        self.assertIn("placeholder host", result.stderr)
        self.assertFalse(marker.exists(), "a reserved example feed must fail before SwiftPM")

    def test_release_build_rejects_malformed_feed_port_before_swift(self) -> None:
        script = PROJECT_ROOT / "packaging" / "build_swift_app.sh"
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "build") as tmp:
            root = Path(tmp)
            marker = root / "swift-invoked"
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_swift = fake_bin / "swift"
            fake_swift.write_text(f"#!/bin/zsh\n: > '{marker}'\n", encoding="utf-8")
            fake_swift.chmod(0o755)
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{fake_bin}:{environment['PATH']}",
                    "BUILD_ROOT": str(root / "app-build"),
                    "RELEASE_BUILD": "1",
                    "APP_VERSION": "0.1.0",
                    "BUILD_NUMBER": "1",
                    "SPARKLE_FEED_URL": "https://updates.photosindexer.app:not-a-port/appcast.xml",
                    "SPARKLE_PUBLIC_ED_KEY": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                }
            )

            result = subprocess.run(
                ["zsh", str(script)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

        self.assertEqual(result.returncode, 2)
        self.assertIn("build_swift_app:FAIL:sparkle_update_configuration_invalid", result.stderr)
        self.assertFalse(marker.exists(), "an invalid release feed must fail before SwiftPM")

    def test_release_build_rejects_plistbuddy_command_delimiters_before_swift(self) -> None:
        script = PROJECT_ROOT / "packaging" / "build_swift_app.sh"
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "build") as tmp:
            root = Path(tmp)
            marker = root / "swift-invoked"
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_swift = fake_bin / "swift"
            fake_swift.write_text(f"#!/bin/zsh\n: > '{marker}'\n", encoding="utf-8")
            fake_swift.chmod(0o755)
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{fake_bin}:{environment['PATH']}",
                    "BUILD_ROOT": str(root / "app-build"),
                    "RELEASE_BUILD": "1",
                    "APP_VERSION": "0.1.0",
                    "BUILD_NUMBER": "1",
                    "SPARKLE_FEED_URL": "https://updates.photosindexer.app/appcast.xml;Delete:CFBundleIdentifier",
                    "SPARKLE_PUBLIC_ED_KEY": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                }
            )

            result = subprocess.run(
                ["zsh", str(script)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

        self.assertEqual(result.returncode, 2)
        self.assertIn("build_swift_app:FAIL:sparkle_update_configuration_invalid", result.stderr)
        self.assertFalse(marker.exists(), "an unsafe feed must fail before SwiftPM")

    def test_release_build_validates_sparkle_public_key_before_swift(self) -> None:
        script = (PROJECT_ROOT / "packaging" / "build_swift_app.sh").read_text(encoding="utf-8")

        self.assertIn("base64.b64decode", script)
        self.assertIn("len(decoded_key) != 32", script)
        self.assertIn("build_swift_app:FAIL:sparkle_update_configuration_invalid", script)
        self.assertLess(
            script.index("base64.b64decode"),
            script.index('"$swift_bin" build --package-path'),
        )

    def test_release_build_rejects_version_drift_before_swift(self) -> None:
        script = PROJECT_ROOT / "packaging" / "build_swift_app.sh"
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "build") as tmp:
            root = Path(tmp)
            marker = root / "swift-invoked"
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_swift = fake_bin / "swift"
            fake_swift.write_text(f"#!/bin/zsh\n: > '{marker}'\n", encoding="utf-8")
            fake_swift.chmod(0o755)
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{fake_bin}:{environment['PATH']}",
                    "BUILD_ROOT": str(root / "app-build"),
                    "RELEASE_BUILD": "1",
                    "APP_VERSION": "9.9.9",
                    "BUILD_NUMBER": "1",
                    "SPARKLE_FEED_URL": "https://updates.photosindexer.app/appcast.xml",
                    "SPARKLE_PUBLIC_ED_KEY": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                }
            )
            result = subprocess.run(["zsh", str(script)], capture_output=True, text=True, check=False, env=environment)

        self.assertEqual(result.returncode, 2)
        self.assertIn("APP_VERSION must match pyproject.toml version.", result.stderr)
        self.assertFalse(marker.exists())

    def test_python_helper_rejects_a_shared_writable_build_root_before_preflight(self) -> None:
        script = PROJECT_ROOT / "packaging" / "build_python_helper.sh"
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "build") as tmp:
            build_root = Path(tmp)
            stale_helper = (
                build_root
                / "dist/PhotosIndexerWorker.app/Contents/MacOS/PhotosIndexerWorker"
            )
            stale_helper.parent.mkdir(parents=True)
            stale_helper.write_text("preserve", encoding="utf-8")
            stale_helper.chmod(0o755)
            build_root.chmod(0o777)
            environment = os.environ.copy()
            environment["BUILD_ROOT"] = str(build_root)

            try:
                result = subprocess.run(
                    ["/bin/zsh", str(script)],
                    check=False,
                    capture_output=True,
                    text=True,
                    env=environment,
                )
            finally:
                build_root.chmod(0o700)

            self.assertEqual(result.returncode, 2)
            self.assertIn("build_python_helper:FAIL:build_root_permissions", result.stderr)
            self.assertEqual(stale_helper.read_text(encoding="utf-8"), "preserve")

    def test_swift_package_lock_is_checked_in_for_exact_sparkle_revision(self) -> None:
        resolved_path = PROJECT_ROOT / "app" / "Package.resolved"
        self.assertTrue(resolved_path.is_file(), "app/Package.resolved is required for reproducible release builds")
        document = json.loads(resolved_path.read_text(encoding="utf-8"))
        pins = document.get("pins", [])
        sparkle = [pin for pin in pins if pin.get("identity") == "sparkle"]
        self.assertEqual(len(sparkle), 1)
        self.assertEqual(sparkle[0]["location"], "https://github.com/sparkle-project/Sparkle.git")
        self.assertEqual(sparkle[0]["state"]["version"], "2.9.2")
        self.assertEqual(
            sparkle[0]["state"]["revision"],
            "6276ba2b404829d139c45ff98427cf90e2efc59b",
        )

    def test_swift_validates_exact_sparkle_lock_before_swiftpm(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "build_swift_app.sh").read_text(encoding="utf-8")

        lock_validation = 'validate_sparkle_lock "$package_lock" "2.9.2"'
        swiftpm_invocation = '"$swift_bin" build --package-path'
        self.assertIn(lock_validation, source)
        self.assertIn("build_swift_app:FAIL:sparkle_lock_invalid", source)
        self.assertLess(source.index(lock_validation), source.index(swiftpm_invocation))

    def test_helper_entitlements_are_separate_and_minimal(self) -> None:
        helper_entitlements = (PROJECT_ROOT / "packaging" / "helper-entitlements.plist").read_text(encoding="utf-8")
        self.assertIn("com.apple.security.automation.apple-events", helper_entitlements)
        self.assertIn("com.apple.security.personal-information.photos-library", helper_entitlements)
        self.assertNotIn("get-task-allow", helper_entitlements)

    def test_verify_release_checks_arm64_for_nested_code(self) -> None:
        verify_script = (PROJECT_ROOT / "packaging" / "verify_release.sh").read_text(encoding="utf-8")

        self.assertIn("verify_arm64_tree()", verify_script)
        self.assertIn('verify_arm64_tree "$helper_app"', verify_script)
        self.assertIn('verify_arm64_tree "$framework"', verify_script)
        self.assertIn('[[ "$architectures" == "arm64" ]]', verify_script)
        self.assertIn('$(lipo -archs "$main")" == "arm64"', verify_script)

    def test_verify_release_checks_bundle_inode_and_ownership_before_readiness(self) -> None:
        verify_script = (PROJECT_ROOT / "packaging" / "verify_release.sh").read_text(encoding="utf-8")

        self.assertIn("bundle_integrity_check", verify_script)
        self.assertIn("verify_release:FAIL:bundle_hardlink", verify_script)
        self.assertIn("verify_release:FAIL:bundle_ownership", verify_script)
        self.assertLess(verify_script.index("bundle_integrity_check"), verify_script.index("verify_bundle_permissions"))

    def test_verify_release_artifact_errors_are_path_free(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "verify_release.sh").read_text(encoding="utf-8")

        self.assertIn("verify_release:FAIL:bundle_symlink", source)
        self.assertNotIn("Bundle symlink escapes its containing bundle: $candidate", source)
        self.assertIn("verify_release:FAIL:nested_architecture", source)
        self.assertNotIn("Nested release executable must be arm64-only: $candidate", source)

    def test_verify_release_rejects_invalid_app_paths_without_leaking_them(self) -> None:
        script = PROJECT_ROOT / "packaging" / "verify_release.sh"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "plutil-invoked"
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_plutil = fake_bin / "plutil"
            fake_plutil.write_text(f"#!/bin/zsh\n: > '{marker}'\nexit 0\n", encoding="utf-8")
            fake_plutil.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = str(fake_bin)
            candidates = ("relative.app", str(root / "Missing.app"))

            for candidate in candidates:
                with self.subTest(candidate=candidate):
                    result = subprocess.run(
                        ["/bin/zsh", str(script), candidate],
                        check=False,
                        capture_output=True,
                        text=True,
                        env=environment,
                    )

                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(
                        result.stderr,
                        "verify_release:FAIL:app_bundle_path_invalid\n"
                        "verify_release:HINT:app_bundle_path_invalid:provide_a_real_app_bundle\n",
                    )
                    self.assertNotIn(str(PROJECT_ROOT), result.stderr)
                    self.assertNotIn(candidate, result.stderr)
                    self.assertFalse(marker.exists(), "invalid input must fail before tool checks")

    def test_verify_release_debug_entitlement_error_is_path_free(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "verify_release.sh").read_text(encoding="utf-8")

        self.assertIn("verify_release:FAIL:debug_entitlement", source)
        self.assertNotIn("Release code contains the get-task-allow entitlement: $target", source)

    def test_verify_release_signature_error_is_path_free(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "verify_release.sh").read_text(encoding="utf-8")

        self.assertIn("verify_release:FAIL:code_signature_invalid", source)
        self.assertIn('codesign --verify --strict --verbose=2 "$target" >/dev/null 2>&1', source)
        self.assertNotIn('codesign --verify --strict --verbose=2 "$target" >/dev/null\n', source)

    def test_verify_release_redacts_gatekeeper_assessment_errors(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "verify_release.sh").read_text(encoding="utf-8")

        self.assertIn(
            'if ! spctl --assess --type execute --verbose=4 "$app_path" >/dev/null 2>&1; then',
            source,
        )
        self.assertIn("verify_release:FAIL:gatekeeper_assessment_invalid", source)

    def test_verify_release_reports_missing_developer_id_signature_metadata(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "verify_release.sh").read_text(encoding="utf-8")
        start = source.index("verify_code()")
        end = source.index("\n}\n\nverify_macho_tree", start) + 2
        verify_code = source[start:end]
        command = (
            "set -e\n"
            "verify_no_debug_entitlements() { return 0; }\n"
            "codesign() {\n"
            "  case \" $* \" in\n"
            "    *\" --verify \"*) return 0 ;;\n"
            "    *\" -dvv \"*) print -- 'Identifier=com.photoslocalkeywordindexer.app'; return 0 ;;\n"
            "    *) return 1 ;;\n"
            "  esac\n"
            "}\n"
            f"{verify_code}\n"
            "verify_code /private/tmp/release-fixture.app com.photoslocalkeywordindexer.app\n"
        )

        result = subprocess.run(
            ["/bin/zsh", "-c", command],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 1)
        self.assertEqual(
            result.stderr,
            "verify_release:FAIL:code_signature_identity_invalid\n"
            "verify_release:HINT:code_signature_identity_invalid:sign_with_developer_id_application\n",
        )
        self.assertNotIn("release-fixture", result.stderr)

    def test_verify_release_rejects_a_symlinked_bundle_parent(self) -> None:
        script = PROJECT_ROOT / "packaging" / "verify_release.sh"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / "PhotosLocalKeywordIndexer.app"
            contents = app / "Contents"
            (contents / "MacOS").mkdir(parents=True)
            (contents / "Frameworks" / "Sparkle.framework").mkdir(parents=True)
            external = root / "external-helpers"
            (external / "PhotosIndexerWorker.app" / "Contents" / "MacOS").mkdir(parents=True)
            (external / "PhotosIndexerWorker.app" / "Contents" / "Resources").mkdir(parents=True)
            helper_binary = external / "PhotosIndexerWorker.app" / "Contents" / "MacOS" / "PhotosIndexerWorker"
            helper_binary.touch()
            helper_binary.chmod(0o755)
            with (external / "PhotosIndexerWorker.app" / "Contents" / "Info.plist").open("wb") as stream:
                plistlib.dump(
                    {
                        "CFBundleExecutable": "PhotosIndexerWorker",
                        "CFBundleIdentifier": "com.photoslocalkeywordindexer.worker",
                        "CFBundlePackageType": "APPL",
                        "CFBundleShortVersionString": "1.2.3",
                        "CFBundleVersion": "45",
                        "LSUIElement": True,
                        "NSPhotoLibraryUsageDescription": "Fotos",
                        "NSPhotoLibraryAddUsageDescription": "Fotos",
                        "NSAppleEventsUsageDescription": "Fotos",
                    },
                    stream,
                )
            (contents / "Helpers").symlink_to(external, target_is_directory=True)
            main = contents / "MacOS" / "PhotosLocalKeywordIndexer"
            main.touch()
            main.chmod(0o755)
            (contents / "Info.plist").touch()

            result = subprocess.run(
                ["zsh", str(script), str(app)],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Bundle directories must not be symlinks", result.stderr)

    def test_verify_release_rejects_a_symlink_escaping_nested_bundle(self) -> None:
        script = PROJECT_ROOT / "packaging" / "verify_release.sh"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / "PhotosLocalKeywordIndexer.app"
            contents = app / "Contents"
            (contents / "MacOS").mkdir(parents=True)
            framework = contents / "Frameworks" / "Sparkle.framework"
            (framework / "Versions" / "A" / "Resources").mkdir(parents=True)
            (framework / "Versions" / "A" / "Resources" / "Info.plist").write_text("", encoding="utf-8")
            (framework / "Versions" / "A" / "Sparkle").touch()
            (framework / "Versions" / "A" / "Sparkle").chmod(0o755)
            (framework / "Versions" / "Current").symlink_to("A", target_is_directory=True)
            (framework / "Versions" / "Current" / "Sparkle").chmod(0o755)
            (framework / "Sparkle").symlink_to("/tmp/outside-sparkle")
            helper_app, helper = self._create_embedded_helper_bundle(
                contents / "Helpers" / "PhotosIndexerWorker.app",
                helper_script="#!/bin/zsh\nexit 0\n",
            )
            main = contents / "MacOS" / "PhotosLocalKeywordIndexer"
            main.touch()
            main.chmod(0o755)
            (contents / "Info.plist").write_text("", encoding="utf-8")

            result = subprocess.run(
                ["zsh", str(script), str(app)],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("symlink", result.stderr.lower())

    def test_verify_release_rejects_a_symlink_escaping_app_contents(self) -> None:
        script = PROJECT_ROOT / "packaging" / "verify_release.sh"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / "PhotosLocalKeywordIndexer.app"
            contents = app / "Contents"
            (contents / "MacOS").mkdir(parents=True)
            self._create_embedded_helper_bundle(
                contents / "Helpers" / "PhotosIndexerWorker.app",
                helper_script="#!/bin/zsh\nexit 0\n",
            )
            (contents / "Frameworks" / "Sparkle.framework").mkdir(parents=True)
            (contents / "Resources").mkdir(parents=True)
            main = contents / "MacOS" / "PhotosLocalKeywordIndexer"
            main.touch()
            main.chmod(0o755)
            (contents / "Info.plist").touch()
            (contents / "Resources" / "outside").symlink_to(root / "outside-resource")

            result = subprocess.run(
                ["zsh", str(script), str(app)],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("symlink", result.stderr.lower())

    def test_verify_release_rejects_a_release_bundle_without_sparkle_configuration(self) -> None:
        """A development bundle must not pass the release verifier unchanged."""
        script = PROJECT_ROOT / "packaging" / "verify_release.sh"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / "PhotosLocalKeywordIndexer.app"
            contents = app / "Contents"
            framework = contents / "Frameworks" / "Sparkle.framework"
            for directory in (
                contents / "MacOS",
                contents / "Helpers" / "PhotosIndexerWorker.app" / "Contents" / "MacOS",
                contents / "Helpers" / "PhotosIndexerWorker.app" / "Contents" / "Resources",
                contents / "Resources",
                framework / "Versions" / "Current" / "Resources",
            ):
                directory.mkdir(parents=True, exist_ok=True)
            for executable in (
                contents / "MacOS" / "PhotosLocalKeywordIndexer",
                contents / "Helpers" / "PhotosIndexerWorker.app" / "Contents" / "MacOS" / "PhotosIndexerWorker",
                framework / "Versions" / "Current" / "Sparkle",
            ):
                executable.touch()
                executable.chmod(0o755)
            with (contents / "Helpers" / "PhotosIndexerWorker.app" / "Contents" / "Info.plist").open("wb") as stream:
                plistlib.dump(
                    {
                        "CFBundleExecutable": "PhotosIndexerWorker",
                        "CFBundleIdentifier": "com.photoslocalkeywordindexer.worker",
                        "CFBundlePackageType": "APPL",
                        "CFBundleShortVersionString": "1.2.3",
                        "CFBundleVersion": "45",
                        "LSUIElement": True,
                        "NSPhotoLibraryUsageDescription": "Fotos",
                        "NSPhotoLibraryAddUsageDescription": "Fotos",
                        "NSAppleEventsUsageDescription": "Fotos",
                    },
                    stream,
                )
            (contents / "Resources" / "AppIcon.icns").write_bytes(b"icon")
            with (contents / "Info.plist").open("wb") as stream:
                plistlib.dump({
                    "CFBundleIdentifier": "com.photoslocalkeywordindexer.app",
                    "CFBundleExecutable": "PhotosLocalKeywordIndexer",
                    "CFBundlePackageType": "APPL",
                    "CFBundleIconFile": "AppIcon.icns",
                    "LSMinimumSystemVersion": "14.0",
                    "CFBundleShortVersionString": "1.2.3",
                    "CFBundleVersion": "45",
                    "NSPhotoLibraryUsageDescription": "Analiza fotos locales para preparar una revisión.",
                    "NSAppleEventsUsageDescription": "Controla Fotos solo después de una confirmación explícita.",
                }, stream)
            with (framework / "Versions" / "Current" / "Resources" / "Info.plist").open("wb") as stream:
                plistlib.dump({
                    "CFBundleIdentifier": "org.sparkle-project.Sparkle",
                    "CFBundleShortVersionString": "2.9.2",
                }, stream)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            for name, body in {
                "lipo": '#!/bin/zsh\nprint -- arm64\n',
                "file": '#!/bin/zsh\nprint -- "Mach-O 64-bit executable arm64"\n',
                "codesign": (
                    '#!/bin/zsh\n'
                    'if [[ " $* " == *" -dvv "* ]]; then\n'
                    '  case "${@: -1}" in\n'
                    '    */Helpers/PhotosIndexerWorker.app/Contents/MacOS/PhotosIndexerWorker|*/Helpers/PhotosIndexerWorker.app) identifier="com.photoslocalkeywordindexer.worker" ;;\n'
                    '    */Frameworks/Sparkle.framework|*/Frameworks/Sparkle.framework/Versions/Current/Sparkle) identifier="org.sparkle-project.Sparkle" ;;\n'
                    '    *) identifier="com.photoslocalkeywordindexer.app" ;;\n'
                    '  esac\n'
                    '  print -- "Authority=Developer ID Application: Example"\n'
                    '  print -- "flags=0x10000(runtime)"\n'
                    '  print -- "Identifier=$identifier"\n'
                    '  print -- "TeamIdentifier=TEAMID"\n'
                    '  exit 0\n'
                    'fi\n'
                    'if [[ " $* " == *" --entitlements :- "* ]]; then\n'
                    '  print -r -- \'<plist version="1.0"><dict><key>com.apple.security.automation.apple-events</key><true/><key>com.apple.security.personal-information.photos-library</key><true/></dict></plist>\'\n'
                    'fi\n'
                    'exit 0\n'
                ),
                "spctl": '#!/bin/zsh\nexit 0\n',
            }.items():
                tool = fake_bin / name
                tool.write_text(body, encoding="utf-8")
                tool.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{fake_bin}:{environment['PATH']}"

            result = subprocess.run(
                ["zsh", str(script), str(app)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "verify_release:FAIL:sparkle_update_configuration_missing",
            result.stderr,
            (result.returncode, result.stdout, result.stderr),
        )

    def test_verify_release_rejects_a_helper_bundle_with_mismatched_release_metadata(self) -> None:
        script = PROJECT_ROOT / "packaging" / "verify_release.sh"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / "PhotosLocalKeywordIndexer.app"
            contents = app / "Contents"
            framework = contents / "Frameworks" / "Sparkle.framework"
            helper_app = contents / "Helpers" / "PhotosIndexerWorker.app"
            for directory in (
                contents / "MacOS",
                contents / "Resources",
                helper_app / "Contents" / "MacOS",
                helper_app / "Contents" / "Resources",
                framework / "Versions" / "Current" / "Resources",
            ):
                directory.mkdir(parents=True, exist_ok=True)
            for executable in (
                contents / "MacOS" / "PhotosLocalKeywordIndexer",
                helper_app / "Contents" / "MacOS" / "PhotosIndexerWorker",
                framework / "Versions" / "Current" / "Sparkle",
            ):
                executable.touch()
                executable.chmod(0o755)
            (contents / "Resources" / "AppIcon.icns").write_bytes(b"icon")
            with (contents / "Info.plist").open("wb") as stream:
                plistlib.dump(
                    {
                        "CFBundleIdentifier": "com.photoslocalkeywordindexer.app",
                        "CFBundleExecutable": "PhotosLocalKeywordIndexer",
                        "CFBundlePackageType": "APPL",
                        "CFBundleIconFile": "AppIcon.icns",
                        "LSMinimumSystemVersion": "14.0",
                        "CFBundleShortVersionString": "1.2.3",
                        "CFBundleVersion": "45",
                        "NSPhotoLibraryUsageDescription": "Analiza fotos locales para preparar una revisión.",
                        "NSAppleEventsUsageDescription": "Controla Fotos solo después de una confirmación explícita.",
                        "SUFeedURL": "https://updates.photosindexer.app/appcast.xml",
                        "SUPublicEDKey": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                        "SUEnableAutomaticChecks": True,
                    },
                    stream,
                )
            with (helper_app / "Contents" / "Info.plist").open("wb") as stream:
                plistlib.dump(
                    {
                        "CFBundleExecutable": "PhotosIndexerWorker",
                        "CFBundleIdentifier": "com.photoslocalkeywordindexer.worker",
                        "CFBundlePackageType": "APPL",
                        "CFBundleShortVersionString": "9.9.9",
                        "CFBundleVersion": "999",
                        "LSUIElement": True,
                        "NSPhotoLibraryUsageDescription": "Fotos",
                        "NSPhotoLibraryAddUsageDescription": "Fotos",
                        "NSAppleEventsUsageDescription": "Fotos",
                    },
                    stream,
                )
            with (framework / "Versions" / "Current" / "Resources" / "Info.plist").open("wb") as stream:
                plistlib.dump(
                    {
                        "CFBundleIdentifier": "org.sparkle-project.Sparkle",
                        "CFBundleShortVersionString": "2.9.2",
                    },
                    stream,
                )
            fake_bin = root / "bin"
            fake_bin.mkdir()
            for name, body in {
                "lipo": '#!/bin/zsh\nprint -- arm64\n',
                "file": '#!/bin/zsh\nprint -- "Mach-O 64-bit executable arm64"\n',
                "codesign": '#!/bin/zsh\nexit 0\n',
                "spctl": '#!/bin/zsh\nexit 0\n',
                "plutil": '#!/bin/zsh\nexit 0\n',
                "readlink": '#!/bin/zsh\n/bin/readlink "$@"\n',
                "find": '#!/bin/zsh\n/usr/bin/find "$@"\n',
            }.items():
                tool = fake_bin / name
                tool.write_text(body, encoding="utf-8")
                tool.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{fake_bin}:/usr/bin:/bin:/usr/sbin:/sbin"

            result = subprocess.run(
                ["zsh", str(script), str(app)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("verify_release:FAIL:helper_bundle_version_invalid", result.stderr)

    def test_build_path_guard_rejects_a_symlinked_build_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_root = root / "project"
            project_root.mkdir()
            external = root / "external"
            external.mkdir()
            build_root = project_root / "build"
            build_root.symlink_to(external, target_is_directory=True)

            result = self._run_build_path_guard(build_root / "app", build_root)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("symlink", result.stderr.lower())
            self.assertNotIn(str(build_root), result.stderr)

    def test_build_path_guard_rejects_a_destination_outside_build_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_root = root / "project"
            build_root = project_root / "build"
            build_root.mkdir(parents=True)
            external_destination = root / "external" / "app"

            result = self._run_build_path_guard(external_destination, build_root)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("outside", result.stderr.lower())

    def test_build_path_guard_accepts_an_uncreated_child_inside_build_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_root = root / "project" / "build"
            build_root.mkdir(parents=True)
            destination = build_root / "app" / "PhotosLocalKeywordIndexer.app"

            result = self._run_build_path_guard(destination, build_root)

            self.assertEqual(result.returncode, 0, result.stderr)

    def test_build_path_guard_accepts_an_existing_file_inside_build_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_root = root / "project" / "build"
            build_root.mkdir(parents=True)
            artifact = build_root / "PhotosLocalKeywordIndexer-1.2.3-45.dmg"
            artifact.write_bytes(b"placeholder")

            result = self._run_build_path_guard(artifact, build_root)

            self.assertEqual(result.returncode, 0, result.stderr)

    def test_sparkle_locator_selects_resolved_version_over_stale_framework(self) -> None:
        locator = PROJECT_ROOT / "packaging" / "sparkle_framework.zsh"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_root = root / ".build"
            expected = build_root / "arm64-apple-macosx" / "release" / "Sparkle.framework"
            stale = build_root / "stale-cache" / "Sparkle.framework"
            self._write_framework(expected, "2.9.2")
            self._write_framework(stale, "2.8.1")
            resolved = root / "Package.resolved"
            resolved.write_text(
                json.dumps(
                    {
                        "pins": [
                            {
                                "identity": "sparkle",
                                "location": "https://github.com/sparkle-project/Sparkle.git",
                                "state": {"version": "2.9.2", "revision": SPARKLE_REVISION},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = self._run_sparkle_locator(locator, build_root, resolved)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(Path(result.stdout.strip()), expected)

    def test_sparkle_locator_accepts_the_framework_current_version_directory(self) -> None:
        locator = PROJECT_ROOT / "packaging" / "sparkle_framework.zsh"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_root = root / ".build"
            framework = build_root / "arm64-apple-macosx" / "release" / "Sparkle.framework"
            info = framework / "Versions" / "B" / "Resources" / "Info.plist"
            info.parent.mkdir(parents=True)
            info.write_text(
                "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
                "<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" "
                "\"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">"
                "<plist version=\"1.0\"><dict>"
                "<key>CFBundleShortVersionString</key><string>2.9.2</string>"
                "</dict></plist>",
                encoding="utf-8",
            )
            (framework / "Versions" / "Current").symlink_to("B", target_is_directory=True)
            resolved = root / "Package.resolved"
            resolved.write_text(
                json.dumps(
                    {
                        "pins": [
                            {
                                "identity": "sparkle",
                                "location": "https://github.com/sparkle-project/Sparkle.git",
                                "state": {"version": "2.9.2", "revision": SPARKLE_REVISION},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = self._run_sparkle_locator(locator, build_root, resolved)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(Path(result.stdout.strip()), framework)

    def test_sparkle_locator_prefers_the_release_framework_over_package_artifacts(self) -> None:
        locator = PROJECT_ROOT / "packaging" / "sparkle_framework.zsh"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_root = root / ".build"
            first = build_root / "arm64-apple-macosx" / "release" / "Sparkle.framework"
            second = build_root / "artifacts" / "sparkle" / "macos-arm64" / "Sparkle.framework"
            self._write_framework(first, "2.9.2")
            self._write_framework(second, "2.9.2")
            resolved = root / "Package.resolved"
            resolved.write_text(
                json.dumps(
                    {
                        "pins": [
                            {
                                "identity": "sparkle",
                                "location": "https://github.com/sparkle-project/Sparkle.git",
                                "state": {"version": "2.9.2", "revision": SPARKLE_REVISION},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = self._run_sparkle_locator(locator, build_root, resolved)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(Path(result.stdout.strip()), first)

    @unittest.skipUnless(
        shutil.which("xcrun") and Path("/usr/bin/lipo").is_file() and Path("/usr/bin/file").is_file(),
        "requires the macOS Mach-O toolchain",
    )
    def test_sparkle_copy_thinner_makes_nested_macho_arm64_without_mutating_source(self) -> None:
        helper = PROJECT_ROOT / "packaging" / "sparkle_framework.zsh"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_binary = root / "source" / "Sparkle"
            copied_framework = root / "copy" / "Sparkle.framework"
            copied_binary = copied_framework / "Versions" / "B" / "Sparkle"
            self._write_test_macho(source_binary, ("x86_64", "arm64"))
            copied_binary.parent.mkdir(parents=True)
            shutil.copy2(source_binary, copied_binary)
            source_bytes = source_binary.read_bytes()

            result = self._run_sparkle_thinner(helper, copied_framework)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                subprocess.run(
                    ["/usr/bin/lipo", "-archs", str(copied_binary)],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip(),
                "arm64",
            )
            self.assertEqual(source_binary.read_bytes(), source_bytes)
            self.assertTrue(os.access(copied_binary, os.X_OK))
            self.assertEqual(list(copied_framework.rglob(".sparkle-arm64.*")), [])

    @unittest.skipUnless(
        shutil.which("xcrun") and Path("/usr/bin/lipo").is_file() and Path("/usr/bin/file").is_file(),
        "requires the macOS Mach-O toolchain",
    )
    def test_sparkle_copy_thinner_rejects_macho_without_arm64(self) -> None:
        helper = PROJECT_ROOT / "packaging" / "sparkle_framework.zsh"
        with tempfile.TemporaryDirectory() as tmp:
            framework = Path(tmp) / "Sparkle.framework"
            binary = framework / "Versions" / "B" / "Sparkle"
            self._write_test_macho(binary, ("x86_64",))
            original = binary.read_bytes()

            result = self._run_sparkle_thinner(helper, framework)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("sparkle_framework:FAIL:architecture", result.stderr)
            self.assertEqual(binary.read_bytes(), original)

    def test_sparkle_copy_thinner_rejects_missing_lipo_before_modifying_framework(self) -> None:
        helper = PROJECT_ROOT / "packaging" / "sparkle_framework.zsh"
        with tempfile.TemporaryDirectory() as tmp:
            framework = Path(tmp) / "Sparkle.framework"
            binary = framework / "Versions" / "B" / "Sparkle"
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b"unchanged")

            result = self._run_sparkle_thinner(
                helper,
                framework,
                lipo=Path(tmp) / "missing-lipo",
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("sparkle_framework:FAIL:lipo_unavailable", result.stderr)
            self.assertEqual(binary.read_bytes(), b"unchanged")

    def test_sparkle_locator_fails_closed_when_only_noncanonical_frameworks_are_ambiguous(self) -> None:
        locator = PROJECT_ROOT / "packaging" / "sparkle_framework.zsh"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_root = root / ".build"
            first = build_root / "artifacts" / "sparkle" / "macos-arm64" / "Sparkle.framework"
            second = build_root / "stale-cache" / "Sparkle.framework"
            self._write_framework(first, "2.9.2")
            self._write_framework(second, "2.9.2")
            resolved = root / "Package.resolved"
            resolved.write_text(
                json.dumps(
                    {
                        "pins": [
                            {
                                "identity": "sparkle",
                                "location": "https://github.com/sparkle-project/Sparkle.git",
                                "state": {"version": "2.9.2", "revision": SPARKLE_REVISION},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = self._run_sparkle_locator(locator, build_root, resolved)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("ambiguous", result.stderr.lower())

    def test_sparkle_locator_rejects_a_different_package_origin(self) -> None:
        locator = PROJECT_ROOT / "packaging" / "sparkle_framework.zsh"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_root = root / ".build"
            self._write_framework(
                build_root / "arm64-apple-macosx" / "release" / "Sparkle.framework",
                "2.9.2",
            )
            resolved = root / "Package.resolved"
            resolved.write_text(
                json.dumps(
                    {
                        "pins": [
                            {
                                "identity": "sparkle",
                                "location": "https://example.invalid/Sparkle.git",
                                "state": {"version": "2.9.2", "revision": SPARKLE_REVISION},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = self._run_sparkle_locator(locator, build_root, resolved)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("origin", result.stderr.lower())

    def test_sparkle_locator_sanitizes_a_malformed_legacy_lock_shape(self) -> None:
        locator = PROJECT_ROOT / "packaging" / "sparkle_framework.zsh"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_root = root / ".build"
            self._write_framework(
                build_root / "arm64-apple-macosx" / "release" / "Sparkle.framework",
                "2.9.2",
            )
            resolved = root / "Package.resolved"
            resolved.write_text(json.dumps({"version": 1, "object": "not-an-object"}), encoding="utf-8")

            result = self._run_sparkle_locator(locator, build_root, resolved)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("pins list", result.stderr)
            self.assertNotIn("Traceback", result.stderr)

    def test_sparkle_lock_reader_does_not_echo_python_exception_details(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "sparkle_framework.zsh").read_text(encoding="utf-8")

        self.assertIn('except (OSError, ValueError):', source)
        self.assertNotIn('f"Cannot read Package.resolved: {exc}"', source)
        self.assertIn('print("Package.resolved cannot be read.", file=sys.stderr)', source)

    def test_sparkle_locator_sanitizes_a_non_object_lock_document(self) -> None:
        locator = PROJECT_ROOT / "packaging" / "sparkle_framework.zsh"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_root = root / ".build"
            self._write_framework(
                build_root / "arm64-apple-macosx" / "release" / "Sparkle.framework",
                "2.9.2",
            )
            resolved = root / "Package.resolved"
            resolved.write_text("[]", encoding="utf-8")

            result = self._run_sparkle_locator(locator, build_root, resolved)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("pins list", result.stderr)
            self.assertNotIn("Traceback", result.stderr)

    def test_sparkle_locator_sanitizes_a_non_object_framework_plist(self) -> None:
        locator = PROJECT_ROOT / "packaging" / "sparkle_framework.zsh"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_root = root / ".build"
            framework = build_root / "arm64-apple-macosx" / "release" / "Sparkle.framework"
            self._write_framework(framework, "2.9.2")
            (framework / "Versions" / "A" / "Resources" / "Info.plist").write_text(
                "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
                "<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" "
                "\"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">"
                "<plist version=\"1.0\"><array/></plist>",
                encoding="utf-8",
            )
            resolved = root / "Package.resolved"
            resolved.write_text(
                json.dumps(
                    {
                        "pins": [
                            {
                                "identity": "sparkle",
                                "location": "https://github.com/sparkle-project/Sparkle.git",
                                "state": {"version": "2.9.2", "revision": SPARKLE_REVISION},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = self._run_sparkle_locator(locator, build_root, resolved)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("No Sparkle.framework", result.stderr)
            self.assertNotIn("Traceback", result.stderr)

    def test_sparkle_locator_rejects_a_non_sparkle_identity_at_canonical_origin(self) -> None:
        locator = PROJECT_ROOT / "packaging" / "sparkle_framework.zsh"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_root = root / ".build"
            self._write_framework(
                build_root / "arm64-apple-macosx" / "release" / "Sparkle.framework",
                "2.9.2",
            )
            resolved = root / "Package.resolved"
            resolved.write_text(
                json.dumps(
                    {
                        "pins": [
                            {
                                "identity": "not-sparkle",
                                "location": "https://github.com/sparkle-project/Sparkle.git",
                                "state": {"version": "2.9.2", "revision": SPARKLE_REVISION},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = self._run_sparkle_locator(locator, build_root, resolved)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("identity", result.stderr.lower())

    def test_sparkle_locator_rejects_duplicate_canonical_sparkle_pins(self) -> None:
        locator = PROJECT_ROOT / "packaging" / "sparkle_framework.zsh"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_root = root / ".build"
            self._write_framework(
                build_root / "arm64-apple-macosx" / "release" / "Sparkle.framework",
                "2.9.2",
            )
            resolved = root / "Package.resolved"
            resolved.write_text(
                json.dumps(
                    {
                        "pins": [
                            {
                                "identity": "sparkle",
                                "location": "https://github.com/sparkle-project/Sparkle.git",
                                "state": {"version": "2.9.2", "revision": SPARKLE_REVISION},
                            },
                            {
                                "identity": "sparkle",
                                "location": "https://github.com/sparkle-project/Sparkle.git",
                                "state": {"version": "2.8.1", "revision": "other"},
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = self._run_sparkle_locator(locator, build_root, resolved)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("exactly one", result.stderr.lower())

    def test_sparkle_locator_rejects_a_different_resolved_revision(self) -> None:
        locator = PROJECT_ROOT / "packaging" / "sparkle_framework.zsh"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_root = root / ".build"
            self._write_framework(
                build_root / "arm64-apple-macosx" / "release" / "Sparkle.framework",
                "2.9.2",
            )
            resolved = root / "Package.resolved"
            resolved.write_text(
                json.dumps(
                    {
                        "pins": [
                            {
                                "identity": "sparkle",
                                "location": "https://github.com/sparkle-project/Sparkle.git",
                                "state": {"version": "2.9.2", "revision": "different"},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = self._run_sparkle_locator(locator, build_root, resolved)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("revision", result.stderr.lower())

    def test_sparkle_lock_rejects_an_oversized_regular_file(self) -> None:
        locator = PROJECT_ROOT / "packaging" / "sparkle_framework.zsh"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            resolved = root / "Package.resolved"
            resolved.write_text(
                json.dumps(
                    {
                        "pins": [
                            {
                                "identity": "sparkle",
                                "location": "https://github.com/sparkle-project/Sparkle.git",
                                "state": {"version": "2.9.2", "revision": SPARKLE_REVISION},
                            }
                        ],
                        "padding": "x" * (1024 * 1024 + 1),
                    }
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    "zsh",
                    "-c",
                    'source "$1"; validate_sparkle_lock "$2" "2.9.2" "$3"',
                    "sparkle-lock-test",
                    str(locator),
                    str(resolved),
                    SPARKLE_REVISION,
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("bounded", result.stderr.lower())

    @staticmethod
    def _write_framework(path: Path, version: str) -> None:
        info = path / "Versions" / "A" / "Resources" / "Info.plist"
        info.parent.mkdir(parents=True)
        info.write_text(
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
            "<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" "
            "\"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">"
            "<plist version=\"1.0\"><dict>"
            f"<key>CFBundleShortVersionString</key><string>{version}</string>"
            "</dict></plist>",
            encoding="utf-8",
        )

    @staticmethod
    def _write_test_macho(path: Path, architectures: tuple[str, ...]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        objects: list[Path] = []
        try:
            for architecture in architectures:
                object_path = path.parent / f".{path.name}.{architecture}.o"
                subprocess.run(
                    [
                        "xcrun",
                        "clang",
                        "-c",
                        "-x",
                        "c",
                        "-",
                        "-arch",
                        architecture,
                        "-o",
                        str(object_path),
                    ],
                    input="int sparkle_fixture(void) { return 0; }\n",
                    text=True,
                    check=True,
                    capture_output=True,
                )
                objects.append(object_path)
            subprocess.run(
                ["/usr/bin/lipo", "-create", *(str(item) for item in objects), "-output", str(path)],
                check=True,
                capture_output=True,
                text=True,
            )
            path.chmod(0o755)
        finally:
            for object_path in objects:
                object_path.unlink(missing_ok=True)

    @staticmethod
    def _run_sparkle_thinner(
        helper: Path,
        framework: Path,
        *,
        lipo: Path = Path("/usr/bin/lipo"),
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "zsh",
                "-c",
                'source "$1"; thin_sparkle_framework_arm64 "$2" "$3" "$4"',
                "sparkle-thin-test",
                str(helper),
                str(framework),
                str(lipo),
                "/usr/bin/file",
            ],
            check=False,
            capture_output=True,
            text=True,
        )

    @staticmethod
    def _run_sparkle_locator(locator: Path, build_root: Path, resolved: Path) -> subprocess.CompletedProcess[str]:
        command = (
            'source "$1"; '
            'locate_sparkle_framework "$2" "$3" "2.9.2" "$4"'
        )
        return subprocess.run(
            [
                "zsh",
                "-c",
                command,
                "sparkle-test",
                str(locator),
                str(build_root),
                str(resolved),
                SPARKLE_REVISION,
            ],
            check=False,
            capture_output=True,
            text=True,
        )

    @staticmethod
    def _run_build_path_guard(path: Path, root: Path) -> subprocess.CompletedProcess[str]:
        guard = PROJECT_ROOT / "packaging" / "build_path_guard.zsh"
        command = 'source "$1"; validate_build_path "$2" "$3" "test path"'
        return subprocess.run(
            ["zsh", "-c", command, "build-path-test", str(guard), str(path), str(root)],
            check=False,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
