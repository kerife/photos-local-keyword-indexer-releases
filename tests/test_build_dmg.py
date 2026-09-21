from __future__ import annotations

import os
import json
import plistlib
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "packaging" / "build_dmg.sh"


class BuildDmgTests(unittest.TestCase):
    def test_release_dmg_compares_the_complete_nested_helper_app(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")

        self.assertIn(
            'source_helper_app="$project_root/build/python-helper/dist/PhotosIndexerWorker.app"',
            source,
        )
        self.assertIn(
            'app_helper_app="$app_path/Contents/Helpers/PhotosIndexerWorker.app"',
            source,
        )
        self.assertIn(
            'source_helper="$source_helper_app/Contents/MacOS/PhotosIndexerWorker"',
            source,
        )
        self.assertIn(
            'app_helper="$app_helper_app/Contents/MacOS/PhotosIndexerWorker"',
            source,
        )
        self.assertIn(
            'source_fingerprint_marker="$source_helper_app/Contents/Resources/.photos-indexer-source-fingerprint"',
            source,
        )
        self.assertNotIn('dist/PhotosIndexerWorker/PhotosIndexerWorker', source)

    def test_app_bundle_outside_build_root_has_stable_path_error(self) -> None:
        output_parent = PROJECT_ROOT / "dist"
        output_parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=output_parent) as output_tmp:
            result = subprocess.run(
                ["/bin/zsh", str(SCRIPT), "/tmp/not-a-build-app/PhotosLocalKeywordIndexer.app", output_tmp],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(
            result.stderr,
            "build_dmg:FAIL:app_bundle_path_invalid\n"
            "build_dmg:HINT:app_bundle_path_invalid:provide_a_complete_app_bundle_under_build\n",
        )

    def test_final_cleanup_failure_does_not_echo_private_paths_or_ready(self) -> None:
        build_root = PROJECT_ROOT / "build"
        output_parent = PROJECT_ROOT / "dist"
        build_root.mkdir(exist_ok=True)
        output_parent.mkdir(exist_ok=True)

        with (
            tempfile.TemporaryDirectory(dir=build_root) as build_tmp,
            tempfile.TemporaryDirectory(dir=output_parent) as output_tmp,
        ):
            app, environment = self._fixtures(Path(build_tmp))
            fake_rm = Path(build_tmp) / "bin" / "rm"
            fake_rm.write_text(
                "#!/bin/zsh\n"
                "print -u2 -- \"${@[-1]}\"\n"
                "exit 1\n",
                encoding="utf-8",
            )
            fake_rm.chmod(0o755)
            environment["VERIFY_DMG_LAYOUT"] = "0"

            result = subprocess.run(
                ["zsh", str(SCRIPT), str(app), output_tmp],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, "")
            self.assertEqual(
                result.stderr,
                "build_dmg:FAIL:final_cleanup_failed\n"
                "build_dmg:HINT:final_cleanup_failed:remove_stale_private_build_state_then_retry\n",
            )
            self.assertNotIn(str(output_tmp), result.stdout + result.stderr)
            self.assertNotIn("release_dmg:READY", result.stdout + result.stderr)

    def test_applications_alias_failure_is_path_free_and_precedes_dmg_creation(self) -> None:
        build_root = PROJECT_ROOT / "build"
        output_parent = PROJECT_ROOT / "dist"
        build_root.mkdir(exist_ok=True)
        output_parent.mkdir(exist_ok=True)

        with (
            tempfile.TemporaryDirectory(dir=build_root) as build_tmp,
            tempfile.TemporaryDirectory(dir=output_parent) as output_tmp,
        ):
            app, environment = self._fixtures(Path(build_tmp))
            fake_ln = Path(build_tmp) / "bin" / "ln"
            fake_ln.write_text(
                "#!/bin/zsh\n"
                "print -u2 -- \"${@[-1]}\"\n"
                "exit 1\n",
                encoding="utf-8",
            )
            fake_ln.chmod(0o755)
            hdiutil_marker = Path(environment["HDIUTIL_MARKER"])

            result = subprocess.run(
                ["zsh", str(SCRIPT), str(app), output_tmp],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertEqual(
                result.stderr,
                "build_dmg:FAIL:applications_alias_failed\n"
                "build_dmg:HINT:applications_alias_failed:retry_the_dmg_build\n",
            )
            self.assertNotIn(str(app), result.stdout + result.stderr)
            self.assertFalse(hdiutil_marker.exists())
            self.assertEqual(list(Path(output_tmp).iterdir()), [])

    def test_staging_copy_failure_is_path_free_and_precedes_dmg_creation(self) -> None:
        build_root = PROJECT_ROOT / "build"
        output_parent = PROJECT_ROOT / "dist"
        build_root.mkdir(exist_ok=True)
        output_parent.mkdir(exist_ok=True)

        with (
            tempfile.TemporaryDirectory(dir=build_root) as build_tmp,
            tempfile.TemporaryDirectory(dir=output_parent) as output_tmp,
        ):
            app, environment = self._fixtures(Path(build_tmp))
            fake_ditto = Path(build_tmp) / "bin" / "ditto"
            fake_ditto.write_text(
                "#!/bin/zsh\n"
                "mkdir -p -- \"$2\"\n"
                "print -u2 -- \"$1 -> $2\"\n"
                "exit 1\n",
                encoding="utf-8",
            )
            fake_ditto.chmod(0o755)
            hdiutil_marker = Path(environment["HDIUTIL_MARKER"])

            result = subprocess.run(
                ["zsh", str(SCRIPT), str(app), output_tmp],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertEqual(
                result.stderr,
                "build_dmg:FAIL:app_staging_failed\n"
                "build_dmg:HINT:app_staging_failed:retry_the_dmg_build\n",
            )
            self.assertNotIn(str(app), result.stdout + result.stderr)
            self.assertFalse(hdiutil_marker.exists())
            self.assertEqual(list(Path(output_tmp).iterdir()), [])

    def test_dmg_creation_failure_sanitizes_tool_output_and_removes_its_partial_artifact(self) -> None:
        build_root = PROJECT_ROOT / "build"
        output_parent = PROJECT_ROOT / "dist"
        build_root.mkdir(exist_ok=True)
        output_parent.mkdir(exist_ok=True)

        with (
            tempfile.TemporaryDirectory(dir=build_root) as build_tmp,
            tempfile.TemporaryDirectory(dir=output_parent) as output_tmp,
        ):
            app, environment = self._fixtures(Path(build_tmp))
            fake_hdiutil = Path(build_tmp) / "bin" / "hdiutil"
            fake_hdiutil.write_text(
                "#!/bin/zsh\n"
                "if [[ \"$1\" == \"create\" ]]; then\n"
                "  print -n partial > \"${@[-1]}\"\n"
                "  print -- \"${@[-1]}\"\n"
                "  print -u2 -- \"$4\"\n"
                "  exit 1\n"
                "fi\n"
                "exit 1\n",
                encoding="utf-8",
            )
            fake_hdiutil.chmod(0o755)
            expected = Path(output_tmp) / "PhotosLocalKeywordIndexer-1.2.3-45-dev-arm64.dmg"

            result = subprocess.run(
                ["zsh", str(SCRIPT), str(app), output_tmp],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertEqual(
                result.stderr,
                "build_dmg:FAIL:dmg_creation_failed\n"
                "build_dmg:HINT:dmg_creation_failed:retry_the_dmg_build\n",
            )
            self.assertNotIn(str(expected), result.stdout + result.stderr)
            self.assertNotIn(str(app), result.stdout + result.stderr)
            self.assertFalse(expected.exists())

    def test_release_dmg_rejects_stale_source_fingerprint_before_signing(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        guard = "build_dmg:FAIL:helper_source_fingerprint_stale"
        signing = '"$project_root/packaging/sign_app.sh" "$app_path"'
        self.assertIn(guard, source)
        self.assertLess(source.index(guard), source.index(signing))

    def test_dmg_creation_rejects_a_zero_byte_artifact_before_validation(self) -> None:
        build_root = PROJECT_ROOT / "build"
        output_parent = PROJECT_ROOT / "dist"
        build_root.mkdir(exist_ok=True)
        output_parent.mkdir(exist_ok=True)

        with (
            tempfile.TemporaryDirectory(dir=build_root) as build_tmp,
            tempfile.TemporaryDirectory(dir=output_parent) as output_tmp,
        ):
            app, environment = self._fixtures(Path(build_tmp))
            imageinfo_marker = Path(build_tmp) / "imageinfo-invoked"
            fake_hdiutil = Path(build_tmp) / "bin" / "hdiutil"
            fake_hdiutil.write_text(
                "#!/bin/zsh\n"
                "if [[ \"$1\" == \"create\" ]]; then\n"
                "  : > \"${@[-1]}\"\n"
                "  exit 0\n"
                "fi\n"
                "if [[ \"$1\" == \"imageinfo\" ]]; then\n"
                f"  : > '{imageinfo_marker}'\n"
                "  exit 0\n"
                "fi\n"
                "exit 1\n",
                encoding="utf-8",
            )
            fake_hdiutil.chmod(0o755)

            result = subprocess.run(
                ["zsh", str(SCRIPT), str(app), output_tmp],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            expected = Path(output_tmp) / "PhotosLocalKeywordIndexer-1.2.3-45-dev-arm64.dmg"
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("dmg_empty_artifact", result.stderr)
            self.assertFalse(expected.exists())
            self.assertFalse(imageinfo_marker.exists())

    def test_dmg_layout_requires_frozen_helper_runtime_verification(self) -> None:
        source = (PROJECT_ROOT / "packaging" / "verify_dmg_layout.sh").read_text(encoding="utf-8")

        helper_verifier = '"$project_root/packaging/verify_embedded_helper.sh" "$app_path"'
        self.assertIn(helper_verifier, source)
        self.assertIn("dmg_helper_runtime", source)
        self.assertLess(source.index(helper_verifier), source.index("emit_json READY none"))

    def test_release_identity_preflight_precedes_output_reservation(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")

        identity_guard = '[[ "$release_build" == "1" && -z "${DEVELOPER_ID_APPLICATION:-}" ]]'
        output_creation = 'mkdir -p "$output_root"'
        reservation = 'if ! mkdir "$reservation_path" 2>/dev/null; then'
        self.assertIn(identity_guard, source)
        self.assertLess(source.index(identity_guard), source.index(output_creation))
        self.assertLess(source.index(identity_guard), source.index(reservation))

    def test_dmg_checks_plistbuddy_before_reading_bundle_metadata(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")

        gate = "build_dmg:FAIL:plistbuddy_missing"
        metadata_read = '/usr/libexec/PlistBuddy -c "Print :CFBundleShortVersionString"'
        reservation = 'if ! mkdir "$reservation_path" 2>/dev/null; then'
        self.assertIn(gate, source)
        self.assertLess(source.index(gate), source.index(metadata_read))
        self.assertLess(source.index(gate), source.index(reservation))

    def test_dmg_cleanup_only_removes_the_output_directory_it_created(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")

        self.assertIn('output_root_created_identity=""', source)
        self.assertIn("record_output_root_identity()", source)
        self.assertIn("output_root_is_owned()", source)
        self.assertIn("stat -f '%d:%i' -- \"$output_root\"", source)
        self.assertIn(
            "if (( preserve_dmg == 0 && output_root_preexisting == 0 )) && output_root_is_owned; then\n"
            '    rmdir -- "$output_root" >/dev/null 2>&1 || cleanup_failed=1\n'
            "  fi",
            source,
        )
        creation = source.index('mkdir -p "$output_root"')
        identity = source.index("record_output_root_identity", creation)
        reservation = source.index('if ! mkdir "$reservation_path" 2>/dev/null; then')
        self.assertLess(identity, reservation)

    def test_release_missing_identity_fails_before_bundle_and_tool_inspection(self) -> None:
        (PROJECT_ROOT / "build").mkdir(exist_ok=True)
        (PROJECT_ROOT / "dist").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "build") as app_tmp, tempfile.TemporaryDirectory(
            dir=PROJECT_ROOT / "dist"
        ) as output_tmp:
            app = Path(app_tmp) / "PhotosLocalKeywordIndexer.app"
            app.mkdir()
            environment = os.environ.copy()
            environment["RELEASE_BUILD"] = "1"
            environment.pop("DEVELOPER_ID_APPLICATION", None)

            result = subprocess.run(
                ["/bin/zsh", str(SCRIPT), str(app), output_tmp],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(
            result.stderr,
            "build_dmg:FAIL:developer_id_identity_missing\n"
            "build_dmg:HINT:developer_id_identity_missing:install_or_select_a_developer_id_application_identity\n",
        )
        self.assertNotIn(str(app), result.stderr)

    def test_incomplete_app_bundle_has_a_stable_rebuild_hint(self) -> None:
        build_root = PROJECT_ROOT / "build"
        output_parent = PROJECT_ROOT / "dist"
        build_root.mkdir(exist_ok=True)
        output_parent.mkdir(exist_ok=True)

        with (
            tempfile.TemporaryDirectory(dir=build_root) as app_tmp,
            tempfile.TemporaryDirectory(dir=output_parent) as output_tmp,
        ):
            app = Path(app_tmp) / "PhotosLocalKeywordIndexer.app"
            app.mkdir()
            environment = os.environ.copy()
            environment["RELEASE_BUILD"] = "0"

            result = subprocess.run(
                ["/bin/zsh", str(SCRIPT), str(app), output_tmp],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(
            result.stderr,
            "build_dmg:FAIL:app_bundle_incomplete\n"
            "build_dmg:HINT:app_bundle_incomplete:rebuild_the_app_before_creating_a_dmg\n",
        )
        self.assertNotIn(str(app), result.stderr)

    def test_missing_app_bundle_has_a_sanitized_path_hint(self) -> None:
        build_root = PROJECT_ROOT / "build"
        output_parent = PROJECT_ROOT / "dist"
        build_root.mkdir(exist_ok=True)
        output_parent.mkdir(exist_ok=True)

        with (
            tempfile.TemporaryDirectory(dir=build_root) as build_tmp,
            tempfile.TemporaryDirectory(dir=output_parent) as output_tmp,
        ):
            app = Path(build_tmp) / "PhotosLocalKeywordIndexer.app"
            environment = os.environ.copy()
            environment["RELEASE_BUILD"] = "0"

            result = subprocess.run(
                ["/bin/zsh", str(SCRIPT), str(app), output_tmp],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertEqual(list(Path(output_tmp).iterdir()), [])

        self.assertEqual(result.returncode, 2)
        self.assertEqual(
            result.stderr,
            "build_dmg:FAIL:app_bundle_path_invalid\n"
            "build_dmg:HINT:app_bundle_path_invalid:provide_a_complete_app_bundle_under_build\n",
        )
        self.assertNotIn(str(PROJECT_ROOT), result.stderr)
        self.assertNotIn(str(app), result.stderr)

    def test_invalid_app_version_has_a_stable_rebuild_hint_before_output_reservation(self) -> None:
        build_root = PROJECT_ROOT / "build"
        output_parent = PROJECT_ROOT / "dist"
        build_root.mkdir(exist_ok=True)
        output_parent.mkdir(exist_ok=True)

        with (
            tempfile.TemporaryDirectory(dir=build_root) as build_tmp,
            tempfile.TemporaryDirectory(dir=output_parent) as output_tmp,
        ):
            app, environment = self._fixtures(Path(build_tmp), version="1.2", build="0")

            result = subprocess.run(
                ["/bin/zsh", str(SCRIPT), str(app), output_tmp],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertFalse(Path(environment["HDIUTIL_MARKER"]).exists())
            self.assertEqual(list(Path(output_tmp).iterdir()), [])

        self.assertEqual(result.returncode, 2)
        self.assertEqual(
            result.stderr,
            "build_dmg:FAIL:app_version_invalid\n"
            "build_dmg:HINT:app_version_invalid:fix_app_version_and_rebuild\n",
        )
        self.assertNotIn(str(app), result.stderr)

    def test_release_tool_errors_are_sanitized_before_publishing_dmg(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")

        self.assertIn(
            'if ! spctl --assess --type execute --verbose=4 "$app_path" >/dev/null 2>&1; then',
            source,
        )
        self.assertIn("build_dmg:FAIL:app_assessment_invalid", source)
        self.assertIn(
            'if ! codesign --force --sign "$DEVELOPER_ID_APPLICATION" --timestamp "$dmg_path" >/dev/null 2>&1; then',
            source,
        )
        self.assertIn("build_dmg:FAIL:dmg_signing_failed", source)
        self.assertIn(
            'if ! codesign --verify --verbose=2 --strict "$dmg_path" >/dev/null 2>&1; then',
            source,
        )
        self.assertIn("build_dmg:FAIL:dmg_signature_invalid", source)

    def test_release_dmg_rejects_stale_embedded_helper_before_signing(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")

        freshness_check = 'cmp -s "$source_helper" "$app_helper"'
        self.assertIn(freshness_check, source)
        self.assertIn("build_dmg:FAIL:embedded_helper_stale", source)
        self.assertIn(
            "build_dmg:HINT:embedded_helper_stale:rebuild_the_app_with_the_current_helper",
            source,
        )
        self.assertLess(source.index("embedded_helper_stale"), source.index('"$project_root/packaging/sign_app.sh"'))

    def test_release_dmg_checks_the_complete_embedded_helper_payload_before_signing(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")

        self.assertIn("helper_payload_stale", source)
        self.assertIn("helper_payload_check", source)
        self.assertIn("os.walk", source)
        self.assertLess(source.index("helper_payload_stale"), source.index('"$project_root/packaging/sign_app.sh"'))

    def test_development_dmg_name_is_unambiguously_notarization_ineligible(self) -> None:
        build_root = PROJECT_ROOT / "build"
        output_parent = PROJECT_ROOT / "dist"
        build_root.mkdir(exist_ok=True)
        output_parent.mkdir(exist_ok=True)

        with (
            tempfile.TemporaryDirectory(dir=build_root) as build_tmp,
            tempfile.TemporaryDirectory(dir=output_parent) as output_tmp,
        ):
            app, environment = self._fixtures(Path(build_tmp))

            result = subprocess.run(
                ["zsh", str(SCRIPT), str(app), output_tmp],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            expected = Path(output_tmp) / "PhotosLocalKeywordIndexer-1.2.3-45-dev-arm64.dmg"
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(expected.is_file(), result.stdout)
            self.assertTrue(result.stdout.strip().endswith("release_dmg:READY_DEV"))

    def test_successful_build_keeps_a_new_output_directory_that_contains_the_dmg(self) -> None:
        build_root = PROJECT_ROOT / "build"
        output_parent = PROJECT_ROOT / "dist"
        build_root.mkdir(exist_ok=True)
        output_parent.mkdir(exist_ok=True)

        with (
            tempfile.TemporaryDirectory(dir=build_root) as build_tmp,
            tempfile.TemporaryDirectory(dir=output_parent) as output_tmp,
        ):
            app, environment = self._fixtures(Path(build_tmp))
            output_root = Path(output_tmp) / "new-output"

            result = subprocess.run(
                ["zsh", str(SCRIPT), str(app), str(output_root)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            expected = output_root / "PhotosLocalKeywordIndexer-1.2.3-45-dev-arm64.dmg"
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(expected.is_file(), result.stdout)
            self.assertTrue(result.stdout.strip().endswith("release_dmg:READY_DEV"))

    def test_dmg_build_neutralizes_a_permissive_caller_umask(self) -> None:
        build_root = PROJECT_ROOT / "build"
        output_parent = PROJECT_ROOT / "dist"
        build_root.mkdir(exist_ok=True)
        output_parent.mkdir(exist_ok=True)

        with (
            tempfile.TemporaryDirectory(dir=build_root) as build_tmp,
            tempfile.TemporaryDirectory(dir=output_parent) as output_tmp,
        ):
            app, environment = self._fixtures(Path(build_tmp))
            result = subprocess.run(
                [
                    "/bin/zsh",
                    "-c",
                    'umask 000; exec /bin/zsh "$@"',
                    "build-dmg-under-permissive-umask",
                    str(SCRIPT),
                    str(app),
                    output_tmp,
                ],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            expected = Path(output_tmp) / "PhotosLocalKeywordIndexer-1.2.3-45-dev-arm64.dmg"
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(expected.is_file())
            self.assertEqual(
                expected.stat().st_mode & 0o022,
                0,
                "a caller umask must not produce a group/world-writable release artifact",
            )

    def test_dmg_layout_json_report_is_machine_readable_and_path_free(self) -> None:
        build_root = PROJECT_ROOT / "build"
        output_parent = PROJECT_ROOT / "dist"
        build_root.mkdir(exist_ok=True)
        output_parent.mkdir(exist_ok=True)

        with (
            tempfile.TemporaryDirectory(dir=build_root) as build_tmp,
            tempfile.TemporaryDirectory(dir=output_parent) as output_tmp,
        ):
            app, environment = self._fixtures(Path(build_tmp))
            build_result = subprocess.run(
                ["zsh", str(SCRIPT), str(app), output_tmp],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertEqual(build_result.returncode, 0, build_result.stderr)
            dmg = Path(output_tmp) / "PhotosLocalKeywordIndexer-1.2.3-45-dev-arm64.dmg"
            verify_result = subprocess.run(
                ["zsh", str(PROJECT_ROOT / "packaging" / "verify_dmg_layout.sh"), "--json", str(dmg)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertEqual(verify_result.returncode, 0, verify_result.stderr)
            self.assertEqual(verify_result.stderr, "")
            report = json.loads(verify_result.stdout)
            self.assertEqual(report["schema_version"], 1)
            self.assertEqual(report["status"], "ready")
            self.assertEqual(report["next_action"], "run_release_preflight_then_build_release")
            self.assertNotIn(str(PROJECT_ROOT), verify_result.stdout)
            self.assertTrue(all(set(check) == {"state", "code"} for check in report["checks"]))

            human_result = subprocess.run(
                ["zsh", str(PROJECT_ROOT / "packaging" / "verify_dmg_layout.sh"), str(dmg)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertEqual(human_result.returncode, 0, human_result.stderr)
            self.assertIn(
                "dmg_layout:HINT:development_build:"
                "not_for_official_distribution_run_verify_public_beta_before_any_authorized_publication\n",
                human_result.stdout,
            )
            self.assertTrue(human_result.stdout.strip().endswith("dmg_layout:READY_DEV"))

    def test_dmg_layout_invalid_arguments_are_path_free_in_human_and_json_modes(self) -> None:
        script = PROJECT_ROOT / "packaging" / "verify_dmg_layout.sh"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "hdiutil-invoked"
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_hdiutil = fake_bin / "hdiutil"
            fake_hdiutil.write_text(f"#!/bin/zsh\n: > '{marker}'\nexit 0\n", encoding="utf-8")
            fake_hdiutil.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{fake_bin}:{environment['PATH']}"

            human_result = subprocess.run(
                ["/bin/zsh", str(script), "relative.dmg"],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            json_result = subprocess.run(
                ["/bin/zsh", str(script), "--json", "relative.dmg"],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertFalse(marker.exists())

        self.assertEqual(human_result.returncode, 2)
        self.assertEqual(
            human_result.stderr,
            "dmg_layout:FAIL:invalid_arguments\n"
            "dmg_layout:HINT:invalid_arguments:provide_an_absolute_dmg_under_dist\n",
        )
        self.assertNotIn(str(PROJECT_ROOT), human_result.stdout + human_result.stderr)
        self.assertEqual(json_result.returncode, 2)
        self.assertEqual(json_result.stderr, "")
        report = json.loads(json_result.stdout)
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["checks"], [{"state": "FAIL", "code": "invalid_arguments"}])
        self.assertEqual(report["next_action"], "provide_an_absolute_dmg_under_dist")
        self.assertNotIn(str(PROJECT_ROOT), json_result.stdout)

    def test_development_dmg_does_not_overwrite_the_versioned_artifact(self) -> None:
        build_root = PROJECT_ROOT / "build"
        output_parent = PROJECT_ROOT / "dist"
        build_root.mkdir(exist_ok=True)
        output_parent.mkdir(exist_ok=True)

        with (
            tempfile.TemporaryDirectory(dir=build_root) as build_tmp,
            tempfile.TemporaryDirectory(dir=output_parent) as output_tmp,
        ):
            app, environment = self._fixtures(Path(build_tmp))
            expected = Path(output_tmp) / "PhotosLocalKeywordIndexer-1.2.3-45-dev-arm64.dmg"
            expected.write_bytes(b"existing release")

            result = subprocess.run(
                ["zsh", str(SCRIPT), str(app), output_tmp],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("Refusing to overwrite", result.stderr)
            self.assertNotIn(str(output_tmp), result.stderr)
            self.assertEqual(expected.read_bytes(), b"existing release")

    def test_concurrent_dmg_builds_reserve_the_artifact_name_atomically(self) -> None:
        build_root = PROJECT_ROOT / "build"
        output_parent = PROJECT_ROOT / "dist"
        build_root.mkdir(exist_ok=True)
        output_parent.mkdir(exist_ok=True)

        with (
            tempfile.TemporaryDirectory(dir=build_root) as build_tmp,
            tempfile.TemporaryDirectory(dir=output_parent) as output_tmp,
        ):
            app, environment = self._fixtures(Path(build_tmp))
            environment["VERIFY_DMG_LAYOUT"] = "0"
            environment["HDIUTIL_CREATE_SLEEP"] = "1"
            expected = Path(output_tmp) / "PhotosLocalKeywordIndexer-1.2.3-45-dev-arm64.dmg"
            reservation = Path(f"{expected}.reservation")

            first = subprocess.Popen(
                ["zsh", str(SCRIPT), str(app), output_tmp],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=environment,
            )
            try:
                deadline = time.monotonic() + 3
                while not reservation.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(reservation.is_dir(), "the first build must reserve its output")

                second = subprocess.run(
                    ["zsh", str(SCRIPT), str(app), output_tmp],
                    check=False,
                    capture_output=True,
                    text=True,
                    env=environment,
                )
                self.assertEqual(second.returncode, 2)
                self.assertIn("Another build is already producing", second.stderr)
            finally:
                first_stdout, first_stderr = first.communicate(timeout=5)

            self.assertEqual(first.returncode, 0, first_stderr)
            self.assertTrue(expected.is_file(), first_stdout)
            self.assertFalse(reservation.exists(), "the owner must release its reservation")

    def test_dmg_cleanup_does_not_remove_a_replaced_reservation(self) -> None:
        build_root = PROJECT_ROOT / "build"
        output_parent = PROJECT_ROOT / "dist"
        build_root.mkdir(exist_ok=True)
        output_parent.mkdir(exist_ok=True)

        with (
            tempfile.TemporaryDirectory(dir=build_root) as build_tmp,
            tempfile.TemporaryDirectory(dir=output_parent) as output_tmp,
        ):
            app, environment = self._fixtures(Path(build_tmp))
            environment["VERIFY_DMG_LAYOUT"] = "0"
            fake_hdiutil = Path(build_tmp) / "bin" / "hdiutil"
            fake_hdiutil.write_text(
                "#!/bin/zsh\n"
                "if [[ \"$1\" == \"create\" ]]; then\n"
                "  target=\"${@[-1]}\"\n"
                "  lock=\"$target.reservation\"\n"
                "  rm -f -- \"$lock/owner\"\n"
                "  rmdir -- \"$lock\"\n"
                "  mkdir -- \"$lock\"\n"
                "  print -n x > \"$target\"\n"
                "  exit 0\n"
                "fi\n"
                "if [[ \"$1\" == \"imageinfo\" ]]; then exit 0; fi\n"
                "exit 1\n",
                encoding="utf-8",
            )
            fake_hdiutil.chmod(0o755)
            expected = Path(output_tmp) / "PhotosLocalKeywordIndexer-1.2.3-45-dev-arm64.dmg"
            reservation = Path(f"{expected}.reservation")

            result = subprocess.run(
                ["zsh", str(SCRIPT), str(app), output_tmp],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(expected.is_file(), result.stdout)
            self.assertTrue(
                reservation.is_dir(),
                "cleanup must not remove a reservation that replaced the owner's lock",
            )

    def test_dmg_removes_new_output_directory_when_reservation_fails(self) -> None:
        build_root = PROJECT_ROOT / "build"
        output_parent = PROJECT_ROOT / "dist"
        build_root.mkdir(exist_ok=True)
        output_parent.mkdir(exist_ok=True)

        with tempfile.TemporaryDirectory(dir=build_root) as build_tmp:
            app, environment = self._fixtures(Path(build_tmp))
            output_root = output_parent / Path(build_tmp).name / "new-output"
            fake_mkdir = Path(build_tmp) / "bin" / "mkdir"
            fake_mkdir.write_text(
                "#!/bin/zsh\n"
                "if [[ \"$1\" == \"-p\" ]]; then exec /bin/mkdir \"$@\"; fi\n"
                "exit 1\n",
                encoding="utf-8",
            )
            fake_mkdir.chmod(0o755)

            result = subprocess.run(
                ["zsh", str(SCRIPT), str(app), str(output_root)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("Another build is already producing", result.stderr)
            self.assertFalse(output_root.exists(), "a failed reservation must not leave a new output directory")

    def test_dmg_rejects_a_group_world_writable_output_directory_before_hdiutil(self) -> None:
        build_root = PROJECT_ROOT / "build"
        output_parent = PROJECT_ROOT / "dist"
        build_root.mkdir(exist_ok=True)
        output_parent.mkdir(exist_ok=True)

        with (
            tempfile.TemporaryDirectory(dir=build_root) as build_tmp,
            tempfile.TemporaryDirectory(dir=output_parent) as output_tmp,
        ):
            app, environment = self._fixtures(Path(build_tmp))
            output_root = Path(output_tmp)
            output_root.chmod(0o777)

            result = subprocess.run(
                ["zsh", str(SCRIPT), str(app), str(output_root)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("output_directory_permissions", result.stderr)
            self.assertFalse(
                Path(environment["HDIUTIL_MARKER"]).exists(),
                "an unsafe destination must fail before hdiutil can create an artifact",
            )

    def test_development_dmg_rejects_unsafe_bundle_version_before_hdiutil(self) -> None:
        build_root = PROJECT_ROOT / "build"
        output_parent = PROJECT_ROOT / "dist"
        build_root.mkdir(exist_ok=True)
        output_parent.mkdir(exist_ok=True)

        with (
            tempfile.TemporaryDirectory(dir=build_root) as build_tmp,
            tempfile.TemporaryDirectory(dir=output_parent) as output_tmp,
        ):
            app, environment = self._fixtures(Path(build_tmp), version="1.2.3/../../outside")
            marker = Path(build_tmp) / "hdiutil-invoked"

            result = subprocess.run(
                ["zsh", str(SCRIPT), str(app), output_tmp],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("build_dmg:FAIL:app_version_invalid", result.stderr)
            self.assertIn(
                "build_dmg:HINT:app_version_invalid:fix_app_version_and_rebuild",
                result.stderr,
            )
            self.assertFalse(marker.exists(), "unsafe metadata must fail before hdiutil runs")

    def test_development_dmg_rejects_an_unexpected_bundle_name_before_hdiutil(self) -> None:
        build_root = PROJECT_ROOT / "build"
        output_parent = PROJECT_ROOT / "dist"
        build_root.mkdir(exist_ok=True)
        output_parent.mkdir(exist_ok=True)

        with (
            tempfile.TemporaryDirectory(dir=build_root) as build_tmp,
            tempfile.TemporaryDirectory(dir=output_parent) as output_tmp,
        ):
            app, environment = self._fixtures(Path(build_tmp))
            unexpected_app = app.with_name("UnexpectedProduct.app")
            app.rename(unexpected_app)

            result = subprocess.run(
                ["zsh", str(SCRIPT), str(unexpected_app), output_tmp],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("PhotosLocalKeywordIndexer.app", result.stderr)
            self.assertFalse(
                Path(environment["HDIUTIL_MARKER"]).exists(),
                "an unexpected bundle name must fail before hdiutil runs",
            )

    def test_development_dmg_rejects_invalid_layout_verification_flag_before_hdiutil(self) -> None:
        build_root = PROJECT_ROOT / "build"
        output_parent = PROJECT_ROOT / "dist"
        build_root.mkdir(exist_ok=True)
        output_parent.mkdir(exist_ok=True)

        with (
            tempfile.TemporaryDirectory(dir=build_root) as build_tmp,
            tempfile.TemporaryDirectory(dir=output_parent) as output_tmp,
        ):
            app, environment = self._fixtures(Path(build_tmp))
            environment["VERIFY_DMG_LAYOUT"] = "maybe"

            result = subprocess.run(
                ["zsh", str(SCRIPT), str(app), output_tmp],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertEqual(result.returncode, 2)
            self.assertEqual(
                result.stderr,
                "build_dmg:FAIL:verify_dmg_layout_invalid\n"
                "build_dmg:HINT:verify_dmg_layout_invalid:use_VERIFY_DMG_LAYOUT_0_or_1\n",
            )
            self.assertFalse(Path(environment["HDIUTIL_MARKER"]).exists())
            self.assertEqual(list(Path(output_tmp).iterdir()), [])

    def test_development_dmg_rejects_invalid_release_flag_before_hdiutil(self) -> None:
        build_root = PROJECT_ROOT / "build"
        output_parent = PROJECT_ROOT / "dist"
        build_root.mkdir(exist_ok=True)
        output_parent.mkdir(exist_ok=True)

        with (
            tempfile.TemporaryDirectory(dir=build_root) as build_tmp,
            tempfile.TemporaryDirectory(dir=output_parent) as output_tmp,
        ):
            app, environment = self._fixtures(Path(build_tmp))
            environment["RELEASE_BUILD"] = "maybe"

            result = subprocess.run(
                ["/bin/zsh", str(SCRIPT), str(app), output_tmp],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertEqual(result.returncode, 2)
            self.assertEqual(
                result.stderr,
                "build_dmg:FAIL:release_build_invalid\n"
                "build_dmg:HINT:release_build_invalid:use_RELEASE_BUILD_0_or_1\n",
            )
            self.assertFalse(Path(environment["HDIUTIL_MARKER"]).exists())
            self.assertEqual(list(Path(output_tmp).iterdir()), [])

    def test_release_dmg_rejects_disabled_layout_verification_before_hdiutil(self) -> None:
        build_root = PROJECT_ROOT / "build"
        output_parent = PROJECT_ROOT / "dist"
        build_root.mkdir(exist_ok=True)
        output_parent.mkdir(exist_ok=True)

        with (
            tempfile.TemporaryDirectory(dir=build_root) as build_tmp,
            tempfile.TemporaryDirectory(dir=output_parent) as output_tmp,
        ):
            app, environment = self._fixtures(Path(build_tmp))
            environment["RELEASE_BUILD"] = "1"
            environment["VERIFY_DMG_LAYOUT"] = "0"

            result = subprocess.run(
                ["zsh", str(SCRIPT), str(app), output_tmp],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("build_dmg:FAIL:dmg_layout_verification_required", result.stderr)
            self.assertFalse(Path(environment["HDIUTIL_MARKER"]).exists())

    def test_dmg_rejects_output_symlink_even_when_target_stays_inside_dist(self) -> None:
        build_root = PROJECT_ROOT / "build"
        output_parent = PROJECT_ROOT / "dist"
        build_root.mkdir(exist_ok=True)
        output_parent.mkdir(exist_ok=True)

        with (
            tempfile.TemporaryDirectory(dir=build_root) as build_tmp,
            tempfile.TemporaryDirectory(dir=output_parent) as output_tmp,
        ):
            app, environment = self._fixtures(Path(build_tmp))
            real_parent = Path(output_tmp) / "real-parent"
            real_parent.mkdir()
            link = Path(output_tmp) / "linked-parent"
            link.symlink_to(real_parent, target_is_directory=True)
            output_path = link / "new-output"

            result = subprocess.run(
                ["zsh", str(SCRIPT), str(app), str(output_path)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("build_dmg:FAIL:output_directory_symlink_escape", result.stderr)
            self.assertIn("symlink", result.stderr.lower())
            self.assertFalse((real_parent / "PhotosLocalKeywordIndexer-1.2.3-45-dev-arm64.dmg").exists())

    def test_development_dmg_removes_partial_artifact_when_image_validation_fails(self) -> None:
        build_root = PROJECT_ROOT / "build"
        output_parent = PROJECT_ROOT / "dist"
        build_root.mkdir(exist_ok=True)
        output_parent.mkdir(exist_ok=True)

        with (
            tempfile.TemporaryDirectory(dir=build_root) as build_tmp,
            tempfile.TemporaryDirectory(dir=output_parent) as output_tmp,
        ):
            app, environment = self._fixtures(Path(build_tmp))
            environment["HDIUTIL_IMAGEINFO_STATUS"] = "1"
            expected = Path(output_tmp) / "PhotosLocalKeywordIndexer-1.2.3-45-dev-arm64.dmg"

            result = subprocess.run(
                ["zsh", str(SCRIPT), str(app), output_tmp],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("build_dmg:FAIL:dmg_image_invalid", result.stderr)
            self.assertFalse(expected.exists(), "a failed validation must not leave a partial DMG")

    def test_dmg_imageinfo_failure_does_not_echo_artifact_path(self) -> None:
        """Tool diagnostics must not disclose the private release artifact path."""
        build_root = PROJECT_ROOT / "build"
        output_parent = PROJECT_ROOT / "dist"
        build_root.mkdir(exist_ok=True)
        output_parent.mkdir(exist_ok=True)

        with (
            tempfile.TemporaryDirectory(dir=build_root) as build_tmp,
            tempfile.TemporaryDirectory(dir=output_parent) as output_tmp,
        ):
            app, environment = self._fixtures(Path(build_tmp))
            fake_hdiutil = Path(build_tmp) / "bin" / "hdiutil"
            fake_hdiutil.write_text(
                "#!/bin/zsh\n"
                "if [[ \"$1\" == \"create\" ]]; then\n"
                "  print -n x > \"${@[-1]}\"\n"
                "  exit 0\n"
                "fi\n"
                "if [[ \"$1\" == \"imageinfo\" ]]; then\n"
                "  print -u2 -- \"$2\"\n"
                "  exit 1\n"
                "fi\n"
                "exit 1\n",
                encoding="utf-8",
            )
            fake_hdiutil.chmod(0o755)

            result = subprocess.run(
                ["zsh", str(SCRIPT), str(app), output_tmp],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            expected = str(Path(output_tmp) / "PhotosLocalKeywordIndexer-1.2.3-45-dev-arm64.dmg")
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn(expected, result.stdout + result.stderr)
            self.assertEqual(
                result.stderr,
                "build_dmg:FAIL:dmg_image_invalid\n"
                "build_dmg:HINT:dmg_image_invalid:retry_the_dmg_build\n",
            )

    def test_dmg_cleanup_does_not_remove_a_replacement_artifact(self) -> None:
        build_root = PROJECT_ROOT / "build"
        output_parent = PROJECT_ROOT / "dist"
        build_root.mkdir(exist_ok=True)
        output_parent.mkdir(exist_ok=True)

        with (
            tempfile.TemporaryDirectory(dir=build_root) as build_tmp,
            tempfile.TemporaryDirectory(dir=output_parent) as output_tmp,
        ):
            app, environment = self._fixtures(Path(build_tmp))
            fake_hdiutil = Path(build_tmp) / "bin" / "hdiutil"
            fake_hdiutil.write_text(
                "#!/bin/zsh\n"
                "if [[ \"$1\" == \"create\" ]]; then\n"
                "  print -n x > \"${@[-1]}\"\n"
                "  exit 0\n"
                "fi\n"
                "if [[ \"$1\" == \"imageinfo\" ]]; then\n"
                "  rm -f -- \"$2\"\n"
                "  print -n -- replacement > \"$2\"\n"
                "  exit 1\n"
                "fi\n"
                "exit 1\n",
                encoding="utf-8",
            )
            fake_hdiutil.chmod(0o755)
            expected = Path(output_tmp) / "PhotosLocalKeywordIndexer-1.2.3-45-dev-arm64.dmg"

            result = subprocess.run(
                ["zsh", str(SCRIPT), str(app), output_tmp],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("build_dmg:FAIL:dmg_image_invalid", result.stderr)
            self.assertTrue(expected.is_file(), "cleanup must not delete a replacement artifact")
            self.assertEqual(expected.read_bytes(), b"replacement")

    def test_dmg_cleanup_does_not_remove_a_replaced_staging_directory(self) -> None:
        build_root = PROJECT_ROOT / "build"
        output_parent = PROJECT_ROOT / "dist"
        build_root.mkdir(exist_ok=True)
        output_parent.mkdir(exist_ok=True)

        with (
            tempfile.TemporaryDirectory(dir=build_root) as build_tmp,
            tempfile.TemporaryDirectory(dir=output_parent) as output_tmp,
        ):
            app, environment = self._fixtures(Path(build_tmp))
            environment["VERIFY_DMG_LAYOUT"] = "0"
            staging_path_file = Path(build_tmp) / "staging-path"
            environment["STAGING_PATH_MARKER"] = str(staging_path_file)
            fake_hdiutil = Path(build_tmp) / "bin" / "hdiutil"
            fake_hdiutil.write_text(
                "#!/bin/zsh\n"
                "if [[ \"$1\" == \"create\" ]]; then\n"
                "  args=(\"$@\")\n"
                "  source_dir=\"\"\n"
                "  for (( i = 1; i <= $#; i++ )); do\n"
                "    if [[ \"${args[i]}\" == \"-srcfolder\" ]]; then source_dir=\"${args[i + 1]}\"; break; fi\n"
                "  done\n"
                "  print -r -- \"$source_dir\" > \"$STAGING_PATH_MARKER\"\n"
                "  /bin/rm -rf -- \"$source_dir\"\n"
                "  /bin/mkdir -- \"$source_dir\"\n"
                "  print -n -- replacement > \"$source_dir/replacement-marker\"\n"
                "  : > \"${@[-1]}\"\n"
                "  exit 1\n"
                "fi\n"
                "exit 1\n",
                encoding="utf-8",
            )
            fake_hdiutil.chmod(0o755)

            result = subprocess.run(
                ["zsh", str(SCRIPT), str(app), output_tmp],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            staging_dir = Path(staging_path_file.read_text(encoding="utf-8").strip())
            self.assertTrue(
                (staging_dir / "replacement-marker").is_file(),
                "cleanup must not remove a staging directory replaced during hdiutil",
            )

    def test_dmg_rechecks_staging_identity_before_population_and_creation(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        guard = (
            "if ! staging_is_owned; then\n"
            '  print -u2 -- "build_dmg:FAIL:staging_directory_invalid"\n'
            '  print -u2 -- "build_dmg:HINT:staging_directory_invalid:retry_the_dmg_build"\n'
            "  exit 1\n"
            "fi\n"
        )

        record = source.index("record_staging_identity\n")
        populate_guard = source.index(guard, record)
        populate = source.index('ditto "$app_path" "$staging_dir/$app_name.app"', record)
        create_guard = source.index(guard, populate_guard + len(guard))
        create = source.index("if ! hdiutil create", populate)
        self.assertLess(record, populate_guard)
        self.assertLess(populate_guard, populate)
        self.assertLess(populate, create_guard)
        self.assertLess(create_guard, create)

    def test_dmg_rechecks_source_app_identity_before_and_after_copy(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        guard = (
            "if ! app_bundle_is_owned; then\n"
            '  print -u2 -- "build_dmg:FAIL:app_bundle_identity_changed"\n'
            '  print -u2 -- "build_dmg:HINT:app_bundle_identity_changed:rebuild_the_app_then_retry"\n'
            "  exit 1\n"
            "fi\n"
        )

        record = source.index("record_app_bundle_identity\n")
        copy_guard = source.index(guard, record)
        copy = source.index('ditto "$app_path" "$staging_dir/$app_name.app"', record)
        result_guard = source.index(guard, copy_guard + len(guard))
        create = source.index("if ! hdiutil create", copy)
        self.assertLess(record, copy_guard)
        self.assertLess(copy_guard, copy)
        self.assertLess(copy, result_guard)
        self.assertLess(result_guard, create)

    def test_development_dmg_rejects_missing_sparkle_framework(self) -> None:
        build_root = PROJECT_ROOT / "build"
        output_parent = PROJECT_ROOT / "dist"
        build_root.mkdir(exist_ok=True)
        output_parent.mkdir(exist_ok=True)

        with (
            tempfile.TemporaryDirectory(dir=build_root) as build_tmp,
            tempfile.TemporaryDirectory(dir=output_parent) as output_tmp,
        ):
            app, environment = self._fixtures(Path(build_tmp))
            shutil.rmtree(app / "Contents" / "Frameworks" / "Sparkle.framework")

            result = subprocess.run(
                ["zsh", str(SCRIPT), str(app), output_tmp],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Sparkle", result.stderr)

    @staticmethod
    def _fixtures(root: Path, *, version: str = "1.2.3", build: str = "45") -> tuple[Path, dict[str, str]]:
        app = root / "PhotosLocalKeywordIndexer.app"
        executable = app / "Contents" / "MacOS" / "PhotosLocalKeywordIndexer"
        executable.parent.mkdir(parents=True)
        executable.touch()
        executable.chmod(0o755)
        helper_app = app / "Contents" / "Helpers" / "PhotosIndexerWorker.app"
        helper = helper_app / "Contents" / "MacOS" / "PhotosIndexerWorker"
        helper_info = helper_app / "Contents" / "Info.plist"
        helper_resources = helper_app / "Contents" / "Resources"
        helper_frameworks = helper_app / "Contents" / "Frameworks"
        helper.parent.mkdir(parents=True)
        helper_resources.mkdir(parents=True)
        helper_frameworks.mkdir(parents=True)
        helper.write_text(
            "#!/bin/zsh\n"
            "if [[ \"$1\" == \"--self-check\" ]]; then\n"
            "  print -- '{\"status\":\"ok\",\"runtime\":\"embedded\",\"architecture\":\"arm64\",\"protocol\":\"jsonl\"}'\n"
            "  exit 0\n"
            "fi\n"
            "exit 1\n",
            encoding="utf-8",
        )
        helper.chmod(0o755)
        with helper_info.open("wb") as stream:
            plistlib.dump(
                {
                    "CFBundleExecutable": "PhotosIndexerWorker",
                    "CFBundleIdentifier": "com.photoslocalkeywordindexer.worker",
                    "CFBundlePackageType": "APPL",
                    "LSUIElement": True,
                    "NSPhotoLibraryUsageDescription": "Fotos",
                    "NSPhotoLibraryAddUsageDescription": "Fotos",
                    "NSAppleEventsUsageDescription": "Fotos",
                },
                stream,
            )
        fingerprint = subprocess.run(
            [
                "/usr/bin/python3",
                str(PROJECT_ROOT / "packaging" / "helper_source_fingerprint.py"),
                str(PROJECT_ROOT),
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        marker = helper_resources / ".photos-indexer-source-fingerprint"
        marker.write_text(fingerprint, encoding="utf-8")
        marker.chmod(0o600)
        icon = app / "Contents" / "Resources" / "AppIcon.icns"
        icon.parent.mkdir(parents=True)
        icon.write_bytes(b"test-icon")
        (app / "Contents" / "Frameworks" / "Sparkle.framework").mkdir(parents=True)
        with (app / "Contents" / "Info.plist").open("wb") as stream:
            plistlib.dump(
                {
                    "CFBundleShortVersionString": version,
                    "CFBundleVersion": build,
                },
                stream,
            )

        fake_bin = root / "bin"
        fake_bin.mkdir()
        fake_hdiutil = fake_bin / "hdiutil"
        fake_hdiutil.write_text(
            """#!/bin/zsh
if [[ "$1" == "create" ]]; then
  : > "$HDIUTIL_MARKER"
  [[ -z "${HDIUTIL_CREATE_SLEEP:-}" ]] || sleep "$HDIUTIL_CREATE_SLEEP"
  print -n x > "${@[-1]}"
  exit 0
fi
if [[ "$1" == "imageinfo" ]]; then
  exit "${HDIUTIL_IMAGEINFO_STATUS:-0}"
fi
if [[ "$1" == "attach" ]]; then
  mount_point="$5"
  cp -R "$FAKE_DMG_APP" "$mount_point/PhotosLocalKeywordIndexer.app"
  ln -s /Applications "$mount_point/Applications"
  exit 0
fi
if [[ "$1" == "detach" ]]; then
  exit 0
fi
exit 1
""",
            encoding="utf-8",
        )
        fake_hdiutil.chmod(0o755)
        fake_lipo = fake_bin / "lipo"
        fake_lipo.write_text(
            '#!/bin/zsh\n[[ "$1" == "-archs" ]] && print -- arm64\n',
            encoding="utf-8",
        )
        fake_lipo.chmod(0o755)
        environment = os.environ.copy()
        environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
        environment["RELEASE_BUILD"] = "0"
        environment["HDIUTIL_MARKER"] = str(root / "hdiutil-invoked")
        environment["FAKE_DMG_APP"] = str(app)
        return app, environment


if __name__ == "__main__":
    unittest.main()
