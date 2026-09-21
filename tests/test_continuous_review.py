from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from photos_indexer.manifest import (
    ManifestError,
    compute_review_decision_digest,
    load_manifest,
    reviewed_rows_match_source,
    write_manifest,
)
from photos_indexer.app_storage import AppStorage
from photos_indexer.service import review_manifest_v4
from photos_indexer.workflows import ApplyDependencies, run_apply

from tests.test_workflows import RevalidatingSelector, StatefulBridge, make_manifest, make_photo


class ContinuousReviewManifestTests(unittest.TestCase):
    def test_schema4_records_manual_edits_and_their_origins(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            photo = make_photo(1)
            photo.proposed_keywords = ["gato"]
            photo.proposed_caption = "Dos gatos descansan juntos."
            photo.caption_state = "proposed"
            source_path = make_manifest(root / "source", [photo])

            reviewed_path = review_manifest_v4(
                source_path,
                {photo.uuid: ["gato", "animal doméstico"]},
                {photo.uuid: "Un gato y un perro descansan juntos."},
            )

            source = load_manifest(source_path.parent)
            reviewed = load_manifest(reviewed_path.parent)
            row = reviewed.photos[0]
            self.assertEqual(reviewed.schema_version, 4)
            self.assertEqual(row.model_proposed_keywords, ["gato"])
            self.assertEqual(row.approved_keywords, ["gato", "animal doméstico"])
            self.assertEqual(
                row.keyword_origins,
                {"gato": "model", "animal doméstico": "manual"},
            )
            self.assertEqual(row.proposed_keywords, row.approved_keywords)
            self.assertEqual(row.model_proposed_caption, "Dos gatos descansan juntos.")
            self.assertEqual(row.approved_caption, "Un gato y un perro descansan juntos.")
            self.assertEqual(row.proposed_caption, row.approved_caption)
            self.assertEqual(row.caption_origin, "manual")
            self.assertEqual(reviewed.review_decision_digest, compute_review_decision_digest(reviewed))
            self.assertTrue(reviewed_rows_match_source(source, reviewed))

    def test_schema4_rejects_a_stale_review_decision_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            photo = make_photo(1)
            source_path = make_manifest(root / "source", [photo])
            reviewed_path = review_manifest_v4(source_path, {photo.uuid: ["playa"]}, {})
            payload = json.loads(reviewed_path.read_text(encoding="utf-8"))
            payload["photos"][0]["approved_keywords"] = ["playa", "mar"]
            reviewed_path.write_text(json.dumps(payload), encoding="utf-8")
            reviewed_path.chmod(0o600)

            with self.assertRaises(ManifestError):
                load_manifest(reviewed_path.parent)

    def test_schema4_preserves_a_rejected_model_caption_as_audit_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            photo = make_photo(1)
            photo.proposed_caption = "Un perro en la playa."
            photo.caption_state = "proposed"
            source_path = make_manifest(root / "source", [photo])

            reviewed_path = review_manifest_v4(source_path, {photo.uuid: ["playa"]}, {})
            row = load_manifest(reviewed_path.parent).photos[0]

            self.assertEqual(row.model_proposed_caption, "Un perro en la playa.")
            self.assertIsNone(row.approved_caption)
            self.assertIsNone(row.proposed_caption)
            self.assertIsNone(row.caption_origin)

    def test_schema4_rejects_an_unsafe_manual_keyword(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            photo = make_photo(1)
            source_path = make_manifest(root / "source", [photo])

            with self.assertRaises(ManifestError):
                review_manifest_v4(source_path, {photo.uuid: ["kevin@example.com"]}, {})

    def test_schema4_apply_revalidates_the_source_before_opening_photos(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            photo = make_photo(1)
            source_path = make_manifest(root / "source", [photo])
            reviewed_path = review_manifest_v4(source_path, {photo.uuid: ["playa"]}, {})
            source = load_manifest(source_path.parent)
            source.photos[0].title = "Título cambiado"
            write_manifest(source_path.parent, source)

            def unexpected_adapter() -> object:
                raise AssertionError("provenance must fail before Photos adapters")

            result = run_apply(
                reviewed_path,
                dependencies=ApplyDependencies(
                    selector_factory=unexpected_adapter,
                    bridge_factory=unexpected_adapter,
                    global_lock_path=root / "global.lock",
                ),
            )

            self.assertEqual(result.exit_code, 2)
            self.assertEqual(result.error_codes, ("REVIEW_PROVENANCE_INVALID",))

    def test_schema4_import_keeps_a_matching_source_mutation_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            photo = make_photo(1)
            source_path = make_manifest(root / "external" / "source", [photo])
            source_path.parent.parent.chmod(0o700)
            reviewed_path = review_manifest_v4(source_path, {photo.uuid: ["playa"]}, {})

            imported = AppStorage.for_home(root / "home").import_manifest(reviewed_path)
            status = json.loads((imported / "import-status.json").read_text(encoding="utf-8"))

            self.assertEqual(status["mode"], "mutation_ready")
            self.assertEqual(status["reason"], "SOURCE_RUN_IMPORTED")

    def test_schema4_preserves_an_unselected_analysis_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            failed = make_photo(1)
            failed.scan_state = "analysis_failed"
            failed.proposed_keywords = []
            failed.confidence = 0.0
            failed.errors = [{"stage": "analysis", "code": "ANALYSIS_FAILED"}]
            source_path = make_manifest(
                root / "source",
                [failed],
                scan_status="ready_with_errors",
            )

            reviewed_path = review_manifest_v4(source_path, {}, {})
            row = load_manifest(reviewed_path.parent).photos[0]

            self.assertEqual(row.scan_state, "analysis_failed")
            self.assertEqual(row.errors, failed.errors)

    def test_schema4_allows_manual_cataloging_after_model_analysis_failure(self) -> None:
        """A verified photo identity must remain manually catalogable if Ollama fails."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            failed = make_photo(1)
            failed.scan_state = "analysis_failed"
            failed.proposed_keywords = []
            failed.confidence = None
            failed.model_used = None
            failed.model_reason = None
            failed.errors = [{"stage": "analysis", "code": "ANALYSIS_FAILED"}]
            source_path = make_manifest(
                root / "runs" / "source",
                [failed],
                scan_status="ready_with_errors",
            )
            source_path.parent.parent.chmod(0o700)

            reviewed_path = review_manifest_v4(
                source_path,
                {failed.uuid: ["gato"]},
                {failed.uuid: "Un gato descansa en casa."},
            )
            reviewed = load_manifest(reviewed_path.parent)
            row = reviewed.photos[0]
            self.assertEqual(row.scan_state, "ready")
            self.assertEqual(row.keyword_origins, {"gato": "manual"})
            self.assertEqual(row.caption_origin, "manual")
            self.assertEqual(row.errors, failed.errors)

            bridge = StatefulBridge({"local-1": ["PERRO"]}, reviewed_path)
            applied = run_apply(
                reviewed_path,
                dependencies=ApplyDependencies(
                    selector_factory=RevalidatingSelector,
                    bridge_factory=lambda: bridge,
                    global_lock_path=root / "global" / "mutation.lock",
                ),
            )

            self.assertEqual(applied.exit_code, 1, applied)
            self.assertEqual(load_manifest(reviewed_path.parent).photos[0].apply_state, "verified")
            self.assertEqual(bridge.states["local-1"], ["PERRO", "gato"])
            self.assertEqual(bridge.descriptions["local-1"], "Un gato descansa en casa.")


if __name__ == "__main__":
    unittest.main()
