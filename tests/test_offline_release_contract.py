from __future__ import annotations

import json
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "packaging" / "verify_offline_release_contract.py"


class OfflineReleaseContractTests(unittest.TestCase):
    def test_contract_requires_native_nested_helper_embedding_and_adhoc_sealing(self) -> None:
        module = runpy.run_path(str(SCRIPT))

        checks, _ = module["validate"](PROJECT_ROOT)
        check_states = {check["code"]: check["state"] for check in checks}

        for code in (
            "APP_NESTED_HELPER_COPY",
            "APP_NESTED_HELPER_PATHS",
            "APP_DEVELOPMENT_ADHOC_SIGNING_ORDER",
            "EMBEDDED_HELPER_NATIVE_BUNDLE_IDENTITY",
            "EMBEDDED_HELPER_INTEGRITY_GUARD",
            "DMG_NESTED_HELPER_PATH",
        ):
            self.assertEqual(check_states.get(code), "PASS", code)

        with tempfile.TemporaryDirectory() as tmp:
            copied_root = Path(tmp) / "project"
            shutil.copytree(
                PROJECT_ROOT,
                copied_root,
                ignore=shutil.ignore_patterns(".git", ".venv*", "build", "dist", ".build", "__pycache__"),
            )
            builder = copied_root / "packaging" / "build_swift_app.sh"
            source = builder.read_text(encoding="utf-8")
            builder.write_text(
                source.replace(
                    'ditto "$helper" "$app_bundle/Contents/Helpers/PhotosIndexerWorker.app"',
                    'ditto "$helper" "$app_bundle/Contents/Helpers/PhotosIndexerWorker"',
                    1,
                ),
                encoding="utf-8",
            )

            mutated_checks, _ = module["validate"](copied_root)

        mutated_states = {check["code"]: check["state"] for check in mutated_checks}
        self.assertEqual(mutated_states.get("APP_NESTED_HELPER_COPY"), "FAIL")

    def test_system_python_without_tomllib_returns_stable_json(self) -> None:
        system_python = Path("/usr/bin/python3")
        if not system_python.is_file():
            self.skipTest("macOS system Python is unavailable")
        version = subprocess.run(
            [str(system_python), "-c", "import sys; print(sys.version_info >= (3, 11))"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if version == "True":
            self.skipTest("system Python already provides tomllib")

        result = subprocess.run(
            [str(system_python), str(SCRIPT), "--json"],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "")
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["next_action"], "use_python_3_11_or_newer")
        self.assertEqual(
            report["checks"],
            [{"state": "FAIL", "code": "OFFLINE_PYTHON_RUNTIME"}],
        )

        human_result = subprocess.run(
            [str(system_python), str(SCRIPT)],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(human_result.returncode, 1)
        self.assertEqual(human_result.stderr, "")
        self.assertIn(
            "offline_contract:NEXT:use_python_3_11_or_newer",
            human_result.stdout.splitlines(),
        )

    def test_contract_accepts_conditional_dmg_identity_guard(self) -> None:
        module = runpy.run_path(str(SCRIPT))
        checks, passed = module["validate"](PROJECT_ROOT)

        self.assertTrue(passed, checks)

    def test_contract_requires_private_app_settings_persistence(self) -> None:
        module = runpy.run_path(str(SCRIPT))
        checks, passed = module["validate"](PROJECT_ROOT)

        self.assertTrue(passed, checks)
        self.assertEqual(
            next(check for check in checks if check["code"] == "APP_SETTINGS_PRIVATE_PERSISTENCE")["state"],
            "PASS",
        )

        with tempfile.TemporaryDirectory() as tmp:
            copied_root = Path(tmp) / "project"
            shutil.copytree(
                PROJECT_ROOT,
                copied_root,
                ignore=shutil.ignore_patterns(".git", ".venv*", "build", "dist", ".build", "__pycache__"),
            )
            store = copied_root / "app" / "PhotosLocalKeywordIndexer" / "Services" / "AppSettingsStore.swift"
            source = store.read_text(encoding="utf-8")
            store.write_text(source.replace(".posixPermissions: 0o600", ".posixPermissions: 0o644"), encoding="utf-8")

            copied_checks, copied_passed = module["validate"](copied_root)

        self.assertFalse(copied_passed, copied_checks)
        self.assertEqual(
            next(
                check for check in copied_checks if check["code"] == "APP_SETTINGS_PRIVATE_PERSISTENCE"
            )["state"],
            "FAIL",
        )

    def test_private_settings_guard_rejects_disconnected_regular_save_with_autonomy_save_present(self) -> None:
        module = runpy.run_path(str(SCRIPT))
        checks, _ = module["validate"](PROJECT_ROOT)
        self.assertEqual(
            next(check for check in checks if check["code"] == "APP_SETTINGS_PRIVATE_PERSISTENCE")["state"],
            "PASS",
        )
        with tempfile.TemporaryDirectory() as tmp:
            copied_root = Path(tmp) / "project"
            shutil.copytree(
                PROJECT_ROOT, copied_root,
                ignore=shutil.ignore_patterns(".git", ".venv*", "build", "dist", ".build", "__pycache__"),
            )
            model = copied_root / "app" / "PhotosLocalKeywordIndexer" / "AppModel.swift"
            source = model.read_text(encoding="utf-8")
            self.assertIn("do { try settingsStore.save(currentSettings) }", source)
            model.write_text(
                source.replace("try? settingsStore.save(currentSettings)", "_ = currentSettings", 1),
                encoding="utf-8",
            )
            mutated_checks, _ = module["validate"](copied_root)
        self.assertEqual(
            next(check for check in mutated_checks if check["code"] == "APP_SETTINGS_PRIVATE_PERSISTENCE")["state"],
            "FAIL",
        )

    def test_runtime_sparkle_contract_requires_a_bound_nonempty_hostname(self) -> None:
        module = runpy.run_path(str(SCRIPT))

        checks, passed = module["validate"](PROJECT_ROOT)

        self.assertTrue(passed, checks)
        with tempfile.TemporaryDirectory() as tmp:
            copied_root = Path(tmp) / "project"
            shutil.copytree(
                PROJECT_ROOT,
                copied_root,
                ignore=shutil.ignore_patterns(".git", ".venv*", "build", "dist", ".build", "__pycache__"),
            )
            service = copied_root / "app" / "PhotosLocalKeywordIndexer" / "Services" / "UpdateService.swift"
            source = service.read_text(encoding="utf-8")
            service.write_text(source.replace("              !host.isEmpty,\n", ""), encoding="utf-8")

            copied_checks, copied_passed = module["validate"](copied_root)

        self.assertFalse(copied_passed, copied_checks)
        self.assertEqual(
            next(
                check for check in copied_checks if check["code"] == "APP_RUNTIME_SPARKLE_PLACEHOLDER_GUARD"
            )["state"],
            "FAIL",
        )

    def test_contract_requires_notarize_evidence_cleanup_on_detach_failure(self) -> None:
        module = runpy.run_path(str(SCRIPT))

        checks, passed = module["validate"](PROJECT_ROOT)

        self.assertTrue(passed, checks)
        self.assertEqual(
            next(check for check in checks if check["code"] == "NOTARIZE_EVIDENCE_CLEANUP_ON_UNMOUNT")["state"],
            "PASS",
        )

        with tempfile.TemporaryDirectory() as tmp:
            copied_root = Path(tmp) / "project"
            shutil.copytree(
                PROJECT_ROOT,
                copied_root,
                ignore=shutil.ignore_patterns(".git", ".venv*", "build", "dist", ".build", "__pycache__"),
            )
            notarize = copied_root / "packaging" / "notarize.sh"
            source = notarize.read_text(encoding="utf-8")
            safe_cleanup = (
                'if (( evidence_written == 1 )) && evidence_is_owned; then\n'
                '    rm -f -- "$evidence_path" >/dev/null 2>&1 || cleanup_failed=1\n'
                '  fi'
            )
            notarize.write_text(
                source.replace(
                    safe_cleanup,
                    safe_cleanup.replace(
                        ' >/dev/null 2>&1 || cleanup_failed=1',
                        '',
                    ),
                    1,
                ),
                encoding="utf-8",
            )

            copied_checks, copied_passed = module["validate"](copied_root)

        self.assertFalse(copied_passed, copied_checks)
        self.assertEqual(
            next(
                check
                for check in copied_checks
                if check["code"] == "NOTARIZE_EVIDENCE_CLEANUP_ON_UNMOUNT"
            )["state"],
            "FAIL",
        )

    def test_contract_requires_notarize_evidence_cleanup_on_interrupt(self) -> None:
        module = runpy.run_path(str(SCRIPT))

        checks, passed = module["validate"](PROJECT_ROOT)

        self.assertTrue(passed, checks)
        self.assertEqual(
            next(check for check in checks if check["code"] == "NOTARIZE_EVIDENCE_CLEANUP_ON_INTERRUPT")["state"],
            "PASS",
        )

    def test_contract_rejects_malformed_project_shape_without_raising(self) -> None:
        module = runpy.run_path(str(SCRIPT))
        with tempfile.TemporaryDirectory() as tmp:
            copied_root = Path(tmp) / "project"
            shutil.copytree(
                PROJECT_ROOT,
                copied_root,
                ignore=shutil.ignore_patterns(".git", ".venv*", "build", "dist", ".build", "__pycache__"),
            )
            (copied_root / "pyproject.toml").write_text("project = []\n", encoding="utf-8")

            checks, passed = module["validate"](copied_root)

        self.assertFalse(passed, checks)
        self.assertEqual(
            next(check for check in checks if check["code"] == "PYTHON_CONSTRAINT")["state"],
            "FAIL",
        )

    def test_contract_requires_safe_stale_notarize_lock_diagnostic(self) -> None:
        module = runpy.run_path(str(SCRIPT))

        checks, passed = module["validate"](PROJECT_ROOT)

        self.assertTrue(passed, checks)
        self.assertEqual(
            next(check for check in checks if check["code"] == "NOTARIZE_STALE_LOCK_DIAGNOSTIC")["state"],
            "PASS",
        )

    def test_contract_requires_atomic_notarize_lock_owner_creation(self) -> None:
        module = runpy.run_path(str(SCRIPT))

        checks, passed = module["validate"](PROJECT_ROOT)

        self.assertTrue(passed, checks)
        self.assertEqual(
            next(check for check in checks if check["code"] == "NOTARIZE_LOCK_OWNER_ATOMIC")["state"],
            "PASS",
        )

    def test_swift_manifest_import_cleans_partial_copies_on_failure(self) -> None:
        source = (PROJECT_ROOT / "app" / "PhotosLocalKeywordIndexer" / "Models" / "PreviewModels.swift").read_text(
            encoding="utf-8"
        )

        self.assertIn("var createdImportDirectories: [URL] = []", source)
        self.assertIn("createdImportDirectories.append(destination)", source)
        self.assertIn("for directory in createdImportDirectories.reversed()", source)
        self.assertIn("fileManager.removeItem(at: directory)", source)
        import_start = source.index("static func importManifest(")
        append_index = source.index("createdImportDirectories.append(destination)", import_start)
        catch_index = source.index("} catch {", import_start)
        cleanup_index = source.index("for directory in createdImportDirectories.reversed()", import_start)
        self.assertLess(append_index, catch_index)
        self.assertLess(
            catch_index,
            cleanup_index,
        )
        self.assertLess(cleanup_index, source.index("throw HistoryManifestImportError.destinationUnavailable", cleanup_index))

    def test_contract_rejects_sparkle_config_with_photo_feature_settings(self) -> None:
        module = runpy.run_path(str(SCRIPT))
        with tempfile.TemporaryDirectory() as tmp:
            copied_root = Path(tmp) / "project"
            shutil.copytree(
                PROJECT_ROOT,
                copied_root,
                ignore=shutil.ignore_patterns(".git", ".venv*", "build", "dist", ".build", "__pycache__"),
            )
            config_path = copied_root / "packaging" / "sparkle-config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["include_caption"] = True
            config_path.write_text(json.dumps(config), encoding="utf-8")

            checks, passed = module["validate"](copied_root)

        self.assertFalse(passed, checks)
        self.assertEqual(
            next(check for check in checks if check["code"] == "SPARKLE_CONFIG_PRIVACY_SHAPE")["state"],
            "FAIL",
        )

    def test_contract_rejects_release_that_changes_safe_feature_defaults(self) -> None:
        module = runpy.run_path(str(SCRIPT))
        with tempfile.TemporaryDirectory() as tmp:
            copied_root = Path(tmp) / "project"
            shutil.copytree(
                PROJECT_ROOT,
                copied_root,
                ignore=shutil.ignore_patterns(".git", ".venv*", "build", "dist", ".build", "__pycache__"),
            )
            app_model = copied_root / "app" / "PhotosLocalKeywordIndexer" / "AppModel.swift"
            source = app_model.read_text(encoding="utf-8")
            app_model.write_text(
                source.replace('@Published var appleMaps = false', '@Published var appleMaps = true'),
                encoding="utf-8",
            )

            checks, passed = module["validate"](copied_root)

        self.assertFalse(passed, checks)
        self.assertEqual(
            next(check for check in checks if check["code"] == "APP_SAFE_FEATURE_DEFAULTS")["state"],
            "FAIL",
        )

    def test_blocked_contract_selects_a_local_repair_action(self) -> None:
        module = runpy.run_path(str(SCRIPT))
        report = module["make_report"]([{"state": "FAIL", "code": "APP_INFO_INVALID"}], False)
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["next_action"], "review_offline_contract")

    def test_contract_checker_is_file_only(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        for forbidden in ("subprocess", "socket", "urllib", "httpx", "notarytool", "codesign"):
            self.assertNotIn(forbidden, source)

    def test_contract_covers_the_complete_release_script_chain(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        for script_name in (
            "sign_app.sh",
            "notarize.sh",
            "write_release_evidence.sh",
            "build_path_guard.zsh",
            "sparkle_framework.zsh",
        ):
            self.assertIn(f'"{script_name}"', source)

    def test_contract_rejects_a_generic_release_stage_ready_marker(self) -> None:
        module = runpy.run_path(str(SCRIPT))
        with tempfile.TemporaryDirectory() as tmp:
            copied_root = Path(tmp) / "project"
            shutil.copytree(
                PROJECT_ROOT,
                copied_root,
                ignore=shutil.ignore_patterns(".git", ".venv*", "build", "dist", ".build", "__pycache__"),
            )
            build_script = copied_root / "packaging" / "build_swift_app.sh"
            source = build_script.read_text(encoding="utf-8")
            build_script.write_text(
                source.replace(
                    'print -- "release_app:READY_FOR_SIGNING_AND_DMG"',
                    'print -- "release_app:READY"',
                ),
                encoding="utf-8",
            )

            checks, passed = module["validate"](copied_root)

        self.assertFalse(passed, checks)
        self.assertEqual(
            next(check for check in checks if check["code"] == "RELEASE_STAGE_MARKERS_SCOPED")["state"],
            "FAIL",
        )

    def test_contract_rejects_a_false_distribution_marker_from_the_app_build(self) -> None:
        module = runpy.run_path(str(SCRIPT))
        with tempfile.TemporaryDirectory() as tmp:
            copied_root = Path(tmp) / "project"
            shutil.copytree(
                PROJECT_ROOT,
                copied_root,
                ignore=shutil.ignore_patterns(".git", ".venv*", "build", "dist", ".build", "__pycache__"),
            )
            build_script = copied_root / "packaging" / "build_swift_app.sh"
            source = build_script.read_text(encoding="utf-8")
            build_script.write_text(
                source.replace(
                    'print -- "release_app:READY_FOR_SIGNING_AND_DMG"',
                    'print -- "release_app:READY_FOR_DISTRIBUTION"\n'
                    'print -- "release_app:READY_FOR_SIGNING_AND_DMG"',
                ),
                encoding="utf-8",
            )

            checks, passed = module["validate"](copied_root)

        self.assertFalse(passed, checks)
        self.assertEqual(
            next(check for check in checks if check["code"] == "RELEASE_STAGE_MARKERS_SCOPED")["state"],
            "FAIL",
        )

    def test_contract_rejects_a_false_distribution_marker_written_to_stderr(self) -> None:
        module = runpy.run_path(str(SCRIPT))
        with tempfile.TemporaryDirectory() as tmp:
            copied_root = Path(tmp) / "project"
            shutil.copytree(
                PROJECT_ROOT,
                copied_root,
                ignore=shutil.ignore_patterns(".git", ".venv*", "build", "dist", ".build", "__pycache__"),
            )
            build_script = copied_root / "packaging" / "build_swift_app.sh"
            source = build_script.read_text(encoding="utf-8")
            build_script.write_text(
                source.replace(
                    'print -- "release_app:READY_FOR_SIGNING_AND_DMG"',
                    'print -u2 -- "release_app:READY_FOR_DISTRIBUTION"\n'
                    'print -- "release_app:READY_FOR_SIGNING_AND_DMG"',
                ),
                encoding="utf-8",
            )

            checks, passed = module["validate"](copied_root)

        self.assertFalse(passed, checks)
        self.assertEqual(
            next(check for check in checks if check["code"] == "RELEASE_STAGE_MARKERS_SCOPED")["state"],
            "FAIL",
        )

    def test_contract_rejects_a_false_distribution_marker_in_single_quotes(self) -> None:
        module = runpy.run_path(str(SCRIPT))
        with tempfile.TemporaryDirectory() as tmp:
            copied_root = Path(tmp) / "project"
            shutil.copytree(
                PROJECT_ROOT,
                copied_root,
                ignore=shutil.ignore_patterns(".git", ".venv*", "build", "dist", ".build", "__pycache__"),
            )
            build_script = copied_root / "packaging" / "build_swift_app.sh"
            source = build_script.read_text(encoding="utf-8")
            build_script.write_text(
                source.replace(
                    'print -- "release_app:READY_FOR_SIGNING_AND_DMG"',
                    "print -- 'release_app:READY_FOR_DISTRIBUTION'\n"
                    'print -- "release_app:READY_FOR_SIGNING_AND_DMG"',
                ),
                encoding="utf-8",
            )

            checks, passed = module["validate"](copied_root)

        self.assertFalse(passed, checks)
        self.assertEqual(
            next(check for check in checks if check["code"] == "RELEASE_STAGE_MARKERS_SCOPED")["state"],
            "FAIL",
        )

    def test_contract_rejects_a_generic_signature_ready_marker(self) -> None:
        module = runpy.run_path(str(SCRIPT))
        with tempfile.TemporaryDirectory() as tmp:
            copied_root = Path(tmp) / "project"
            shutil.copytree(
                PROJECT_ROOT,
                copied_root,
                ignore=shutil.ignore_patterns(".git", ".venv*", "build", "dist", ".build", "__pycache__"),
            )
            sign_script = copied_root / "packaging" / "sign_app.sh"
            source = sign_script.read_text(encoding="utf-8")
            sign_script.write_text(
                source.replace(
                    'print -- "release_signature:READY_FOR_DMG"',
                    'print -- "release_signature:READY"',
                ),
                encoding="utf-8",
            )

            checks, passed = module["validate"](copied_root)

        self.assertFalse(passed, checks)
        self.assertEqual(
            next(check for check in checks if check["code"] == "RELEASE_STAGE_MARKERS_SCOPED")["state"],
            "FAIL",
        )

    def test_contract_rejects_an_extra_generic_signature_ready_marker(self) -> None:
        module = runpy.run_path(str(SCRIPT))
        with tempfile.TemporaryDirectory() as tmp:
            copied_root = Path(tmp) / "project"
            shutil.copytree(
                PROJECT_ROOT,
                copied_root,
                ignore=shutil.ignore_patterns(".git", ".venv*", "build", "dist", ".build", "__pycache__"),
            )
            sign_script = copied_root / "packaging" / "sign_app.sh"
            source = sign_script.read_text(encoding="utf-8")
            sign_script.write_text(
                source.replace(
                    'print -- "release_signature:READY_FOR_DMG"',
                    'print -- "release_signature:READY"\n'
                    'print -- "release_signature:READY_FOR_DMG"',
                ),
                encoding="utf-8",
            )

            checks, passed = module["validate"](copied_root)

        self.assertFalse(passed, checks)
        self.assertEqual(
            next(check for check in checks if check["code"] == "RELEASE_STAGE_MARKERS_SCOPED")["state"],
            "FAIL",
        )

    def test_contract_rejects_a_generic_helper_ready_marker(self) -> None:
        module = runpy.run_path(str(SCRIPT))
        with tempfile.TemporaryDirectory() as tmp:
            copied_root = Path(tmp) / "project"
            shutil.copytree(
                PROJECT_ROOT,
                copied_root,
                ignore=shutil.ignore_patterns(".git", ".venv*", "build", "dist", ".build", "__pycache__"),
            )
            helper_script = copied_root / "packaging" / "build_python_helper.sh"
            source = helper_script.read_text(encoding="utf-8")
            helper_script.write_text(
                source.replace(
                    'print -- "release_helper:READY_FOR_APP_BUNDLE"',
                    'print -- "release_helper:READY"',
                ),
                encoding="utf-8",
            )

            checks, passed = module["validate"](copied_root)

        self.assertFalse(passed, checks)
        self.assertEqual(
            next(check for check in checks if check["code"] == "RELEASE_STAGE_MARKERS_SCOPED")["state"],
            "FAIL",
        )

    def test_contract_requires_the_final_distribution_marker(self) -> None:
        module = runpy.run_path(str(SCRIPT))
        with tempfile.TemporaryDirectory() as tmp:
            copied_root = Path(tmp) / "project"
            shutil.copytree(
                PROJECT_ROOT,
                copied_root,
                ignore=shutil.ignore_patterns(".git", ".venv*", "build", "dist", ".build", "__pycache__"),
            )
            notarize_script = copied_root / "packaging" / "notarize.sh"
            source = notarize_script.read_text(encoding="utf-8")
            notarize_script.write_text(
                source.replace('print -- "notarize:READY_FOR_DISTRIBUTION"\n', ""),
                encoding="utf-8",
            )

            checks, passed = module["validate"](copied_root)

        self.assertFalse(passed, checks)
        self.assertEqual(
            next(check for check in checks if check["code"] == "RELEASE_STAGE_MARKERS_SCOPED")["state"],
            "FAIL",
        )

    def test_contract_rejects_an_extra_generic_notarize_ready_marker(self) -> None:
        module = runpy.run_path(str(SCRIPT))
        with tempfile.TemporaryDirectory() as tmp:
            copied_root = Path(tmp) / "project"
            shutil.copytree(
                PROJECT_ROOT,
                copied_root,
                ignore=shutil.ignore_patterns(".git", ".venv*", "build", "dist", ".build", "__pycache__"),
            )
            notarize_script = copied_root / "packaging" / "notarize.sh"
            source = notarize_script.read_text(encoding="utf-8")
            notarize_script.write_text(
                source.replace(
                    'print -- "notarize:READY_FOR_DISTRIBUTION"',
                    'print -- "notarize:READY"\n'
                    'print -- "notarize:READY_FOR_DISTRIBUTION"',
                ),
                encoding="utf-8",
            )

            checks, passed = module["validate"](copied_root)

        self.assertFalse(passed, checks)
        self.assertEqual(
            next(check for check in checks if check["code"] == "RELEASE_STAGE_MARKERS_SCOPED")["state"],
            "FAIL",
        )

    def test_contract_rejects_an_extra_generic_preflight_ready_marker(self) -> None:
        module = runpy.run_path(str(SCRIPT))
        with tempfile.TemporaryDirectory() as tmp:
            copied_root = Path(tmp) / "project"
            shutil.copytree(
                PROJECT_ROOT,
                copied_root,
                ignore=shutil.ignore_patterns(".git", ".venv*", "build", "dist", ".build", "__pycache__"),
            )
            preflight = copied_root / "packaging" / "release_preflight.sh"
            source = preflight.read_text(encoding="utf-8")
            preflight.write_text(
                source.replace(
                    'print -- "release_preflight:READY:release_gates_available_runtime_not_checked"',
                    'print -- "release_preflight:READY"\n'
                    'print -- "release_preflight:READY:release_gates_available_runtime_not_checked"',
                ),
                encoding="utf-8",
            )

            checks, passed = module["validate"](copied_root)

        self.assertFalse(passed, checks)
        self.assertEqual(
            next(check for check in checks if check["code"] == "RELEASE_STAGE_MARKERS_SCOPED")["state"],
            "FAIL",
        )

    def test_contract_requires_final_dmg_identity_before_distribution_marker(self) -> None:
        module = runpy.run_path(str(SCRIPT))
        with tempfile.TemporaryDirectory() as tmp:
            copied_root = Path(tmp) / "project"
            shutil.copytree(
                PROJECT_ROOT,
                copied_root,
                ignore=shutil.ignore_patterns(".git", ".venv*", "build", "dist", ".build", "__pycache__"),
            )
            notarize_script = copied_root / "packaging" / "notarize.sh"
            source = notarize_script.read_text(encoding="utf-8")
            notarize_script.write_text(
                source.replace("if ! dmg_matches_finalized_fingerprint; then", "if false; then"),
                encoding="utf-8",
            )

            checks, passed = module["validate"](copied_root)

        self.assertFalse(passed, checks)
        self.assertEqual(
            next(check for check in checks if check["code"] == "RELEASE_STAGE_MARKERS_SCOPED")["state"],
            "FAIL",
        )

    def test_contract_requires_final_evidence_identity_before_distribution_marker(self) -> None:
        module = runpy.run_path(str(SCRIPT))
        with tempfile.TemporaryDirectory() as tmp:
            copied_root = Path(tmp) / "project"
            shutil.copytree(
                PROJECT_ROOT,
                copied_root,
                ignore=shutil.ignore_patterns(".git", ".venv*", "build", "dist", ".build", "__pycache__"),
            )
            notarize_script = copied_root / "packaging" / "notarize.sh"
            source = notarize_script.read_text(encoding="utf-8")
            notarize_script.write_text(
                source.replace("if ! evidence_matches_release_fingerprint; then", "if false; then"),
                encoding="utf-8",
            )

            checks, passed = module["validate"](copied_root)

        self.assertFalse(passed, checks)
        self.assertEqual(
            next(check for check in checks if check["code"] == "RELEASE_STAGE_MARKERS_SCOPED")["state"],
            "FAIL",
        )

    def test_contract_rejects_early_integrated_evidence_marker(self) -> None:
        module = runpy.run_path(str(SCRIPT))
        with tempfile.TemporaryDirectory() as tmp:
            copied_root = Path(tmp) / "project"
            shutil.copytree(
                PROJECT_ROOT,
                copied_root,
                ignore=shutil.ignore_patterns(".git", ".venv*", "build", "dist", ".build", "__pycache__"),
            )
            notarize_script = copied_root / "packaging" / "notarize.sh"
            source = notarize_script.read_text(encoding="utf-8")
            notarize_script.write_text(
                source.replace('"$notary_result" "$evidence_path" >/dev/null', '"$notary_result" "$evidence_path"'),
                encoding="utf-8",
            )

            checks, passed = module["validate"](copied_root)

        self.assertFalse(passed, checks)
        self.assertEqual(
            next(check for check in checks if check["code"] == "RELEASE_STAGE_MARKERS_SCOPED")["state"],
            "FAIL",
        )

    def test_contract_rejects_completion_before_final_integrity_checks(self) -> None:
        module = runpy.run_path(str(SCRIPT))
        with tempfile.TemporaryDirectory() as tmp:
            copied_root = Path(tmp) / "project"
            shutil.copytree(
                PROJECT_ROOT,
                copied_root,
                ignore=shutil.ignore_patterns(".git", ".venv*", "build", "dist", ".build", "__pycache__"),
            )
            notarize_script = copied_root / "packaging" / "notarize.sh"
            source = notarize_script.read_text(encoding="utf-8")
            source = source.replace("notarize_completed=1\n", "", 1)
            source = source.replace(
                "if ! dmg_matches_finalized_fingerprint; then",
                "notarize_completed=1\nif ! dmg_matches_finalized_fingerprint; then",
                1,
            )
            notarize_script.write_text(source, encoding="utf-8")

            checks, passed = module["validate"](copied_root)

        self.assertFalse(passed, checks)
        self.assertEqual(
            next(check for check in checks if check["code"] == "RELEASE_STAGE_MARKERS_SCOPED")["state"],
            "FAIL",
        )

    def test_contract_requires_descriptor_bound_notarization_fingerprints(self) -> None:
        module = runpy.run_path(str(SCRIPT))
        with tempfile.TemporaryDirectory() as tmp:
            copied_root = Path(tmp) / "project"
            shutil.copytree(
                PROJECT_ROOT,
                copied_root,
                ignore=shutil.ignore_patterns(".git", ".venv*", "build", "dist", ".build", "__pycache__"),
            )
            notarize_script = copied_root / "packaging" / "notarize.sh"
            source = notarize_script.read_text(encoding="utf-8")
            notarize_script.write_text(
                source.replace("metadata_after = os.fstat(descriptor)", "metadata_after = metadata_before", 1),
                encoding="utf-8",
            )

            checks, passed = module["validate"](copied_root)

        self.assertFalse(passed, checks)
        self.assertEqual(
            next(
                check
                for check in checks
                if check["code"] == "NOTARIZE_FILE_FINGERPRINT_FD_BOUND"
            )["state"],
            "FAIL",
        )

    def test_contract_requires_cleanup_before_distribution_markers(self) -> None:
        module = runpy.run_path(str(SCRIPT))
        with tempfile.TemporaryDirectory() as tmp:
            copied_root = Path(tmp) / "project"
            shutil.copytree(
                PROJECT_ROOT,
                copied_root,
                ignore=shutil.ignore_patterns(".git", ".venv*", "build", "dist", ".build", "__pycache__"),
            )
            notarize_script = copied_root / "packaging" / "notarize.sh"
            source = notarize_script.read_text(encoding="utf-8")
            notarize_script.write_text(
                source.replace("if (( cleanup_failed == 1 )); then", "if false; then", 1),
                encoding="utf-8",
            )

            checks, passed = module["validate"](copied_root)

        self.assertFalse(passed, checks)
        self.assertEqual(
            next(
                check
                for check in checks
                if check["code"] == "NOTARIZE_FINAL_CLEANUP_BEFORE_READY"
            )["state"],
            "FAIL",
        )

    def test_contract_requires_dmg_cleanup_before_ready_for_notarization(self) -> None:
        module = runpy.run_path(str(SCRIPT))
        with tempfile.TemporaryDirectory() as tmp:
            copied_root = Path(tmp) / "project"
            shutil.copytree(
                PROJECT_ROOT,
                copied_root,
                ignore=shutil.ignore_patterns(".git", ".venv*", "build", "dist", ".build", "__pycache__"),
            )
            build_script = copied_root / "packaging" / "build_dmg.sh"
            source = build_script.read_text(encoding="utf-8")
            build_script.write_text(
                source.replace("if (( cleanup_failed == 1 )); then", "if false; then", 1),
                encoding="utf-8",
            )

            checks, passed = module["validate"](copied_root)

        self.assertFalse(passed, checks)
        self.assertEqual(
            next(
                check
                for check in checks
                if check["code"] == "BUILD_DMG_FINAL_CLEANUP_BEFORE_READY"
            )["state"],
            "FAIL",
        )

    def test_contract_requires_helper_cleanup_before_ready_for_app_bundle(self) -> None:
        module = runpy.run_path(str(SCRIPT))
        with tempfile.TemporaryDirectory() as tmp:
            copied_root = Path(tmp) / "project"
            shutil.copytree(
                PROJECT_ROOT,
                copied_root,
                ignore=shutil.ignore_patterns(".git", ".venv*", "build", "dist", ".build", "__pycache__"),
            )
            helper_script = copied_root / "packaging" / "build_python_helper.sh"
            source = helper_script.read_text(encoding="utf-8")
            helper_script.write_text(
                source.replace(
                    'if [[ -e "$isolation_root" || -L "$isolation_root" ]]; then',
                    "if false; then",
                    1,
                ),
                encoding="utf-8",
            )

            checks, passed = module["validate"](copied_root)

        self.assertFalse(passed, checks)
        self.assertEqual(
            next(
                check
                for check in checks
                if check["code"] == "HELPER_FINAL_CLEANUP_BEFORE_READY"
            )["state"],
            "FAIL",
        )

    def test_contract_requires_helper_signal_traps_cleared_before_ready(self) -> None:
        module = runpy.run_path(str(SCRIPT))
        with tempfile.TemporaryDirectory() as tmp:
            copied_root = Path(tmp) / "project"
            shutil.copytree(
                PROJECT_ROOT,
                copied_root,
                ignore=shutil.ignore_patterns(".git", ".venv*", "build", "dist", ".build", "__pycache__"),
            )
            helper_script = copied_root / "packaging" / "build_python_helper.sh"
            source = helper_script.read_text(encoding="utf-8")
            helper_script.write_text(
                source.replace(
                    "else\n  trap - EXIT INT TERM\nfi",
                    "else\n  trap - EXIT\nfi",
                    1,
                ),
                encoding="utf-8",
            )

            checks, passed = module["validate"](copied_root)

        self.assertFalse(passed, checks)
        self.assertEqual(
            next(
                check
                for check in checks
                if check["code"] == "HELPER_SIGNAL_TRAPS_CLEARED_BEFORE_READY"
            )["state"],
            "FAIL",
        )

    def test_contract_requires_release_verifier_cleanup_before_pass(self) -> None:
        module = runpy.run_path(str(SCRIPT))
        with tempfile.TemporaryDirectory() as tmp:
            copied_root = Path(tmp) / "project"
            shutil.copytree(
                PROJECT_ROOT,
                copied_root,
                ignore=shutil.ignore_patterns(".git", ".venv*", "build", "dist", ".build", "__pycache__"),
            )
            verifier = copied_root / "packaging" / "verify_release.sh"
            source = verifier.read_text(encoding="utf-8")
            verifier.write_text(
                source.replace(
                    'if (( cleanup_failed == 1 )) || [[ -e "$entitlement_tmp" || -L "$entitlement_tmp" ]]; then',
                    "if false; then",
                    1,
                ),
                encoding="utf-8",
            )

            checks, passed = module["validate"](copied_root)

        self.assertFalse(passed, checks)
        self.assertEqual(
            next(
                check
                for check in checks
                if check["code"] == "RELEASE_VERIFIER_FINAL_CLEANUP_BEFORE_PASS"
            )["state"],
            "FAIL",
        )

    def test_contract_requires_embedded_helper_cleanup_before_ready(self) -> None:
        module = runpy.run_path(str(SCRIPT))
        with tempfile.TemporaryDirectory() as tmp:
            copied_root = Path(tmp) / "project"
            shutil.copytree(
                PROJECT_ROOT,
                copied_root,
                ignore=shutil.ignore_patterns(".git", ".venv*", "build", "dist", ".build", "__pycache__"),
            )
            verifier = copied_root / "packaging" / "verify_embedded_helper.sh"
            source = verifier.read_text(encoding="utf-8")
            verifier.write_text(
                source.replace(
                    'if (( cleanup_failed == 1 )) || [[ -e "$diagnostic_root" || -L "$diagnostic_root" ]]; then',
                    "if false; then",
                    1,
                ),
                encoding="utf-8",
            )

            checks, passed = module["validate"](copied_root)

        self.assertFalse(passed, checks)
        self.assertEqual(
            next(
                check
                for check in checks
                if check["code"] == "EMBEDDED_HELPER_FINAL_CLEANUP_BEFORE_READY"
            )["state"],
            "FAIL",
        )

    def test_contract_requires_dmg_layout_cleanup_before_ready(self) -> None:
        module = runpy.run_path(str(SCRIPT))
        with tempfile.TemporaryDirectory() as tmp:
            copied_root = Path(tmp) / "project"
            shutil.copytree(
                PROJECT_ROOT,
                copied_root,
                ignore=shutil.ignore_patterns(".git", ".venv*", "build", "dist", ".build", "__pycache__"),
            )
            verifier = copied_root / "packaging" / "verify_dmg_layout.sh"
            source = verifier.read_text(encoding="utf-8")
            verifier.write_text(
                source.replace(
                    'if (( cleanup_failed == 1 || mounted == 1 )) || [[ -e "$mount_point" || -L "$mount_point" ]]; then',
                    "if false; then",
                    1,
                ),
                encoding="utf-8",
            )

            checks, passed = module["validate"](copied_root)

        self.assertFalse(passed, checks)
        self.assertEqual(
            next(
                check
                for check in checks
                if check["code"] == "DMG_LAYOUT_FINAL_CLEANUP_BEFORE_READY"
            )["state"],
            "FAIL",
        )

    def test_contract_requires_preflight_helper_cleanup_before_ready(self) -> None:
        module = runpy.run_path(str(SCRIPT))
        with tempfile.TemporaryDirectory() as tmp:
            copied_root = Path(tmp) / "project"
            shutil.copytree(
                PROJECT_ROOT,
                copied_root,
                ignore=shutil.ignore_patterns(".git", ".venv*", "build", "dist", ".build", "__pycache__"),
            )
            preflight = copied_root / "packaging" / "release_preflight.sh"
            source = preflight.read_text(encoding="utf-8")
            preflight.write_text(
                source.replace(
                    "else\n  trap - EXIT INT TERM",
                    "fi\ntrap - EXIT INT TERM",
                    1,
                ),
                encoding="utf-8",
            )

            checks, passed = module["validate"](copied_root)

        self.assertFalse(passed, checks)
        self.assertEqual(
            next(
                check
                for check in checks
                if check["code"] == "PREFLIGHT_HELPER_FINAL_CLEANUP_BEFORE_READY"
            )["state"],
            "FAIL",
        )

    def test_contract_scopes_preflight_helper_cleanup_failure_in_json_diagnostics(self) -> None:
        module = runpy.run_path(str(SCRIPT))
        with tempfile.TemporaryDirectory() as tmp:
            copied_root = Path(tmp) / "project"
            shutil.copytree(
                PROJECT_ROOT,
                copied_root,
                ignore=shutil.ignore_patterns(".git", ".venv*", "build", "dist", ".build", "__pycache__"),
            )
            preflight = copied_root / "packaging" / "release_preflight.sh"
            source = preflight.read_text(encoding="utf-8")
            preflight.write_text(
                source.replace(', "HELPER_CHECK_CLEANUP_FAILED"', ""),
                encoding="utf-8",
            )

            checks, passed = module["validate"](copied_root)

        self.assertFalse(passed, checks)
        self.assertEqual(
            next(
                check
                for check in checks
                if check["code"] == "PREFLIGHT_HELPER_CLEANUP_DIAGNOSTIC_SCOPED"
            )["state"],
            "FAIL",
        )

    def test_contract_scopes_helper_fingerprint_failures_in_json_diagnostics(self) -> None:
        module = runpy.run_path(str(SCRIPT))
        with tempfile.TemporaryDirectory() as tmp:
            copied_root = Path(tmp) / "project"
            shutil.copytree(
                PROJECT_ROOT,
                copied_root,
                ignore=shutil.ignore_patterns(".git", ".venv*", "build", "dist", ".build", "__pycache__"),
            )
            preflight = copied_root / "packaging" / "release_preflight.sh"
            source = preflight.read_text(encoding="utf-8")
            preflight.write_text(
                source.replace('"HELPER_SOURCE_FINGERPRINT_STALE", ', ""),
                encoding="utf-8",
            )

            checks, passed = module["validate"](copied_root)

        self.assertFalse(passed, checks)
        self.assertEqual(
            next(
                check
                for check in checks
                if check["code"] == "PREFLIGHT_HELPER_FINGERPRINT_DIAGNOSTICS_SCOPED"
            )["state"],
            "FAIL",
        )

    def test_helper_runtime_gate_does_not_require_undeclared_scriptingbridge(self) -> None:
        helper_script = (PROJECT_ROOT / "packaging" / "build_python_helper.sh").read_text(encoding="utf-8")
        helper_spec = (PROJECT_ROOT / "packaging" / "PhotosIndexerWorker.spec").read_text(encoding="utf-8")

        self.assertNotIn("ScriptingBridge", helper_script)
        self.assertNotIn("ScriptingBridge", helper_spec)

    def test_contract_accepts_legacy_sparkle_lock_schema(self) -> None:
        module = runpy.run_path(str(SCRIPT))
        with tempfile.TemporaryDirectory() as tmp:
            copied_root = Path(tmp) / "project"
            shutil.copytree(
                PROJECT_ROOT,
                copied_root,
                ignore=shutil.ignore_patterns(".git", ".venv*", "build", "dist", ".build", "__pycache__"),
            )
            lock_path = copied_root / "app" / "Package.resolved"
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
            lock_path.write_text(
                json.dumps({"version": 1, "object": {"pins": lock["pins"]}}),
                encoding="utf-8",
            )

            checks, passed = module["validate"](copied_root)

        self.assertTrue(passed, checks)
        self.assertEqual(
            next(check for check in checks if check["code"] == "SPARKLE_LOCK_EXACT")["state"],
            "PASS",
        )

    def test_contract_rejects_disabled_runtime_provenance_comparison(self) -> None:
        module = runpy.run_path(str(SCRIPT))
        with tempfile.TemporaryDirectory() as tmp:
            copied_root = Path(tmp) / "project"
            shutil.copytree(
                PROJECT_ROOT,
                copied_root,
                ignore=shutil.ignore_patterns(".git", ".venv*", "build", "dist", ".build", "__pycache__"),
            )
            helper_script = copied_root / "packaging" / "build_python_helper.sh"
            helper_script.write_text(
                helper_script.read_text(encoding="utf-8").replace(
                    "if version(package) != expected:",
                    "if False:",
                ),
                encoding="utf-8",
            )

            checks, passed = module["validate"](copied_root)

        self.assertFalse(passed, checks)
        self.assertEqual(
            next(check for check in checks if check["code"] == "HELPER_RUNTIME_PROVENANCE_PREFLIGHT")["state"],
            "FAIL",
        )

    def test_contract_rejects_dynamic_build_dmg_path_errors(self) -> None:
        module = runpy.run_path(str(SCRIPT))
        with tempfile.TemporaryDirectory() as tmp:
            copied_root = Path(tmp) / "project"
            shutil.copytree(
                PROJECT_ROOT,
                copied_root,
                ignore=shutil.ignore_patterns(".git", ".venv*", "build", "dist", ".build", "__pycache__"),
            )
            script = copied_root / "packaging" / "build_dmg.sh"
            script.write_text(
                script.read_text(encoding="utf-8").replace(
                    'print -u2 -- "build_dmg:FAIL:output_directory_symlink_escape"',
                    'print -u2 -- "build_dmg:FAIL:output_directory_symlink_escape: $output_root"',
                ),
                encoding="utf-8",
            )

            checks, passed = module["validate"](copied_root)

        self.assertFalse(passed, checks)
        self.assertEqual(
            next(check for check in checks if check["code"] == "BUILD_DMG_ERROR_REDACTION")["state"],
            "FAIL",
        )

    def test_contract_rejects_runtime_provenance_comparison_guarded_by_false(self) -> None:
        module = runpy.run_path(str(SCRIPT))
        with tempfile.TemporaryDirectory() as tmp:
            copied_root = Path(tmp) / "project"
            shutil.copytree(
                PROJECT_ROOT,
                copied_root,
                ignore=shutil.ignore_patterns(".git", ".venv*", "build", "dist", ".build", "__pycache__"),
            )
            helper_script = copied_root / "packaging" / "build_python_helper.sh"
            helper_script.write_text(
                helper_script.read_text(encoding="utf-8").replace(
                    "if version(package) != expected:",
                    "if False and version(package) != expected:",
                ),
                encoding="utf-8",
            )

            checks, passed = module["validate"](copied_root)

        self.assertFalse(passed, checks)
        self.assertEqual(
            next(check for check in checks if check["code"] == "HELPER_RUNTIME_PROVENANCE_PREFLIGHT")["state"],
            "FAIL",
        )

    def test_contract_rejects_case_variant_duplicate_sparkle_pin(self) -> None:
        module = runpy.run_path(str(SCRIPT))
        with tempfile.TemporaryDirectory() as tmp:
            copied_root = Path(tmp) / "project"
            shutil.copytree(
                PROJECT_ROOT,
                copied_root,
                ignore=shutil.ignore_patterns(".git", ".venv*", "build", "dist", ".build", "__pycache__"),
            )
            lock_path = copied_root / "app" / "Package.resolved"
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
            duplicate = dict(lock["pins"][0])
            duplicate["identity"] = "Sparkle"
            lock["pins"].append(duplicate)
            lock_path.write_text(json.dumps(lock), encoding="utf-8")

            checks, passed = module["validate"](copied_root)

        self.assertFalse(passed, checks)
        self.assertEqual(
            next(check for check in checks if check["code"] == "SPARKLE_LOCK_EXACT")["state"],
            "FAIL",
        )

    def test_contract_rejects_a_symlinked_sparkle_lock(self) -> None:
        module = runpy.run_path(str(SCRIPT))
        with tempfile.TemporaryDirectory() as tmp:
            copied_root = Path(tmp) / "project"
            shutil.copytree(
                PROJECT_ROOT,
                copied_root,
                ignore=shutil.ignore_patterns(".git", ".venv*", "build", "dist", ".build", "__pycache__"),
            )
            lock_path = copied_root / "app" / "Package.resolved"
            external_lock = Path(tmp) / "external-Package.resolved"
            external_lock.write_bytes(lock_path.read_bytes())
            lock_path.unlink()
            lock_path.symlink_to(external_lock)

            checks, passed = module["validate"](copied_root)

        self.assertFalse(passed, checks)
        self.assertEqual(
            next(check for check in checks if check["code"] == "SPARKLE_LOCK_EXACT")["state"],
            "FAIL",
        )

    def test_offline_contract_rejects_a_non_arm64_pyinstaller_spec(self) -> None:
        module = runpy.run_path(str(SCRIPT))
        with tempfile.TemporaryDirectory() as tmp:
            copied_root = Path(tmp) / "project"
            shutil.copytree(
                PROJECT_ROOT,
                copied_root,
                ignore=shutil.ignore_patterns(".git", ".venv*", "build", "dist", ".build", "__pycache__"),
            )
            spec_path = copied_root / "packaging" / "PhotosIndexerWorker.spec"
            spec_path.write_text(
                spec_path.read_text(encoding="utf-8").replace('target_arch="arm64"', 'target_arch="x86_64"'),
                encoding="utf-8",
            )

            checks, passed = module["validate"](copied_root)

        self.assertFalse(passed, checks)
        self.assertEqual(
            next(check for check in checks if check["code"] == "PYINSTALLER_SPEC_ARM64")["state"],
            "FAIL",
        )

    def test_offline_contract_requires_the_release_preflight_script(self) -> None:
        module = runpy.run_path(str(SCRIPT))
        with tempfile.TemporaryDirectory() as tmp:
            copied_root = Path(tmp) / "project"
            shutil.copytree(
                PROJECT_ROOT,
                copied_root,
                ignore=shutil.ignore_patterns(".git", ".venv*", "build", "dist", ".build", "__pycache__"),
            )
            (copied_root / "packaging" / "release_preflight.sh").unlink()

            checks, passed = module["validate"](copied_root)

        self.assertFalse(passed, checks)
        self.assertEqual(
            next(check for check in checks if check["code"] == "PACKAGING_SCRIPTS_PRESENT")["state"],
            "FAIL",
        )

    def test_offline_contract_rejects_a_symlinked_helper_fingerprint_script(self) -> None:
        module = runpy.run_path(str(SCRIPT))
        with tempfile.TemporaryDirectory() as tmp:
            copied_root = Path(tmp) / "project"
            shutil.copytree(
                PROJECT_ROOT,
                copied_root,
                ignore=shutil.ignore_patterns(".git", ".venv*", "build", "dist", ".build", "__pycache__"),
            )
            fingerprint = copied_root / "packaging" / "helper_source_fingerprint.py"
            target = Path(tmp) / "external-helper-source-fingerprint.py"
            target.write_text(fingerprint.read_text(encoding="utf-8"), encoding="utf-8")
            fingerprint.unlink()
            fingerprint.symlink_to(target)

            checks, passed = module["validate"](copied_root)

        self.assertFalse(passed, checks)
        self.assertEqual(
            next(check for check in checks if check["code"] == "HELPER_SOURCE_FINGERPRINT_CHAIN")["state"],
            "FAIL",
        )

    def test_offline_contract_rejects_helper_app_protocol_mismatch(self) -> None:
        module = runpy.run_path(str(SCRIPT))
        with tempfile.TemporaryDirectory() as tmp:
            copied_root = Path(tmp) / "project"
            shutil.copytree(
                PROJECT_ROOT,
                copied_root,
                ignore=shutil.ignore_patterns(".git", ".venv*", "build", "dist", ".build", "__pycache__"),
            )
            swift_ipc = copied_root / "app" / "PhotosLocalKeywordIndexer" / "Models" / "IPCModels.swift"
            swift_ipc.write_text(
                swift_ipc.read_text(encoding="utf-8").replace(
                    'case photoProgress = "photo_progress"',
                    'case photoProgress = "other_event"',
                ),
                encoding="utf-8",
            )

            checks, passed = module["validate"](copied_root)

        self.assertFalse(passed, checks)
        self.assertEqual(
            next(check for check in checks if check["code"] == "APP_HELPER_IPC_PROTOCOL_CONTRACT")["state"],
            "FAIL",
        )

    def test_offline_contract_is_machine_readable_without_credentials_or_network(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--json"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        report = json.loads(result.stdout)
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["tool"], "photos-local-keyword-indexer")
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["next_action"], "run_signed_release_preflight")
        self.assertNotIn(str(PROJECT_ROOT), result.stdout)
        self.assertNotIn("DEVELOPER_ID_APPLICATION", result.stdout)
        self.assertNotIn("APPLE_NOTARY_PROFILE", result.stdout)
        self.assertTrue(report["checks"])
        self.assertTrue(all(set(check) == {"state", "code"} for check in report["checks"]))
        check_codes = {check["code"] for check in report["checks"]}
        self.assertIn("PACKAGE_SWIFT_SPARKLE_EXACT", check_codes)
        self.assertIn("SPARKLE_LOCK_SHAPE_GUARD", check_codes)
        self.assertIn("IMPLEMENTATION_NO_PRIVATE_DB", check_codes)
        self.assertIn("APP_HELPER_VERSION_TRACEABLE", check_codes)
        self.assertIn("APP_POSTCOPY_HELPER_RUNTIME_GATE", check_codes)
        self.assertIn("APP_HELPER_IPC_PROTOCOL_CONTRACT", check_codes)
        self.assertIn("APP_HELPER_IPC_ERROR_REDACTION", check_codes)
        self.assertIn("APP_PRIVACY_USAGE_DESCRIPTIONS", check_codes)
        self.assertIn("APP_ICON_METADATA", check_codes)
        self.assertIn("APP_ICON_ATOMIC_OUTPUT", check_codes)
        self.assertIn("APP_ICON_PATH_GUARD", check_codes)
        self.assertIn("RELEASE_PLIST_ROOT_SHAPE", check_codes)
        self.assertIn("RELEASE_PLIST_LINT_ERROR_REDACTION", check_codes)
        self.assertIn("RELEASE_BUNDLE_ERROR_REDACTION", check_codes)
        self.assertIn("RELEASE_DEBUG_ENTITLEMENT_ERROR_REDACTION", check_codes)
        self.assertIn("RELEASE_CODE_SIGNATURE_ERROR_REDACTION", check_codes)
        self.assertIn("RELEASE_EVIDENCE_OUTPUT_PATH_GUARD", check_codes)
        self.assertIn("RELEASE_EVIDENCE_INPUT_PATH_GUARD", check_codes)
        self.assertIn("RELEASE_EVIDENCE_APP_PATH_GUARD", check_codes)
        self.assertIn("RELEASE_EVIDENCE_ERROR_REDACTION", check_codes)
        self.assertIn("RELEASE_EVIDENCE_PUBLICATION_ERROR_REDACTION", check_codes)
        self.assertIn("RELEASE_HELPER_VERIFICATION_REQUIRED", check_codes)
        self.assertIn("APP_SPARKLE_PLACEHOLDER_GUARD", check_codes)
        self.assertIn("APP_SPARKLE_PUBLIC_KEY_PREFLIGHT", check_codes)
        self.assertIn("APP_RUNTIME_SPARKLE_PLACEHOLDER_GUARD", check_codes)
        self.assertIn("APP_SOURCE_HELPER_PATH_GUARD", check_codes)
        self.assertIn("EMBEDDED_HELPER_BUNDLE_PATH_GUARD", check_codes)
        self.assertIn("EMBEDDED_HELPER_PAYLOAD_SYMLINK_GUARD", check_codes)
        self.assertIn("EMBEDDED_HELPER_PAYLOAD_SCAN_GUARD", check_codes)
        self.assertIn("APP_IMPORT_PARTIAL_COPY_CLEANUP", check_codes)
        self.assertIn("APP_POSTBUILD_CLEANUP", check_codes)
        self.assertIn("APP_SWIFT_LOCKED_BUILD", check_codes)
        self.assertIn("APP_SWIFT_SELECTED_TOOLCHAIN", check_codes)
        self.assertIn("PREFLIGHT_SWIFT_SELECTED_TOOLCHAIN", check_codes)
        self.assertIn("APP_NO_BARE_SWIFT_SHIM_DEPENDENCY", check_codes)
        self.assertIn("APP_BUILD_ROOT_ERROR_REDACTION", check_codes)
        self.assertIn("APP_MAIN_BINARY_ARCHITECTURE", check_codes)
        self.assertIn("APP_PLISTBUDDY_PREFLIGHT", check_codes)
        self.assertIn("APP_PACKAGE_LOCK_GUARD", check_codes)
        self.assertIn("APP_SPARKLE_LOCK_PREBUILD", check_codes)
        self.assertIn("SPARKLE_LOCK_SIZE_BOUND", check_codes)
        self.assertIn("SPARKLE_LOCK_ERROR_REDACTION", check_codes)
        self.assertIn("SPARKLE_FRAMEWORK_PLIST_SHAPE", check_codes)
        self.assertIn("HELPER_LIPO_PRECHECK", check_codes)
        self.assertIn("HELPER_LIPO_CLEANUP", check_codes)
        self.assertIn("HELPER_PYTHON_PATH_STABLE", check_codes)
        self.assertIn("HELPER_RUNTIME_IMPORT_PREFLIGHT", check_codes)
        self.assertIn("HELPER_RUNTIME_PROVENANCE_PREFLIGHT", check_codes)
        self.assertIn("HELPER_ARCHITECTURE_ERROR_REDACTION", check_codes)
        self.assertIn("HELPER_DEPENDENCY_ERROR_REDACTION", check_codes)
        self.assertIn("HELPER_RUNTIME_ISOLATION", check_codes)
        self.assertIn("PREFLIGHT_HELPER_RUNTIME_ISOLATION", check_codes)
        self.assertIn("HELPER_APPLESCRIPT_ERROR_PREFLIGHT", check_codes)
        self.assertIn("HELPER_STALE_CLEANUP", check_codes)
        self.assertIn("HELPER_POSTBUILD_CLEANUP", check_codes)
        self.assertIn("HELPER_OUTPUT_SYMLINK_GUARD", check_codes)
        self.assertIn("HELPER_DIST_PATH_GUARD", check_codes)
        self.assertIn("BUILD_PATH_ERROR_REDACTION", check_codes)
        self.assertIn("HELPER_BUILD_DIRECTORIES_GUARD", check_codes)
        self.assertIn("HELPER_BUILD_DIRECTORIES_AFTER_PREFLIGHT", check_codes)
        self.assertIn("HELPER_BUILD_DIRECTORY_SETUP_CLEANUP", check_codes)
        self.assertIn("PYINSTALLER_SPEC_ARM64", check_codes)
        self.assertIn("RELEASE_DMG_LAYOUT_REQUIRED", check_codes)
        self.assertIn("DMG_HELPER_RUNTIME_GATE", check_codes)
        self.assertIn("DMG_EMBEDDED_HELPER_FRESHNESS_GATE", check_codes)
        self.assertIn("DMG_EMBEDDED_HELPER_PAYLOAD_FRESHNESS_GATE", check_codes)
        self.assertIn("RELEASE_BUNDLE_INTEGRITY_GUARD", check_codes)
        self.assertIn("RELEASE_EVIDENCE_VERSION_GATE", check_codes)
        self.assertIn("BUILD_DMG_HDIUTIL_PREFLIGHT", check_codes)
        self.assertIn("BUILD_DMG_COPY_TOOL_PREFLIGHT", check_codes)
        self.assertIn("BUILD_DMG_ERROR_REDACTION", check_codes)
        self.assertIn("DMG_IDENTITY_PREFLIGHT", check_codes)
        self.assertIn("DMG_ARTIFACT_RESERVATION", check_codes)
        self.assertIn("DMG_OUTPUT_DIRECTORY_CLEANUP", check_codes)
        self.assertIn("NOTARIZE_LAYOUT_PRE_SUBMIT", check_codes)
        self.assertIn("NOTARIZE_INTERNAL_SUCCESS_OUTPUT_SCOPED", check_codes)
        self.assertIn("NOTARIZE_TOOL_PREFLIGHT", check_codes)
        self.assertIn("NOTARIZE_DMG_HARDLINK_GUARD", check_codes)
        self.assertIn("NOTARIZE_MUTATION_LOCK", check_codes)
        self.assertIn("NOTARIZE_EVIDENCE_EXISTS_REDACTION", check_codes)
        self.assertIn("SIGN_APP_BUNDLE_INTEGRITY_GUARD", check_codes)
        self.assertIn("SIGN_APP_VERIFY_TOOL_PREFLIGHT", check_codes)
        self.assertIn("SIGN_APP_SYMLINK_ERROR_REDACTION", check_codes)
        self.assertEqual(
            report["release_gates"],
            [
                {"id": "developer_id", "state": "not_checked", "code": "DEVELOPER_ID_RUNTIME_REQUIRED"},
                {"id": "notarization", "state": "not_checked", "code": "NOTARIZATION_RUNTIME_REQUIRED"},
                {"id": "tcc", "state": "not_checked", "code": "TCC_RUNTIME_REQUIRED"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
