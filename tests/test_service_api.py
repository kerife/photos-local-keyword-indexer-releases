from __future__ import annotations

import unittest
import json
import shutil
from datetime import datetime
from pathlib import Path
import tempfile
import uuid
from unittest import mock

from photos_indexer.manifest import MAX_MANIFEST_BYTES, ManifestError, PhotoRecord, ScanManifest, build_scan_manifest, load_manifest, write_manifest
from photos_indexer.service import (
    CancellationToken,
    ScanRequest,
    apply_manifest,
    app_runs_root,
    rollback_manifest,
    review_manifest,
    scan,
    support_snapshot,
    validate_app_manifest_path,
    validate_app_runs_root,
)
from photos_indexer.taxonomy import TAXONOMY_ID, TAXONOMY_SHA256
from photos_indexer.workflows import DEFAULT_MODEL, ApplyDependencies, WorkflowResult, run_apply


def _manifest() -> ScanManifest:
    photo = PhotoRecord(
        uuid="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        photos_local_identifier="private-local-id",
        title="Private title that must not leave the service",
        date=datetime(2026, 8, 25, 10, 0),
        existing_keywords=["Viaje"],
        proposed_keywords=["playa"],
        contains_people=False,
        contains_text=False,
        confidence=0.9,
    )
    return ScanManifest(
        run_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        created_at=datetime(2026, 8, 25, 10, 0).astimezone(),
        app={"name": "photos-local-keyword-indexer", "version": "0.1.0"},
        model={"name": "qwen3-vl:4b", "ollama_version": "0.12.7", "endpoint": "http://127.0.0.1:11434"},
        selection={"requested": 1, "eligible": 1, "screenshots_excluded": 0, "access": "authorized"},
        photos=[photo],
        summary={"ready": 1, "noop": 0, "analysis_failed": 0},
        policy={
            "id": TAXONOMY_ID,
            "taxonomy_sha256": TAXONOMY_SHA256,
            "max_keywords": 8,
            "confidence_threshold": 0.6,
        },
    )


class ServiceTests(unittest.TestCase):
    def test_review_rejects_selection_for_a_ready_photo_with_scan_errors(self) -> None:
        source = _manifest()
        source.scan_status = "ready_with_errors"
        source.photos[0].errors = [{"stage": "analysis", "code": "ANALYSIS_FAILED"}]
        with tempfile.TemporaryDirectory() as tmp:
            source_path = write_manifest(Path(tmp) / "runs" / "source", source)
            source_path.parent.parent.chmod(0o700)

            with self.assertRaises(ManifestError) as raised:
                review_manifest(source_path, {source.photos[0].uuid: ["playa"]})

            self.assertIn("non-actionable", str(raised.exception))

    def test_app_storage_helpers_reject_a_non_path_home_value(self) -> None:
        with self.assertRaises(ValueError):
            app_runs_root(123)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            validate_app_runs_root(Path("/tmp/runs"), home=123, create=False)  # type: ignore[arg-type]

    def test_review_rejects_a_non_path_manifest_with_a_sanitized_error(self) -> None:
        with self.assertRaises(ValueError):
            review_manifest(123, {})  # type: ignore[arg-type]

    def test_scan_request_rejects_non_string_model_policy(self) -> None:
        for policy in ([], {}, 1):
            with self.subTest(policy=policy):
                request = ScanRequest(Path("/tmp/runs"), model_policy=policy)  # type: ignore[arg-type]
                with self.assertRaises(ValueError):
                    request.selected_model()

    def test_scan_projects_invalid_model_name_to_model_invalid(self) -> None:
        events: list[dict[str, object]] = []
        result = scan(ScanRequest(Path("runs"), model=""), events.append, scan_runner=lambda **kwargs: self.fail("runner called"))

        self.assertEqual(result.exit_code, 2)
        self.assertEqual(result.error_codes, ("MODEL_INVALID",))

    def test_scan_request_rejects_non_string_model_fields(self) -> None:
        for field in ("model", "fast_model", "detailed_model"):
            with self.subTest(field=field):
                request = ScanRequest(Path("/tmp/runs"), **{field: []})  # type: ignore[arg-type]
                with self.assertRaises(ValueError):
                    request.selected_model()

    def test_scan_request_validates_random_selection_as_boolean(self) -> None:
        for value in (1, "true", [], {}):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    ScanRequest(Path("runs"), random_selection=value)  # type: ignore[arg-type]

    def test_scan_request_preserves_legacy_positional_privacy_options(self) -> None:
        request = ScanRequest(
            Path("runs"), 1, "single", None, "qwen3-vl:4b", "qwen3-vl:8b", True, False
        )

        self.assertTrue(request.apple_maps)
        self.assertFalse(request.include_caption)
        self.assertFalse(request.random_selection)

    def test_adaptive_defaults_to_the_installed_fast_model_for_detailed_photos(self) -> None:
        request = ScanRequest(Path("runs"), model_policy="adaptive")

        self.assertEqual(request.fast_model, DEFAULT_MODEL)
        self.assertEqual(request.detailed_model, DEFAULT_MODEL)

    def test_scan_forwards_random_selection_to_workflow(self) -> None:
        captured: dict[str, object] = {}

        def runner(path: Path, **kwargs: object) -> WorkflowResult:
            del path
            captured.update(kwargs)
            return WorkflowResult(exit_code=0)

        result = scan(
            ScanRequest(Path("runs"), limit=1, random_selection=True),
            lambda event: None,
            scan_runner=runner,
        )

        self.assertEqual(result.exit_code, 0)
        self.assertTrue(captured["random_selection"])

    def test_scan_request_rejects_missing_required_model_fields(self) -> None:
        for field in ("fast_model", "detailed_model"):
            with self.subTest(field=field):
                request = ScanRequest(Path("/tmp/runs"), **{field: None})  # type: ignore[arg-type]
                with self.assertRaises(ValueError):
                    request.selected_model()

    def test_scan_request_rejects_empty_model_names(self) -> None:
        for field in ("model", "fast_model", "detailed_model"):
            for value in ("", "   "):
                with self.subTest(field=field, value=value):
                    request = ScanRequest(Path("/tmp/runs"), **{field: value})
                    with self.assertRaises(ValueError):
                        request.selected_model()

    def test_support_snapshot_is_shareable_without_photo_content_or_private_paths(self) -> None:
        result = WorkflowResult(
            exit_code=1,
            error_codes=("WORKSPACE_FAILED",),
            warning_codes=("FEWER_PHOTOS_AVAILABLE",),
            safe_instruction="ollama pull qwen3-vl:4b",
            next_action="fix_fatal_error",
            manifest=_manifest(),
            counts={"scan": {"ready": 1}, "apply": {"not_run": 1}, "rollback": {"not_run": 1}},
            status_summary={
                "scan_status": "ready_with_errors",
                "photos_access": "authorized",
                "selected": 1,
                "processed": 1,
                "errors_by_code": {"WORKSPACE_FAILED": 1},
            },
        )

        snapshot = support_snapshot(result)
        serialized = json.dumps(snapshot, ensure_ascii=False)

        self.assertEqual(snapshot["format_version"], 1)
        self.assertEqual(snapshot["error_codes"], ["WORKSPACE_FAILED"])
        self.assertEqual(snapshot["safe_instruction"], "ollama pull qwen3-vl:4b")
        self.assertEqual(snapshot["model"], {
            "name": "qwen3-vl:4b",
            "ollama_version": "0.12.7",
            "endpoint": "http://127.0.0.1:11434",
        })
        for forbidden in (
            "Private title",
            "private-local-id",
            "Viaje",
            "playa",
            "caption",
            "location",
            "2026-08-25",
        ):
            self.assertNotIn(forbidden.casefold(), serialized.casefold())

    def test_support_snapshot_accepts_many_excluded_screenshots_on_empty_scan(self) -> None:
        manifest = _manifest()
        manifest.selection.update({"requested": 1, "eligible": 0, "screenshots_excluded": 1061})
        manifest.photos.clear()
        manifest.summary = {"ready": 0, "noop": 0, "analysis_failed": 0}
        manifest.scan_digest = manifest.compute_scan_digest()
        result = WorkflowResult(
            exit_code=0,
            manifest=manifest,
            warning_codes=("FEWER_PHOTOS_AVAILABLE",),
            next_action="none",
        )

        snapshot = support_snapshot(result)

        self.assertEqual(snapshot["exit_code"], 0)
        self.assertEqual(snapshot["error_codes"], [])
        self.assertEqual(snapshot["warning_codes"], ["FEWER_PHOTOS_AVAILABLE"])

    def test_support_snapshot_keeps_preflight_recovery_action_without_raw_details(self) -> None:
        result = WorkflowResult(
            exit_code=2,
            error_codes=("OLLAMA_MODEL_MISSING",),
            safe_instruction="ollama pull qwen3-vl:4b",
            next_action="fix_fatal_error",
        )

        snapshot = support_snapshot(result)

        self.assertEqual(snapshot, {
            "format_version": 1,
            "exit_code": 2,
            "error_codes": ["OLLAMA_MODEL_MISSING"],
            "warning_codes": [],
            "safe_instruction": "ollama pull qwen3-vl:4b",
            "next_action": "fix_fatal_error",
        })

    def test_support_snapshot_fails_closed_for_success_with_error_codes(self) -> None:
        result = WorkflowResult(
            exit_code=0,
            error_codes=("ANALYSIS_FAILED",),
            warning_codes=("PHOTOS_ACCESS_LIMITED",),
            next_action="rollback_available",
        )

        snapshot = support_snapshot(result)

        self.assertEqual(snapshot["exit_code"], 2)
        self.assertEqual(snapshot["error_codes"], ["UNSAFE_WORKFLOW_RESULT"])
        self.assertEqual(snapshot["warning_codes"], ["PHOTOS_ACCESS_LIMITED"])
        self.assertEqual(snapshot["next_action"], "fix_fatal_error")

    def test_support_snapshot_fails_closed_for_success_with_safe_instruction(self) -> None:
        result = WorkflowResult(
            exit_code=0,
            safe_instruction="ollama pull qwen3-vl:4b",
            next_action="none",
        )

        snapshot = support_snapshot(result)

        self.assertEqual(snapshot["exit_code"], 2)
        self.assertEqual(snapshot["error_codes"], ["UNSAFE_WORKFLOW_RESULT"])
        self.assertNotIn("safe_instruction", snapshot)
        self.assertEqual(snapshot["next_action"], "fix_fatal_error")

    def test_support_snapshot_fails_closed_for_success_with_status_errors(self) -> None:
        result = WorkflowResult(
            exit_code=0,
            status_summary={"errors_by_code": {"ANALYSIS_FAILED": 1}},
            next_action="none",
        )

        snapshot = support_snapshot(result)

        self.assertEqual(snapshot["exit_code"], 2)
        self.assertEqual(snapshot["error_codes"], ["UNSAFE_WORKFLOW_RESULT"])
        self.assertEqual(snapshot["next_action"], "fix_fatal_error")

    def test_support_snapshot_fails_closed_for_success_with_failed_stage_counts(self) -> None:
        result = WorkflowResult(
            exit_code=0,
            counts={"apply": {"failed": 1}},
            next_action="none",
        )

        snapshot = support_snapshot(result)

        self.assertEqual(snapshot["exit_code"], 2)
        self.assertEqual(snapshot["error_codes"], ["UNSAFE_WORKFLOW_RESULT"])
        self.assertEqual(snapshot["next_action"], "fix_fatal_error")

    def test_support_snapshot_fails_closed_for_success_with_failure_recovery_action(self) -> None:
        result = WorkflowResult(exit_code=0, next_action="retry_failed_operation")

        snapshot = support_snapshot(result)

        self.assertEqual(snapshot["exit_code"], 2)
        self.assertEqual(snapshot["error_codes"], ["UNSAFE_WORKFLOW_RESULT"])
        self.assertEqual(snapshot["next_action"], "fix_fatal_error")

    def test_support_snapshot_ignores_malformed_optional_result_fields(self) -> None:
        result = WorkflowResult(
            exit_code=1,
            counts=None,  # type: ignore[arg-type]
            safe_instruction=42,  # type: ignore[arg-type]
            status_summary=None,  # type: ignore[arg-type]
        )

        snapshot = support_snapshot(result)

        self.assertEqual(snapshot, {
            "format_version": 1,
            "exit_code": 2,
            "error_codes": ["UNSAFE_WORKFLOW_RESULT"],
            "warning_codes": [],
            "next_action": "fix_fatal_error",
        })

    def test_support_snapshot_includes_safe_adaptive_model_details(self) -> None:
        base = _manifest()
        adaptive = ScanManifest(
            run_id=base.run_id,
            created_at=base.created_at,
            app=dict(base.app),
            model={
                "policy": "adaptive",
                "fast_name": "qwen3-vl:4b",
                "detailed_name": "qwen3-vl:8b",
                "ollama_version": "0.32.1",
                "endpoint": "http://127.0.0.1:11434",
            },
            selection=dict(base.selection),
            photos=[PhotoRecord(
                uuid=base.photos[0].uuid,
                photos_local_identifier=base.photos[0].photos_local_identifier,
                title=base.photos[0].title,
                date=base.photos[0].date,
                existing_keywords=list(base.photos[0].existing_keywords),
                proposed_keywords=list(base.photos[0].proposed_keywords),
                contains_people=base.photos[0].contains_people,
                contains_text=base.photos[0].contains_text,
                confidence=base.photos[0].confidence,
                model_used="qwen3-vl:4b",
                model_reason="single_policy",
            )],
            summary=dict(base.summary),
            policy=dict(base.policy),
            schema_version=2,
        )

        snapshot = support_snapshot(WorkflowResult(exit_code=0, manifest=adaptive))

        self.assertEqual(snapshot["model"], {
            "policy": "adaptive",
            "fast_name": "qwen3-vl:4b",
            "detailed_name": "qwen3-vl:8b",
            "ollama_version": "0.32.1",
            "endpoint": "http://127.0.0.1:11434",
        })

    def test_scan_service_converts_a_photos_access_exception_to_a_safe_completion(self) -> None:
        from photos_indexer.adapters import PhotosAccessError

        events: list[dict[str, object]] = []

        def denied_runner(root: Path, **kwargs: object) -> WorkflowResult:
            raise PhotosAccessError()

        result = scan(
            ScanRequest(runs_root=Path("runs"), limit=1),
            events.append,
            scan_runner=denied_runner,
        )

        self.assertEqual(result.exit_code, 2)
        self.assertEqual(result.error_codes, ("PHOTOS_ACCESS_DENIED",))
        self.assertEqual(result.next_action, "grant_photos_access")
        self.assertEqual(events, [
            {"type": "started", "operation": "scan"},
            {
                "type": "completed",
                "exit_code": 2,
                "error_codes": ["PHOTOS_ACCESS_DENIED"],
                "warning_codes": [],
                "next_action": "grant_photos_access",
            },
        ])

    def test_failed_scan_completion_preserves_a_valid_pull_instruction(self) -> None:
        events: list[dict[str, object]] = []

        def missing_model_runner(root: Path, **kwargs: object) -> WorkflowResult:
            del root, kwargs
            return WorkflowResult(
                exit_code=2,
                error_codes=("OLLAMA_MODEL_MISSING",),
                safe_instruction="ollama pull qwen3-vl:8b",
                next_action="retry_preflight",
            )

        scan(ScanRequest(runs_root=Path("runs"), limit=1), events.append, scan_runner=missing_model_runner)

        self.assertEqual(events[-1], {
            "type": "completed",
            "exit_code": 2,
            "warning_codes": [],
            "error_codes": ["OLLAMA_MODEL_MISSING"],
            "safe_instruction": "ollama pull qwen3-vl:8b",
            "next_action": "retry_preflight",
        })

    def test_scan_rejects_a_non_path_runs_root_without_raw_exception(self) -> None:
        events: list[dict[str, object]] = []

        def runner(root: Path, **kwargs: object) -> WorkflowResult:
            raise AssertionError("invalid runs root must not reach the workflow")

        result = scan(
            ScanRequest(runs_root=123),  # type: ignore[arg-type]
            events.append,
            scan_runner=runner,
        )

        self.assertEqual(result.exit_code, 2)
        self.assertEqual(result.error_codes, ("RUNS_ROOT_INVALID",))
        self.assertEqual(result.next_action, "fix_fatal_error")
        self.assertEqual(events[-1]["type"], "completed")

    def test_mutation_services_reject_non_path_manifest_without_raw_exception(self) -> None:
        for operation in (apply_manifest, rollback_manifest):
            with self.subTest(operation=operation.__name__):
                events: list[dict[str, object]] = []
                result = operation(123, events.append)  # type: ignore[arg-type]
                self.assertEqual(result.exit_code, 2)
                self.assertEqual(result.error_codes, ("MANIFEST_PATH_INVALID",))
                self.assertEqual(result.next_action, "fix_fatal_error")
                self.assertEqual(events[-1]["type"], "completed")

    def test_scan_service_maps_automation_exception_without_exposing_raw_message(self) -> None:
        from photos_indexer.adapters import PhotoScriptPermissionError

        events: list[dict[str, object]] = []

        def denied_runner(root: Path, **kwargs: object) -> WorkflowResult:
            raise PhotoScriptPermissionError()

        result = scan(
            ScanRequest(runs_root=Path("runs"), limit=1),
            events.append,
            scan_runner=denied_runner,
        )

        self.assertEqual(result.error_codes, ("PHOTOS_AUTOMATION_DENIED",))
        self.assertEqual(result.next_action, "grant_photos_automation")
        self.assertNotIn("-1743", repr(events))
        self.assertNotIn("PhotoScript", repr(events))

    def test_scan_service_maps_photoscript_compile_exception_without_raw_message(self) -> None:
        from photos_indexer.adapters import PhotoScriptUnavailableError

        events: list[dict[str, object]] = []
        raw_message = 'Expected "," but found class name. (-2741), /private/path'

        def broken_runner(root: Path, **kwargs: object) -> WorkflowResult:
            del root, kwargs
            raise PhotoScriptUnavailableError()

        result = scan(
            ScanRequest(runs_root=Path("runs"), limit=1),
            events.append,
            scan_runner=broken_runner,
        )

        self.assertEqual(result.error_codes, ("PHOTOSCRIPT_UNAVAILABLE",))
        self.assertEqual(result.next_action, "fix_fatal_error")
        self.assertNotIn(raw_message, repr(events))
        self.assertNotIn("-2741", repr(events))

    def test_scan_runner_exception_is_projected_as_safe_terminal_result(self) -> None:
        """An unexpected scan failure must not escape without a terminal event."""
        events: list[dict[str, object]] = []

        def failing_runner(*args: object, **kwargs: object) -> WorkflowResult:
            del args, kwargs
            raise RuntimeError("private export path must not escape")

        try:
            result = scan(
                ScanRequest(runs_root=Path("runs"), limit=1),
                events.append,
                scan_runner=failing_runner,
            )
        except Exception as error:  # pragma: no cover - regression contract
            self.fail(f"unexpected scan failure escaped the service boundary: {type(error).__name__}")

        self.assertEqual(result.exit_code, 2)
        self.assertEqual(result.error_codes, ("WORKER_OPERATION_FAILED",))
        self.assertEqual(result.next_action, "fix_fatal_error")
        self.assertEqual([event["type"] for event in events], ["started", "completed"])
        self.assertNotIn("private export path", repr(events))

    def test_scan_rejects_a_manifest_result_outside_the_requested_runs_root(self) -> None:
        """A runner result must not hand an arbitrary path to the native UI."""
        events: list[dict[str, object]] = []

        def drifting_runner(root: Path, **kwargs: object) -> WorkflowResult:
            return WorkflowResult(
                exit_code=0,
                manifest_path=Path("/tmp/outside/manifest.json"),
                manifest=_manifest(),
            )

        result = scan(
            ScanRequest(runs_root=Path("runs"), limit=1),
            events.append,
            scan_runner=drifting_runner,
        )

        self.assertEqual(result.exit_code, 2)
        self.assertEqual(result.error_codes, ("MANIFEST_PATH_INVALID",))
        self.assertEqual(result.next_action, "fix_fatal_error")
        completed = events[-1]
        self.assertEqual(completed["type"], "completed")
        self.assertNotIn("manifest", completed)

    def test_review_removes_partial_run_when_preview_persistence_fails(self) -> None:
        """A failed review must not leave an apply-looking partial run."""
        source = _manifest()
        with tempfile.TemporaryDirectory() as tmp:
            runs_root = Path(tmp) / "runs"
            source_path = write_manifest(runs_root / "source", source)
            runs_root.chmod(0o700)

            with mock.patch(
                "photos_indexer.service.write_preview_csv",
                side_effect=OSError("simulated preview failure"),
            ), self.assertRaises(OSError):
                review_manifest(source_path, {source.photos[0].uuid: ["playa"]})

            self.assertEqual(
                [directory.name for directory in runs_root.iterdir()],
                ["source"],
            )

    def test_apply_rejects_a_reviewed_manifest_when_its_source_run_is_missing(self) -> None:
        """A reviewed copy must remain anchored to the successful dry-run."""
        source = _manifest()
        with tempfile.TemporaryDirectory() as tmp:
            runs_root = Path(tmp) / "runs"
            source_path = write_manifest(runs_root / "source", source)
            source_path.parent.parent.chmod(0o700)
            reviewed_path = review_manifest(source_path, {source.photos[0].uuid: ["playa"]})
            shutil.rmtree(source_path.parent)

            result = run_apply(
                reviewed_path,
                dependencies=ApplyDependencies(
                    selector_factory=lambda: (_ for _ in ()).throw(AssertionError("mutation must not start")),
                    bridge_factory=lambda: (_ for _ in ()).throw(AssertionError("mutation must not start")),
                    global_lock_path=runs_root / "global" / "mutation.lock",
                ),
            )

            self.assertEqual(result.exit_code, 2)
            self.assertEqual(result.error_codes, ("REVIEW_PROVENANCE_INVALID",))

    def test_review_preserves_unrouted_model_fields_for_failed_rows(self) -> None:
        from photos_indexer.workflows import _review_provenance_is_valid

        good = PhotoRecord(
            uuid="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            photos_local_identifier="good-local-id",
            title="Good",
            date=datetime(2026, 8, 25, 10, 0),
            existing_keywords=[],
            proposed_keywords=["playa"],
            contains_people=False,
            contains_text=False,
            confidence=0.9,
            model_used="qwen3-vl:4b",
            model_reason="single_policy",
        )
        failed = PhotoRecord(
            uuid="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            photos_local_identifier="failed-local-id",
            title="Failed",
            date=datetime(2026, 8, 25, 10, 0),
            existing_keywords=[],
            proposed_keywords=[],
            contains_people=None,
            contains_text=None,
            confidence=None,
            scan_state="analysis_failed",
            errors=[{"stage": "analysis", "code": "ANALYSIS_FAILED"}],
        )
        source = build_scan_manifest(
            run_id="cccccccc-cccc-4ccc-8ccc-cccccccccccc",
            app_name="photos-local-keyword-indexer",
            app_version="0.1.0",
            model_name="qwen3-vl:4b",
            ollama_version="0.12.7",
            endpoint="http://127.0.0.1:11434",
            selection={"requested": 2, "eligible": 2, "screenshots_excluded": 0, "access": "authorized"},
            photos=[good, failed],
            summary={"ready": 1, "noop": 0, "analysis_failed": 1},
            scan_status="ready_with_errors",
            model_policy="single",
            fast_model="qwen3-vl:4b",
            detailed_model="qwen3-vl:8b",
            ollama_versions={"qwen3-vl:4b": "0.12.7"},
            confidence_threshold=0.60,
        )
        with tempfile.TemporaryDirectory() as tmp:
            source_path = write_manifest(Path(tmp) / "runs" / "scan", source)
            source_path.parent.parent.chmod(0o700)

            reviewed_path = review_manifest(source_path, {good.uuid: ["playa"]})
            reviewed = load_manifest(reviewed_path.parent)

            failed_review = next(photo for photo in reviewed.photos if photo.uuid == failed.uuid)
            self.assertIsNone(failed_review.model_used)
            self.assertIsNone(failed_review.model_reason)
            self.assertTrue(_review_provenance_is_valid(reviewed_path, reviewed))

    def test_legacy_review_does_not_claim_a_model_for_a_failed_row(self) -> None:
        """Schema-1 migration must not invent successful inference metadata."""
        from photos_indexer.workflows import _review_provenance_is_valid

        good = _manifest().photos[0]
        failed = PhotoRecord(
            uuid="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            photos_local_identifier="failed-local-id",
            title="Failed",
            date=datetime(2026, 8, 25, 10, 0),
            existing_keywords=[],
            proposed_keywords=[],
            contains_people=None,
            contains_text=None,
            confidence=None,
            scan_state="analysis_failed",
            errors=[{"stage": "analysis", "code": "ANALYSIS_FAILED"}],
        )
        source = build_scan_manifest(
            run_id="cccccccc-cccc-4ccc-8ccc-cccccccccccc",
            app_name="photos-local-keyword-indexer",
            app_version="0.1.0",
            model_name="qwen3-vl:4b",
            ollama_version="0.12.7",
            endpoint="http://127.0.0.1:11434",
            selection={"requested": 2, "eligible": 2, "screenshots_excluded": 0, "access": "authorized"},
            photos=[good, failed],
            summary={"ready": 1, "noop": 0, "analysis_failed": 1},
            scan_status="ready_with_errors",
        )
        with tempfile.TemporaryDirectory() as tmp:
            source_path = write_manifest(Path(tmp) / "runs" / "scan", source)
            source_path.parent.parent.chmod(0o700)

            reviewed_path = review_manifest(source_path, {good.uuid: ["playa"]})
            reviewed = load_manifest(reviewed_path.parent)
            failed_review = next(photo for photo in reviewed.photos if photo.uuid == failed.uuid)

            self.assertIsNone(failed_review.model_used)
            self.assertIsNone(failed_review.model_reason)
            self.assertTrue(_review_provenance_is_valid(reviewed_path, reviewed))

    def test_validate_app_runs_root_creates_only_the_expected_private_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            expected = app_runs_root(home)

            validated = validate_app_runs_root(expected, home=home)

            self.assertEqual(validated, expected)
            self.assertTrue(validated.is_dir())
            self.assertEqual(validated.stat().st_mode & 0o777, 0o700)

    def test_validate_app_runs_root_can_validate_without_creating_first_run_storage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            expected = app_runs_root(home)

            validated = validate_app_runs_root(expected, home=home, create=False)

            self.assertEqual(validated, expected)
            self.assertFalse(expected.exists())

    def test_scan_preflight_failure_does_not_create_application_support_storage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            expected = app_runs_root(home)
            events: list[dict[str, object]] = []

            def missing_model_runner(root: Path, **kwargs: object) -> WorkflowResult:
                self.assertEqual(root, expected)
                return WorkflowResult(
                    exit_code=2,
                    error_codes=("OLLAMA_MODEL_MISSING",),
                    safe_instruction="ollama pull qwen3-vl:4b",
                    next_action="fix_fatal_error",
                )

            with mock.patch("photos_indexer.service.Path.home", return_value=home):
                result = scan(
                    ScanRequest(runs_root=expected),
                    events.append,
                    scan_runner=missing_model_runner,
                )

            self.assertEqual(result.error_codes, ("OLLAMA_MODEL_MISSING",))
            self.assertFalse(expected.exists())

    def test_validate_app_runs_root_rejects_relative_outside_and_symlink_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            expected = app_runs_root(home)
            outside = Path(tmp) / "outside"
            outside.mkdir()
            expected.parent.mkdir(parents=True)
            expected.symlink_to(outside, target_is_directory=True)

            with self.assertRaises(ValueError):
                validate_app_runs_root(Path("relative-runs"), home=home)
            with self.assertRaises(ValueError):
                validate_app_runs_root(outside, home=home)
            with self.assertRaises(ValueError):
                validate_app_runs_root(expected, home=home)

    def test_validate_app_manifest_path_requires_private_direct_run_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            run_dir = app_runs_root(home) / "run-1"
            run_dir.mkdir(parents=True, mode=0o700)
            manifest_path = run_dir / "manifest.json"
            manifest_path.write_text("{}", encoding="utf-8")
            manifest_path.chmod(0o600)

            self.assertEqual(validate_app_manifest_path(manifest_path, home=home), manifest_path)
            with self.assertRaises(ValueError):
                validate_app_manifest_path(run_dir / "nested" / "manifest.json", home=home)
            with self.assertRaises(ValueError):
                validate_app_manifest_path(app_runs_root(home) / "manifest.json", home=home)
            with self.assertRaises(ValueError):
                validate_app_manifest_path(Path(tmp) / "manifest.json", home=home)

            manifest_path.chmod(0o644)
            with self.assertRaises(ValueError):
                validate_app_manifest_path(manifest_path, home=home)

    def test_validate_app_manifest_path_rejects_a_non_private_run_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            run_dir = app_runs_root(home) / "run-1"
            run_dir.mkdir(parents=True, mode=0o700)
            manifest_path = run_dir / "manifest.json"
            manifest_path.write_text("{}", encoding="utf-8")
            manifest_path.chmod(0o600)
            run_dir.chmod(0o755)

            with self.assertRaises(ValueError):
                validate_app_manifest_path(manifest_path, home=home)

    def test_validate_app_manifest_path_rejects_an_oversized_manifest_before_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            run_dir = app_runs_root(home) / "run-1"
            run_dir.mkdir(parents=True, mode=0o700)
            manifest_path = run_dir / "manifest.json"
            manifest_path.write_bytes(b"0" * (MAX_MANIFEST_BYTES + 1))
            manifest_path.chmod(0o600)

            with self.assertRaises(ValueError):
                validate_app_manifest_path(manifest_path, home=home)

    def test_scan_emits_only_sanitized_progress_and_never_mutates(self) -> None:
        events: list[dict[str, object]] = []
        replace_calls: list[object] = []
        manifest = _manifest()

        def runner(root: Path, **kwargs: object) -> WorkflowResult:
            self.assertEqual(root, Path("runs"))
            self.assertEqual(kwargs["model"], "qwen3-vl:4b")
            return WorkflowResult(exit_code=0, manifest_path=Path("runs/test/manifest.json"), manifest=manifest)

        result = scan(ScanRequest(runs_root=Path("runs")), events.append, scan_runner=runner)

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(replace_calls, [])
        self.assertEqual(events[0]["type"], "started")
        progress = next(event for event in events if event["type"] == "photo_progress")
        self.assertEqual(progress["uuid"], "aaaaaaaa")
        self.assertEqual(progress["state"], "ready")
        self.assertEqual(progress["model_reason"], "single_policy")
        self.assertEqual(progress["keywords_count"], 1)
        rendered = repr(events)
        self.assertNotIn("private-local-id", rendered)
        self.assertNotIn("Private title", rendered)
        self.assertNotIn("Viaje", rendered)
        self.assertNotIn("playa", rendered)

    def test_progress_reports_effective_keyword_changes_after_mutation(self) -> None:
        """A no-op must not look like a write in the app progress contract."""
        cases = (
            ("noop", 0, {"apply_state": "noop"}),
            ("verified", 1, {"apply_state": "verified", "applied_keywords": ["playa"]}),
            ("uncertain", 1, {
                "apply_state": "verified", "applied_keywords": ["playa"],
                "rollback_state": "uncertain", "rolled_back_keywords": ["playa"],
            }),
        )
        for state, expected_count, updates in cases:
            with self.subTest(state=state):
                manifest = _manifest()
                photo = manifest.photos[0]
                for field, value in updates.items():
                    setattr(photo, field, value)
                events: list[dict[str, object]] = []

                def runner(root: Path, **kwargs: object) -> WorkflowResult:
                    return WorkflowResult(exit_code=0, manifest=manifest)

                scan(ScanRequest(runs_root=Path("runs")), events.append, scan_runner=runner)
                progress = next(event for event in events if event["type"] == "photo_progress")
                self.assertEqual(progress["keywords_count"], expected_count)

    def test_scan_forwards_explicit_caption_opt_in_to_the_workflow(self) -> None:
        captured: dict[str, object] = {}

        def runner(root: Path, **kwargs: object) -> WorkflowResult:
            captured.update(kwargs)
            return WorkflowResult(exit_code=0, manifest=_manifest())

        result = scan(
            ScanRequest(runs_root=Path("runs"), include_caption=True),
            lambda event: None,
            scan_runner=runner,
        )

        self.assertEqual(result.exit_code, 0)
        self.assertTrue(captured["include_caption"])

    def test_scan_emits_photo_progress_as_each_photo_finishes(self) -> None:
        events: list[dict[str, object]] = []

        def runner(root: Path, **kwargs: object) -> WorkflowResult:
            callback = kwargs["progress_callback"]
            self.assertTrue(callable(callback))
            callback(_manifest().photos[0])
            return WorkflowResult(exit_code=0, manifest=_manifest())

        result = scan(
            ScanRequest(runs_root=Path("runs")),
            events.append,
            scan_runner=runner,
        )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual([event["type"] for event in events], ["started", "photo_progress", "completed"])
        progress = [event["uuid"] for event in events if event["type"] == "photo_progress"]
        self.assertEqual(progress, ["aaaaaaaa"])

    def test_scan_does_not_emit_an_unidentifiable_photo_progress_event(self) -> None:
        """A failed Photos lookup must not crash SwiftUI's strict IPC decoder."""
        failed_photo = PhotoRecord(
            uuid=None,
            photos_local_identifier="opaque-local-id",
            title="",
            date=datetime(2026, 8, 25, 10, 0),
            existing_keywords=[],
            proposed_keywords=[],
            contains_people=None,
            contains_text=None,
            confidence=None,
            scan_state="analysis_failed",
            errors=[{"stage": "metadata", "code": "READ_FAILED"}],
        )
        manifest = _manifest()
        manifest.photos = [failed_photo]
        manifest.summary = {"ready": 0, "noop": 0, "analysis_failed": 1}
        events: list[dict[str, object]] = []

        def runner(root: Path, **kwargs: object) -> WorkflowResult:
            callback = kwargs["progress_callback"]
            assert callable(callback)
            callback(failed_photo)
            return WorkflowResult(exit_code=1, manifest=manifest, error_codes=("PHOTO_READ_FAILED",))

        result = scan(ScanRequest(runs_root=Path("runs")), events.append, scan_runner=runner)

        self.assertEqual(result.exit_code, 1)
        self.assertEqual([event["type"] for event in events], ["started", "completed"])
        self.assertNotIn("uuid", repr(events))

    def test_scan_does_not_claim_a_model_for_an_analysis_failed_photo(self) -> None:
        manifest = _manifest()
        photo = manifest.photos[0]
        photo.scan_state = "analysis_failed"
        photo.proposed_keywords = []
        photo.errors = [{"stage": "analysis", "code": "ANALYSIS_FAILED"}]
        manifest.summary = {"ready": 0, "noop": 0, "analysis_failed": 1}
        events: list[dict[str, object]] = []

        result = scan(
            ScanRequest(runs_root=Path("runs")),
            events.append,
            scan_runner=lambda root, **kwargs: WorkflowResult(exit_code=1, manifest=manifest),
        )

        self.assertEqual(result.exit_code, 1)
        progress = next(event for event in events if event["type"] == "photo_progress")
        self.assertEqual(progress["state"], "analysis_failed")
        self.assertNotIn("model_used", progress)
        self.assertNotIn("model_reason", progress)

    def test_scan_rejects_success_with_status_failure_before_terminal_event(self) -> None:
        events: list[dict[str, object]] = []

        result = scan(
            ScanRequest(runs_root=Path("runs")),
            events.append,
            scan_runner=lambda root, **kwargs: WorkflowResult(
                exit_code=0,
                status_summary={"errors_by_code": {"ANALYSIS_FAILED": 1}},
            ),
        )

        self.assertEqual(result.exit_code, 2)
        self.assertEqual(result.error_codes, ("UNSAFE_WORKFLOW_RESULT",))
        self.assertEqual(events[-1], {
            "type": "completed",
            "exit_code": 2,
            "error_codes": ["UNSAFE_WORKFLOW_RESULT"],
            "next_action": "fix_fatal_error",
        })

    def test_scan_drops_an_unknown_model_reason_from_service_events(self) -> None:
        manifest = _manifest()
        manifest.photos[0].model_reason = "private_reason"
        events: list[dict[str, object]] = []

        result = scan(
            ScanRequest(runs_root=Path("runs")),
            events.append,
            scan_runner=lambda root, **kwargs: WorkflowResult(exit_code=0, manifest=manifest),
        )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual([event["type"] for event in events], ["started", "completed"])

    def test_scan_drops_untrusted_runner_metadata_from_service_events(self) -> None:
        """A malformed injected result must not leak paths through callbacks."""
        manifest = _manifest()
        manifest.photos[0].model_used = "/private/secret"
        events: list[dict[str, object]] = []

        result = scan(
            ScanRequest(runs_root=Path("runs")),
            events.append,
            scan_runner=lambda root, **kwargs: WorkflowResult(
                exit_code=1,
                manifest=manifest,
                error_codes=("/private/raw-error",),
                next_action="fix_fatal_error",
            ),
        )

        self.assertEqual(result.exit_code, 1)
        serialized = repr(events)
        self.assertNotIn("/private/secret", serialized)
        self.assertNotIn("/private/raw-error", serialized)
        self.assertEqual(events[-1], {
            "type": "completed",
            "exit_code": 2,
            "error_codes": ["UNSAFE_WORKFLOW_RESULT"],
            "next_action": "fix_fatal_error",
        })

    def test_scan_malformed_runner_manifest_emits_a_safe_terminal_result(self) -> None:
        """A malformed runner manifest must not crash before completion."""
        manifest = _manifest()
        manifest.model = []  # type: ignore[assignment]
        events: list[dict[str, object]] = []

        result = scan(
            ScanRequest(runs_root=Path("runs")),
            events.append,
            scan_runner=lambda root, **kwargs: WorkflowResult(exit_code=0, manifest=manifest),
        )

        self.assertEqual(result.exit_code, 2)
        self.assertEqual(result.error_codes, ("UNSAFE_WORKFLOW_RESULT",))
        self.assertEqual(events[-1], {
            "type": "completed",
            "exit_code": 2,
            "warning_codes": [],
            "error_codes": ["UNSAFE_WORKFLOW_RESULT"],
            "next_action": "fix_fatal_error",
        })

    def test_apply_malformed_runner_manifest_emits_a_safe_terminal_result(self) -> None:
        manifest = _manifest()
        manifest.model = []  # type: ignore[assignment]
        events: list[dict[str, object]] = []
        valid_path = Path("/safe/runs/reviewed/manifest.json")

        with mock.patch("photos_indexer.service.validate_app_manifest_path", return_value=valid_path):
            result = apply_manifest(
                valid_path,
                events.append,
                apply_runner=lambda path, **kwargs: WorkflowResult(exit_code=0, manifest=manifest),
            )

        self.assertEqual(result.exit_code, 2)
        self.assertEqual(result.error_codes, ("UNSAFE_WORKFLOW_RESULT",))
        self.assertEqual(events[-1], {
            "type": "completed",
            "exit_code": 2,
            "warning_codes": [],
            "error_codes": ["UNSAFE_WORKFLOW_RESULT"],
            "next_action": "fix_fatal_error",
        })

    def test_scan_malformed_runner_photo_rows_emit_a_safe_terminal_result(self) -> None:
        manifest = _manifest()
        manifest.photos = [object()]  # type: ignore[list-item]
        events: list[dict[str, object]] = []

        result = scan(
            ScanRequest(runs_root=Path("runs")),
            events.append,
            scan_runner=lambda root, **kwargs: WorkflowResult(exit_code=0, manifest=manifest),
        )

        self.assertEqual(result.exit_code, 2)
        self.assertEqual(result.error_codes, ("UNSAFE_WORKFLOW_RESULT",))
        self.assertEqual(events[-1], {
            "type": "completed",
            "exit_code": 2,
            "warning_codes": [],
            "error_codes": ["UNSAFE_WORKFLOW_RESULT"],
            "next_action": "fix_fatal_error",
        })

    def test_scan_malformed_runner_photo_fields_emit_a_safe_terminal_result(self) -> None:
        """A mutable PhotoRecord must not crash progress projection."""
        manifest = _manifest()
        manifest.photos[0].proposed_keywords = None  # type: ignore[assignment]
        events: list[dict[str, object]] = []

        try:
            result = scan(
                ScanRequest(runs_root=Path("runs")),
                events.append,
                scan_runner=lambda root, **kwargs: WorkflowResult(exit_code=0, manifest=manifest),
            )
        except Exception as error:  # pragma: no cover - the assertion is the regression contract
            self.fail(f"malformed runner photo escaped the service boundary: {type(error).__name__}")

        self.assertEqual(result.exit_code, 2)
        self.assertEqual(result.error_codes, ("UNSAFE_WORKFLOW_RESULT",))
        self.assertEqual(events[-1], {
            "type": "completed",
            "exit_code": 2,
            "warning_codes": [],
            "error_codes": ["UNSAFE_WORKFLOW_RESULT"],
            "next_action": "fix_fatal_error",
        })

    def test_scan_converts_malformed_result_code_fields_to_safe_terminal_event(self) -> None:
        events: list[dict[str, object]] = []
        result = WorkflowResult(exit_code=1, next_action="fix_fatal_error")
        object.__setattr__(result, "warning_codes", None)

        returned = scan(
            ScanRequest(Path("runs")),
            events.append,
            scan_runner=lambda root, **kwargs: result,
        )

        self.assertEqual(returned.exit_code, 1)
        self.assertEqual(events[-1], {
            "type": "completed",
            "exit_code": 2,
            "error_codes": ["UNSAFE_WORKFLOW_RESULT"],
            "next_action": "fix_fatal_error",
        })

    def test_scan_converts_an_unhashable_exit_code_to_a_safe_terminal_event(self) -> None:
        events: list[dict[str, object]] = []
        result = WorkflowResult(exit_code=1, next_action="fix_fatal_error")
        object.__setattr__(result, "exit_code", [])

        returned = scan(
            ScanRequest(Path("runs")),
            events.append,
            scan_runner=lambda root, **kwargs: result,
        )

        # The service preserves the runner object for direct callers; its
        # callback projection is the fail-closed contract.
        self.assertIs(returned, result)
        self.assertEqual(events[-1], {
            "type": "completed",
            "exit_code": 2,
            "error_codes": ["UNSAFE_WORKFLOW_RESULT"],
            "next_action": "fix_fatal_error",
        })

    def test_scan_rejects_a_manifest_path_with_control_characters(self) -> None:
        events: list[dict[str, object]] = []
        unsafe_path = Path("runs/run\n/manifest.json")

        result = scan(
            ScanRequest(runs_root=Path("runs")),
            events.append,
            scan_runner=lambda root, **kwargs: WorkflowResult(
                exit_code=0, manifest_path=unsafe_path,
            ),
        )

        self.assertEqual(result.exit_code, 2)
        self.assertEqual(result.error_codes, ("MANIFEST_PATH_INVALID",))
        self.assertNotIn("manifest", events[-1])

    def test_scan_rejects_an_unsafe_absolute_root_before_runner(self) -> None:
        called = False
        events: list[dict[str, object]] = []

        def runner(*args: object, **kwargs: object) -> WorkflowResult:
            nonlocal called
            called = True
            return WorkflowResult(exit_code=0)

        with tempfile.TemporaryDirectory() as tmp:
            result = scan(
                ScanRequest(runs_root=Path(tmp) / "outside"),
                events.append,
                scan_runner=runner,
            )

        self.assertFalse(called)
        self.assertEqual(result.error_codes, ("RUNS_ROOT_INVALID",))
        self.assertEqual([event["type"] for event in events], ["completed"])

    def test_scan_passes_a_valid_application_support_descendant_to_runner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            valid_root = app_runs_root(home) / "nested"
            called: list[Path] = []

            def runner(root: Path, **kwargs: object) -> WorkflowResult:
                called.append(root)
                return WorkflowResult(exit_code=0)

            # Patch the process home only for this injected service call; the
            # runner must still receive the normalized validated path.
            with mock.patch("photos_indexer.service.Path.home", return_value=home):
                result = scan(ScanRequest(runs_root=valid_root), lambda event: None, scan_runner=runner)
            self.assertEqual(result.exit_code, 0)
            self.assertEqual(called, [valid_root])
            self.assertFalse(valid_root.exists())

    def test_pre_cancelled_scan_does_not_start_the_workflow(self) -> None:
        token = CancellationToken()
        token.cancel()
        called = False

        def runner(*args: object, **kwargs: object) -> WorkflowResult:
            nonlocal called
            called = True
            return WorkflowResult(exit_code=0)

        result = scan(ScanRequest(runs_root=Path("runs")), lambda event: None, cancellation=token, scan_runner=runner)

        self.assertFalse(called)
        self.assertEqual(result.error_codes, ("CANCELLED",))

    def test_pre_cancelled_apply_and_rollback_do_not_start_mutation_workflows(self) -> None:
        token = CancellationToken()
        token.cancel()
        events: list[dict[str, object]] = []
        called: list[str] = []

        def apply_runner(*args: object, **kwargs: object) -> WorkflowResult:
            called.append("apply")
            return WorkflowResult(exit_code=0)

        def rollback_runner(*args: object, **kwargs: object) -> WorkflowResult:
            called.append("rollback")
            return WorkflowResult(exit_code=0)

        apply_result = apply_manifest(
            Path("runs/reviewed/manifest.json"),
            events.append,
            cancellation=token,
            apply_runner=apply_runner,
        )
        rollback_result = rollback_manifest(
            Path("runs/reviewed/manifest.json"),
            events.append,
            cancellation=token,
            rollback_runner=rollback_runner,
        )

        self.assertEqual(called, [])
        self.assertEqual(apply_result.error_codes, ("CANCELLED",))
        self.assertEqual(rollback_result.error_codes, ("CANCELLED",))
        self.assertEqual([event["type"] for event in events], ["completed", "completed"])

    def test_apply_runner_exception_is_projected_as_safe_terminal_result(self) -> None:
        events: list[dict[str, object]] = []

        def failing_runner(*args: object, **kwargs: object) -> WorkflowResult:
            del args, kwargs
            raise RuntimeError("private path must not escape")

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            manifest_path = app_runs_root(home) / "run" / "manifest.json"
            manifest_path.parent.mkdir(parents=True, mode=0o700)
            manifest_path.write_bytes(b"not loaded by injected runner")
            manifest_path.chmod(0o600)
            with mock.patch("photos_indexer.service.Path.home", return_value=home):
                result = apply_manifest(manifest_path, events.append, apply_runner=failing_runner)

        self.assertEqual(result.exit_code, 2)
        self.assertEqual(result.error_codes, ("WORKER_OPERATION_FAILED",))
        self.assertEqual(result.next_action, "fix_fatal_error")
        self.assertEqual([event["type"] for event in events], ["started", "completed"])
        self.assertNotIn("private path", repr(events))

    def test_apply_and_rollback_reject_unsafe_absolute_manifests_before_runner(self) -> None:
        events: list[dict[str, object]] = []
        called: list[str] = []

        def apply_runner(*args: object, **kwargs: object) -> WorkflowResult:
            called.append("apply")
            return WorkflowResult(exit_code=0)

        def rollback_runner(*args: object, **kwargs: object) -> WorkflowResult:
            called.append("rollback")
            return WorkflowResult(exit_code=0)

        with tempfile.TemporaryDirectory() as tmp:
            unsafe_manifest = Path(tmp) / "manifest.json"
            apply_result = apply_manifest(
                unsafe_manifest,
                events.append,
                apply_runner=apply_runner,
            )
            rollback_result = rollback_manifest(
                unsafe_manifest,
                events.append,
                rollback_runner=rollback_runner,
            )

        self.assertEqual(called, [])
        self.assertEqual(apply_result.error_codes, ("MANIFEST_PATH_INVALID",))
        self.assertEqual(rollback_result.error_codes, ("MANIFEST_PATH_INVALID",))
        self.assertEqual([event["type"] for event in events], ["completed", "completed"])

    def test_apply_and_rollback_reject_relative_manifests_before_runner(self) -> None:
        events: list[dict[str, object]] = []
        called: list[str] = []

        def apply_runner(*args: object, **kwargs: object) -> WorkflowResult:
            called.append("apply")
            return WorkflowResult(exit_code=0)

        def rollback_runner(*args: object, **kwargs: object) -> WorkflowResult:
            called.append("rollback")
            return WorkflowResult(exit_code=0)

        apply_result = apply_manifest(
            Path("runs/reviewed/manifest.json"), events.append, apply_runner=apply_runner,
        )
        rollback_result = rollback_manifest(
            Path("runs/reviewed/manifest.json"), events.append, rollback_runner=rollback_runner,
        )

        self.assertEqual(called, [])
        self.assertEqual(apply_result.error_codes, ("MANIFEST_PATH_INVALID",))
        self.assertEqual(rollback_result.error_codes, ("MANIFEST_PATH_INVALID",))

    def test_mutation_services_reject_runner_manifest_path_drift(self) -> None:
        """Mutation events must keep the validated manifest identity."""
        valid_path = Path("/safe/runs/reviewed/manifest.json")
        events: list[dict[str, object]] = []
        called: list[str] = []

        def drifting_runner(path: Path, **kwargs: object) -> WorkflowResult:
            called.append(str(path))
            return WorkflowResult(
                exit_code=0,
                manifest_path=Path("/tmp/outside/manifest.json"),
            )

        with mock.patch("photos_indexer.service.validate_app_manifest_path", return_value=valid_path):
            applied = apply_manifest(valid_path, events.append, apply_runner=drifting_runner)
            rolled_back = rollback_manifest(valid_path, events.append, rollback_runner=drifting_runner)

        self.assertEqual(called, [str(valid_path), str(valid_path)])
        self.assertEqual(applied.error_codes, ("MANIFEST_PATH_INVALID",))
        self.assertEqual(rolled_back.error_codes, ("MANIFEST_PATH_INVALID",))
        self.assertEqual([event["type"] for event in events], ["started", "completed", "started", "completed"])
        self.assertNotIn("manifest", events[1])
        self.assertNotIn("manifest", events[3])

    def test_mutation_services_reject_malformed_runner_manifest_path(self) -> None:
        valid_path = Path("/safe/runs/reviewed/manifest.json")
        events: list[dict[str, object]] = []

        def malformed_runner(path: Path, **kwargs: object) -> WorkflowResult:
            del path, kwargs
            return WorkflowResult(exit_code=0, manifest_path=123)  # type: ignore[arg-type]

        with mock.patch("photos_indexer.service.validate_app_manifest_path", return_value=valid_path):
            applied = apply_manifest(valid_path, events.append, apply_runner=malformed_runner)
            rolled_back = rollback_manifest(valid_path, events.append, rollback_runner=malformed_runner)

        self.assertEqual(applied.exit_code, 2)
        self.assertEqual(rolled_back.exit_code, 2)
        self.assertEqual(applied.error_codes, ("MANIFEST_PATH_INVALID",))
        self.assertEqual(rolled_back.error_codes, ("MANIFEST_PATH_INVALID",))
        self.assertEqual([event["type"] for event in events], ["started", "completed", "started", "completed"])

    def test_review_creates_a_private_copy_and_apply_rejects_the_unreviewed_source(self) -> None:
        photo = PhotoRecord(
            uuid="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", photos_local_identifier="local-1", title="Private", date=datetime(2026, 8, 25),
            existing_keywords=["Viaje"], proposed_keywords=["playa", "mar"], contains_people=False, contains_text=False,
            confidence=0.9, model_used="qwen3-vl:4b", model_reason="single_policy",
        )
        manifest = build_scan_manifest(
            run_id=str(uuid.uuid4()), app_name="photos-local-keyword-indexer", app_version="0.1.0", model_name="qwen3-vl:4b",
            ollama_version="0.12.7", endpoint="http://127.0.0.1:11434",
            selection={"requested": 1, "eligible": 1, "screenshots_excluded": 0, "access": "authorized"}, photos=[photo],
            summary={"ready": 1, "noop": 0, "analysis_failed": 0}, confidence_threshold=0.6,
            model_policy="single", fast_model="qwen3-vl:4b", detailed_model="qwen3-vl:4b", ollama_versions={"qwen3-vl:4b": "0.12.7"},
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "runs"
            source_dir = root / "source"
            source_path = write_manifest(source_dir, manifest)
            root.chmod(0o700)
            source_bytes = source_path.read_bytes()

            reviewed_path = review_manifest(source_path, {photo.uuid: ["mar"]})
            reviewed = load_manifest(reviewed_path.parent)

            self.assertNotEqual(reviewed_path.parent, source_dir)
            self.assertEqual(source_path.read_bytes(), source_bytes)
            self.assertEqual(reviewed.schema_version, 3)
            self.assertEqual(reviewed.reviewed_from_run_id, manifest.run_id)
            self.assertEqual(reviewed.source_scan_digest, manifest.scan_digest)
            self.assertEqual(reviewed.photos[0].proposed_keywords, ["mar"])
            self.assertEqual(run_apply(source_path).error_codes, ("MANIFEST_NOT_REVIEWED",))

    def test_review_rejects_keywords_that_were_not_proposed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_dir = Path(tmp) / "runs" / "source"
            source_path = write_manifest(source_dir, _manifest())
            source_path.parent.parent.chmod(0o700)
            with self.assertRaises(ValueError):
                review_manifest(source_path, {"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa": ["inventada"]})

    def test_review_rejects_a_non_private_runs_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs_root = Path(tmp) / "runs"
            source_path = write_manifest(runs_root / "source", _manifest())
            runs_root.chmod(0o755)

            with self.assertRaises(ValueError):
                review_manifest(source_path, {})

    def test_review_keeps_only_explicitly_approved_caption_without_keywords(self) -> None:
        source = _manifest()
        source.photos[0].proposed_caption = "Una playa con olas suaves."
        source.photos[0].caption_state = "proposed"
        with tempfile.TemporaryDirectory() as tmp:
            source_path = write_manifest(Path(tmp) / "runs" / "source", source)
            source_path.parent.parent.chmod(0o700)

            reviewed_path = review_manifest(
                source_path,
                {},
                {"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa": True},
            )
            reviewed = load_manifest(reviewed_path.parent).photos[0]
            self.assertEqual(reviewed.proposed_keywords, [])
            self.assertEqual(reviewed.proposed_caption, "Una playa con olas suaves.")
            self.assertEqual(reviewed.caption_state, "proposed")

            rejected_path = review_manifest(
                source_path,
                {},
                {"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa": False},
            )
            rejected = load_manifest(rejected_path.parent).photos[0]
            self.assertIsNone(rejected.proposed_caption)
            self.assertEqual(rejected.caption_state, "not_requested")

            with self.assertRaises(ValueError):
                review_manifest(source_path, {}, {"not-a-uuid": True})

    def test_apply_rejects_a_reviewed_manifest_that_already_contains_mutation_state(self) -> None:
        """A reviewed copy must remain a pristine approval boundary."""
        source = _manifest()
        source.photos[0].proposed_caption = "Una playa visible."
        source.photos[0].caption_state = "proposed"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "runs"
            source_path = write_manifest(root / "source", source)
            root.chmod(0o700)
            reviewed_path = review_manifest(
                source_path,
                {source.photos[0].uuid: ["playa"]},
                {source.photos[0].uuid: True},
            )

            reviewed = load_manifest(reviewed_path.parent)
            reviewed.photos[0].apply_state = "verified"
            reviewed.photos[0].applied_keywords = ["playa"]
            reviewed.photos[0].applied_caption = "Una playa visible."
            reviewed.photos[0].caption_state = "verified"
            write_manifest(reviewed_path.parent, reviewed)

            result = run_apply(
                reviewed_path,
                dependencies=ApplyDependencies(
                    selector_factory=lambda: (_ for _ in ()).throw(AssertionError("mutation must not start")),
                    bridge_factory=lambda: (_ for _ in ()).throw(AssertionError("mutation must not start")),
                    global_lock_path=root / "global" / "mutation.lock",
                ),
            )

            self.assertEqual(result.exit_code, 2)
            self.assertEqual(result.error_codes, ("MUTATION_EVIDENCE_INVALID",))

    def test_review_allows_caption_only_for_a_noop_photo(self) -> None:
        source = _manifest()
        source.photos[0].scan_state = "noop"
        source.photos[0].proposed_keywords = []
        source.photos[0].proposed_caption = "Una playa con olas suaves."
        source.photos[0].caption_state = "proposed"
        source.summary = {"ready": 0, "noop": 1, "analysis_failed": 0}
        with tempfile.TemporaryDirectory() as tmp:
            source_path = write_manifest(Path(tmp) / "runs" / "source", source)
            source_path.parent.parent.chmod(0o700)
            reviewed_path = review_manifest(
                source_path,
                {},
                {"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa": True},
            )
            reviewed = load_manifest(reviewed_path.parent).photos[0]
            self.assertEqual(reviewed.scan_state, "noop")
            self.assertEqual(reviewed.proposed_keywords, [])
            self.assertEqual(reviewed.proposed_caption, "Una playa con olas suaves.")

            rejected_path = review_manifest(
                source_path,
                {},
                {"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa": False},
            )
            rejected = load_manifest(rejected_path.parent).photos[0]
            self.assertEqual(rejected.scan_state, "noop")
            self.assertEqual(rejected.proposed_keywords, [])
            self.assertIsNone(rejected.proposed_caption)
            self.assertEqual(rejected.caption_state, "not_requested")

    def test_review_rejects_a_tampered_source_digest(self) -> None:
        source = _manifest()
        with tempfile.TemporaryDirectory() as tmp:
            source_path = write_manifest(Path(tmp) / "runs" / "source", source)
            source_path.parent.parent.chmod(0o700)
            payload = json.loads(source_path.read_text(encoding="utf-8"))
            payload["scan_digest"] = "0" * 64
            source_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ValueError):
                review_manifest(source_path, {}, {})


if __name__ == "__main__":
    unittest.main()
