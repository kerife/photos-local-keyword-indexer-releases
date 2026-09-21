from __future__ import annotations

import unittest

from photos_indexer.service import support_snapshot
from tests.test_service_api import _manifest
from photos_indexer.workflows import WorkflowResult


class SupportSnapshotConsistencyTests(unittest.TestCase):
    def test_support_snapshot_fails_closed_for_success_with_partial_scan_status(self) -> None:
        """A partial scan status cannot be reported as a successful run."""
        snapshot = support_snapshot(WorkflowResult(
            exit_code=0,
            status_summary={"scan_status": "ready_with_errors"},
            next_action="none",
        ))

        self.assertEqual(snapshot["exit_code"], 2)
        self.assertEqual(snapshot["error_codes"], ["UNSAFE_WORKFLOW_RESULT"])
        self.assertEqual(snapshot["next_action"], "fix_fatal_error")

    def test_support_snapshot_fails_closed_for_success_with_scan_error_in_manifest(self) -> None:
        """A manifest row can carry scan failure evidence even without counts."""
        manifest = _manifest()
        photo = manifest.photos[0]
        photo.scan_state = "analysis_failed"
        photo.proposed_keywords = []
        manifest.scan_status = "ready_with_errors"
        manifest.summary = {"ready": 0, "noop": 0, "analysis_failed": 1}

        snapshot = support_snapshot(WorkflowResult(
            exit_code=0,
            manifest=manifest,
            next_action="none",
        ))

        self.assertEqual(snapshot["exit_code"], 2)
        self.assertEqual(snapshot["error_codes"], ["UNSAFE_WORKFLOW_RESULT"])
        self.assertEqual(snapshot["next_action"], "fix_fatal_error")

    def test_support_snapshot_drops_diagnostics_from_an_unsafe_terminal(self) -> None:
        """Fail-closed support output must not present untrusted state as evidence."""
        snapshot = support_snapshot(WorkflowResult(
            exit_code=0,
            error_codes=("ANALYSIS_FAILED",),
            counts={"scan": {"ready": 1}},
            status_summary={"scan_status": "ready", "processed": 1},
            manifest=_manifest(),
            next_action="none",
        ))

        self.assertEqual(snapshot["error_codes"], ["UNSAFE_WORKFLOW_RESULT"])
        for field in ("counts", "status", "scan_status", "selection", "model"):
            self.assertNotIn(field, snapshot)

    def test_support_snapshot_fails_closed_for_a_mutated_manifest_state(self) -> None:
        manifest = _manifest()
        manifest.photos[0].apply_state = []  # type: ignore[assignment]

        snapshot = support_snapshot(WorkflowResult(exit_code=0, manifest=manifest))

        self.assertEqual(snapshot["exit_code"], 2)
        self.assertEqual(snapshot["error_codes"], ["UNSAFE_WORKFLOW_RESULT"])
        self.assertEqual(snapshot["next_action"], "fix_fatal_error")

    def test_support_snapshot_fails_closed_for_a_non_manifest_success_result(self) -> None:
        """A runner must not smuggle arbitrary data as a successful manifest."""
        snapshot = support_snapshot(WorkflowResult(
            exit_code=0,
            manifest={"scan_status": "ready", "private": "must not escape"},  # type: ignore[arg-type]
            next_action="none",
        ))

        self.assertEqual(snapshot["exit_code"], 2)
        self.assertEqual(snapshot["error_codes"], ["UNSAFE_WORKFLOW_RESULT"])
        self.assertEqual(snapshot["next_action"], "fix_fatal_error")
        self.assertNotIn("private", repr(snapshot))

    def test_support_snapshot_fails_closed_for_malformed_manifest_projection_fields(self) -> None:
        """In-memory runner output must not crash support diagnostics."""
        for field in ("model", "selection"):
            with self.subTest(field=field):
                manifest = _manifest()
                setattr(manifest, field, [])
                snapshot = support_snapshot(WorkflowResult(
                    exit_code=0, manifest=manifest, next_action="none"
                ))
                self.assertEqual(snapshot["exit_code"], 2)
                self.assertEqual(snapshot["error_codes"], ["UNSAFE_WORKFLOW_RESULT"])
                self.assertEqual(snapshot["next_action"], "fix_fatal_error")

    def test_support_snapshot_preserves_bounded_error_code_counts(self) -> None:
        snapshot = support_snapshot(WorkflowResult(
            exit_code=2,
            error_codes=("PHOTOS_ACCESS_DENIED",),
            status_summary={"errors_by_code": {"PHOTOS_ACCESS_DENIED": 1}},
        ))

        self.assertEqual(
            snapshot["status"],
            {"errors_by_code": {"PHOTOS_ACCESS_DENIED": 1}},
        )

    def test_support_snapshot_rejects_boolean_exit_codes(self) -> None:
        snapshot = support_snapshot(WorkflowResult(exit_code=False))  # type: ignore[arg-type]

        self.assertEqual(snapshot["exit_code"], 2)

    def test_support_snapshot_fails_closed_for_malformed_next_action(self) -> None:
        snapshot = support_snapshot(WorkflowResult(
            exit_code=0,
            next_action=[],  # type: ignore[arg-type]
        ))

        self.assertEqual(snapshot["exit_code"], 2)
        self.assertEqual(snapshot["error_codes"], ["UNSAFE_WORKFLOW_RESULT"])
        self.assertEqual(snapshot["next_action"], "fix_fatal_error")

    def test_support_snapshot_rejects_malformed_error_codes_in_success(self) -> None:
        snapshot = support_snapshot(WorkflowResult(
            exit_code=0,
            error_codes={"PRIVATE": "must not be projected"},  # type: ignore[arg-type]
            next_action="none",
        ))

        self.assertEqual(snapshot["exit_code"], 2)
        self.assertEqual(snapshot["error_codes"], ["UNSAFE_WORKFLOW_RESULT"])
        self.assertEqual(snapshot["next_action"], "fix_fatal_error")

    def test_support_snapshot_rejects_none_error_codes_in_success(self) -> None:
        """Support must apply the same terminal collection contract as IPC."""
        snapshot = support_snapshot(WorkflowResult(
            exit_code=0,
            error_codes=None,  # type: ignore[arg-type]
            next_action="none",
        ))

        self.assertEqual(snapshot["exit_code"], 2)
        self.assertEqual(snapshot["error_codes"], ["UNSAFE_WORKFLOW_RESULT"])
        self.assertEqual(snapshot["next_action"], "fix_fatal_error")

    def test_support_snapshot_rejects_none_warning_codes_in_success(self) -> None:
        """Support must not turn malformed diagnostics into a clean success."""
        snapshot = support_snapshot(WorkflowResult(
            exit_code=0,
            warning_codes=None,  # type: ignore[arg-type]
            next_action="none",
        ))

        self.assertEqual(snapshot["exit_code"], 2)
        self.assertEqual(snapshot["error_codes"], ["UNSAFE_WORKFLOW_RESULT"])
        self.assertEqual(snapshot["next_action"], "fix_fatal_error")

    def test_support_snapshot_rejects_malformed_nested_error_counts_in_success(self) -> None:
        snapshot = support_snapshot(WorkflowResult(
            exit_code=0,
            status_summary={"errors_by_code": {"ANALYSIS_FAILED": []}},
            next_action="none",
        ))

        self.assertEqual(snapshot["exit_code"], 2)
        self.assertEqual(snapshot["error_codes"], ["UNSAFE_WORKFLOW_RESULT"])
        self.assertEqual(snapshot["next_action"], "fix_fatal_error")

    def test_support_snapshot_rejects_malformed_error_counter_in_success(self) -> None:
        snapshot = support_snapshot(WorkflowResult(
            exit_code=0,
            status_summary={"errors_by_code": {"ANALYSIS_FAILED": []}},  # type: ignore[dict-item]
            next_action="none",
        ))

        self.assertEqual(snapshot["exit_code"], 2)
        self.assertEqual(snapshot["error_codes"], ["UNSAFE_WORKFLOW_RESULT"])

    def test_support_snapshot_rejects_malformed_status_summary_in_success(self) -> None:
        snapshot = support_snapshot(WorkflowResult(
            exit_code=0,
            status_summary=["failed"],  # type: ignore[arg-type]
            next_action="none",
        ))

        self.assertEqual(snapshot["exit_code"], 2)
        self.assertEqual(snapshot["error_codes"], ["UNSAFE_WORKFLOW_RESULT"])

    def test_support_snapshot_rejects_failed_result_without_error_codes(self) -> None:
        snapshot = support_snapshot(WorkflowResult(exit_code=2, error_codes=(), next_action="fix_fatal_error"))

        self.assertEqual(snapshot["exit_code"], 2)
        self.assertEqual(snapshot["error_codes"], ["UNSAFE_WORKFLOW_RESULT"])

    def test_support_snapshot_rejects_unbounded_code_lists(self) -> None:
        codes = tuple(f"ERR_{index}" for index in range(17))
        snapshot = support_snapshot(WorkflowResult(exit_code=1, error_codes=codes, next_action="fix_fatal_error"))

        self.assertEqual(snapshot["exit_code"], 2)
        self.assertEqual(snapshot["error_codes"], ["UNSAFE_WORKFLOW_RESULT"])

    def test_support_snapshot_rejects_none_status_summary_in_success(self) -> None:
        snapshot = support_snapshot(WorkflowResult(
            exit_code=0,
            status_summary=None,  # type: ignore[arg-type]
            next_action="none",
        ))

        self.assertEqual(snapshot["exit_code"], 2)
        self.assertEqual(snapshot["error_codes"], ["UNSAFE_WORKFLOW_RESULT"])

    def test_support_snapshot_fails_closed_for_success_with_terminal_status_failure(self) -> None:
        result = WorkflowResult(
            exit_code=0,
            status_summary={"apply_status": "uncertain", "rollback_status": "complete"},
            next_action="none",
        )

        snapshot = support_snapshot(result)

        self.assertEqual(snapshot["exit_code"], 2)
        self.assertEqual(snapshot["error_codes"], ["UNSAFE_WORKFLOW_RESULT"])
        self.assertEqual(snapshot["next_action"], "fix_fatal_error")

    def test_support_snapshot_fails_closed_for_unknown_terminal_status(self) -> None:
        snapshot = support_snapshot(WorkflowResult(
            exit_code=0,
            status_summary={"apply_status": "garbage"},
            next_action="none",
        ))

        self.assertEqual(snapshot["exit_code"], 2)
        self.assertEqual(snapshot["error_codes"], ["UNSAFE_WORKFLOW_RESULT"])
        self.assertEqual(snapshot["next_action"], "fix_fatal_error")

    def test_support_snapshot_fails_closed_for_non_string_terminal_status(self) -> None:
        snapshot = support_snapshot(WorkflowResult(
            exit_code=0,
            status_summary={"apply_status": []},  # type: ignore[dict-item]
            next_action="none",
        ))

        self.assertEqual(snapshot["exit_code"], 2)
        self.assertEqual(snapshot["error_codes"], ["UNSAFE_WORKFLOW_RESULT"])
        self.assertEqual(snapshot["next_action"], "fix_fatal_error")

    def test_support_snapshot_rejects_malformed_counts_in_success(self) -> None:
        snapshot = support_snapshot(WorkflowResult(
            exit_code=0,
            counts={"apply": {"failed": []}},  # type: ignore[dict-item]
            next_action="none",
        ))

        self.assertEqual(snapshot["exit_code"], 2)
        self.assertEqual(snapshot["error_codes"], ["UNSAFE_WORKFLOW_RESULT"])
        self.assertEqual(snapshot["next_action"], "fix_fatal_error")

    def test_support_snapshot_fails_closed_for_unhashable_status_values(self) -> None:
        snapshot = support_snapshot(WorkflowResult(
            exit_code=0,
            status_summary={"apply_status": [], "rollback_status": {}},
            next_action="none",
        ))

        self.assertEqual(snapshot["exit_code"], 2)
        self.assertEqual(snapshot["error_codes"], ["UNSAFE_WORKFLOW_RESULT"])
        self.assertEqual(snapshot["next_action"], "fix_fatal_error")

    def test_support_snapshot_fails_closed_for_an_unhashable_scan_status(self) -> None:
        snapshot = support_snapshot(WorkflowResult(
            exit_code=0,
            status_summary={"scan_status": []},  # type: ignore[dict-item]
            next_action="none",
        ))

        self.assertEqual(snapshot["exit_code"], 2)
        self.assertEqual(snapshot["error_codes"], ["UNSAFE_WORKFLOW_RESULT"])
        self.assertEqual(snapshot["next_action"], "fix_fatal_error")

    def test_support_snapshot_ignores_malformed_manifest_values(self) -> None:
        snapshot = support_snapshot(WorkflowResult(
            exit_code=1,
            manifest={"scan_status": "ready", "private": "must not escape"},  # type: ignore[arg-type]
            next_action="fix_failed_scan",
        ))

        self.assertEqual(snapshot["exit_code"], 2)
        self.assertEqual(snapshot["error_codes"], ["UNSAFE_WORKFLOW_RESULT"])
        self.assertNotIn("private", repr(snapshot))
        self.assertNotIn("scan_status", snapshot)

    def test_support_snapshot_rejects_a_manifest_with_malformed_scan_status(self) -> None:
        manifest = _manifest()
        manifest.scan_status = []  # type: ignore[assignment]

        snapshot = support_snapshot(WorkflowResult(
            exit_code=0,
            manifest=manifest,
            next_action="none",
        ))

        self.assertEqual(snapshot["exit_code"], 2)
        self.assertEqual(snapshot["error_codes"], ["UNSAFE_WORKFLOW_RESULT"])
        self.assertEqual(snapshot["next_action"], "fix_fatal_error")
