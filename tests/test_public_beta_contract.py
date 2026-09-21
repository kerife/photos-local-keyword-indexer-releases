from __future__ import annotations

import hashlib
import importlib.util
import plistlib
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "packaging" / "verify_public_beta.py"


def load_verifier():
    spec = importlib.util.spec_from_file_location("verify_public_beta", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PublicBetaContractTests(unittest.TestCase):
    def _mounted_app_fixture(self, mount: Path) -> None:
        app = mount / "PhotosLocalKeywordIndexer.app"
        resources = app / "Contents/Resources"
        for relative in (
            "Documentation/LICENSE", "Documentation/Sparkle-LICENSE", "Documentation/docs/privacy.md",
            "Documentation/docs/support.md", "Documentation/docs/release/third-party-notices.md",
            "ThirdPartyNotices/photoscript/LICENSE",
        ):
            path = resources / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("notice\n", encoding="utf-8")
        helper = app / "Contents/Helpers/PhotosIndexerWorker.app/Contents"
        helper.mkdir(parents=True, exist_ok=True)
        (app / "Contents/Info.plist").write_bytes(plistlib.dumps({}))
        (helper / "Info.plist").write_bytes(plistlib.dumps({
            "CFBundleIdentifier": "com.photoslocalkeywordindexer.worker",
            "CFBundleShortVersionString": "0.1.1", "CFBundleVersion": "2",
        }))
        (helper / "Resources").mkdir()
        (helper / "Resources/.photos-indexer-source-fingerprint").write_text("b" * 64, encoding="utf-8")
        (helper / "Resources/.photos-indexer-dependency-inventory.json").write_text(
            '{"dependencies":[{"license":"MIT","name":"photoscript","notice_files":["photoscript/LICENSE"],"version":"0.5.3"}],"membership_source":"pyinstaller-analysis-toc-v1","schema_version":1}',
            encoding="utf-8",
        )

    def test_sidecar_must_bind_the_exact_dev_dmg_name_and_digest(self) -> None:
        verifier = load_verifier()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dmg = root / "PhotosLocalKeywordIndexer-0.1.1-2-dev-arm64.dmg"
            dmg.write_bytes(b"beta artifact")
            digest = hashlib.sha256(dmg.read_bytes()).hexdigest()
            sidecar = root / f"{dmg.name}.sha256"
            sidecar.write_text(f"{digest}  {dmg.name}\n", encoding="utf-8")

            result = verifier.validate_checksum_sidecar(dmg, sidecar)

            self.assertEqual(result, (True, "BETA_CHECKSUM_VALID"))
            sidecar.write_text(f"{digest}  another.dmg\n", encoding="utf-8")
            self.assertEqual(
                verifier.validate_checksum_sidecar(dmg, sidecar),
                (False, "BETA_CHECKSUM_ARTIFACT_MISMATCH"),
            )

    def test_acceptance_evidence_requires_only_the_closed_sanitized_schema(self) -> None:
        verifier = load_verifier()
        digest = "a" * 64
        evidence = {
            "schema_version": 1,
            "artifact_sha256": digest,
            "clean_account_install": "passed",
            "first_launch": "passed",
            "photos_permission": "passed",
            "automation_permission": "passed",
            "dry_run": "passed",
            "apply_readback": "passed",
            "rollback_readback": "passed",
            "interruption_recovery": "passed",
            "reopen_no_duplicate": "passed",
            "accessibility_review": "passed",
            "temporary_cleanup": "passed",
        }

        self.assertEqual(verifier.validate_acceptance_evidence(evidence, digest), (True, "BETA_ACCEPTANCE_EVIDENCE_VALID"))
        evidence["note"] = "private detail"
        self.assertEqual(
            verifier.validate_acceptance_evidence(evidence, digest),
            (False, "BETA_ACCEPTANCE_EVIDENCE_SCHEMA_INVALID"),
        )

    def test_required_docs_are_versioned_and_contain_unsigned_manual_channel_markers(self) -> None:
        verifier = load_verifier()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            release_notes = root / "docs/release/unsigned-beta-0.1.1-2.md"
            release_notes.parent.mkdir(parents=True)
            (root / "README.md").write_text("unsigned beta manual updates Sparkle disabled Gatekeeper", encoding="utf-8")
            downloads = root / "docs/release/public-downloads-readme.md"
            downloads.write_text("unsigned beta manual updates Sparkle disabled Gatekeeper", encoding="utf-8")
            release_notes.write_text("unsigned beta manual updates Sparkle disabled Gatekeeper", encoding="utf-8")
            (root / "docs/privacy.md").write_text("privacy", encoding="utf-8")
            (root / "docs/support.md").write_text("support", encoding="utf-8")
            (root / "docs/release/third-party-notices.md").write_text("notices", encoding="utf-8")
            (root / "LICENSE").write_text("MIT License", encoding="utf-8")

            self.assertEqual(
                verifier.validate_public_docs(root, "0.1.1", "2"),
                (True, "BETA_PUBLIC_DOCS_VALID"),
            )
            release_notes.write_text("signed official release", encoding="utf-8")
            self.assertEqual(
                verifier.validate_public_docs(root, "0.1.1", "2"),
                (False, "BETA_PUBLIC_DOCS_INVALID"),
            )

    def test_frozen_helper_inventory_is_minimal_and_tied_to_source_fingerprint(self) -> None:
        verifier = load_verifier()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            info = root / "Info.plist"
            info.write_bytes(__import__("plistlib").dumps({
                "CFBundleIdentifier": "com.photoslocalkeywordindexer.worker",
                "CFBundleShortVersionString": "0.1.1",
                "CFBundleVersion": "2",
            }))
            marker = root / ".photos-indexer-source-fingerprint"
            marker.write_text("b" * 64, encoding="utf-8")

            dependency_inventory = root / ".photos-indexer-dependency-inventory.json"
            dependency_inventory.write_text(
                '{"dependencies":[{"license":"MIT","name":"photoscript","notice_files":["photoscript/LICENSE"],"version":"0.5.3"}],"membership_source":"pyinstaller-analysis-toc-v1","schema_version":1}\n',
                encoding="utf-8",
            )
            notice = root / "ThirdPartyNotices/photoscript/LICENSE"
            notice.parent.mkdir(parents=True)
            notice.write_text("license text\n", encoding="utf-8")
            inventory = verifier.frozen_helper_inventory(info, marker, dependency_inventory)

            self.assertEqual(inventory["bundle_id"], "com.photoslocalkeywordindexer.worker")
            self.assertEqual(inventory["source_fingerprint"], "b" * 64)
            self.assertEqual(inventory["dependencies"][0]["name"], "photoscript")
            self.assertEqual(set(inventory), {"bundle_id", "version", "build", "source_fingerprint", "dependencies"})

    def test_frozen_helper_inventory_requires_the_candidate_version_build_and_source(self) -> None:
        verifier = load_verifier()
        inventory = {
            "bundle_id": "com.photoslocalkeywordindexer.worker",
            "version": "0.1.1",
            "build": "2",
            "source_fingerprint": "c" * 64,
        }

        self.assertTrue(verifier.inventory_matches_candidate(inventory, "0.1.1", "2", "c" * 64))
        self.assertFalse(verifier.inventory_matches_candidate(inventory, "0.1.1", "3", "c" * 64))
        self.assertFalse(verifier.inventory_matches_candidate(inventory, "0.1.1", "2", "d" * 64))

    def test_embedded_public_documents_require_the_complete_license_privacy_support_set(self) -> None:
        verifier = load_verifier()
        with tempfile.TemporaryDirectory() as temporary:
            resources = Path(temporary) / "Resources"
            for relative in (
                "Documentation/LICENSE",
                "Documentation/Sparkle-LICENSE",
                "Documentation/docs/privacy.md",
                "Documentation/docs/support.md",
                "Documentation/docs/release/third-party-notices.md",
            ):
                path = resources / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("notice\n", encoding="utf-8")

            self.assertEqual(verifier.validate_embedded_public_documents(resources), (True, "BETA_EMBEDDED_DOCUMENTS_VALID"))
            (resources / "Documentation/Sparkle-LICENSE").unlink()
            self.assertEqual(verifier.validate_embedded_public_documents(resources), (False, "BETA_EMBEDDED_DOCUMENTS_MISSING"))

    def test_mounted_inventory_detaches_in_a_finally_block(self) -> None:
        source = SCRIPT_PATH.read_text(encoding="utf-8")
        finally_block = source.index("finally:\n        detach = subprocess.run")
        self.assertLess(finally_block, source.index("if detach.returncode != 0", finally_block))

    def test_mounted_inventory_detaches_after_an_inspection_exception(self) -> None:
        verifier = load_verifier()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mount = root / "mount"
            calls: list[list[str]] = []
            def run(command, **_kwargs):
                calls.append(command)
                if command[:2] == ["hdiutil", "attach"]:
                    self._mounted_app_fixture(mount)
                    return subprocess.CompletedProcess(command, 0, "/dev/disk99\n", "")
                if command[:2] == ["hdiutil", "detach"]:
                    shutil.rmtree(mount)
                    mount.mkdir()
                return subprocess.CompletedProcess(command, 0, "", "")
            with mock.patch.object(verifier.tempfile, "mkdtemp", return_value=str(root)), \
                 mock.patch.object(verifier.subprocess, "run", side_effect=run), \
                 mock.patch.object(verifier, "validate_embedded_public_documents", side_effect=OSError):
                inventory, result = verifier.mounted_inventory(Path("/tmp/candidate.dmg"), "0.1.1", "2", "b" * 64)
            self.assertIsNone(inventory)
            self.assertEqual(result, (False, "BETA_DMG_INSPECTION_INVALID"))
            self.assertIn(["hdiutil", "detach", "/dev/disk99"], calls)

    def test_mounted_inventory_blocks_when_detach_fails(self) -> None:
        verifier = load_verifier()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mount = root / "mount"
            def run(command, **_kwargs):
                if command[:2] == ["hdiutil", "attach"]:
                    self._mounted_app_fixture(mount)
                    return subprocess.CompletedProcess(command, 0, "/dev/disk99\n", "")
                if command[:2] == ["hdiutil", "detach"]:
                    return subprocess.CompletedProcess(command, 1, "", "")
                return subprocess.CompletedProcess(command, 0, "", "")
            with mock.patch.object(verifier.tempfile, "mkdtemp", return_value=str(root)), \
                 mock.patch.object(verifier.subprocess, "run", side_effect=run):
                inventory, result = verifier.mounted_inventory(Path("/tmp/candidate.dmg"), "0.1.1", "2", "b" * 64)
            self.assertIsNone(inventory)
            self.assertEqual(result, (False, "BETA_DMG_UNMOUNT_FAILED"))


if __name__ == "__main__":
    unittest.main()
