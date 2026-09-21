from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from photos_indexer.manifest import load_manifest, write_manifest
from photos_indexer.service import app_runs_root, review_manifest, scan, ScanRequest, support_snapshot
from photos_indexer.workflows import WorkflowResult, run_status

from tests.test_workflows import make_manifest, make_photo, make_reviewed_manifest


class StatusSupportHardeningTests(unittest.TestCase):
    def test_status_does_not_hide_uncertain_mutation_on_a_scan_error_row(self) -> None:
        """Mutation state on an errored row must remain manual-review-only."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            photo = make_photo(1)
            photo.errors = [{"stage": "analysis", "code": "LOW_CONFIDENCE"}]
            photo.apply_state = "uncertain"
            photo.applied_keywords = ["playa"]
            photo.errors.append({"stage": "apply", "code": "WRITE_UNCERTAIN"})
            manifest_path = make_reviewed_manifest(
                Path(temporary_directory) / "run", [photo], scan_status="ready_with_errors"
            )

            result = run_status(manifest_path)

            self.assertEqual(result.status_summary["apply_status"], "uncertain")
            self.assertEqual(result.status_summary["uncertain"], 1)
            self.assertEqual(result.next_action, "manual_review")

    def test_uncertain_keywords_are_not_reported_as_verified(self) -> None:
        """An uncertain write has evidence of an attempt, not read-back proof."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            photo = make_photo(1)
            photo.apply_state = "uncertain"
            photo.applied_keywords = ["playa"]
            photo.errors = [{"stage": "apply", "code": "WRITE_UNCERTAIN"}]
            manifest_path = make_reviewed_manifest(Path(temporary_directory) / "run", [photo])
            manifest = load_manifest(manifest_path.parent)
            manifest.photos[0].mutation_digest = None
            write_manifest(manifest_path.parent, manifest)

            result = run_status(manifest_path)

            self.assertEqual(result.exit_code, 2)
            self.assertEqual(result.error_codes, ("MUTATION_EVIDENCE_INVALID",))
            self.assertEqual(result.status_summary["keywords_verified"], 0)

    def test_failed_scan_status_surfaces_its_stable_run_error_code(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            manifest_path = make_manifest(Path(temporary_directory) / "run", [], scan_status="failed")
            manifest = load_manifest(manifest_path.parent)

            result = run_status(manifest_path)

            self.assertEqual(result.exit_code, 1)
            self.assertEqual(result.error_codes, tuple(error["code"] for error in manifest.run_errors))
            self.assertEqual(result.error_codes, ("WORKSPACE_FAILED",))
            self.assertEqual(result.next_action, "fix_failed_scan")

    def test_failed_scan_status_counts_the_run_failure_when_no_rows_exist(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            manifest_path = make_manifest(Path(temporary_directory) / "run", [], scan_status="failed")

            result = run_status(manifest_path)

            self.assertEqual(result.status_summary["failed"], 1)

    def test_status_counts_one_failed_rollback_row_once(self) -> None:
        """A single rollback failure must not be double-counted in status."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            photo = make_photo(1)
            photo.apply_state = "verified"
            photo.applied_keywords = ["playa"]
            photo.rollback_state = "failed"
            photo.errors = [{"stage": "rollback", "code": "ROLLBACK_FAILED"}]
            manifest_path = make_reviewed_manifest(Path(temporary_directory) / "run", [photo])

            result = run_status(manifest_path)

            self.assertEqual(result.status_summary["failed"], 1)

    def test_partial_scan_status_is_nonzero_and_recommends_a_rescan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            photo = make_photo(1)
            photo.scan_state = "analysis_failed"
            photo.proposed_keywords = []
            photo.errors = [{"stage": "analysis", "code": "LOW_CONFIDENCE"}]
            manifest_path = make_manifest(
                Path(temporary_directory) / "run",
                [photo],
                scan_status="ready_with_errors",
            )

            result = run_status(manifest_path)

            self.assertEqual(result.exit_code, 1)
            self.assertEqual(result.error_codes, ("LOW_CONFIDENCE",))
            self.assertEqual(result.next_action, "fix_failed_scan")

    def test_status_counts_a_scan_cleanup_error_as_a_failed_photo(self) -> None:
        """A scan error must not appear as an error-free processed row."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            photo = make_photo(1)
            photo.errors = [{"stage": "cleanup", "code": "EXPORT_DELETE_FAILED"}]
            manifest_path = make_manifest(
                Path(temporary_directory) / "run",
                [photo],
                scan_status="ready_with_errors",
            )

            result = run_status(manifest_path)

            self.assertEqual(result.exit_code, 1)
            self.assertEqual(result.error_codes, ("EXPORT_DELETE_FAILED",))
            self.assertEqual(result.status_summary["failed"], 1)

    def test_caption_only_status_reports_actionable_caption_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            photo = make_photo(1)
            photo.scan_state = "noop"
            photo.proposed_keywords = []
            photo.proposed_caption = "Una playa visible."
            photo.caption_state = "proposed"
            manifest_path = make_manifest(Path(temporary_directory) / "run", [photo])

            result = run_status(manifest_path)

            self.assertEqual(result.status_summary["captions_proposed"], 1)
            self.assertEqual(result.status_summary["captions_verified"], 0)
            self.assertEqual(result.status_summary["captions_removed"], 0)
            snapshot = support_snapshot(result)
            self.assertEqual(snapshot["status"]["captions_proposed"], 1)
            self.assertNotIn("Una playa visible.", repr(snapshot))

    def test_status_blocks_an_unstarted_failed_caption_review(self) -> None:
        """A failed caption state must not look ready for a fresh apply."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            photo = make_photo(1)
            photo.proposed_caption = "Una playa visible."
            photo.caption_state = "proposed"
            manifest_path = make_reviewed_manifest(Path(temporary_directory) / "run", [photo])
            manifest = load_manifest(manifest_path.parent)
            manifest.photos[0].caption_state = "failed"
            write_manifest(manifest_path.parent, manifest)

            result = run_status(manifest_path)

            self.assertEqual(result.exit_code, 2)
            self.assertEqual(result.error_codes, ("REVIEW_NOT_PRISTINE",))
            self.assertEqual(result.status_summary["apply_status"], "blocked")
            self.assertEqual(result.status_summary["rollback_status"], "blocked")
            self.assertEqual(result.next_action, "rescan_after_provenance")

    def test_status_distinguishes_preserved_and_pending_approved_fields(self) -> None:
        """Status must separate retained metadata from pending approvals."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            keyword_preserved = make_photo(1)
            keyword_preserved.apply_state = "noop"
            caption_preserved = make_photo(2)
            caption_preserved.scan_state = "noop"
            caption_preserved.proposed_keywords = []
            caption_preserved.proposed_caption = "Una playa visible."
            caption_preserved.caption_state = "preserved"
            caption_preserved.apply_state = "noop"
            caption_pending = make_photo(3)
            caption_pending.scan_state = "noop"
            caption_pending.proposed_keywords = []
            caption_pending.proposed_caption = "Un canal visible."
            caption_pending.caption_state = "proposed"
            manifest_path = make_manifest(
                Path(temporary_directory) / "run",
                [keyword_preserved, caption_preserved, caption_pending],
            )

            result = run_status(manifest_path)

            self.assertEqual(result.status_summary["keywords_preserved"], 1)
            self.assertEqual(result.status_summary["keywords_pending"], 0)
            self.assertEqual(result.status_summary["captions_preserved"], 1)
            self.assertEqual(result.status_summary["captions_pending"], 1)
            snapshot = support_snapshot(result)
            self.assertEqual(snapshot["status"]["captions_preserved"], 1)
            self.assertEqual(snapshot["status"]["captions_pending"], 1)

    def test_reviewed_status_reports_the_approved_scope_separately_from_scan_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source_path = make_manifest(Path(temporary_directory) / "source", [make_photo(1), make_photo(2)])
            source = load_manifest(source_path.parent)
            source_path.parent.parent.chmod(0o700)
            reviewed_path = review_manifest(
                source_path,
                {source.photos[0].uuid: ["playa"]},
            )

            result = run_status(reviewed_path)

            self.assertEqual(result.status_summary["selected"], 2)
            self.assertEqual(result.status_summary["approved_photos"], 1)
            self.assertEqual(result.status_summary["approved_keywords"], 1)
            self.assertEqual(result.status_summary["approved_captions"], 0)
            snapshot = support_snapshot(result)
            self.assertEqual(snapshot["status"]["approved_photos"], 1)

    def test_status_and_support_report_the_recorded_selection_strategy(self) -> None:
        """Support/status must distinguish a random scope from recent photos."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            manifest_path = make_manifest(Path(temporary_directory) / "run", [make_photo(1)])
            manifest = load_manifest(manifest_path.parent)
            manifest.selection["strategy"] = "random"
            write_manifest(manifest_path.parent, manifest)

            result = run_status(manifest_path)
            snapshot = support_snapshot(result)

            self.assertEqual(result.status_summary["selection_strategy"], "random")
            self.assertEqual(snapshot["status"]["selection_strategy"], "random")

    def test_status_excludes_scan_error_rows_from_approved_scope(self) -> None:
        """Approved counts must match rows that apply can actually process."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            source_path = make_manifest(Path(temporary_directory) / "source", [make_photo(1)])
            source_path.parent.parent.chmod(0o700)
            source = load_manifest(source_path.parent)
            reviewed_path = review_manifest(
                source_path,
                {source.photos[0].uuid: ["playa"]},
            )

            source = load_manifest(source_path.parent)
            reviewed = load_manifest(reviewed_path.parent)
            scan_error = {"stage": "analysis", "code": "LOW_CONFIDENCE"}
            source.photos[0].errors = [scan_error]
            reviewed.photos[0].errors = [scan_error]
            source.scan_status = "ready_with_errors"
            reviewed.scan_status = "ready_with_errors"
            write_manifest(source_path.parent, source)
            reviewed.source_scan_digest = source.scan_digest
            write_manifest(reviewed_path.parent, reviewed)

            result = run_status(reviewed_path)

            self.assertEqual(result.status_summary["approved_photos"], 0)
            self.assertEqual(result.status_summary["approved_keywords"], 0)
            self.assertEqual(result.status_summary["keywords_pending"], 0)

    def test_scan_rejects_a_durable_manifest_symlink_before_projecting_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            home = Path(temporary_directory) / "home"
            runs_root = app_runs_root(home)
            run_dir = runs_root / "run"
            run_dir.mkdir(parents=True, mode=0o700)
            runs_root.chmod(0o700)
            outside = Path(temporary_directory) / "outside.json"
            outside.write_text("private", encoding="utf-8")
            manifest_path = run_dir / "manifest.json"
            manifest_path.symlink_to(outside)
            events: list[dict[str, object]] = []

            def runner(root: Path, **kwargs: object) -> WorkflowResult:
                del root, kwargs
                return WorkflowResult(exit_code=0, manifest_path=manifest_path)

            with mock.patch("photos_indexer.service.Path.home", return_value=home):
                result = scan(ScanRequest(runs_root=runs_root), events.append, scan_runner=runner)

            self.assertEqual(result.exit_code, 2)
            self.assertEqual(result.error_codes, ("MANIFEST_PATH_INVALID",))
            self.assertNotIn("manifest", events[-1])

    def test_scan_rejects_a_missing_absolute_manifest_before_projecting_it(self) -> None:
        """A distributed runner cannot report success with a phantom run."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            home = Path(temporary_directory).resolve() / "home"
            runs_root = app_runs_root(home)
            manifest_path = runs_root / "phantom-run" / "manifest.json"
            events: list[dict[str, object]] = []

            def runner(root: Path, **kwargs: object) -> WorkflowResult:
                del root, kwargs
                return WorkflowResult(exit_code=0, manifest_path=manifest_path)

            with mock.patch("photos_indexer.service.Path.home", return_value=home):
                result = scan(ScanRequest(runs_root=runs_root), events.append, scan_runner=runner)

            self.assertEqual(result.exit_code, 2)
            self.assertEqual(result.error_codes, ("MANIFEST_PATH_INVALID",))
            self.assertFalse(manifest_path.exists())
            self.assertNotIn("manifest", events[-1])
