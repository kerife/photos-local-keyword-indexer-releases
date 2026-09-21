from __future__ import annotations

import sys
import tempfile
import threading
import time
import uuid
from concurrent.futures import Future
from pathlib import Path

import pytest

from photos_indexer.ipc import _safe_service_event
from photos_indexer.queue_coordinator import AnalysisOutcome, QueueCoordinator
from photos_indexer.queue_session import QueueSession, SessionConfig, load_session


def _session(concurrency: int = 2, photo_count: int = 3) -> QueueSession:
    return QueueSession.new(
        session_id=str(uuid.uuid4()),
        config=SessionConfig(photo_count=photo_count, inference_concurrency=concurrency),
    )


def test_inference_is_bounded_and_preparation_is_serialized() -> None:
    active_prepares = 0
    max_prepares = 0
    active_analyses = 0
    max_analyses = 0
    lock = threading.Lock()
    release = threading.Event()

    def prepare(candidate: object) -> object:
        nonlocal active_prepares, max_prepares
        with lock:
            active_prepares += 1
            max_prepares = max(max_prepares, active_prepares)
        time.sleep(0.01)
        with lock:
            active_prepares -= 1
        return candidate

    def analyze(prepared: object) -> AnalysisOutcome:
        nonlocal active_analyses, max_analyses
        with lock:
            active_analyses += 1
            max_analyses = max(max_analyses, active_analyses)
        release.wait(1)
        with lock:
            active_analyses -= 1
        return AnalysisOutcome(source_run_id=str(uuid.uuid4()), manifest_path=Path(f"/runs/{prepared}/manifest.json"))

    with tempfile.TemporaryDirectory() as temporary_directory:
        coordinator = QueueCoordinator(
            session=_session(concurrency=2),
            session_dir=Path(temporary_directory) / "session",
            discover=lambda count: [f"photo-{index}" for index in range(count)],
            prepare=prepare,
            analyze=analyze,
            emit=lambda event: None,
        )
        coordinator.start()
        try:
            deadline = time.monotonic() + 1
            while max_analyses < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
            assert max_analyses == 2
            assert max_prepares == 1
        finally:
            release.set()
            coordinator.wait()


def test_concurrency_change_applies_to_queued_photos_without_waiting_for_active_analysis() -> None:
    active = 0
    maximum = 0
    lock = threading.Lock()
    first_started = threading.Event()
    release = threading.Event()

    def analyze(prepared: object) -> AnalysisOutcome:
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
            first_started.set()
        release.wait(1)
        with lock:
            active -= 1
        return AnalysisOutcome(
            source_run_id=str(uuid.uuid4()),
            manifest_path=Path(f"/runs/{prepared}/manifest.json"),
        )

    with tempfile.TemporaryDirectory() as temporary_directory:
        coordinator = QueueCoordinator(
            session=_session(concurrency=1, photo_count=2),
            session_dir=Path(temporary_directory) / "session",
            discover=lambda count: [f"photo-{index}" for index in range(count)],
            prepare=lambda candidate: candidate,
            analyze=analyze,
            emit=lambda event: None,
        )
        coordinator.start()
        try:
            assert first_started.wait(1)
            coordinator.update_config(
                expected_revision=coordinator.session.revision,
                config=SessionConfig(photo_count=2, inference_concurrency=2),
            )
            deadline = time.monotonic() + 1
            while maximum < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
            assert maximum == 2
        finally:
            release.set()
            coordinator.close()


def test_reconfiguration_does_not_over_admit_while_a_photo_is_preparing() -> None:
    first_prepare_started = threading.Event()
    release_first_prepare = threading.Event()
    prepared: list[str] = []

    def prepare(candidate: object) -> object:
        value = str(candidate)
        prepared.append(value)
        if value == "photo-0":
            first_prepare_started.set()
            assert release_first_prepare.wait(2)
        return candidate

    with tempfile.TemporaryDirectory() as temporary_directory:
        coordinator = QueueCoordinator(
            session=_session(concurrency=1, photo_count=2),
            session_dir=Path(temporary_directory) / "session",
            discover=lambda count: ["photo-0", "photo-1"][:count],
            prepare=prepare,
            analyze=lambda candidate: AnalysisOutcome(
                source_run_id=str(uuid.uuid4()),
                manifest_path=Path(f"/runs/{candidate}/manifest.json"),
            ),
            emit=lambda event: None,
        )
        start = threading.Thread(target=coordinator.start)
        start.start()
        try:
            assert first_prepare_started.wait(1)
            coordinator.update_config(
                expected_revision=coordinator.session.revision,
                config=SessionConfig(photo_count=2, inference_concurrency=1),
            )
            assert prepared == ["photo-0"]
        finally:
            release_first_prepare.set()
            start.join(timeout=2)
            assert not start.is_alive()
            coordinator.wait()
            assert [item.state for item in coordinator.current_items()] == ["ready", "ready"]
            coordinator.close()


def test_immediate_analysis_callback_does_not_hold_state_lock_during_next_prepare() -> None:
    second_prepare_started = threading.Event()
    release_second_prepare = threading.Event()

    class ImmediateExecutor:
        def submit(self, operation: object, *args: object) -> Future[AnalysisOutcome]:
            future: Future[AnalysisOutcome] = Future()
            try:
                future.set_result(operation(*args))  # type: ignore[operator]
            except BaseException as error:
                future.set_exception(error)
            return future

        def shutdown(self, **_: object) -> None:
            return None

    def prepare(candidate: object) -> object:
        if candidate == "photo-1":
            second_prepare_started.set()
            assert release_second_prepare.wait(2)
        return candidate

    with tempfile.TemporaryDirectory() as temporary_directory:
        coordinator = QueueCoordinator(
            session=_session(concurrency=1, photo_count=2),
            session_dir=Path(temporary_directory) / "session",
            discover=lambda count: ["photo-0", "photo-1"][:count],
            prepare=prepare,
            analyze=lambda candidate: AnalysisOutcome(
                source_run_id=str(uuid.uuid4()),
                manifest_path=Path(f"/runs/{candidate}/manifest.json"),
            ),
            emit=lambda event: None,
        )
        coordinator._executor = ImmediateExecutor()  # type: ignore[assignment]
        start = threading.Thread(target=coordinator.start)
        start.start()
        try:
            assert second_prepare_started.wait(1)
            started = time.monotonic()
            coordinator.pause(expected_revision=coordinator.session.revision)
            assert time.monotonic() - started < 0.2
        finally:
            release_second_prepare.set()
            start.join(timeout=2)
            assert not start.is_alive()
            coordinator.close()


def test_close_waits_for_a_blocked_prepare_before_shutting_down_inference() -> None:
    prepare_started = threading.Event()
    release_prepare = threading.Event()
    close_finished = threading.Event()
    analysis_calls: list[object] = []
    start_errors: list[BaseException] = []

    def prepare(candidate: object) -> object:
        prepare_started.set()
        assert release_prepare.wait(2)
        return candidate

    with tempfile.TemporaryDirectory() as temporary_directory:
        coordinator = QueueCoordinator(
            session=_session(concurrency=1, photo_count=1),
            session_dir=Path(temporary_directory) / "session",
            discover=lambda count: ["photo-0"][:count],
            prepare=prepare,
            analyze=lambda candidate: analysis_calls.append(candidate) or AnalysisOutcome(
                source_run_id=str(uuid.uuid4()),
                manifest_path=Path(f"/runs/{candidate}/manifest.json"),
            ),
            emit=lambda event: None,
        )

        def start_queue() -> None:
            try:
                coordinator.start()
            except BaseException as error:
                start_errors.append(error)

        start = threading.Thread(target=start_queue)
        start.start()
        try:
            assert prepare_started.wait(1)
            coordinator.stop(expected_revision=coordinator.session.revision)
            closer = threading.Thread(target=lambda: (coordinator.close(), close_finished.set()))
            closer.start()
            assert not close_finished.wait(0.15)
        finally:
            release_prepare.set()
            start.join(timeout=2)
            closer.join(timeout=2)
            assert not start.is_alive()
            assert not closer.is_alive()
            assert close_finished.is_set()
            assert start_errors == []
            assert analysis_calls == ["photo-0"]


def test_close_waits_while_prepare_is_reserved_for_the_photos_lane() -> None:
    lane_acquired = threading.Event()
    release_lane = threading.Event()
    close_finished = threading.Event()
    update_errors: list[BaseException] = []

    with tempfile.TemporaryDirectory() as temporary_directory:
        session = QueueSession.new(
            session_id=str(uuid.uuid4()),
            config=SessionConfig(photo_count=1, inference_concurrency=1, auto_analyze=False),
        )
        coordinator = QueueCoordinator(
            session=session,
            session_dir=Path(temporary_directory) / "session",
            discover=lambda count: ["photo-0"][:count],
            prepare=lambda candidate: candidate,
            analyze=lambda candidate: AnalysisOutcome(
                source_run_id=str(uuid.uuid4()),
                manifest_path=Path(f"/runs/{candidate}/manifest.json"),
            ),
            emit=lambda event: None,
        )
        coordinator.start()

        def occupy_photos_lane() -> None:
            with coordinator._photos_lane.read() as acquired:
                assert acquired
                lane_acquired.set()
                assert release_lane.wait(2)

        lane = threading.Thread(target=occupy_photos_lane)
        lane.start()
        assert lane_acquired.wait(1)

        def enable_analysis() -> None:
            try:
                coordinator.update_config(
                    expected_revision=coordinator.session.revision,
                    config=SessionConfig(photo_count=1, inference_concurrency=1, auto_analyze=True),
                )
            except BaseException as error:
                update_errors.append(error)

        update = threading.Thread(target=enable_analysis)
        update.start()
        closer: threading.Thread | None = None
        try:
            deadline = time.monotonic() + 1
            while coordinator.current_items()[0].state != "preparing" and time.monotonic() < deadline:
                time.sleep(0.01)
            assert coordinator.current_items()[0].state == "preparing"
            coordinator.stop(expected_revision=coordinator.session.revision)
            closer = threading.Thread(target=lambda: (coordinator.close(), close_finished.set()))
            closer.start()
            assert not close_finished.wait(0.15)
        finally:
            release_lane.set()
            lane.join(timeout=2)
            update.join(timeout=2)
            if closer is not None:
                closer.join(timeout=2)
                assert not closer.is_alive()
            assert not lane.is_alive()
            assert not update.is_alive()
            assert close_finished.is_set()
            assert update_errors == []


def test_each_result_becomes_ready_before_other_analysis_finishes() -> None:
    second_release = threading.Event()
    events: list[dict[str, object]] = []

    def analyze(prepared: object) -> AnalysisOutcome:
        if prepared == "photo-1":
            second_release.wait(1)
        return AnalysisOutcome(source_run_id=str(uuid.uuid4()), manifest_path=Path(f"/runs/{prepared}/manifest.json"))

    with tempfile.TemporaryDirectory() as temporary_directory:
        coordinator = QueueCoordinator(
            session=_session(concurrency=2, photo_count=2),
            session_dir=Path(temporary_directory) / "session",
            discover=lambda count: [f"photo-{index}" for index in range(count)],
            prepare=lambda candidate: candidate,
            analyze=analyze,
            emit=events.append,
        )
        coordinator.start()
        try:
            deadline = time.monotonic() + 1
            while not any(event.get("state") == "ready" for event in events) and time.monotonic() < deadline:
                time.sleep(0.01)
            assert any(event.get("state") == "ready" for event in events)
            assert any(item.state == "analyzing" for item in coordinator.session.items)
        finally:
            second_release.set()
            coordinator.wait()


def test_failed_outcome_retains_evidence_and_does_not_block_healthy_item() -> None:
    run_ids = {
        "bad-photo": str(uuid.uuid4()),
        "good-photo": str(uuid.uuid4()),
    }
    events: list[dict[str, object]] = []

    def analyze(prepared: object) -> AnalysisOutcome:
        photo = str(prepared)
        return AnalysisOutcome(
            source_run_id=run_ids[photo],
            manifest_path=Path(f"/runs/{photo}/manifest.json"),
            state="failed" if photo == "bad-photo" else "ready",
        )

    with tempfile.TemporaryDirectory() as temporary_directory:
        coordinator = QueueCoordinator(
            session=_session(concurrency=1, photo_count=2),
            session_dir=Path(temporary_directory) / "session",
            discover=lambda count: ["bad-photo", "good-photo"][:count],
            prepare=lambda candidate: candidate,
            analyze=analyze,
            emit=events.append,
        )
        coordinator.start()
        coordinator.wait()

        failed, ready = coordinator.current_items()
        assert (failed.state, failed.source_run_id) == ("failed", run_ids["bad-photo"])
        assert (ready.state, ready.source_run_id) == ("ready", run_ids["good-photo"])
        failed_events = [
            event
            for event in events
            if event.get("type") == "queue_item"
            and event.get("item_id") == failed.item_id
            and event.get("state") == "failed"
        ]
        assert failed_events[-1]["manifest"] == "/runs/bad-photo/manifest.json"
        assert any(
            event.get("type") == "queue_session"
            and event.get("attention") == 1
            and event.get("ready") == 1
            for event in events
        )


def test_invalid_analysis_outcome_state_fails_closed_without_evidence() -> None:
    run_id = str(uuid.uuid4())
    manifest_path = Path("/runs/invalid/manifest.json")
    events: list[dict[str, object]] = []

    with tempfile.TemporaryDirectory() as temporary_directory:
        coordinator = QueueCoordinator(
            session=_session(concurrency=1, photo_count=1),
            session_dir=Path(temporary_directory) / "session",
            discover=lambda count: ["photo-0"][:count],
            prepare=lambda candidate: candidate,
            analyze=lambda prepared: AnalysisOutcome(
                source_run_id=run_id,
                manifest_path=manifest_path,
                state="unexpected",  # type: ignore[arg-type]
            ),
            emit=events.append,
        )
        coordinator.start()
        coordinator.wait()

        item = coordinator.current_items()[0]
        assert item.state == "failed"
        assert item.source_run_id is None
        assert not any(event.get("manifest") == str(manifest_path) for event in events)


def test_pause_is_checkpointed_and_does_not_start_more_work() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        session_dir = Path(temporary_directory) / "session"
        coordinator = QueueCoordinator(
            session=_session(concurrency=1, photo_count=1),
            session_dir=session_dir,
            discover=lambda count: ["photo-0"],
            prepare=lambda candidate: candidate,
            analyze=lambda prepared: AnalysisOutcome(
                source_run_id=str(uuid.uuid4()),
                manifest_path=Path(f"/runs/{prepared}/manifest.json"),
            ),
            emit=lambda event: None,
        )

        coordinator.pause()

        assert coordinator.session.state == "paused"
        assert load_session(session_dir).state == "paused"


def test_pause_stays_responsive_while_discovery_is_blocked() -> None:
    discovery_started = threading.Event()
    release_discovery = threading.Event()
    discovery_calls = 0

    def discover(count: int) -> list[str]:
        nonlocal discovery_calls
        discovery_calls += 1
        discovery_started.set()
        assert release_discovery.wait(2)
        return ["photo-0"][:count]

    with tempfile.TemporaryDirectory() as temporary_directory:
        coordinator = QueueCoordinator(
            session=_session(concurrency=1, photo_count=1),
            session_dir=Path(temporary_directory) / "session",
            discover=discover,
            prepare=lambda candidate: candidate,
            analyze=lambda prepared: AnalysisOutcome(
                source_run_id=str(uuid.uuid4()),
                manifest_path=Path(f"/runs/{prepared}/manifest.json"),
            ),
            emit=lambda event: None,
        )
        start = threading.Thread(target=coordinator.start)
        start.start()
        try:
            assert discovery_started.wait(1)
            started = time.monotonic()
            coordinator.pause(expected_revision=coordinator.session.revision)
            assert time.monotonic() - started < 0.2
            assert coordinator.session.state == "paused"
            assert coordinator.current_items() == []
        finally:
            release_discovery.set()
            start.join(timeout=2)
            assert not start.is_alive()
            coordinator.resume(expected_revision=coordinator.session.revision)
            coordinator.wait()
            assert [item.state for item in coordinator.current_items()] == ["ready"]
            assert discovery_calls == 1
            coordinator.close()


def test_pause_stays_responsive_while_discard_refills_a_blocked_discovery() -> None:
    discovery_started = threading.Event()
    release_discovery = threading.Event()
    discoveries = iter([["photo-0"], ["photo-1"]])
    with tempfile.TemporaryDirectory() as temporary_directory:
        def discover(count: int) -> list[str]:
            selected = next(discoveries)
            if selected == ["photo-1"]:
                discovery_started.set()
                assert release_discovery.wait(2)
            return selected[:count]

        coordinator = QueueCoordinator(
            session=_session(concurrency=1, photo_count=1),
            session_dir=Path(temporary_directory) / "session",
            discover=discover,
            prepare=lambda candidate: candidate,
            analyze=lambda prepared: AnalysisOutcome(
                source_run_id=str(uuid.uuid4()),
                manifest_path=Path(f"/runs/{prepared}/manifest.json"),
            ),
            emit=lambda event: None,
        )
        coordinator.start()
        coordinator.wait()
        first = coordinator.current_items()[0]
        try:
            discard = threading.Thread(
                target=coordinator.discard,
                kwargs={
                    "item_id": first.item_id,
                    "revision": first.revision,
                    "decision_id": str(uuid.uuid4()),
                },
            )
            discard.start()
            assert discovery_started.wait(1)
            started = time.monotonic()
            coordinator.pause(expected_revision=coordinator.session.revision)
            assert time.monotonic() - started < 0.2
            assert coordinator.item(first.item_id, first.revision).state == "discarded"
        finally:
            release_discovery.set()
            discard.join(timeout=2)
            assert not discard.is_alive()
            coordinator.close()


def test_auto_analyze_off_keeps_discovered_photos_queued_until_enabled() -> None:
    session = QueueSession.new(
        session_id=str(uuid.uuid4()),
        config=SessionConfig(
            photo_count=1,
            inference_concurrency=1,
            auto_analyze=False,
        ),
    )
    analyze_calls: list[object] = []
    with tempfile.TemporaryDirectory() as temporary_directory:
        coordinator = QueueCoordinator(
            session=session,
            session_dir=Path(temporary_directory) / "session",
            discover=lambda count: ["photo-0"],
            prepare=lambda candidate: candidate,
            analyze=lambda prepared: analyze_calls.append(prepared) or AnalysisOutcome(
                source_run_id=str(uuid.uuid4()),
                manifest_path=Path(f"/runs/{prepared}/manifest.json"),
            ),
            emit=lambda event: None,
        )

        coordinator.start()
        coordinator.wait()
        assert [item.state for item in coordinator.current_items()] == ["discovered"]
        assert analyze_calls == []

        coordinator.update_config(
            expected_revision=coordinator.session.revision,
            config=SessionConfig(photo_count=1, inference_concurrency=1, auto_analyze=True),
        )
        coordinator.wait()

        assert analyze_calls == ["photo-0"]
        assert [item.state for item in coordinator.current_items()] == ["ready"]


def test_shrinking_the_window_keeps_current_cards_and_delays_replenishment() -> None:
    discovered = iter(["photo-0", "photo-1", "photo-2"])
    with tempfile.TemporaryDirectory() as temporary_directory:
        coordinator = QueueCoordinator(
            session=_session(concurrency=1, photo_count=2),
            session_dir=Path(temporary_directory) / "session",
            discover=lambda count: [next(discovered) for _ in range(count)],
            prepare=lambda candidate: candidate,
            analyze=lambda prepared: AnalysisOutcome(
                source_run_id=str(uuid.uuid4()),
                manifest_path=Path(f"/runs/{prepared}/manifest.json"),
            ),
            emit=lambda event: None,
        )
        coordinator.start()
        coordinator.wait()
        originals = coordinator.current_items()

        coordinator.update_config(
            expected_revision=coordinator.session.revision,
            config=SessionConfig(photo_count=1, inference_concurrency=1),
        )
        assert len(coordinator.current_items()) == 2
        assert load_session(Path(temporary_directory) / "session").config.photo_count == 1

        coordinator.discard(
            item_id=originals[0].item_id,
            revision=originals[0].revision,
            decision_id=str(uuid.uuid4()),
        )
        coordinator.wait()
        assert len(coordinator.current_items()) == 2

        coordinator.discard(
            item_id=originals[1].item_id,
            revision=originals[1].revision,
            decision_id=str(uuid.uuid4()),
        )
        coordinator.wait()
        assert [item.state for item in coordinator.current_items()].count("ready") == 1


def test_discard_replenishes_the_active_window_without_repeating_a_decision() -> None:
    discovered = iter(["photo-0", "photo-1"])
    events: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory() as temporary_directory:
        coordinator = QueueCoordinator(
            session=_session(concurrency=1, photo_count=1),
            session_dir=Path(temporary_directory) / "session",
            discover=lambda count: [next(discovered) for _ in range(count)],
            prepare=lambda candidate: candidate,
            analyze=lambda prepared: AnalysisOutcome(
                source_run_id=str(uuid.uuid4()),
                manifest_path=Path(f"/runs/{prepared}/manifest.json"),
            ),
            emit=events.append,
        )
        coordinator.start()
        coordinator.wait()
        first = coordinator.current_items()[0]
        decision_id = str(uuid.uuid4())

        coordinator.discard(item_id=first.item_id, revision=first.revision, decision_id=decision_id)
        coordinator.discard(item_id=first.item_id, revision=first.revision, decision_id=decision_id)
        coordinator.wait()

        current = coordinator.current_items()
        assert any(item.item_id == first.item_id and item.state == "discarded" for item in current)
        assert len([item for item in current if item.state not in {"discarded", "verified"}]) == 1
        assert len(coordinator.session.decisions) == 1


def test_failed_item_can_be_discarded_and_replenished() -> None:
    discovered = iter(["bad-photo", "good-photo"])

    def analyze(prepared: object) -> AnalysisOutcome:
        if prepared == "bad-photo":
            raise ValueError("analysis failed")
        return AnalysisOutcome(
            source_run_id=str(uuid.uuid4()),
            manifest_path=Path(f"/runs/{prepared}/manifest.json"),
        )

    with tempfile.TemporaryDirectory() as temporary_directory:
        coordinator = QueueCoordinator(
            session=_session(concurrency=1, photo_count=1),
            session_dir=Path(temporary_directory) / "session",
            discover=lambda count: [next(discovered) for _ in range(count)],
            prepare=lambda candidate: candidate,
            analyze=analyze,
            emit=lambda event: None,
        )
        coordinator.start()
        coordinator.wait()
        failed = coordinator.current_items()[0]
        assert failed.state == "failed"

        coordinator.discard(
            item_id=failed.item_id,
            revision=failed.revision,
            decision_id=str(uuid.uuid4()),
        )
        coordinator.wait()

        current = coordinator.current_items()
        assert coordinator.item(failed.item_id, failed.revision).state == "discarded"
        assert len([item for item in current if item.state == "ready"]) == 1


def test_failed_item_can_be_rescanned_but_not_persisted() -> None:
    attempts = 0

    def analyze(prepared: object) -> AnalysisOutcome:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ValueError("analysis failed")
        return AnalysisOutcome(
            source_run_id=str(uuid.uuid4()),
            manifest_path=Path(f"/runs/{prepared}/manifest.json"),
        )

    with tempfile.TemporaryDirectory() as temporary_directory:
        coordinator = QueueCoordinator(
            session=_session(concurrency=1, photo_count=1),
            session_dir=Path(temporary_directory) / "session",
            discover=lambda count: ["photo-0"][:count],
            prepare=lambda candidate: candidate,
            analyze=analyze,
            emit=lambda event: None,
        )
        coordinator.start()
        coordinator.wait()
        failed = coordinator.current_items()[0]

        coordinator.rescan(
            item_id=failed.item_id,
            revision=failed.revision,
            decision_id=str(uuid.uuid4()),
        )
        coordinator.wait()

        assert [(item.revision, item.state) for item in coordinator.session.items] == [
            (1, "failed"),
            (2, "ready"),
        ]


def test_discarding_an_active_analysis_does_not_resurrect_the_item() -> None:
    started = threading.Event()
    release = threading.Event()

    def analyze(prepared: object) -> AnalysisOutcome:
        started.set()
        release.wait(1)
        return AnalysisOutcome(
            source_run_id=str(uuid.uuid4()),
            manifest_path=Path(f"/runs/{prepared}/manifest.json"),
        )

    with tempfile.TemporaryDirectory() as temporary_directory:
        coordinator = QueueCoordinator(
            session=_session(concurrency=1, photo_count=1),
            session_dir=Path(temporary_directory) / "session",
            discover=lambda count: ["photo-0"][:count],
            prepare=lambda candidate: candidate,
            analyze=analyze,
            emit=lambda event: None,
        )
        coordinator.start()
        assert started.wait(1)
        item = coordinator.current_items()[0]
        try:
            coordinator.discard(
                item_id=item.item_id,
                revision=item.revision,
                decision_id=str(uuid.uuid4()),
            )
            release.set()
            coordinator.wait()

            assert coordinator.item(item.item_id, item.revision).state == "discarded"
        finally:
            release.set()
            coordinator.close()


def test_persist_is_serialized_and_verified_only_after_the_callback_succeeds() -> None:
    events: list[dict[str, object]] = []
    states_during_callback: list[str] = []
    with tempfile.TemporaryDirectory() as temporary_directory:
        coordinator = QueueCoordinator(
            session=_session(concurrency=1, photo_count=1),
            session_dir=Path(temporary_directory) / "session",
            discover=lambda count: ["photo-0"],
            prepare=lambda candidate: candidate,
            analyze=lambda prepared: AnalysisOutcome(
                source_run_id=str(uuid.uuid4()),
                manifest_path=Path(f"/runs/{prepared}/manifest.json"),
            ),
            emit=events.append,
        )
        coordinator.start()
        coordinator.wait()
        item = coordinator.current_items()[0]

        coordinator.persist(
            item_id=item.item_id,
            revision=item.revision,
            decision_id=str(uuid.uuid4()),
            operation=lambda manifest: states_during_callback.append(
                coordinator.item(item.item_id, item.revision).state
            ) or True,
        )
        coordinator.wait()

        assert states_during_callback == ["saving"]
        assert coordinator.item(item.item_id, item.revision).state == "verified"
        assert any(event.get("state") == "save_queued" for event in events)
        assert any(event.get("state") == "saving" for event in events)
        assert any(event.get("state") == "verified" for event in events)


@pytest.mark.parametrize(
    ("operation_result", "terminal_state"),
    [(True, "verified"), (False, "uncertain")],
)
def test_persist_events_include_matching_decision_id_for_write_states(
    operation_result: bool,
    terminal_state: str,
) -> None:
    events: list[dict[str, object]] = []
    decision_id = str(uuid.uuid4())
    with tempfile.TemporaryDirectory() as temporary_directory:
        coordinator = QueueCoordinator(
            session=_session(concurrency=1, photo_count=1),
            session_dir=Path(temporary_directory) / "session",
            discover=lambda count: ["photo-0"],
            prepare=lambda candidate: candidate,
            analyze=lambda prepared: AnalysisOutcome(
                source_run_id=str(uuid.uuid4()),
                manifest_path=Path(f"/runs/{prepared}/manifest.json"),
            ),
            emit=events.append,
        )
        coordinator.start()
        coordinator.wait()
        item = coordinator.current_items()[0]

        coordinator.persist(
            item_id=item.item_id,
            revision=item.revision,
            decision_id=decision_id,
            operation=lambda manifest: operation_result,
        )
        coordinator.wait()

        item_events = [
            event
            for event in events
            if event.get("type") == "queue_item"
            and event.get("item_id") == item.item_id
            and event.get("revision") == item.revision
        ]
        assert all("decision_id" not in event for event in item_events if event.get("state") == "ready")
        write_events = [
            event for event in item_events if event.get("state") in {"save_queued", "saving", terminal_state}
        ]
        assert [event["state"] for event in write_events] == ["save_queued", "saving", terminal_state]
        assert all(event.get("decision_id") == decision_id for event in write_events)
        assert all(_safe_service_event(event)["decision_id"] == decision_id for event in write_events)


def test_recovered_failed_write_uses_only_matching_persist_revision_decision() -> None:
    session = _session(concurrency=1, photo_count=2)
    matching = session.enqueue(item_id=str(uuid.uuid4()), state="ready")
    matching_decision_id = str(uuid.uuid4())
    session.record_decision(
        decision_id=matching_decision_id,
        item_id=matching.item_id,
        revision=matching.revision,
        action="persist",
    )
    session.checkpoint_item(item_id=matching.item_id, revision=matching.revision, state="failed")

    prior_revision = session.enqueue(item_id=str(uuid.uuid4()), state="ready")
    session.record_decision(
        decision_id=str(uuid.uuid4()),
        item_id=prior_revision.item_id,
        revision=prior_revision.revision,
        action="persist",
    )
    session.checkpoint_item(item_id=prior_revision.item_id, revision=prior_revision.revision, state="uncertain")
    session.record_decision(
        decision_id=str(uuid.uuid4()),
        item_id=prior_revision.item_id,
        revision=prior_revision.revision,
        action="rescan",
    )
    current_revision = session.current_item(prior_revision.item_id)
    session.checkpoint_item(
        item_id=current_revision.item_id,
        revision=current_revision.revision,
        state="failed",
    )

    events: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory() as temporary_directory:
        coordinator = QueueCoordinator(
            session=session,
            session_dir=Path(temporary_directory) / "session",
            discover=lambda count: [],
            prepare=lambda candidate: candidate,
            analyze=lambda prepared: AnalysisOutcome(
                source_run_id=str(uuid.uuid4()),
                manifest_path=Path(f"/runs/{prepared}/manifest.json"),
            ),
            emit=events.append,
        )
        coordinator.emit_recovered_items()
        coordinator.close()

    failed_events = {
        (event["item_id"], event["revision"]): event
        for event in events
        if event.get("type") == "queue_item" and event.get("state") == "failed"
    }
    assert failed_events[(matching.item_id, matching.revision)]["decision_id"] == matching_decision_id
    assert "decision_id" not in failed_events[(current_revision.item_id, current_revision.revision)]


def test_multiple_persists_are_queued_and_photos_writes_never_overlap() -> None:
    session = _session(concurrency=2, photo_count=2)
    first = session.enqueue(item_id=str(uuid.uuid4()), state="ready")
    second = session.enqueue(item_id=str(uuid.uuid4()), state="ready")
    first_started = threading.Event()
    release_first = threading.Event()
    order: list[str] = []
    active_writes = 0
    maximum_active_writes = 0
    lock = threading.Lock()

    def operation(label: str):
        def run(_: Path) -> bool:
            nonlocal active_writes, maximum_active_writes
            with lock:
                active_writes += 1
                maximum_active_writes = max(maximum_active_writes, active_writes)
                order.append(f"{label}:start")
            if label == "first":
                first_started.set()
                assert release_first.wait(1)
            with lock:
                order.append(f"{label}:end")
                active_writes -= 1
            return True

        return run

    events: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory() as temporary_directory:
        coordinator = QueueCoordinator(
            session=session,
            session_dir=Path(temporary_directory) / "session",
            discover=lambda count: [],
            prepare=lambda candidate: candidate,
            analyze=lambda prepared: AnalysisOutcome(
                source_run_id=str(uuid.uuid4()),
                manifest_path=Path(f"/runs/{prepared}/manifest.json"),
            ),
            emit=events.append,
            restored_manifests={
                (first.item_id, first.revision): Path("/runs/first/manifest.json"),
                (second.item_id, second.revision): Path("/runs/second/manifest.json"),
            },
        )
        first_thread = threading.Thread(
            target=lambda: coordinator.persist(
                item_id=first.item_id,
                revision=first.revision,
                decision_id=str(uuid.uuid4()),
                operation=operation("first"),
            )
        )
        second_thread = threading.Thread(
            target=lambda: coordinator.persist(
                item_id=second.item_id,
                revision=second.revision,
                decision_id=str(uuid.uuid4()),
                operation=operation("second"),
            )
        )
        first_thread.start()
        assert first_started.wait(1)
        second_thread.start()
        deadline = time.monotonic() + 1
        while coordinator.waiting_photo_writes < 2 and time.monotonic() < deadline:
            time.sleep(0.001)
        assert coordinator.waiting_photo_writes == 2
        assert coordinator.item(second.item_id, second.revision).state == "save_queued"

        release_first.set()
        first_thread.join(1)
        second_thread.join(1)
        coordinator.close()

        assert order == ["first:start", "first:end", "second:start", "second:end"]
        assert maximum_active_writes == 1
        assert coordinator.item(first.item_id, first.revision).state == "verified"
        assert coordinator.item(second.item_id, second.revision).state == "verified"
        item_states = [
            (event.get("item_id"), event.get("state"))
            for event in events
            if event.get("type") == "queue_item"
        ]
        assert (second.item_id, "save_queued") in item_states
        assert item_states.index((first.item_id, "saving")) < item_states.index((second.item_id, "saving"))
        session_events = [event for event in events if event.get("type") == "queue_session"]
        assert any(event.get("save_queued") == 1 and event.get("saving") == 1 for event in session_events)


def test_waiting_write_precedes_the_next_photo_preparation() -> None:
    session = _session(concurrency=2, photo_count=3)
    ready = session.enqueue(item_id=str(uuid.uuid4()), state="ready")
    first = session.enqueue(item_id=str(uuid.uuid4()), state="discovered")
    second = session.enqueue(item_id=str(uuid.uuid4()), state="discovered")
    first_prepare_started = threading.Event()
    release_first_prepare = threading.Event()
    events: list[str] = []

    def prepare(candidate: object) -> object:
        events.append(f"prepare:{candidate}:start")
        if candidate == "photo-1":
            first_prepare_started.set()
            assert release_first_prepare.wait(1)
        events.append(f"prepare:{candidate}:end")
        return candidate

    def analyze(prepared: object) -> AnalysisOutcome:
        return AnalysisOutcome(
            source_run_id=str(uuid.uuid4()),
            manifest_path=Path(f"/runs/{prepared}/manifest.json"),
        )

    with tempfile.TemporaryDirectory() as temporary_directory:
        coordinator = QueueCoordinator(
            session=session,
            session_dir=Path(temporary_directory) / "session",
            discover=lambda count: [],
            prepare=prepare,
            analyze=analyze,
            emit=lambda event: None,
            restored_candidates={
                (first.item_id, first.revision): "photo-1",
                (second.item_id, second.revision): "photo-2",
            },
            restored_manifests={
                (ready.item_id, ready.revision): Path("/runs/ready/manifest.json"),
            },
        )
        start_thread = threading.Thread(target=coordinator.start)
        persist_thread = threading.Thread(
            target=lambda: coordinator.persist(
                item_id=ready.item_id,
                revision=ready.revision,
                decision_id=str(uuid.uuid4()),
                operation=lambda manifest: events.append("write") or True,
            )
        )
        start_thread.start()
        assert first_prepare_started.wait(1)
        persist_thread.start()
        deadline = time.monotonic() + 1
        while coordinator.waiting_photo_writes == 0 and time.monotonic() < deadline:
            time.sleep(0.001)
        assert coordinator.waiting_photo_writes == 1

        release_first_prepare.set()
        start_thread.join(1)
        persist_thread.join(1)
        coordinator.wait()

        assert "prepare:photo-2:start" in events, (events, coordinator.current_items())
        assert events.index("write") < events.index("prepare:photo-2:start")


def test_persist_defers_rescheduling_until_an_active_schedule_finishes() -> None:
    session = _session(concurrency=2, photo_count=3)
    ready = session.enqueue(item_id=str(uuid.uuid4()), state="ready")
    first = session.enqueue(item_id=str(uuid.uuid4()), state="discovered")
    second = session.enqueue(item_id=str(uuid.uuid4()), state="discovered")
    first_prepare_started = threading.Event()
    release_first_prepare = threading.Event()
    second_restored = threading.Event()
    release_schedule = threading.Event()
    second_prepare_started = threading.Event()
    release_second_prepare = threading.Event()
    wait_finished = threading.Event()

    def prepare(candidate: object) -> object:
        if candidate == "photo-1":
            first_prepare_started.set()
            assert release_first_prepare.wait(1)
        if candidate == "photo-2":
            second_prepare_started.set()
            assert release_second_prepare.wait(1)
        return candidate

    def analyze(prepared: object) -> AnalysisOutcome:
        return AnalysisOutcome(
            source_run_id=str(uuid.uuid4()),
            manifest_path=Path(f"/runs/{prepared}/manifest.json"),
        )

    with tempfile.TemporaryDirectory() as temporary_directory:
        coordinator = QueueCoordinator(
            session=session,
            session_dir=Path(temporary_directory) / "session",
            discover=lambda count: [],
            prepare=prepare,
            analyze=analyze,
            emit=lambda event: None,
            restored_candidates={
                (first.item_id, first.revision): "photo-1",
                (second.item_id, second.revision): "photo-2",
            },
            restored_manifests={
                (ready.item_id, ready.revision): Path("/runs/ready/manifest.json"),
            },
        )
        original_restore = coordinator._restore_queued_if_current

        def restore_then_hold(item_id: str, revision: int) -> None:
            original_restore(item_id, revision)
            if item_id == second.item_id:
                second_restored.set()
                assert release_schedule.wait(1)

        coordinator._restore_queued_if_current = restore_then_hold  # type: ignore[method-assign]
        start_thread = threading.Thread(target=coordinator.start)
        persist_thread = threading.Thread(
            target=lambda: coordinator.persist(
                item_id=ready.item_id,
                revision=ready.revision,
                decision_id=str(uuid.uuid4()),
                operation=lambda manifest: True,
            )
        )
        start_thread.start()
        assert first_prepare_started.wait(1)
        persist_thread.start()
        deadline = time.monotonic() + 1
        while coordinator.waiting_photo_writes == 0 and time.monotonic() < deadline:
            time.sleep(0.001)
        assert coordinator.waiting_photo_writes == 1

        release_first_prepare.set()
        assert second_restored.wait(1)
        persist_thread.join(1)
        assert not persist_thread.is_alive()
        reschedule_was_deferred = not second_prepare_started.is_set()
        wait_thread = threading.Thread(target=lambda: (coordinator.wait(), wait_finished.set()))
        wait_thread.start()
        assert not wait_finished.wait(0.05)

        release_schedule.set()
        assert second_prepare_started.wait(1)
        assert not wait_finished.wait(0.05)
        release_second_prepare.set()
        start_thread.join(1)
        coordinator.wait()
        wait_thread.join(1)

        assert reschedule_was_deferred
        assert second_prepare_started.is_set()
        assert wait_finished.is_set()
        assert coordinator.item(second.item_id, second.revision).state == "ready"


def test_immediate_analysis_at_queue_limit_uses_no_recursive_scheduler_calls() -> None:
    class ImmediateExecutor:
        def submit(self, operation: object, *args: object) -> Future[AnalysisOutcome]:
            future: Future[AnalysisOutcome] = Future()
            try:
                future.set_result(operation(*args))  # type: ignore[operator]
            except BaseException as error:
                future.set_exception(error)
            return future

        def shutdown(self, **_: object) -> None:
            return None

    maximum_scheduler_depth = 0

    def prepare(candidate: object) -> object:
        nonlocal maximum_scheduler_depth
        frame = sys._getframe()
        depth = 0
        while frame is not None:
            if frame.f_code.co_name == "_schedule_available":
                depth += 1
            frame = frame.f_back
        maximum_scheduler_depth = max(maximum_scheduler_depth, depth)
        return candidate

    with tempfile.TemporaryDirectory() as temporary_directory:
        coordinator = QueueCoordinator(
            session=_session(concurrency=1, photo_count=50),
            session_dir=Path(temporary_directory) / "session",
            discover=lambda count: [f"photo-{index}" for index in range(count)],
            prepare=prepare,
            analyze=lambda candidate: AnalysisOutcome(
                source_run_id=str(uuid.uuid4()),
                manifest_path=Path(f"/runs/{candidate}/manifest.json"),
            ),
            emit=lambda event: None,
        )
        coordinator._executor = ImmediateExecutor()  # type: ignore[assignment]
        coordinator.start()
        coordinator.wait()

        assert len(coordinator.current_items()) == 50
        assert all(item.state == "ready" for item in coordinator.current_items())
        assert maximum_scheduler_depth == 1
