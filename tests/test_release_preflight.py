from __future__ import annotations

import os
import json
import plistlib
import re
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "packaging" / "release_preflight.sh"


class ReleasePreflightTests(unittest.TestCase):
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

    def test_release_preflight_checks_the_native_helper_app_bundle(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")

        self.assertIn(
            'helper_app="$project_root/build/python-helper/dist/PhotosIndexerWorker.app"',
            source,
        )
        self.assertIn('helper="$helper_app/Contents/MacOS/PhotosIndexerWorker"', source)
        self.assertIn(
            'helper_source_marker="$helper_app/Contents/Resources/.photos-indexer-source-fingerprint"',
            source,
        )
        self.assertIn('helper_info="$helper_app/Contents/Info.plist"', source)
        self.assertIn('CFBundleIdentifier', source)
        self.assertIn('com.photoslocalkeywordindexer.worker', source)
        self.assertIn('CFBundleShortVersionString', source)
        self.assertIn('CFBundleVersion', source)
        self.assertIn('helper_release_metadata_invalid', source)

    def test_release_preflight_script_is_present_and_syntax_valid(self) -> None:
        self.assertTrue(SCRIPT.is_file())
        result = subprocess.run(["zsh", "-n", str(SCRIPT)], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_human_ready_message_keeps_unchecked_runtime_explicit(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")

        self.assertIn(
            "release_preflight:READY:release_gates_available_runtime_not_checked",
            source,
        )
        self.assertNotIn("release_preflight:READY:all_gates_available", source)

    def test_release_preflight_reports_missing_local_requirements_without_secrets(self) -> None:
        result = subprocess.run(["zsh", str(SCRIPT)], capture_output=True, text=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        output = result.stdout + result.stderr
        self.assertRegex(output, r"release_preflight:(PASS|FAIL):")
        self.assertIn("python3.12", output)
        self.assertIn("developer_id", output)
        self.assertRegex(output, r"release_preflight:(HINT|NEXT):")
        self.assertNotIn("helper_missingun_", output)
        self.assertNotIn("DEVELOPER_ID_APPLICATION=", output)
        self.assertNotIn("APPLE_NOTARY_PROFILE=", output)

    def test_release_preflight_validates_the_metadata_required_by_the_release_build(self) -> None:
        environment = os.environ.copy()
        environment.pop("APP_VERSION", None)
        environment.pop("BUILD_NUMBER", None)
        missing = subprocess.run(
            ["zsh", str(SCRIPT), "--json"],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        missing_report = json.loads(missing.stdout)
        self.assertIn(
            {"state": "FAIL", "code": "RELEASE_METADATA_MISSING"},
            missing_report["checks"],
        )
        self.assertIn(
            {"code": "RELEASE_METADATA_MISSING", "action": "set_matching_app_version_and_positive_build_number"},
            missing_report["hints"],
        )

        with (PROJECT_ROOT / "pyproject.toml").open("rb") as stream:
            project_version = tomllib.load(stream)["project"]["version"]
        environment["APP_VERSION"] = project_version
        environment["BUILD_NUMBER"] = "1"
        valid = subprocess.run(
            ["zsh", str(SCRIPT), "--json"],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        valid_report = json.loads(valid.stdout)
        self.assertIn({"state": "PASS", "code": "RELEASE_METADATA"}, valid_report["checks"])

    def test_human_release_preflight_emits_a_hint_for_every_failed_gate(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("print -- \"release_preflight:HINT:${code}:review_release_preflight\"", source)
        result = subprocess.run(["zsh", str(SCRIPT)], capture_output=True, text=True, check=False)
        output = result.stdout + result.stderr
        failed = set(line.rsplit(":", 1)[-1] for line in output.splitlines() if ":FAIL:" in line)
        hinted = set(line.split(":")[2] for line in output.splitlines() if ":HINT:" in line)

        self.assertTrue(failed)
        self.assertTrue(failed.issubset(hinted), (failed - hinted, output))

    def test_missing_xcode_select_has_an_actionable_hint(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        start = source.index("hint_action()")
        end = source.index("\nreport()", start)
        functions = source[start:end]
        command = (
            "set -e\n"
            f"{functions}\n"
            "json_mode=0\n"
            "hint_action xcode_select_missing\n"
            "emit_hint xcode_select_missing\n"
        )
        result = subprocess.run(
            ["/bin/zsh", "-c", command],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            result.stdout,
            "install_full_xcode_and_select_developer_directory\n"
            "release_preflight:HINT:xcode_select_missing:install_full_xcode_and_select_developer_directory\n",
        )
        self.assertEqual(result.stderr, "")

    def test_release_preflight_json_mode_is_machine_readable_and_path_free(self) -> None:
        """The release diagnostic can be attached to a support report safely.

        The JSON form is deliberately made only of bounded status codes and
        actions.  It must not echo the checkout, helper path, environment
        values, or tool stderr (which can contain private paths or credentials).
        """
        result = subprocess.run(["zsh", str(SCRIPT), "--json"], capture_output=True, text=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        diagnostic = json.loads(result.stdout)
        self.assertEqual(diagnostic["schema_version"], 2)
        self.assertEqual(diagnostic["tool"], "photos-local-keyword-indexer")
        self.assertIn(diagnostic["status"], {"ready", "blocked"})
        self.assertEqual(diagnostic["status_scope"], "release_gates")
        self.assertEqual(diagnostic["runtime_status"], "not_checked")
        self.assertRegex(diagnostic["next_action"], r"^[a-z0-9_]+$")
        self.assertRegex(diagnostic["next_action_action"], r"^[a-z0-9_]+$")
        matching_hints = [
            hint for hint in diagnostic["hints"] if hint["code"].casefold() == diagnostic["next_action"].casefold()
        ]
        if matching_hints:
            self.assertEqual(diagnostic["next_action_action"], matching_hints[0]["action"])
        self.assertIsInstance(diagnostic["checks"], list)
        sections = diagnostic["diagnostic_sections"]
        self.assertEqual(
            [section["id"] for section in sections],
            ["toolchain", "signing", "notarization", "runtime"],
        )
        for section in sections[:3]:
            self.assertIn(section["state"], {"ready", "blocked"})
            self.assertIsInstance(section["failed_codes"], list)
            self.assertTrue(all(re.fullmatch(r"[A-Z0-9_]+", code) for code in section["failed_codes"]))
        self.assertEqual(
            sections[3],
            {
                "id": "runtime",
                "state": "not_checked",
                "failed_codes": [
                    "PHOTOS_TCC_RUNTIME_TEST_REQUIRED",
                    "AUTOMATION_TCC_RUNTIME_TEST_REQUIRED",
                ],
                "next_action": "run_signed_smoke_test",
            },
        )
        self.assertEqual(
            diagnostic["runtime_checks"],
            [
                {
                    "surface": "photos",
                    "state": "NOT_CHECKED",
                    "code": "PHOTOS_TCC_RUNTIME_TEST_REQUIRED",
                    "action": "run_signed_smoke_test",
                },
                {
                    "surface": "automation",
                    "state": "NOT_CHECKED",
                    "code": "AUTOMATION_TCC_RUNTIME_TEST_REQUIRED",
                    "action": "run_signed_smoke_test",
                },
            ],
        )
        serialized = json.dumps(diagnostic, ensure_ascii=False)
        self.assertNotIn(str(PROJECT_ROOT), serialized)
        self.assertNotIn("/Users/", serialized)
        self.assertNotIn("OLLAMA_MODELS", serialized)
        self.assertNotIn("DEVELOPER_ID_APPLICATION", serialized)
        self.assertNotIn("APPLE_NOTARY_PROFILE", serialized)
        for check in diagnostic["checks"]:
            self.assertEqual(set(check), {"state", "code"})
            self.assertRegex(check["state"], r"^(PASS|FAIL)$")
            self.assertRegex(check["code"], r"^[A-Z0-9_]+$")
        for check in diagnostic["runtime_checks"]:
            self.assertEqual(set(check), {"surface", "state", "code", "action"})
            self.assertRegex(check["surface"], r"^(photos|automation)$")
            self.assertEqual(check["state"], "NOT_CHECKED")
            self.assertRegex(check["code"], r"^[A-Z0-9_]+$")
            self.assertRegex(check["action"], r"^[a-z0-9_]+$")

    def test_release_preflight_uses_the_same_developer_id_action_in_human_and_json_modes(self) -> None:
        human = subprocess.run(["zsh", str(SCRIPT)], capture_output=True, text=True, check=False)
        human_action = next(
            line.rsplit(":", 1)[-1]
            for line in human.stdout.splitlines()
            if line.startswith("release_preflight:HINT:developer_id_identity_missing:")
        )

        machine = subprocess.run(["zsh", str(SCRIPT), "--json"], capture_output=True, text=True, check=False)
        diagnostic = json.loads(machine.stdout)
        machine_action = next(
            hint["action"]
            for hint in diagnostic["hints"]
            if hint["code"] == "DEVELOPER_ID_IDENTITY_MISSING"
        )

        self.assertEqual(human_action, machine_action)

    def test_release_preflight_helper_hint_is_machine_safe_in_human_mode(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")

        self.assertIn(
            'print -- "release_preflight:HINT:${code}:run_verify_helper_1_build_python_helper"',
            source,
        )
        self.assertNotIn("run_VERIFY_HELPER_1_build_python_helper", source)
        self.assertIn(
            'print -- "release_preflight:HINT:${code}:rebuild_helper_with_verify_helper_1"',
            source,
        )
        self.assertNotIn("rebuild_helper_with_VERIFY_HELPER_1", source)

    def test_release_preflight_rejects_unknown_arguments_without_echoing_them(self) -> None:
        result = subprocess.run(
            ["zsh", str(SCRIPT), "--json", "private/path/should-not-echo"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr,
            "release_preflight:FAIL:invalid_arguments\n"
            "release_preflight:HINT:invalid_arguments:use_no_arguments_or_json\n",
        )
        self.assertNotIn("private/path/should-not-echo", result.stdout + result.stderr)
        self.assertNotIn(str(PROJECT_ROOT), result.stdout + result.stderr)

    def test_release_preflight_exposes_actionable_hints_for_the_release_blockers(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8") if SCRIPT.exists() else ""
        for code in (
            "xcodebuild_missing",
            "xcode_toolchain_unavailable",
            "python3.12_missing",
            "pyinstaller_missing",
            "helper_missing",
            "helper_binary_invalid",
            "notary_profile_missing",
            "notary_profile_unavailable",
        ):
            self.assertIn(f'"{code}"', source)
        self.assertIn("release_preflight:NEXT:", source)

    def test_release_preflight_requires_a_real_signed_sparkle_configuration(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8") if SCRIPT.exists() else ""
        self.assertIn('sparkle_feed="${SPARKLE_FEED_URL:-}"', source)
        self.assertIn('sparkle_public_key="${SPARKLE_PUBLIC_ED_KEY:-}"', source)
        self.assertIn("sparkle_update_configuration_missing", source)
        self.assertIn("sparkle_update_configuration_invalid", source)
        self.assertIn("base64.b64decode", source)
        self.assertIn('placeholder_domains = ("example.invalid", "example.org", "example.com", "example.net")', source)
        self.assertIn("parsed.port", source)

    def test_release_preflight_reports_missing_sparkle_configuration_without_values(self) -> None:
        environment = os.environ.copy()
        environment.pop("SPARKLE_FEED_URL", None)
        environment.pop("SPARKLE_PUBLIC_ED_KEY", None)
        result = subprocess.run(
            ["zsh", str(SCRIPT)],
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )

        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("release_preflight:FAIL:sparkle_update_configuration_missing", output)
        self.assertIn("release_preflight:HINT:sparkle_update_configuration_missing", output)
        self.assertNotIn("SPARKLE_FEED_URL=", output)
        self.assertNotIn("SPARKLE_PUBLIC_ED_KEY=", output)

    def test_release_preflight_accepts_a_canonical_sparkle_key_and_feed(self) -> None:
        environment = os.environ.copy()
        environment["SPARKLE_FEED_URL"] = "https://updates.photosindexer.app/appcast.xml"
        environment["SPARKLE_PUBLIC_ED_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
        result = subprocess.run(
            ["zsh", str(SCRIPT)],
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )

        output = result.stdout + result.stderr
        self.assertIn("release_preflight:PASS:sparkle_update_configuration", output)

    def test_release_preflight_rejects_placeholder_sparkle_feed(self) -> None:
        for domain in ("example.invalid", "example.org", "example.com", "example.net"):
            with self.subTest(domain=domain):
                environment = os.environ.copy()
                environment["SPARKLE_FEED_URL"] = f"https://updates.{domain}/appcast.xml"
                environment["SPARKLE_PUBLIC_ED_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
                result = subprocess.run(
                    ["zsh", str(SCRIPT)],
                    capture_output=True,
                    text=True,
                    check=False,
                    env=environment,
                )

                output = result.stdout + result.stderr
                self.assertIn("release_preflight:FAIL:sparkle_update_configuration_invalid", output)
                self.assertNotIn(domain, output)

    def test_release_preflight_sanitizes_malformed_sparkle_feed_failure(self) -> None:
        environment = os.environ.copy()
        environment["SPARKLE_FEED_URL"] = "https://[bad"
        environment["SPARKLE_PUBLIC_ED_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
        result = subprocess.run(
            ["zsh", str(SCRIPT)],
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )

        output = result.stdout + result.stderr
        self.assertIn("release_preflight:FAIL:sparkle_update_configuration_invalid", output)
        self.assertNotIn("Traceback", result.stderr)

    def test_release_preflight_rejects_a_malformed_sparkle_port(self) -> None:
        environment = os.environ.copy()
        environment["SPARKLE_FEED_URL"] = "https://updates.photosindexer.app:not-a-port/appcast.xml"
        environment["SPARKLE_PUBLIC_ED_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
        result = subprocess.run(
            ["zsh", str(SCRIPT)],
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )

        output = result.stdout + result.stderr
        self.assertIn("release_preflight:FAIL:sparkle_update_configuration_invalid", output)
        self.assertNotIn("Traceback", result.stderr)

    def test_release_preflight_rejects_a_sparkle_feed_without_a_hostname(self) -> None:
        environment = os.environ.copy()
        environment["SPARKLE_FEED_URL"] = "https://:443/appcast.xml"
        environment["SPARKLE_PUBLIC_ED_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
        result = subprocess.run(
            ["zsh", str(SCRIPT)],
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )

        output = result.stdout + result.stderr
        self.assertIn("release_preflight:FAIL:sparkle_update_configuration_invalid", output)
        self.assertNotIn("release_preflight:PASS:sparkle_update_configuration", output)
        self.assertNotIn("https://", output)

    def test_release_preflight_rejects_sparkle_feed_command_delimiters(self) -> None:
        for delimiter in (';', '"', "'", "\\"):
            with self.subTest(delimiter=repr(delimiter)):
                environment = os.environ.copy()
                environment["SPARKLE_FEED_URL"] = (
                    f"https://updates.photosindexer.app/appcast{delimiter}unsafe.xml"
                )
                environment["SPARKLE_PUBLIC_ED_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
                result = subprocess.run(
                    ["zsh", str(SCRIPT)],
                    capture_output=True,
                    text=True,
                    check=False,
                    env=environment,
                )

                output = result.stdout + result.stderr
                self.assertIn("release_preflight:FAIL:sparkle_update_configuration_invalid", output)
                self.assertNotIn("release_preflight:PASS:sparkle_update_configuration", output)
                self.assertNotIn("unsafe.xml", output)

    def test_release_preflight_rejects_an_at_sign_anywhere_in_the_sparkle_feed(self) -> None:
        environment = os.environ.copy()
        environment["SPARKLE_FEED_URL"] = "https://updates.photosindexer.app/app@2.xml"
        environment["SPARKLE_PUBLIC_ED_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
        result = subprocess.run(
            ["zsh", str(SCRIPT)],
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )

        output = result.stdout + result.stderr
        self.assertIn("release_preflight:FAIL:sparkle_update_configuration_invalid", output)
        self.assertNotIn("release_preflight:PASS:sparkle_update_configuration", output)
        self.assertNotIn("app@2.xml", output)

    def test_release_preflight_contains_no_network_or_model_mutation_commands(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8") if SCRIPT.exists() else ""
        self.assertNotIn("ollama pull", source)
        self.assertNotIn("notarytool submit", source)
        self.assertNotIn("codesign --force", source)

    def test_release_preflight_validates_swift_from_the_selected_xcode_toolchain(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8") if SCRIPT.exists() else ""

        self.assertIn("xcrun --find swift", source)
        self.assertIn("swift_toolchain_unavailable", source)

    def test_release_preflight_reports_python_build_dependency_and_helper(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8") if SCRIPT.exists() else ""
        self.assertIn("pyinstaller_missing", source)
        self.assertIn("helper_missing", source)
        self.assertIn("helper_binary_invalid", source)
        self.assertIn('helper_app="$project_root/build/python-helper/dist/PhotosIndexerWorker.app"', source)
        self.assertIn('[[ -d "$helper_app" && ! -L "$helper_app" ]]', source)
        self.assertIn("helper_wrong_architecture", source)
        self.assertIn('lipo -archs "$helper"', source)

    def test_release_preflight_reports_helper_architecture_failure_once(self) -> None:
        """A single failed gate must not duplicate its check or hint."""
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertEqual(source.count('report FAIL helper_wrong_architecture'), 1)

    def test_release_preflight_checks_the_icon_generator_dependency(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8") if SCRIPT.exists() else ""
        self.assertIn('check_command sips /usr/bin/sips', source)
        self.assertIn('"sips_missing"', source)
        self.assertIn('"sips_missing") print -- "run_on_macos_release_host_with_sips"', source)

    def test_release_preflight_checks_the_frozen_helper_runtime_without_claiming_tcc(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8") if SCRIPT.exists() else ""
        self.assertIn('"$helper" --self-check', source)
        self.assertIn("helper_runtime", source)
        self.assertIn("helper_runtime_invalid", source)
        self.assertIn("env -i", source)
        self.assertIn('"runtime":"embedded"', source)
        self.assertNotIn("tccutil", source)
        self.assertIn('"state": "NOT_CHECKED"', source)
        self.assertIn('"action": "run_signed_smoke_test"', source)

    def test_release_preflight_isolates_frozen_helper_runtime_check(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8") if SCRIPT.exists() else ""
        self.assertIn('helper_check_root="$(mktemp -d "${TMPDIR:-/tmp}/photos-indexer-preflight.', source)
        self.assertIn('HOME="$helper_check_root"', source)
        self.assertIn('TMPDIR="$helper_check_root"', source)
        self.assertIn("cleanup_helper_check", source)
        self.assertNotIn('HOME="/tmp"', source)
        self.assertNotIn('TMPDIR="/tmp"', source)

    def test_release_preflight_reports_a_real_frozen_runtime_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            packaging = root / "packaging"
            packaging.mkdir(parents=True)
            isolated_script = packaging / "release_preflight.sh"
            isolated_script.write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
            isolated_script.chmod(0o755)
            (root / "pyproject.toml").write_text(
                (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            (packaging / "build_path_guard.zsh").write_text(
                (PROJECT_ROOT / "packaging" / "build_path_guard.zsh").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            (packaging / "helper_source_fingerprint.py").write_text(
                (PROJECT_ROOT / "packaging" / "helper_source_fingerprint.py").read_text(encoding="utf-8"),
                encoding="utf-8",
            )

            helper_root = root / "build/python-helper/dist/PhotosIndexerWorker.app"
            helper = helper_root / "Contents" / "MacOS" / "PhotosIndexerWorker"
            helper_info = helper_root / "Contents" / "Info.plist"
            helper_resources = helper_root / "Contents" / "Resources"
            helper_frameworks = helper_root / "Contents" / "Frameworks"
            helper.parent.mkdir(parents=True)
            helper_resources.mkdir(parents=True)
            helper_frameworks.mkdir(parents=True)
            helper.write_text(
                '#!/bin/zsh\n'
                '[[ "$1" == "--self-check" ]] || exit 1\n'
                'print -- \'{"status":"ok","runtime":"embedded","architecture":"arm64","protocol":"jsonl"}\'\n',
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
            marker_path = helper_resources / ".photos-indexer-source-fingerprint"
            marker_path.write_text(self._current_helper_source_fingerprint(), encoding="utf-8")
            marker_path.chmod(0o600)
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
                ["zsh", str(isolated_script)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

        self.assertNotEqual(result.returncode, 0, "the isolated host should still lack release signing prerequisites")
        self.assertIn("release_preflight:PASS:helper_runtime", result.stdout)
        self.assertNotIn("PHOTOS_TCC_RUNTIME_TEST_REQUIRED", result.stdout)
        self.assertNotIn("AUTOMATION_TCC_RUNTIME_TEST_REQUIRED", result.stdout)

    def test_release_preflight_rejects_a_group_writable_helper_before_self_check(self) -> None:
        """Do not execute a frozen runtime another local account may alter."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            root.chmod(0o755)
            packaging = root / "packaging"
            packaging.mkdir(parents=True)
            isolated_script = packaging / "release_preflight.sh"
            isolated_script.write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
            isolated_script.chmod(0o755)
            (packaging / "build_path_guard.zsh").write_text(
                (PROJECT_ROOT / "packaging" / "build_path_guard.zsh").read_text(encoding="utf-8"),
                encoding="utf-8",
            )

            helper_root = root / "build/python-helper/dist/PhotosIndexerWorker.app"
            marker = root / "helper-executed"
            helper = helper_root / "Contents" / "MacOS" / "PhotosIndexerWorker"
            helper_info = helper_root / "Contents" / "Info.plist"
            helper_resources = helper_root / "Contents" / "Resources"
            helper_frameworks = helper_root / "Contents" / "Frameworks"
            helper.parent.mkdir(parents=True)
            helper_resources.mkdir(parents=True)
            helper_frameworks.mkdir(parents=True)
            helper.write_text(
                "#!/bin/zsh\n"
                f": > '{marker}'\n"
                "print -- '{\"status\":\"ok\",\"runtime\":\"embedded\","
                "\"architecture\":\"arm64\",\"protocol\":\"jsonl\"}'\n",
                encoding="utf-8",
            )
            helper.chmod(0o775)
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
                ["zsh", str(isolated_script)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            machine_result = subprocess.run(
                ["zsh", str(isolated_script), "--json"],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("release_preflight:FAIL:helper_payload_permissions", result.stdout)
        self.assertFalse(marker.exists(), "an unsafe helper must fail before its self-check executes")
        diagnostic = json.loads(machine_result.stdout)
        toolchain = next(section for section in diagnostic["diagnostic_sections"] if section["id"] == "toolchain")
        self.assertIn("HELPER_PAYLOAD_PERMISSIONS", toolchain["failed_codes"])

    def test_release_preflight_rejects_a_helper_with_mismatched_release_metadata_before_self_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            packaging = root / "packaging"
            packaging.mkdir(parents=True)
            isolated_script = packaging / "release_preflight.sh"
            isolated_script.write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
            isolated_script.chmod(0o755)
            (root / "pyproject.toml").write_text(
                (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            (packaging / "build_path_guard.zsh").write_text(
                (PROJECT_ROOT / "packaging" / "build_path_guard.zsh").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            (packaging / "helper_source_fingerprint.py").write_text(
                (PROJECT_ROOT / "packaging" / "helper_source_fingerprint.py").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            helper_root = root / "build/python-helper/dist/PhotosIndexerWorker.app"
            marker = root / "helper-executed"
            helper = helper_root / "Contents" / "MacOS" / "PhotosIndexerWorker"
            helper_info = helper_root / "Contents" / "Info.plist"
            helper_resources = helper_root / "Contents" / "Resources"
            helper_frameworks = helper_root / "Contents" / "Frameworks"
            helper.parent.mkdir(parents=True)
            helper_resources.mkdir(parents=True)
            helper_frameworks.mkdir(parents=True)
            helper.write_text(
                "#!/bin/zsh\n"
                f": > '{marker}'\n"
                "print -- '{\"status\":\"ok\",\"runtime\":\"embedded\","
                "\"architecture\":\"arm64\",\"protocol\":\"jsonl\"}'\n",
                encoding="utf-8",
            )
            helper.chmod(0o755)
            with helper_info.open("wb") as stream:
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
            marker_path = helper_resources / ".photos-indexer-source-fingerprint"
            marker_path.write_text(self._current_helper_source_fingerprint(), encoding="utf-8")
            marker_path.chmod(0o600)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_lipo = fake_bin / "lipo"
            fake_lipo.write_text('#!/bin/zsh\n[[ "$1" == "-archs" ]] && print -- arm64\n', encoding="utf-8")
            fake_lipo.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
            with (PROJECT_ROOT / "pyproject.toml").open("rb") as stream:
                project_version = tomllib.load(stream)["project"]["version"]
            environment["APP_VERSION"] = project_version
            environment["BUILD_NUMBER"] = "45"

            result = subprocess.run(
                ["zsh", str(isolated_script)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("release_preflight:FAIL:helper_release_metadata_invalid", result.stdout)
        self.assertFalse(marker.exists(), "a mismatched helper must fail before its self-check executes")

    def test_release_preflight_rejects_a_sparkle_lock_with_an_unexpected_origin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "Package.resolved"
            lock.write_text(
                '{"pins":[{"identity":"sparkle",'
                '"location":"https://example.invalid/Sparkle.git",'
                '"state":{"version":"2.9.2",'
                '"revision":"6276ba2b404829d139c45ff98427cf90e2efc59b"}}]}',
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment["SPARKLE_LOCK_PATH"] = str(lock)

            result = subprocess.run(
                ["zsh", str(SCRIPT)],
                capture_output=True,
                text=True,
                check=False,
                env=environment,
            )

        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("release_preflight:FAIL:sparkle_lock_invalid", output)
        self.assertNotIn("release_preflight:PASS:sparkle_lock", output)

    def test_release_preflight_rejects_an_x86_64_python_build_interpreter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_python = Path(tmp) / "python3.12-x86_64"
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
if [[ "$1" == "-c" && "$2" == *"import PyInstaller"* ]]; then
  exit 0
fi
exit 1
""",
                encoding="utf-8",
            )
            fake_python.chmod(0o755)
            environment = os.environ.copy()
            environment["PYTHON_BIN"] = str(fake_python)

            result = subprocess.run(
                ["zsh", str(SCRIPT)],
                capture_output=True,
                text=True,
                check=False,
                env=environment,
            )

        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("release_preflight:FAIL:python3.12_wrong_architecture", output)

    def test_release_preflight_checks_tools_required_by_bundle_verification(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8") if SCRIPT.exists() else ""
        for tool in ("swift", "plutil", "lipo", "file", "readlink"):
            self.assertIn(f"check_command {tool} {tool}", source)
        self.assertIn("check_command system_python3 /usr/bin/python3", source)
        self.assertIn("/usr/libexec/PlistBuddy", source)

    def test_release_preflight_distinguishes_command_line_tools_from_full_xcode(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8") if SCRIPT.exists() else ""
        self.assertIn("check_command xcode_select xcode-select", source)
        self.assertIn("xcode_command_line_tools_active", source)
        self.assertIn("xcode_full_installation_missing", source)
        self.assertIn("xcode_full_installation_not_selected", source)
        self.assertIn("install_full_xcode", source)
        self.assertIn("select_installed_xcode", source)
        self.assertIn("xcode-select -p", source)
        self.assertIn("install_full_xcode_and_select_developer_directory", source)

    def test_release_preflight_honors_full_developer_dir_but_rejects_clt_override(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8") if SCRIPT.exists() else ""
        self.assertIn('selected_developer_dir="${DEVELOPER_DIR:-}"', source)
        self.assertIn('if [[ -z "$selected_developer_dir" ]] && command -v xcode-select', source)
        self.assertIn('selected_developer_dir="$(xcode-select -p', source)
        self.assertIn('[[ "$selected_developer_dir" == "/Library/Developer/CommandLineTools" ]]', source)

    def test_release_preflight_rejects_command_line_tools_even_when_xcodebuild_answers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_bin = Path(tmp)
            (fake_bin / "xcodebuild").write_text(
                "#!/bin/zsh\n[[ \"$1\" == \"-version\" ]] && print -- 'Xcode 16.0'\n",
                encoding="utf-8",
            )
            (fake_bin / "xcode-select").write_text(
                "#!/bin/zsh\n[[ \"$1\" == \"-p\" ]] && print -- /Library/Developer/CommandLineTools\n",
                encoding="utf-8",
            )
            for tool in ("xcodebuild", "xcode-select"):
                (fake_bin / tool).chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
            result = subprocess.run(
                ["zsh", str(SCRIPT)],
                capture_output=True,
                text=True,
                check=False,
                env=environment,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("release_preflight:FAIL:xcode_command_line_tools_active", result.stdout)
        self.assertNotIn("release_preflight:PASS:xcode_toolchain", result.stdout)

    def test_release_preflight_requires_the_selected_developer_id_identity(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8") if SCRIPT.exists() else ""
        self.assertIn('developer_identity="${DEVELOPER_ID_APPLICATION:-}"', source)
        self.assertIn("developer_id_identity_missing", source)
        self.assertIn("developer_id_identity_unavailable", source)

    def test_release_preflight_blocks_when_notary_profile_cannot_authenticate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_bin = Path(tmp)
            fake_xcrun = fake_bin / "xcrun"
            fake_xcrun.write_text(
                """#!/bin/zsh
if [[ "$1" == "--find" && "$2" == "notarytool" ]]; then
  exit 0
fi
if [[ "$1" == "notarytool" && "$2" == "history" ]]; then
  print -u2 -- "simulated rejection for $4"
  exit 1
fi
exec /usr/bin/xcrun "$@"
""",
                encoding="utf-8",
            )
            fake_xcrun.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
            environment["APPLE_NOTARY_PROFILE"] = "profile-do-not-leak"

            result = subprocess.run(
                ["zsh", str(SCRIPT)],
                capture_output=True,
                text=True,
                check=False,
                env=environment,
            )

        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("release_preflight:FAIL:notary_profile_unavailable", output)
        self.assertIn(
            "release_preflight:HINT:notary_profile_unavailable:verify_network_and_recreate_notary_profile_then_retry",
            output,
        )
        self.assertNotIn("profile-do-not-leak", output)

    def test_release_preflight_explains_how_to_configure_a_missing_notary_profile(self) -> None:
        environment = os.environ.copy()
        environment.pop("APPLE_NOTARY_PROFILE", None)

        result = subprocess.run(
            ["zsh", str(SCRIPT)],
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )

        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("release_preflight:FAIL:notary_profile_missing", output)
        self.assertIn(
            "release_preflight:HINT:notary_profile_missing:configure_with_xcrun_notarytool_store_credentials_then_retry",
            output,
        )


if __name__ == "__main__":
    unittest.main()
