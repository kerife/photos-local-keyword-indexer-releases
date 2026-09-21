from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from photos_indexer.manifest import compute_mutation_digest, compute_rollback_digest, load_manifest, write_manifest
from photos_indexer.workflows import ApplyDependencies, run_apply, run_rollback, run_status

from tests.test_workflows import RevalidatingSelector, StatefulBridge, make_photo, make_reviewed_manifest


class StatusTraceabilityTests(unittest.TestCase):
    def test_apply_resumes_unstarted_row_after_verified_checkpoint(self) -> None:
        """A crash after one verified row must not strand later approved rows."""
        with tempfile.TemporaryDirectory() as tmp:
            first = make_photo(1)
            first.apply_state = "verified"
            first.applied_keywords = ["playa"]
            second = make_photo(2)
            manifest_path = make_reviewed_manifest(Path(tmp) / "run", [first, second])
            manifest = load_manifest(manifest_path.parent)
            manifest.photos[0].mutation_digest = compute_mutation_digest(manifest, manifest.photos[0])
            write_manifest(manifest_path.parent, manifest)
            bridge = StatefulBridge({"local-1": ["PERRO", "playa"], "local-2": ["PERRO"]}, manifest_path)

            result = run_apply(
                manifest_path,
                dependencies=ApplyDependencies(
                    selector_factory=RevalidatingSelector,
                    bridge_factory=lambda: bridge,
                    global_lock_path=Path(tmp) / "global" / "mutation.lock",
                ),
            )

            self.assertEqual(result.exit_code, 0)
            self.assertEqual(
                bridge.replace_calls,
                [(str(manifest.photos[1].uuid), ["PERRO", "playa"])],
            )
            self.assertEqual(
                [photo.apply_state for photo in load_manifest(manifest_path.parent).photos],
                ["verified", "verified"],
            )

    def test_rollback_rejects_failed_row_without_persisted_rollback_error(self) -> None:
        """Rollback must not retry a state whose failure cause was lost."""
        with tempfile.TemporaryDirectory() as tmp:
            photo = make_photo(1)
            photo.apply_state = "verified"
            photo.applied_keywords = ["playa"]
            photo.rollback_state = "failed"
            manifest_path = make_reviewed_manifest(Path(tmp) / "run", [photo])
            manifest = load_manifest(manifest_path.parent)
            row = manifest.photos[0]
            row.rollback_digest = compute_rollback_digest(manifest, row)
            write_manifest(manifest_path.parent, manifest)
            calls: list[str] = []
            bridge = StatefulBridge({"local-1": ["PERRO", "playa"]}, manifest_path)

            result = run_rollback(
                manifest_path,
                dependencies=ApplyDependencies(
                    selector_factory=lambda: calls.append("selector"),
                    bridge_factory=lambda: (calls.append("bridge") or bridge),
                    global_lock_path=Path(tmp) / "global" / "mutation.lock",
                ),
            )

            self.assertEqual(result.exit_code, 2)
            self.assertEqual(result.error_codes, ("MANIFEST_INVALID",))
            self.assertEqual(calls, [])
            self.assertEqual(bridge.replace_calls, [])

    def test_apply_without_effective_mutation_recommends_recovery_for_scan_errors(self) -> None:
        """A partial scan error must not finish with an unexplained no-op action."""
        with tempfile.TemporaryDirectory() as tmp:
            failed = make_photo(1)
            failed.uuid = None
            failed.existing_keywords = []
            failed.proposed_keywords = []
            failed.scan_state = "analysis_failed"
            failed.errors = [{"stage": "analysis", "code": "LOW_CONFIDENCE"}]
            already_present = make_photo(2)
            manifest_path = make_reviewed_manifest(
                Path(tmp) / "run", [failed, already_present], scan_status="ready_with_errors"
            )
            bridge = StatefulBridge({"local-2": ["PERRO", "playa"]}, manifest_path)

            result = run_apply(
                manifest_path,
                dependencies=ApplyDependencies(
                    selector_factory=RevalidatingSelector,
                    bridge_factory=lambda: bridge,
                    global_lock_path=Path(tmp) / "global" / "mutation.lock",
                ),
            )

            self.assertEqual(result.exit_code, 1)
            self.assertEqual(result.error_codes, ("LOW_CONFIDENCE",))
            self.assertEqual(result.next_action, "fix_failed_scan")

    def test_status_rejects_failed_apply_without_a_persisted_apply_error(self) -> None:
        """A failed mutation row must retain its cause before status can guide retry."""
        with tempfile.TemporaryDirectory() as tmp:
            path = make_reviewed_manifest(Path(tmp) / "run", [make_photo(1)])
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["photos"][0]["apply_state"] = "failed"
            payload["photos"][0]["errors"] = []
            path.write_text(json.dumps(payload), encoding="utf-8")
            path.chmod(0o600)

            result = run_status(path)

            self.assertEqual(result.exit_code, 2)
            self.assertEqual(result.error_codes, ("MANIFEST_INVALID",))
            self.assertEqual(result.next_action, "fix_fatal_error")

    def test_status_rejects_uncertain_apply_without_a_persisted_apply_error(self) -> None:
        """An uncertainty without a cause must not be presented as recoverable."""
        with tempfile.TemporaryDirectory() as tmp:
            path = make_reviewed_manifest(Path(tmp) / "run", [make_photo(1)])
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["photos"][0]["apply_state"] = "uncertain"
            payload["photos"][0]["errors"] = []
            path.write_text(json.dumps(payload), encoding="utf-8")
            path.chmod(0o600)

            result = run_status(path)

            self.assertEqual(result.exit_code, 2)
            self.assertEqual(result.error_codes, ("MANIFEST_INVALID",))
            self.assertEqual(result.next_action, "fix_fatal_error")

    def test_apply_preserves_scan_warnings_for_terminal_history_and_support(self) -> None:
        """Mutation completion must retain warnings from the selected scan."""
        with tempfile.TemporaryDirectory() as tmp:
            path = make_reviewed_manifest(Path(tmp) / "run", [make_photo(1)])
            source_path = path.parent.parent / f"{path.parent.name}-source" / "manifest.json"
            source = load_manifest(source_path.parent)
            source.selection["requested"] = 20
            source.selection["access"] = "limited"
            write_manifest(source_path.parent, source)
            reviewed = load_manifest(path.parent)
            reviewed.selection = dict(source.selection)
            reviewed.source_scan_digest = source.scan_digest
            write_manifest(path.parent, reviewed)
            bridge = StatefulBridge({"local-1": ["PERRO"]}, path)
            result = run_apply(
                path,
                dependencies=ApplyDependencies(
                    selector_factory=RevalidatingSelector,
                    bridge_factory=lambda: bridge,
                    global_lock_path=Path(tmp) / "global" / "mutation.lock",
                ),
            )

            self.assertEqual(result.warning_codes, ("FEWER_PHOTOS_AVAILABLE", "PHOTOS_ACCESS_LIMITED"))

    def test_apply_preserves_partial_scan_errors_in_terminal_result(self) -> None:
        """Applying valid rows from a partial scan remains a partial result."""
        with tempfile.TemporaryDirectory() as tmp:
            failed = make_photo(1)
            failed.uuid = None
            failed.proposed_keywords = []
            failed.scan_state = "analysis_failed"
            failed.errors = [{"stage": "analysis", "code": "LOW_CONFIDENCE"}]
            path = make_reviewed_manifest(
                Path(tmp) / "run", [failed, make_photo(2)], scan_status="ready_with_errors"
            )
            bridge = StatefulBridge({"local-2": ["PERRO"]}, path)
            result = run_apply(
                path,
                dependencies=ApplyDependencies(
                    selector_factory=RevalidatingSelector,
                    bridge_factory=lambda: bridge,
                    global_lock_path=Path(tmp) / "global" / "mutation.lock",
                ),
            )

            self.assertEqual(result.exit_code, 1)
            self.assertEqual(result.error_codes, ("LOW_CONFIDENCE",))

    def test_rollback_without_pending_mutation_recommends_recovery_for_scan_errors(self) -> None:
        """A completed rollback must still expose an inherited scan failure."""
        with tempfile.TemporaryDirectory() as tmp:
            failed = make_photo(1)
            failed.uuid = None
            failed.existing_keywords = []
            failed.proposed_keywords = []
            failed.scan_state = "analysis_failed"
            failed.errors = [{"stage": "analysis", "code": "LOW_CONFIDENCE"}]
            applied = make_photo(2)
            applied.apply_state = "verified"
            applied.applied_keywords = ["playa"]
            manifest_path = make_reviewed_manifest(
                Path(tmp) / "run", [failed, applied], scan_status="ready_with_errors"
            )
            bridge = StatefulBridge({"local-2": ["PERRO", "playa"]}, manifest_path)

            result = run_rollback(
                manifest_path,
                dependencies=ApplyDependencies(
                    selector_factory=RevalidatingSelector,
                    bridge_factory=lambda: bridge,
                    global_lock_path=Path(tmp) / "global" / "mutation.lock",
                ),
            )

            self.assertEqual(result.exit_code, 1)
            self.assertEqual(result.error_codes, ("LOW_CONFIDENCE",))
            self.assertEqual(result.next_action, "fix_failed_scan")

    def test_status_recommends_scan_recovery_after_rollback_completed_with_scan_errors(self) -> None:
        """Status must not hide scan recovery after all mutation work ends."""
        with tempfile.TemporaryDirectory() as tmp:
            failed = make_photo(1)
            failed.uuid = None
            failed.existing_keywords = []
            failed.proposed_keywords = []
            failed.scan_state = "analysis_failed"
            failed.errors = [{"stage": "analysis", "code": "LOW_CONFIDENCE"}]
            removed = make_photo(2)
            removed.apply_state = "verified"
            removed.applied_keywords = ["playa"]
            removed.rollback_state = "verified_removed"
            removed.rolled_back_keywords = ["playa"]
            manifest_path = make_reviewed_manifest(
                Path(tmp) / "run", [failed, removed], scan_status="ready_with_errors"
            )

            result = run_status(manifest_path)

            self.assertEqual(result.exit_code, 1)
            self.assertEqual(result.error_codes, ("LOW_CONFIDENCE",))
            self.assertEqual(result.next_action, "fix_failed_scan")


if __name__ == "__main__":
    unittest.main()
