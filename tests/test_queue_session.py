from __future__ import annotations

import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from photos_indexer.queue_session import (
    ITEM_STATES,
    DecisionConflictError,
    QueueItem,
    QueueSession,
    QueueSessionError,
    SessionConfig,
    StaleRevisionError,
    load_session,
    write_session,
)


SESSION_ID = "00000000-0000-4000-8000-000000000001"
ITEM_ID = "00000000-0000-4000-8000-000000000002"
SOURCE_RUN_ID = "00000000-0000-4000-8000-000000000003"
DECISION_ID = "00000000-0000-4000-8000-000000000004"
SECOND_ITEM_ID = "00000000-0000-4000-8000-000000000005"
SECOND_DECISION_ID = "00000000-0000-4000-8000-000000000006"


def _ready_session() -> QueueSession:
    session = QueueSession.new(
        session_id=SESSION_ID,
        config=SessionConfig(photo_count=10, inference_concurrency=2),
    )
    session.enqueue(item_id=ITEM_ID, source_run_id=SOURCE_RUN_ID, state="ready")
    return session


class SessionConfigTests(unittest.TestCase):
    def test_accepts_only_supported_photo_count_and_concurrency_bounds(self) -> None:
        for photo_count, concurrency in ((1, 1), (50, 4)):
            with self.subTest(photo_count=photo_count, concurrency=concurrency):
                config = SessionConfig(photo_count=photo_count, inference_concurrency=concurrency)
                self.assertEqual(config.photo_count, photo_count)
                self.assertEqual(config.inference_concurrency, concurrency)

        for photo_count, concurrency in ((0, 1), (51, 1), (1, 0), (1, 5), (True, 1), (1, False)):
            with self.subTest(photo_count=photo_count, concurrency=concurrency), self.assertRaises(
                QueueSessionError
            ):
                SessionConfig(photo_count=photo_count, inference_concurrency=concurrency)

    def test_round_trip_preserves_safe_operational_settings(self) -> None:
        config = SessionConfig(
            photo_count=12,
            inference_concurrency=3,
            auto_analyze=False,
            include_caption=True,
            apple_maps=True,
            random_selection=True,
            model_policy="adaptive",
            model=None,
            fast_model="qwen3-vl:4b",
            detailed_model="qwen3-vl:8b",
        )

        self.assertEqual(SessionConfig.from_dict(config.to_dict()), config)


class QueueSessionModelTests(unittest.TestCase):
    def test_terminal_items_do_not_consume_active_photo_capacity(self) -> None:
        session = QueueSession.new(
            session_id=SESSION_ID,
            config=SessionConfig(photo_count=1, inference_concurrency=1),
        )
        session.enqueue(item_id=ITEM_ID, state="ready")
        session.record_decision(
            decision_id=DECISION_ID,
            item_id=ITEM_ID,
            revision=1,
            action="discard",
        )

        replacement = session.enqueue(item_id=SECOND_ITEM_ID, state="queued")

        self.assertEqual(replacement.item_id, SECOND_ITEM_ID)
        self.assertEqual(
            [(item.item_id, item.state) for item in session.items],
            [(ITEM_ID, "discarded"), (SECOND_ITEM_ID, "queued")],
        )

    def test_stopped_session_round_trip_keeps_history_beyond_active_capacity(self) -> None:
        session = QueueSession.new(
            session_id=SESSION_ID,
            config=SessionConfig(photo_count=1, inference_concurrency=1),
        )
        session.enqueue(item_id=ITEM_ID, state="ready")
        session.record_decision(
            decision_id=DECISION_ID,
            item_id=ITEM_ID,
            revision=1,
            action="discard",
        )
        session.enqueue(item_id=SECOND_ITEM_ID, state="ready")
        session.record_decision(
            decision_id=SECOND_DECISION_ID,
            item_id=SECOND_ITEM_ID,
            revision=1,
            action="persist",
        )
        session.checkpoint_item(item_id=SECOND_ITEM_ID, revision=1, state="verified")
        session.set_state(expected_revision=session.revision, state="stopped")

        restored = QueueSession.from_dict(session.to_dict())

        self.assertEqual(restored.state, "stopped")
        self.assertEqual(
            [(item.item_id, item.state) for item in restored.items],
            [(ITEM_ID, "discarded"), (SECOND_ITEM_ID, "verified")],
        )

    def test_active_items_still_enforce_photo_capacity(self) -> None:
        session = QueueSession.new(
            session_id=SESSION_ID,
            config=SessionConfig(photo_count=1, inference_concurrency=1),
        )
        session.enqueue(item_id=ITEM_ID, state="ready")

        with self.assertRaises(QueueSessionError):
            session.enqueue(item_id=SECOND_ITEM_ID, state="queued")

    def test_shrinking_photo_count_keeps_current_work_and_blocks_replenishment(self) -> None:
        session = QueueSession.new(
            session_id=SESSION_ID,
            config=SessionConfig(photo_count=2, inference_concurrency=1),
        )
        session.enqueue(item_id=ITEM_ID, state="ready")
        session.enqueue(item_id=SECOND_ITEM_ID, state="ready")

        session.update_config(
            expected_revision=session.revision,
            config=SessionConfig(photo_count=1, inference_concurrency=1),
        )

        restored = QueueSession.from_dict(session.to_dict())
        self.assertEqual(restored.config.photo_count, 1)
        self.assertEqual([item.state for item in restored.items], ["ready", "ready"])
        with self.assertRaises(QueueSessionError):
            restored.enqueue(
                item_id="00000000-0000-4000-8000-000000000007",
                state="queued",
            )

    def test_failed_items_allow_recovery_actions_but_never_a_write(self) -> None:
        discarded = QueueSession.new(
            session_id=SESSION_ID,
            config=SessionConfig(photo_count=1, inference_concurrency=1),
        )
        discarded.enqueue(item_id=ITEM_ID, state="failed")
        discarded.record_decision(
            decision_id=DECISION_ID,
            item_id=ITEM_ID,
            revision=1,
            action="discard",
        )
        self.assertEqual(discarded.current_item(ITEM_ID).state, "discarded")

        rescanned = QueueSession.new(
            session_id=SESSION_ID,
            config=SessionConfig(photo_count=1, inference_concurrency=1),
        )
        rescanned.enqueue(item_id=ITEM_ID, state="failed")
        rescanned.record_decision(
            decision_id=DECISION_ID,
            item_id=ITEM_ID,
            revision=1,
            action="rescan",
        )
        self.assertEqual(
            [(item.revision, item.state) for item in rescanned.items],
            [(1, "failed"), (2, "queued")],
        )

        unsafe = QueueSession.new(
            session_id=SESSION_ID,
            config=SessionConfig(photo_count=1, inference_concurrency=1),
        )
        unsafe.enqueue(item_id=ITEM_ID, state="failed")
        with self.assertRaises(QueueSessionError):
            unsafe.record_decision(
                decision_id=DECISION_ID,
                item_id=ITEM_ID,
                revision=1,
                action="persist",
            )
        self.assertEqual(unsafe.current_item(ITEM_ID).state, "failed")
        self.assertEqual(unsafe.decisions, [])

    def test_uncertain_items_allow_safe_discard_and_read_only_rescan(self) -> None:
        discarded = QueueSession.new(
            session_id=SESSION_ID,
            config=SessionConfig(photo_count=1, inference_concurrency=1),
        )
        discarded.enqueue(item_id=ITEM_ID, state="uncertain")
        discarded.record_decision(
            decision_id=DECISION_ID,
            item_id=ITEM_ID,
            revision=1,
            action="discard",
        )
        self.assertEqual(discarded.current_item(ITEM_ID).state, "discarded")

        rescanned = QueueSession.new(
            session_id=SESSION_ID,
            config=SessionConfig(photo_count=1, inference_concurrency=1),
        )
        rescanned.enqueue(item_id=ITEM_ID, state="uncertain")
        rescanned.record_decision(
            decision_id=DECISION_ID,
            item_id=ITEM_ID,
            revision=1,
            action="rescan",
        )
        self.assertEqual(
            [(item.revision, item.state) for item in rescanned.items],
            [(1, "uncertain"), (2, "queued")],
        )

    def test_global_revision_is_persisted_and_advances_once_per_mutation(self) -> None:
        session = QueueSession.new(
            session_id=SESSION_ID,
            config=SessionConfig(photo_count=10, inference_concurrency=2),
        )
        self.assertEqual(session.revision, 0)

        session.enqueue(item_id=ITEM_ID, source_run_id=SOURCE_RUN_ID, state="queued")
        self.assertEqual(session.revision, 1)
        session.checkpoint_item(item_id=ITEM_ID, revision=1, state="ready")
        self.assertEqual(session.revision, 2)
        session.update_config(
            expected_revision=2,
            config=SessionConfig(photo_count=10, inference_concurrency=3),
        )
        self.assertEqual(session.revision, 3)
        session.set_state(expected_revision=3, state="paused")
        self.assertEqual(session.revision, 4)

        session.record_decision(
            decision_id=DECISION_ID,
            item_id=ITEM_ID,
            revision=1,
            action="persist",
        )
        self.assertEqual(session.revision, 5)
        session.record_decision(
            decision_id=DECISION_ID,
            item_id=ITEM_ID,
            revision=1,
            action="persist",
        )
        self.assertEqual(session.revision, 5)

        restored = QueueSession.from_dict(session.to_dict())
        self.assertEqual(restored.revision, 5)
        self.assertEqual(restored.to_dict(), session.to_dict())

    def test_session_controls_reject_stale_global_revision_without_mutating(self) -> None:
        session = _ready_session()
        original = session.to_dict()

        with self.assertRaises(StaleRevisionError):
            session.set_state(expected_revision=0, state="paused")
        with self.assertRaises(StaleRevisionError):
            session.update_config(
                expected_revision=0,
                config=SessionConfig(photo_count=10, inference_concurrency=3),
            )

        self.assertEqual(session.to_dict(), original)

    def test_item_decisions_continue_to_use_the_item_revision(self) -> None:
        session = _ready_session()

        decision = session.record_decision(
            decision_id=DECISION_ID,
            item_id=ITEM_ID,
            revision=1,
            action="persist",
        )

        self.assertEqual(decision.revision, 1)
        self.assertEqual(session.current_item(ITEM_ID).revision, 1)
        self.assertEqual(session.revision, 2)

    def test_round_trip_preserves_every_supported_item_state(self) -> None:
        items = [
            QueueItem(
                item_id=f"00000000-0000-4000-8000-{index:012d}",
                revision=1,
                state=state,
                source_run_id=SOURCE_RUN_ID,
            )
            for index, state in enumerate(sorted(ITEM_STATES), start=10)
        ]
        session = QueueSession(
            session_id=SESSION_ID,
            config=SessionConfig(photo_count=50, inference_concurrency=4),
            items=items,
        )

        restored = QueueSession.from_dict(session.to_dict())

        self.assertEqual([item.state for item in restored.items], sorted(ITEM_STATES))
        self.assertEqual(restored.to_dict(), session.to_dict())

    def test_rejects_unknown_state_and_non_positive_revision(self) -> None:
        with self.assertRaises(QueueSessionError):
            QueueItem(item_id=ITEM_ID, revision=1, state="unknown")
        with self.assertRaises(QueueSessionError):
            QueueItem(item_id=ITEM_ID, revision=0, state="queued")

    def test_replaying_same_decision_is_idempotent(self) -> None:
        session = _ready_session()

        first = session.record_decision(
            decision_id=DECISION_ID,
            item_id=ITEM_ID,
            revision=1,
            action="persist",
        )
        replay = session.record_decision(
            decision_id=DECISION_ID,
            item_id=ITEM_ID,
            revision=1,
            action="persist",
        )

        self.assertIs(replay, first)
        self.assertEqual(len(session.decisions), 1)
        self.assertEqual(session.current_item(ITEM_ID).state, "save_queued")

    def test_rejects_reusing_decision_id_for_a_different_action(self) -> None:
        session = _ready_session()
        session.record_decision(
            decision_id=DECISION_ID,
            item_id=ITEM_ID,
            revision=1,
            action="discard",
        )

        with self.assertRaises(DecisionConflictError):
            session.record_decision(
                decision_id=DECISION_ID,
                item_id=ITEM_ID,
                revision=1,
                action="rescan",
            )

    def test_rejects_a_decision_for_a_stale_revision(self) -> None:
        session = _ready_session()
        session.record_decision(
            decision_id=DECISION_ID,
            item_id=ITEM_ID,
            revision=1,
            action="rescan",
        )

        with self.assertRaises(StaleRevisionError):
            session.record_decision(
                decision_id="00000000-0000-4000-8000-000000000005",
                item_id=ITEM_ID,
                revision=1,
                action="discard",
            )

    def test_rescan_creates_exactly_one_new_revision_across_replays(self) -> None:
        session = _ready_session()

        session.record_decision(
            decision_id=DECISION_ID,
            item_id=ITEM_ID,
            revision=1,
            action="rescan",
        )
        session.record_decision(
            decision_id=DECISION_ID,
            item_id=ITEM_ID,
            revision=1,
            action="rescan",
        )

        self.assertEqual([(item.revision, item.state) for item in session.items], [(1, "ready"), (2, "queued")])

    def test_schema_rejects_unknown_fields_instead_of_persisting_photo_content(self) -> None:
        payload = _ready_session().to_dict()
        payload["items"][0]["keywords"] = ["private proposal"]

        with self.assertRaises(QueueSessionError):
            QueueSession.from_dict(payload)

    def test_malformed_json_types_are_mapped_to_queue_session_error(self) -> None:
        malformed_values = (
            ("state", []),
            ("revision", True),
            ("schema_version", True),
        )
        for field, value in malformed_values:
            with self.subTest(field=field):
                payload = _ready_session().to_dict()
                payload[field] = value
                with self.assertRaises(QueueSessionError):
                    QueueSession.from_dict(payload)

        payload = _ready_session().to_dict()
        payload["items"][0]["state"] = []
        with self.assertRaises(QueueSessionError):
            QueueSession.from_dict(payload)


class QueueSessionStorageTests(unittest.TestCase):
    def test_long_running_session_checkpoints_and_resumes_one_thousand_decisions(self) -> None:
        session = QueueSession.new(
            session_id=SESSION_ID,
            config=SessionConfig(photo_count=1, inference_concurrency=1),
        )
        for index in range(1_000):
            item_id = f"00000000-0000-4000-8000-{index + 100:012d}"
            decision_id = f"10000000-0000-4000-8000-{index + 100:012d}"
            session.enqueue(item_id=item_id, state="ready")
            session.record_decision(
                decision_id=decision_id,
                item_id=item_id,
                revision=1,
                action="discard",
            )

        with tempfile.TemporaryDirectory() as directory:
            session_dir = Path(directory) / SESSION_ID
            session_dir.mkdir(mode=0o700)
            write_session(session_dir, session)
            restored = load_session(session_dir)

        self.assertEqual(len(restored.items), 1_000)
        self.assertEqual(len(restored.decisions), 1_000)
        self.assertEqual(restored.revision, 2_000)

    def test_write_and_load_use_private_atomic_session_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            session_dir = Path(temporary_directory) / "session"
            session = _ready_session()

            path = write_session(session_dir, session)
            restored = load_session(session_dir)

            self.assertEqual(path, session_dir / "session.json")
            self.assertEqual(restored.to_dict(), session.to_dict())
            self.assertEqual(stat.S_IMODE(session_dir.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual([candidate.name for candidate in session_dir.iterdir()], ["session.json"])

    def test_serialized_ledger_has_no_photo_content_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = write_session(Path(temporary_directory) / "session", _ready_session())

            payload = path.read_text(encoding="utf-8").casefold()

            for forbidden_name in (
                '"keywords":', '"caption":', '"prompt":', '"coordinates":',
                '"latitude":', '"longitude":', '"coords":',
            ):
                with self.subTest(forbidden_name=forbidden_name):
                    self.assertNotIn(forbidden_name, payload)

    def test_load_rejects_non_private_session_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            session_dir = Path(temporary_directory) / "session"
            path = write_session(session_dir, _ready_session())
            path.chmod(0o644)

            with self.assertRaises(QueueSessionError):
                load_session(session_dir)

    def test_write_rejects_a_symlinked_session_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "target"
            target.mkdir(mode=0o700)
            session_dir = root / "session"
            session_dir.symlink_to(target, target_is_directory=True)

            with self.assertRaises(QueueSessionError):
                write_session(session_dir, _ready_session())

            self.assertEqual(list(target.iterdir()), [])

    def test_load_rejects_duplicate_json_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            session_dir = Path(temporary_directory) / "session"
            path = write_session(session_dir, _ready_session())
            payload = path.read_text(encoding="utf-8").replace(
                '"schema_version":1',
                '"schema_version":1,"schema_version":1',
                1,
            )
            path.write_text(payload, encoding="utf-8")
            path.chmod(0o600)

            with self.assertRaises(QueueSessionError):
                load_session(session_dir)

    def test_failed_atomic_replacement_preserves_previous_session(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            session_dir = Path(temporary_directory) / "session"
            original = _ready_session()
            path = write_session(session_dir, original)
            original_payload = path.read_bytes()
            replacement = _ready_session()
            replacement.state = "paused"

            with mock.patch("photos_indexer.queue_session.os.replace", side_effect=OSError("simulated failure")):
                with self.assertRaises(OSError):
                    write_session(session_dir, replacement)

            self.assertEqual(path.read_bytes(), original_payload)
            self.assertEqual([candidate.name for candidate in session_dir.iterdir()], ["session.json"])


if __name__ == "__main__":
    unittest.main()
