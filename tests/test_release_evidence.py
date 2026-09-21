from __future__ import annotations

import hashlib
import json
import os
import plistlib
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "packaging" / "write_release_evidence.sh"


class ReleaseEvidenceTests(unittest.TestCase):
    def test_release_evidence_requires_the_nested_helper_bundle_identity(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")

        self.assertIn(
            'app_path / "Contents" / "Helpers" / "PhotosIndexerWorker.app"',
            source,
        )
        self.assertIn('"CFBundleIdentifier": "com.photoslocalkeywordindexer.worker"', source)
        self.assertIn(
            'helper_app / "Contents" / "MacOS" / "PhotosIndexerWorker"',
            source,
        )
        self.assertIn('helper_bundle.get("CFBundleShortVersionString")', source)
        self.assertIn('helper_bundle.get("CFBundleVersion")', source)
        self.assertNotIn('helper_app = app_path / "Contents" / "Helpers" / "PhotosIndexerWorker"', source)

    def test_invalid_evidence_inputs_fail_without_leaking_local_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            relative = ("release.dmg", "App.app", "notary.json", "evidence.json")
            absolute = tuple(
                str(root / name)
                for name in ("missing.dmg", "Missing.app", "missing-notary.json", "missing-evidence.json")
            )

            for arguments in (relative, absolute):
                with self.subTest(arguments=arguments):
                    result = subprocess.run(
                        ["/bin/zsh", str(SCRIPT), *arguments],
                        check=False,
                        capture_output=True,
                        text=True,
                        cwd=root,
                    )

                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(
                        result.stderr,
                        "release_evidence:FAIL:input_path_invalid\n"
                        "release_evidence:HINT:input_path_invalid:provide_private_absolute_release_artifacts\n",
                    )
                    self.assertNotIn(str(PROJECT_ROOT), result.stderr)
                    self.assertNotIn(str(root), result.stderr)
                    self.assertFalse((root / Path(arguments[-1]).name).exists())

    def test_sidecar_publishing_is_create_only_and_never_replaces_a_racing_file(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("os.link(temporary_name, output_path)", source)
        self.assertNotIn("os.replace(temporary_name, output_path)", source)

    def test_sidecar_rechecks_input_identities_before_publication(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")

        self.assertIn("def same_identity(path: Path, expected: os.stat_result) -> bool:", source)
        guard = "if not all(\n    same_identity(path, expected)\n"
        guard_position = source.index(guard)
        temporary = source.index("with tempfile.NamedTemporaryFile(")
        self.assertLess(guard_position, temporary)
        for expected in ("dmg_metadata", "app_metadata", "notary_metadata", "output_parent_metadata"):
            self.assertIn(expected, source[guard_position:temporary])

    def test_sidecar_rejects_dmg_mutation_during_or_after_hashing(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")

        hashing_start = source.index("digest = hashlib.sha256()", source.index("expected_artifact_name"))
        hashing = source[hashing_start : source.index("evidence = {", hashing_start)]
        self.assertIn("opened_dmg_metadata = os.fstat(stream.fileno())", hashing)
        self.assertIn("hashed_dmg_metadata = os.fstat(stream.fileno())", hashing)
        self.assertIn("dmg_metadata = hashed_dmg_metadata", hashing)
        self.assertIn("st_mtime_ns", source[source.index("def same_file_state") : hashing_start])
        identity = source[source.index("def same_identity") : source.index("if not all(")]
        self.assertIn("current.st_size == expected.st_size", identity)
        self.assertIn("current.st_mtime_ns == expected.st_mtime_ns", identity)

    def test_bundle_fingerprint_hashes_stable_regular_file_descriptors(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        function = source[
            source.index("def bundle_tree_fingerprint") : source.index("\n\ndef strict_json_object")
        ]

        self.assertIn("os.O_NOFOLLOW", function)
        self.assertIn("opened_bundle_metadata = os.fstat(stream.fileno())", function)
        self.assertIn("hashed_bundle_metadata = os.fstat(stream.fileno())", function)
        self.assertIn("same_file_state(opened_bundle_metadata, hashed_bundle_metadata)", function)
        self.assertIn("same_file_state(hashed_bundle_metadata, entry.lstat())", function)

    def test_sidecar_publishing_verifies_the_linked_inode_before_accepting_it(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")

        self.assertIn("os.fchmod(stream.fileno(), 0o600)", source)
        self.assertNotIn("os.chmod(temporary_name, 0o600)", source)
        self.assertIn("published_output_stat.st_ino != temporary_stat.st_ino", source)
        self.assertIn("published_output_stat.st_dev != temporary_stat.st_dev", source)
        link = source.index("os.link(temporary_name, output_path)")
        identity_check = source.index("published_output_stat.st_ino != temporary_stat.st_ino", link)
        directory_fsync = source.index("directory_fd = os.open", link)
        self.assertLess(link, identity_check)
        self.assertLess(identity_check, directory_fsync)

    def test_sidecar_writer_cleans_its_atomic_temp_on_interrupt(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("signal.signal(signal.SIGINT", source)
        self.assertIn("signal.signal(signal.SIGTERM", source)
        self.assertIn("os.unlink(temporary_name)", source)

    def test_sidecar_writer_only_cleans_the_atomic_temp_inode_it_created(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")

        self.assertIn("temporary_stat = None", source)
        self.assertIn("temporary_stat = os.fstat(stream.fileno())", source)
        self.assertIn("current_temporary_stat = os.stat(temporary_name, follow_symlinks=False)", source)
        self.assertIn("current_temporary_stat.st_ino == temporary_stat.st_ino", source)
        self.assertIn("current_temporary_stat.st_dev == temporary_stat.st_dev", source)
        cleanup = source[source.index("def cleanup_temporary_file()") : source.index("\n\ndef handle_signal")]
        self.assertLess(cleanup.index("current_temporary_stat.st_ino"), cleanup.index("os.unlink(temporary_name)"))

    def test_sidecar_writer_removes_published_output_if_directory_fsync_fails(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("published_output_stat", source)
        self.assertIn("os.unlink(output_path)", source)
        self.assertIn("st_ino == published_output_stat.st_ino", source)
        self.assertIn("st_dev == published_output_stat.st_dev", source)

    def test_sidecar_publication_failures_are_sanitized_without_tracebacks(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("except BaseException as error:", source)
        self.assertIn('print("Release evidence publication failed.", file=sys.stderr)', source)
        self.assertIn("raise SystemExit(1) from None", source)

    def test_release_evidence_contains_a_path_free_reproducible_bundle_fingerprint(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("bundle_fingerprint", source)
        self.assertIn('"sha256-tree-v1"', source)
        self.assertIn("rglob(\"*\")", source)
        self.assertIn("stat.S_IMODE", source)

        with (
            tempfile.TemporaryDirectory() as first_tmp,
            tempfile.TemporaryDirectory() as second_tmp,
            tempfile.TemporaryDirectory() as third_tmp,
            tempfile.TemporaryDirectory() as fourth_tmp,
        ):
            first = Path(first_tmp)
            second = Path(second_tmp)
            third = Path(third_tmp)
            fourth = Path(fourth_tmp)
            first_dmg, first_app, first_notary, first_sidecar = self._fixtures(first, status="Accepted")
            second_dmg, second_app, second_notary, second_sidecar = self._fixtures(second, status="Accepted")
            third_dmg, third_app, third_notary, third_sidecar = self._fixtures(third, status="Accepted")
            fourth_dmg, fourth_app, fourth_notary, fourth_sidecar = self._fixtures(fourth, status="Accepted")
            (third_app / "Contents" / "runtime-marker").write_bytes(b"changed")
            (fourth_app / "Contents" / "Info.plist").chmod(0o600)

            for dmg, app, notary, sidecar in (
                (first_dmg, first_app, first_notary, first_sidecar),
                (second_dmg, second_app, second_notary, second_sidecar),
                (third_dmg, third_app, third_notary, third_sidecar),
                (fourth_dmg, fourth_app, fourth_notary, fourth_sidecar),
            ):
                result = subprocess.run(
                    ["zsh", str(SCRIPT), str(dmg), str(app), str(notary), str(sidecar)],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

            first_evidence = json.loads(first_sidecar.read_text(encoding="utf-8"))
            second_evidence = json.loads(second_sidecar.read_text(encoding="utf-8"))
            third_evidence = json.loads(third_sidecar.read_text(encoding="utf-8"))
            fourth_evidence = json.loads(fourth_sidecar.read_text(encoding="utf-8"))
            self.assertEqual(first_evidence["bundle_fingerprint"], second_evidence["bundle_fingerprint"])
            self.assertNotEqual(first_evidence["bundle_fingerprint"], third_evidence["bundle_fingerprint"])
            self.assertNotEqual(first_evidence["bundle_fingerprint"], fourth_evidence["bundle_fingerprint"])
            self.assertNotIn(str(first), first_sidecar.read_text(encoding="utf-8"))
            self.assertNotIn(str(second), second_sidecar.read_text(encoding="utf-8"))

    def test_accepted_notarization_writes_sanitized_release_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dmg, app, notary_result, sidecar = self._fixtures(root, status="Accepted")

            result = subprocess.run(
                ["zsh", str(SCRIPT), str(dmg), str(app), str(notary_result), str(sidecar)],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            evidence = json.loads(sidecar.read_text(encoding="utf-8"))
            self.assertEqual(evidence["schema_version"], 1)
            self.assertEqual(evidence["artifact"]["filename"], "PhotosLocalKeywordIndexer-1.2.3-45.dmg")
            self.assertEqual(evidence["artifact"]["sha256"], hashlib.sha256(b"stapled-dmg").hexdigest())
            self.assertEqual(evidence["bundle"], {
                "build": "45",
                "identifier": "com.photoslocalkeywordindexer.app",
                "minimum_macos": "14.0",
                "version": "1.2.3",
            })
            self.assertEqual(evidence["sparkle"]["framework_version"], "2.9.2")
            self.assertEqual(evidence["notarization"], {
                "status": "Accepted",
                "submission_id": "123e4567-e89b-12d3-a456-426614174000",
            })
            serialized = sidecar.read_text(encoding="utf-8")
            self.assertNotIn(str(root), serialized)
            self.assertNotIn("message", serialized)
            self.assertEqual(stat.S_IMODE(sidecar.stat().st_mode), 0o600)

    def test_success_output_does_not_disclose_the_local_sidecar_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dmg, app, notary_result, sidecar = self._fixtures(root, status="Accepted")

            result = subprocess.run(
                ["zsh", str(SCRIPT), str(dmg), str(app), str(notary_result), str(sidecar)],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn(str(root), result.stdout)
            self.assertEqual(result.stdout.strip(), "release_evidence:RECORDED")

    def test_rejected_notarization_does_not_write_a_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dmg, app, notary_result, sidecar = self._fixtures(root, status="Invalid")

            result = subprocess.run(
                ["zsh", str(SCRIPT), str(dmg), str(app), str(notary_result), str(sidecar)],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Accepted", result.stderr)
            self.assertFalse(sidecar.exists())

    def test_malformed_notarization_shape_is_sanitized_without_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dmg, app, notary_result, sidecar = self._fixtures(root, status="Accepted")
            notary_result.write_text("[]", encoding="utf-8")

            result = subprocess.run(
                ["zsh", str(SCRIPT), str(dmg), str(app), str(notary_result), str(sidecar)],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("invalid", result.stderr.lower())
            self.assertNotIn("Traceback", result.stderr)
            self.assertFalse(sidecar.exists())

    def test_duplicate_notarization_keys_do_not_write_a_sidecar(self) -> None:
        """Ambiguous JSON must not be treated as an Accepted notary result."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dmg, app, notary_result, sidecar = self._fixtures(root, status="Accepted")
            notary_result.write_text(
                '{"status":"Invalid","status":"Accepted",'
                '"id":"123e4567-e89b-12d3-a456-426614174000"}',
                encoding="utf-8",
            )

            result = subprocess.run(
                ["zsh", str(SCRIPT), str(dmg), str(app), str(notary_result), str(sidecar)],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("invalid", result.stderr.lower())
            self.assertNotIn("Traceback", result.stderr)
            self.assertFalse(sidecar.exists())

    def test_deeply_nested_notarization_result_is_sanitized_without_sidecar(self) -> None:
        """An adversarial notary response must not escape as a Python traceback."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dmg, app, notary_result, sidecar = self._fixtures(root, status="Accepted")
            notary_result.write_text("[" * 2_000 + "]" * 2_000, encoding="utf-8")

            result = subprocess.run(
                ["zsh", str(SCRIPT), str(dmg), str(app), str(notary_result), str(sidecar)],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("invalid", result.stderr.lower())
            self.assertNotIn("Traceback", result.stderr)
            self.assertNotIn(str(root), result.stderr)
            self.assertFalse(sidecar.exists())

    def test_malformed_bundle_input_does_not_echo_local_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dmg, app, notary_result, sidecar = self._fixtures(root, status="Accepted")
            info = app / "Contents" / "Info.plist"
            info.unlink()
            info.mkdir()

            result = subprocess.run(
                ["zsh", str(SCRIPT), str(dmg), str(app), str(notary_result), str(sidecar)],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("invalid", result.stderr.lower())
            self.assertNotIn(str(root), result.stderr)
            self.assertFalse(sidecar.exists())

    def test_existing_release_evidence_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dmg, app, notary_result, sidecar = self._fixtures(root, status="Accepted")
            sidecar.write_text("existing evidence\n", encoding="utf-8")

            result = subprocess.run(
                ["zsh", str(SCRIPT), str(dmg), str(app), str(notary_result), str(sidecar)],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("new sidecar", result.stderr)
            self.assertEqual(sidecar.read_text(encoding="utf-8"), "existing evidence\n")

    def test_release_evidence_rejects_a_symlinked_output_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real = root / "real"
            real.mkdir()
            link = root / "linked"
            link.symlink_to(real, target_is_directory=True)
            dmg, app, notary_result, _ = self._fixtures(real, status="Accepted")
            linked_dmg = link / dmg.name
            linked_dmg.write_bytes(dmg.read_bytes())
            sidecar = link / f"{dmg.stem}.release-evidence.json"

            result = subprocess.run(
                ["zsh", str(SCRIPT), str(linked_dmg), str(app), str(notary_result), str(sidecar)],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("symlink", result.stderr.lower())
            self.assertFalse(sidecar.exists())

    def test_release_evidence_rejects_a_symlinked_path_ancestor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real = root / "real"
            real.mkdir()
            linked = root / "linked"
            linked.symlink_to(real, target_is_directory=True)
            routed = linked / "release"
            routed.mkdir()
            dmg, app, notary_result, _ = self._fixtures(routed, status="Accepted")
            sidecar = routed / f"{dmg.stem}.release-evidence.json"

            result = subprocess.run(
                ["zsh", str(SCRIPT), str(dmg), str(app), str(notary_result), str(sidecar)],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("symlink", result.stderr.lower())
            self.assertFalse(sidecar.exists())

    def test_release_evidence_rejects_a_symlinked_app_path_ancestor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real = root / "real"
            real.mkdir()
            linked = root / "linked"
            linked.symlink_to(real, target_is_directory=True)
            dmg, app, notary_result, sidecar = self._fixtures(real, status="Accepted")
            routed_app = linked / app.name

            result = subprocess.run(
                ["zsh", str(SCRIPT), str(dmg), str(routed_app), str(notary_result), str(sidecar)],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("symlink", result.stderr.lower())
            self.assertFalse(sidecar.exists())

    def test_mismatched_dmg_filename_does_not_write_release_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dmg, app, notary_result, sidecar = self._fixtures(root, status="Accepted")
            mismatched_dmg = root / "PhotosLocalKeywordIndexer-9.9.9-99.dmg"
            mismatched_dmg.write_bytes(dmg.read_bytes())
            mismatched_sidecar = root / "PhotosLocalKeywordIndexer-9.9.9-99.release-evidence.json"

            result = subprocess.run(
                [
                    "zsh",
                    str(SCRIPT),
                    str(mismatched_dmg),
                    str(app),
                    str(notary_result),
                    str(mismatched_sidecar),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("filename", result.stderr.lower())
            self.assertFalse(mismatched_sidecar.exists())
            self.assertFalse(sidecar.exists())

    def test_incomplete_app_does_not_write_release_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dmg, app, notary_result, sidecar = self._fixtures(root, status="Accepted")
            (app / "Contents" / "Helpers" / "PhotosIndexerWorker.app" / "Contents" / "MacOS" / "PhotosIndexerWorker").unlink()

            result = subprocess.run(
                ["zsh", str(SCRIPT), str(dmg), str(app), str(notary_result), str(sidecar)],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("incomplete", result.stderr.lower())
            self.assertFalse(sidecar.exists())

    def test_helper_version_mismatch_does_not_write_release_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dmg, app, notary_result, sidecar = self._fixtures(root, status="Accepted")
            helper_info = app / "Contents" / "Helpers" / "PhotosIndexerWorker.app" / "Contents" / "Info.plist"
            helper_values = plistlib.loads(helper_info.read_bytes())
            helper_values["CFBundleShortVersionString"] = "9.9.9"
            helper_info.write_bytes(plistlib.dumps(helper_values))

            result = subprocess.run(
                ["zsh", str(SCRIPT), str(dmg), str(app), str(notary_result), str(sidecar)],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("helper bundle version", result.stderr.lower())
            self.assertFalse(sidecar.exists())

    def test_group_writable_bundle_content_does_not_write_release_evidence(self) -> None:
        """A release sidecar must not bless a bundle another user may alter."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dmg, app, notary_result, sidecar = self._fixtures(root, status="Accepted")
            main = app / "Contents" / "MacOS" / "PhotosLocalKeywordIndexer"
            main.chmod(0o775)

            result = subprocess.run(
                ["zsh", str(SCRIPT), str(dmg), str(app), str(notary_result), str(sidecar)],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("bundle", result.stderr.lower())
            self.assertFalse(sidecar.exists())

    def test_release_evidence_rejects_sparkle_symlink_escaping_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dmg, app, notary_result, sidecar = self._fixtures(root, status="Accepted")
            current = app / "Contents" / "Frameworks" / "Sparkle.framework" / "Versions" / "Current"
            real_current = current.with_name("A")
            current.rename(real_current)
            outside_current = root / "outside" / "Current"
            outside_current.parent.mkdir()
            shutil.copytree(real_current, outside_current)
            shutil.rmtree(real_current)
            current.symlink_to(outside_current, target_is_directory=True)

            result = subprocess.run(
                ["zsh", str(SCRIPT), str(dmg), str(app), str(notary_result), str(sidecar)],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("symlink", result.stderr.lower())
            self.assertFalse(sidecar.exists())

    def test_incoherent_bundle_identity_does_not_write_release_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dmg, app, notary_result, sidecar = self._fixtures(root, status="Accepted")
            info = app / "Contents" / "Info.plist"
            values = plistlib.loads(info.read_bytes())
            values["CFBundleExecutable"] = "UnexpectedExecutable"
            info.write_bytes(plistlib.dumps(values))

            result = subprocess.run(
                ["zsh", str(SCRIPT), str(dmg), str(app), str(notary_result), str(sidecar)],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("identity", result.stderr.lower())
            self.assertFalse(sidecar.exists())

    def test_empty_dmg_does_not_write_release_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dmg, app, notary_result, sidecar = self._fixtures(root, status="Accepted")
            dmg.write_bytes(b"")

            result = subprocess.run(
                ["zsh", str(SCRIPT), str(dmg), str(app), str(notary_result), str(sidecar)],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("disk image", result.stderr.lower())
            self.assertFalse(sidecar.exists())

    def test_hardlinked_dmg_does_not_write_release_evidence(self) -> None:
        """Evidence must not bless a DMG shared with another filesystem path."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dmg, app, notary_result, sidecar = self._fixtures(root, status="Accepted")
            alternate_name = root / "shared-release.dmg"
            os.link(dmg, alternate_name)

            result = subprocess.run(
                ["zsh", str(SCRIPT), str(dmg), str(app), str(notary_result), str(sidecar)],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("disk image", result.stderr.lower())
            self.assertFalse(sidecar.exists())

    def test_hardlinked_notary_result_does_not_write_release_evidence(self) -> None:
        """Evidence must not consume a mutable alias for the notary result."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dmg, app, notary_result, sidecar = self._fixtures(root, status="Accepted")
            os.link(notary_result, root / "shared-notary-result.json")

            result = subprocess.run(
                ["zsh", str(SCRIPT), str(dmg), str(app), str(notary_result), str(sidecar)],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("input", result.stderr.lower())
            self.assertFalse(sidecar.exists())

    def test_release_evidence_rejects_a_shared_writable_output_directory(self) -> None:
        """Another local account must not be able to replace release evidence."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dmg, app, notary_result, sidecar = self._fixtures(root, status="Accepted")
            root.chmod(0o777)

            result = subprocess.run(
                ["zsh", str(SCRIPT), str(dmg), str(app), str(notary_result), str(sidecar)],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("directory", result.stderr.lower())
            self.assertFalse(sidecar.exists())

    @staticmethod
    def _fixtures(root: Path, *, status: str) -> tuple[Path, Path, Path, Path]:
        dmg = root / "PhotosLocalKeywordIndexer-1.2.3-45.dmg"
        dmg.write_bytes(b"stapled-dmg")
        app = root / "PhotosLocalKeywordIndexer.app"
        info = app / "Contents" / "Info.plist"
        info.parent.mkdir(parents=True)
        with info.open("wb") as stream:
            plistlib.dump(
                {
                    "CFBundleIdentifier": "com.photoslocalkeywordindexer.app",
                    "CFBundleExecutable": "PhotosLocalKeywordIndexer",
                    "CFBundlePackageType": "APPL",
                    "CFBundleIconFile": "AppIcon.icns",
                    "CFBundleShortVersionString": "1.2.3",
                    "CFBundleVersion": "45",
                    "LSMinimumSystemVersion": "14.0",
                },
                stream,
            )
        sparkle_info = app / "Contents" / "Frameworks" / "Sparkle.framework" / "Versions" / "Current" / "Resources" / "Info.plist"
        sparkle_info.parent.mkdir(parents=True)
        with sparkle_info.open("wb") as stream:
            plistlib.dump({"CFBundleShortVersionString": "2.9.2"}, stream)
        main = app / "Contents" / "MacOS" / "PhotosLocalKeywordIndexer"
        helper_app = app / "Contents" / "Helpers" / "PhotosIndexerWorker.app"
        helper = helper_app / "Contents" / "MacOS" / "PhotosIndexerWorker"
        helper_info = helper_app / "Contents" / "Info.plist"
        helper_resources = helper_app / "Contents" / "Resources"
        helper_frameworks = helper_app / "Contents" / "Frameworks"
        icon = app / "Contents" / "Resources" / "AppIcon.icns"
        main.parent.mkdir(parents=True)
        helper.parent.mkdir(parents=True)
        helper_resources.mkdir(parents=True)
        helper_frameworks.mkdir(parents=True)
        icon.parent.mkdir(parents=True)
        for executable in (main, helper):
            executable.write_bytes(b"fixture executable")
            executable.chmod(0o755)
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
        icon.write_bytes(b"fixture icon")
        sparkle_binary = app / "Contents" / "Frameworks" / "Sparkle.framework" / "Versions" / "Current" / "Sparkle"
        sparkle_binary.write_bytes(b"fixture sparkle")
        sparkle_binary.chmod(0o755)
        notary_result = root / "notary-result.json"
        notary_result.write_text(
            json.dumps(
                {
                    "id": "123e4567-e89b-12d3-a456-426614174000",
                    "status": status,
                    "message": f"private diagnostic under {root}",
                }
            ),
            encoding="utf-8",
        )
        sidecar = root / "PhotosLocalKeywordIndexer-1.2.3-45.release-evidence.json"
        return dmg, app, notary_result, sidecar


if __name__ == "__main__":
    unittest.main()
