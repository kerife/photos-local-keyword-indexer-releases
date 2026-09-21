from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from photos_indexer.manifest import compute_mutation_digest, load_manifest, write_manifest
from photos_indexer.workflows import _reviewed_manifest_can_retry_failed_rows, run_status

from tests.test_workflows import make_photo, make_reviewed_manifest


class MutationEvidenceTests(unittest.TestCase):
    def test_status_blocks_uncertain_applied_data_without_a_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            photo = make_photo(1)
            photo.apply_state = "uncertain"
            photo.applied_keywords = ["playa"]
            photo.errors = [{"stage": "apply", "code": "WRITE_UNCERTAIN"}]
            path = make_reviewed_manifest(Path(tmp) / "run", [photo])
            manifest = load_manifest(path.parent)
            manifest.photos[0].mutation_digest = None
            write_manifest(path.parent, manifest)

            result = run_status(path)

            self.assertEqual(result.exit_code, 2)
            self.assertEqual(result.error_codes, ("MUTATION_EVIDENCE_INVALID",))
            self.assertEqual(result.status_summary["apply_status"], "blocked")
            self.assertEqual(result.next_action, "rescan_after_mutation_evidence")

    def test_status_blocks_rollback_state_without_a_rollback_receipt(self) -> None:
        """A hand-edited rollback result must not look verified to status."""
        with tempfile.TemporaryDirectory() as tmp:
            path = make_reviewed_manifest(Path(tmp) / "run", [make_photo(1)])
            manifest = load_manifest(path.parent)
            photo = manifest.photos[0]
            photo.apply_state = "verified"
            photo.applied_keywords = ["playa"]
            photo.mutation_digest = compute_mutation_digest(manifest, photo)
            write_manifest(path.parent, manifest)

            # Simulate an edit to the rollback journal without a receipt. The
            # apply receipt remains valid, so only rollback evidence should
            # decide whether status can claim a verified removal.
            manifest = load_manifest(path.parent)
            photo = manifest.photos[0]
            photo.rollback_state = "verified_removed"
            photo.rolled_back_keywords = ["playa"]
            write_manifest(path.parent, manifest)

            result = run_status(path)

            self.assertEqual(result.exit_code, 2)
            self.assertEqual(result.error_codes, ("MUTATION_EVIDENCE_INVALID",))
            self.assertEqual(result.status_summary["rollback_status"], "blocked")
            self.assertEqual(result.next_action, "rescan_after_mutation_evidence")

    def test_status_does_not_count_removed_keywords_without_a_rollback_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = make_reviewed_manifest(Path(tmp) / "run", [make_photo(1)])
            manifest = load_manifest(path.parent)
            photo = manifest.photos[0]
            photo.apply_state = "verified"
            photo.applied_keywords = ["playa"]
            photo.mutation_digest = compute_mutation_digest(manifest, photo)
            write_manifest(path.parent, manifest)

            manifest = load_manifest(path.parent)
            photo = manifest.photos[0]
            photo.rollback_state = "verified_removed"
            photo.rolled_back_keywords = ["playa"]
            write_manifest(path.parent, manifest)

            result = run_status(path)

            self.assertEqual(result.error_codes, ("MUTATION_EVIDENCE_INVALID",))
            self.assertEqual(result.status_summary["keywords_removed"], 0)

    def test_status_blocks_a_receipt_attached_to_a_non_verified_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            photo = make_photo(1)
            photo.scan_state = "noop"
            photo.proposed_keywords = []
            manifest_path = make_reviewed_manifest(Path(tmp) / "run", [photo])
            manifest = load_manifest(manifest_path.parent)
            manifest.photos[0].mutation_digest = "a" * 64
            write_manifest(manifest_path.parent, manifest)

            result = run_status(manifest_path)

            self.assertEqual(result.error_codes, ("MUTATION_EVIDENCE_INVALID",))
            self.assertEqual(result.status_summary["rollback_status"], "blocked")

    def test_retry_validation_rejects_receipts_and_untrusted_failed_rows(self) -> None:
        cases: list[tuple[str, dict[str, object]]] = [
            ("rollback_started", {"apply_state": "failed", "rollback_state": "failed"}),
            ("noop_receipt", {"apply_state": "noop", "mutation_digest": "b" * 64}),
            ("failed_receipt", {"apply_state": "failed", "mutation_digest": "c" * 64}),
            ("failed_without_apply_error", {"apply_state": "failed"}),
        ]
        for label, updates in cases:
            with self.subTest(label=label):
                # This helper creates a valid reviewed provenance pair while
                # retaining the mutable state under test.
                with tempfile.TemporaryDirectory() as tmp:
                    path = make_reviewed_manifest(Path(tmp) / "run", [make_photo(1)])
                    manifest = load_manifest(path.parent)
                    photo = manifest.photos[0]
                    photo.apply_state = "failed"
                    photo.errors = [{"stage": "apply", "code": "APPLY_FAILED"}]
                    if label == "rollback_started":
                        photo.apply_state = "verified"
                        photo.applied_keywords = ["playa"]
                    for name, value in updates.items():
                        setattr(photo, name, value)
                    if label == "failed_without_apply_error":
                        photo.errors = []
                    self.assertFalse(_reviewed_manifest_can_retry_failed_rows(manifest))
