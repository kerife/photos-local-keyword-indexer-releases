"""Bounded continuous-review scheduling without photo content in IPC state."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor, wait
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import Condition, Lock, RLock
from typing import Any, Iterator, Literal

from .queue_session import QueueItem, QueueSession, SessionConfig, TERMINAL_ITEM_STATES, write_session


@dataclass(frozen=True, slots=True)
class AnalysisOutcome:
    source_run_id: str
    manifest_path: Path
    state: Literal["ready", "failed"] = "ready"


@dataclass(frozen=True, slots=True)
class PersistenceOutcome:
    verified: bool
    reviewed_run_id: str | None = None
    manifest_path: Path | None = None


_PERSIST_DECISION_EVENT_STATES = frozenset({
    "save_queued", "saving", "verified", "uncertain", "failed",
})


class _PhotosLane:
    """Exclusive Photos access that defers reads while a write is pending."""

    def __init__(self) -> None:
        self._condition = Condition()
        self._active = False
        self._waiting_writers = 0

    @property
    def waiting_writers(self) -> int:
        with self._condition:
            return self._waiting_writers

    @contextmanager
    def read(self) -> Iterator[bool]:
        acquired = False
        with self._condition:
            while self._active and not self._waiting_writers:
                self._condition.wait()
            if not self._waiting_writers:
                self._active = True
                acquired = True
        try:
            yield acquired
        finally:
            if acquired:
                with self._condition:
                    self._active = False
                    self._condition.notify_all()

    @contextmanager
    def write_priority(self) -> Iterator[None]:
        with self._condition:
            self._waiting_writers += 1
            self._condition.notify_all()
        try:
            yield
        finally:
            with self._condition:
                self._waiting_writers -= 1
                self._condition.notify_all()

    @contextmanager
    def write(self) -> Iterator[None]:
        with self._condition:
            while self._active:
                self._condition.wait()
            self._active = True
        try:
            yield
        finally:
            with self._condition:
                self._active = False
                self._condition.notify_all()


class QueueCoordinator:
    """Coordinate a serialized Photos lane and a bounded inference pool.

    Callers provide the product-specific discovery, preparation and analysis
    functions. The coordinator owns only scheduling, private checkpoints and
    metadata-only events, keeping images and proposals outside the session
    ledger and JSONL protocol.
    """

    def __init__(
        self,
        *,
        session: QueueSession,
        session_dir: Path,
        discover: Callable[[int], list[Any]],
        prepare: Callable[[Any], Any],
        analyze: Callable[[Any], AnalysisOutcome],
        emit: Callable[[dict[str, object]], None],
        restored_candidates: Mapping[tuple[str, int], Any] | None = None,
        restored_manifests: Mapping[tuple[str, int], Path] | None = None,
    ) -> None:
        self.session = session
        self.session_dir = Path(session_dir)
        self._discover = discover
        self._prepare = prepare
        self._analyze = analyze
        self._emit = emit
        self._state_lock = RLock()
        self._idle_condition = Condition(self._state_lock)
        self._fill_lock = Lock()
        self._photos_lane = _PhotosLane()
        self._executor = ThreadPoolExecutor(
            max_workers=session.config.inference_concurrency,
            thread_name_prefix="photos-indexer-inference",
        )
        # Writes are accepted from multiple queue-control requests but always
        # execute through one bounded worker.  The Photos lane remains the
        # final guard, so analysis can continue while saves wait their turn,
        # without allowing concurrent PhotoKit/PhotoScript mutations.
        self._write_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="photos-indexer-write",
        )
        self._retired_executors: list[ThreadPoolExecutor] = []
        self._futures: dict[Future[AnalysisOutcome], tuple[str, int]] = {}
        self._completion_scheduling = 0
        self._scheduling_operations = 0
        self._reschedule_requested = False
        self._candidates = dict(restored_candidates or {})
        self._deferred_candidates: list[Any] = []
        self._manifests = {key: Path(path) for key, path in (restored_manifests or {}).items()}

    def start(self) -> None:
        with self._state_lock:
            if self.session.state == "paused":
                self._checkpoint_session()
                return
            if self.session.state != "running":
                self.session.set_state(expected_revision=self.session.revision, state="running")
            self._checkpoint_session()
        self._fill_slots()
        self._schedule_available()

    @property
    def waiting_photo_writes(self) -> int:
        return self._photos_lane.waiting_writers

    def _fill_slots(self) -> None:
        # Separate from `_state_lock`: a slow Photos discovery must not make
        # controls wait, while concurrent controls still cannot take the same
        # deferred candidate or discover duplicate replacements.
        with self._fill_lock:
            with self._state_lock:
                if self.session.state != "running":
                    return
                current = self._current_items()
                active = [item for item in current if item.state not in TERMINAL_ITEM_STATES]
                missing = max(0, self.session.config.photo_count - len(active))
                deferred = list(self._deferred_candidates[:missing])
            if missing == 0:
                return
            current_ids = {item.item_id for item in current}
            discovered: list[Any] = []
            if len(deferred) < missing:
                with self._photos_lane.read() as acquired:
                    if acquired:
                        discovered = self._discover(missing - len(deferred))
            candidates = deferred + discovered
            with self._state_lock:
                # A pause can arrive while Photos is discovering candidates.
                # Do not admit new cards after that control was accepted.
                if self.session.state != "running":
                    self._deferred_candidates.extend(discovered)
                    return
                current_ids.update(item.item_id for item in self._current_items())
                consumed_deferred = 0
                for index, candidate in enumerate(candidates):
                    if sum(
                        item.state not in TERMINAL_ITEM_STATES
                        for item in self._current_items()
                    ) >= self.session.config.photo_count:
                        self._deferred_candidates.extend(candidates[max(index, len(deferred)):])
                        break
                    item_id = str(uuid.uuid4())
                    while item_id in current_ids:
                        item_id = str(uuid.uuid4())
                    item = self.session.enqueue(item_id=item_id, state="discovered")
                    self._candidates[(item.item_id, item.revision)] = candidate
                    current_ids.add(item_id)
                    self._checkpoint_item(item)
                    if index < len(deferred):
                        consumed_deferred += 1
                if consumed_deferred:
                    del self._deferred_candidates[:consumed_deferred]

    def pause(self, *, expected_revision: int | None = None) -> None:
        with self._state_lock:
            self.session.set_state(
                expected_revision=self.session.revision if expected_revision is None else expected_revision,
                state="paused",
            )
            self._checkpoint_session()

    def resume(self, *, expected_revision: int | None = None) -> None:
        with self._state_lock:
            self.session.set_state(
                expected_revision=self.session.revision if expected_revision is None else expected_revision,
                state="running",
            )
            self._checkpoint_session()
        self._fill_slots()
        self._schedule_available()

    def update_config(self, *, expected_revision: int, config: SessionConfig) -> None:
        with self._state_lock:
            previous_concurrency = self.session.config.inference_concurrency
            self.session.update_config(expected_revision=expected_revision, config=config)
            if config.inference_concurrency != previous_concurrency:
                previous_executor = self._executor
                self._executor = ThreadPoolExecutor(
                    max_workers=config.inference_concurrency,
                    thread_name_prefix="photos-indexer-inference",
                )
                previous_executor.shutdown(wait=False, cancel_futures=False)
                self._retired_executors.append(previous_executor)
            self._checkpoint_session()
        self._fill_slots()
        self._schedule_available()

    def stop(self, *, expected_revision: int) -> None:
        with self._state_lock:
            self.session.set_state(expected_revision=expected_revision, state="stopped")
            self._checkpoint_session()

    def emit_session(self) -> None:
        with self._state_lock:
            self._checkpoint_session()

    def emit_recovered_items(self) -> None:
        """Rebuild the UI snapshot from persisted, content-free queue state."""
        with self._state_lock:
            for item in self._current_items():
                self._checkpoint_item(
                    item,
                    manifest_path=self._manifests.get((item.item_id, item.revision)),
                )

    def current_items(self) -> list[QueueItem]:
        with self._state_lock:
            return list(self._current_items())

    def item(self, item_id: str, revision: int) -> QueueItem:
        with self._state_lock:
            return self._item(item_id, revision)

    def candidate(self, item_id: str, revision: int) -> Any:
        with self._state_lock:
            candidate = self._candidates.get((item_id, revision))
            if candidate is None:
                raise ValueError("photo candidate is unavailable")
            return candidate

    def persist(
        self,
        *,
        item_id: str,
        revision: int,
        decision_id: str,
        operation: Callable[[Path], bool | PersistenceOutcome],
    ) -> None:
        """Enqueue one idempotent write on the serialized Photos lane.

        The caller waits for the queued operation's terminal result so the
        IPC completion still means that read-back/receipts finished.  The
        dedicated executor prevents concurrent persist requests from creating
        one Photos worker thread each, while the inference pool remains free
        to analyze other cards.
        """
        should_refill = False
        with self._photos_lane.write_priority():
            with self._state_lock:
                replay = any(decision.decision_id == decision_id for decision in self.session.decisions)
                self.session.record_decision(
                    decision_id=decision_id,
                    item_id=item_id,
                    revision=revision,
                    action="persist",
                )
                item = self._item(item_id, revision)
                if replay:
                    self._checkpoint_item(item, manifest_path=self._manifests.get((item_id, revision)))
                    return
                manifest_path = self._manifests.get((item_id, revision))
                if manifest_path is None:
                    raise ValueError("source manifest is unavailable")
                self._checkpoint_item(item, manifest_path=manifest_path)
            try:
                future = self._write_executor.submit(
                    self._run_persist_operation,
                    item_id,
                    revision,
                    manifest_path,
                    operation,
                )
                outcome = future.result()
            except Exception:
                outcome = False
            with self._state_lock:
                if isinstance(outcome, PersistenceOutcome):
                    verified = outcome.verified
                    reviewed_run_id = outcome.reviewed_run_id
                    if outcome.manifest_path is not None:
                        manifest_path = outcome.manifest_path
                        self._manifests[(item_id, revision)] = manifest_path
                else:
                    verified = outcome is True
                    reviewed_run_id = None
                state = "verified" if verified else "uncertain"
                self.session.checkpoint_item(
                    item_id=item_id,
                    revision=revision,
                    state=state,
                    reviewed_run_id=reviewed_run_id,
                )
                item = self._item(item_id, revision)
                self._checkpoint_item(item, manifest_path=manifest_path)
                should_refill = state == "verified"
        if should_refill:
            self._fill_slots()
            self._schedule_available()

    def _run_persist_operation(
        self,
        item_id: str,
        revision: int,
        manifest_path: Path,
        operation: Callable[[Path], bool | PersistenceOutcome],
    ) -> bool | PersistenceOutcome:
        try:
            with self._photos_lane.write():
                with self._state_lock:
                    item = self.session.checkpoint_item(
                        item_id=item_id,
                        revision=revision,
                        state="saving",
                    )
                    self._checkpoint_item(item, manifest_path=manifest_path)
                return operation(manifest_path)
        except Exception:
            return False

    def discard(self, *, item_id: str, revision: int, decision_id: str) -> None:
        with self._state_lock:
            replay = any(decision.decision_id == decision_id for decision in self.session.decisions)
            self.session.record_decision(
                decision_id=decision_id,
                item_id=item_id,
                revision=revision,
                action="discard",
            )
            item = self._item(item_id, revision)
            self._checkpoint_item(item, manifest_path=self._manifests.get((item_id, revision)))
        if not replay:
            self._fill_slots()
            self._schedule_available()

    def rescan(
        self,
        *,
        item_id: str,
        revision: int,
        decision_id: str,
        candidate_override: Any | None = None,
    ) -> None:
        with self._state_lock:
            replay = any(decision.decision_id == decision_id for decision in self.session.decisions)
            candidate = self._candidates.get((item_id, revision))
            self.session.record_decision(
                decision_id=decision_id,
                item_id=item_id,
                revision=revision,
                action="rescan",
            )
            new_item = self.session.current_item(item_id)
            if not replay:
                if candidate is None:
                    raise ValueError("photo candidate is unavailable")
                self._candidates[(item_id, new_item.revision)] = (
                    candidate if candidate_override is None else candidate_override
                )
            self._checkpoint_item(new_item)
        if not replay:
            self._schedule_available()

    def wait(self) -> None:
        while True:
            with self._idle_condition:
                futures = tuple(self._futures)
                if not futures and self._completion_scheduling == 0 and self._scheduling_operations == 0:
                    return
                if not futures:
                    self._idle_condition.wait()
                    continue
            wait(futures)

    def close(self) -> None:
        self.wait()
        self._write_executor.shutdown(wait=True, cancel_futures=False)
        self._executor.shutdown(wait=True, cancel_futures=False)
        for executor in self._retired_executors:
            executor.shutdown(wait=True, cancel_futures=False)
        self._retired_executors.clear()

    def _schedule_available(self) -> None:
        with self._state_lock:
            if self.session.state != "running" or not self.session.config.auto_analyze:
                return
            # Preparation uses the Photos lane outside the state lock.  A
            # write completion can request more work while that preparation
            # is still restoring a card it deferred for write priority.  Run
            # one follow-up pass after the active scheduler finishes instead
            # of allowing two schedulers to lose that handoff.
            if self._scheduling_operations:
                self._reschedule_requested = True
                return
            self._scheduling_operations += 1

        scheduling_released = False
        try:
            while True:
                with self._state_lock:
                    if self.session.state != "running" or not self.session.config.auto_analyze:
                        break
                    preparing = sum(item.state == "preparing" for item in self._current_items())
                    capacity = self.session.config.inference_concurrency - len(self._futures) - preparing
                    candidates = [
                        item
                        for item in self._current_items()
                        if item.state in {"discovered", "queued"}
                        and (item.item_id, item.revision) in self._candidates
                    ][:max(0, capacity)]
                    for item in candidates:
                        self.session.checkpoint_item(
                            item_id=item.item_id,
                            revision=item.revision,
                            state="preparing",
                        )
                        self._checkpoint_item(item)

                for item in candidates:
                    identity = (item.item_id, item.revision)
                    try:
                        with self._photos_lane.read() as acquired:
                            if not acquired:
                                self._restore_queued_if_current(*identity)
                                continue
                            prepared = self._prepare(self._candidates[identity])
                    except Exception:
                        self._fail_if_current(*identity)
                        continue
                    with self._state_lock:
                        current = self.session.current_item(item.item_id)
                        if current.revision != item.revision or current.state in TERMINAL_ITEM_STATES:
                            # `analyze` owns the workspace finalizer. Let it run even
                            # after a discard or rescan made this result stale; the
                            # completion path below suppresses its stale result.
                            future = self._executor.submit(self._analyze, prepared)
                            self._futures[future] = identity
                        else:
                            self.session.checkpoint_item(item_id=item.item_id, revision=item.revision, state="analyzing")
                            self._checkpoint_item(current)
                            future = self._executor.submit(self._analyze, prepared)
                            self._futures[future] = identity
                    # A direct or very fast executor may already have completed.
                    # Do not invoke this callback while `_state_lock` is held.
                    future.add_done_callback(self._analysis_finished)

                with self._state_lock:
                    if self._reschedule_requested:
                        self._reschedule_requested = False
                        continue
                    self._scheduling_operations -= 1
                    scheduling_released = True
                    self._idle_condition.notify_all()
                    return
        finally:
            if not scheduling_released:
                with self._idle_condition:
                    self._scheduling_operations -= 1
                    self._idle_condition.notify_all()

    def _restore_queued_if_current(self, item_id: str, revision: int) -> None:
        with self._state_lock:
            current = self.session.current_item(item_id)
            if current.revision != revision or current.state != "preparing":
                return
            self.session.checkpoint_item(item_id=item_id, revision=revision, state="queued")
            self._checkpoint_item(current)

    def _fail_if_current(self, item_id: str, revision: int) -> None:
        with self._state_lock:
            current = self.session.current_item(item_id)
            if current.revision != revision or current.state in TERMINAL_ITEM_STATES:
                return
            self.session.checkpoint_item(item_id=item_id, revision=revision, state="failed")
            self._checkpoint_item(current)

    def _analysis_finished(self, future: Future[AnalysisOutcome]) -> None:
        with self._state_lock:
            identity = self._futures.pop(future, None)
            if identity is None:
                return
            item = self._item(*identity)
            current = self.session.current_item(item.item_id)
            if current.revision != item.revision or item.state in {"discarded", "verified"}:
                # A discard may race with a read-only analysis. The future
                # still cleans up its temporary export, but its result must
                # never resurrect a terminal item or create a stale card.
                pass
            else:
                try:
                    outcome = future.result()
                    uuid.UUID(outcome.source_run_id)
                    if not outcome.manifest_path.is_absolute() or outcome.manifest_path.name != "manifest.json":
                        raise ValueError("analysis manifest path is invalid")
                    if outcome.state not in {"ready", "failed"}:
                        raise ValueError("analysis outcome state is invalid")
                except Exception:
                    self.session.checkpoint_item(item_id=item.item_id, revision=item.revision, state="failed")
                    self._checkpoint_item(item)
                else:
                    self.session.checkpoint_item(item_id=item.item_id, revision=item.revision, state="validating")
                    self._checkpoint_item(item)
                    self._manifests[(item.item_id, item.revision)] = outcome.manifest_path
                    self.session.checkpoint_item(
                        item_id=item.item_id,
                        revision=item.revision,
                        state=outcome.state,
                        source_run_id=outcome.source_run_id,
                    )
                    self._checkpoint_item(item, manifest_path=outcome.manifest_path)
            self._completion_scheduling += 1
        try:
            self._schedule_available()
        finally:
            with self._idle_condition:
                self._completion_scheduling -= 1
                self._idle_condition.notify_all()

    def _current_items(self) -> list[QueueItem]:
        latest: dict[str, QueueItem] = {}
        for item in self.session.items:
            current = latest.get(item.item_id)
            if current is None or current.revision < item.revision:
                latest[item.item_id] = item
        return list(latest.values())

    def _item(self, item_id: str, revision: int) -> QueueItem:
        return next(
            item
            for item in self.session.items
            if item.item_id == item_id and item.revision == revision
        )

    def _checkpoint_item(self, item: QueueItem, *, manifest_path: Path | None = None) -> None:
        write_session(self.session_dir, self.session)
        event: dict[str, object] = {
            "type": "queue_item",
            "session_id": self.session.session_id,
            "item_id": item.item_id,
            "revision": item.revision,
            "state": item.state,
        }
        if item.state in _PERSIST_DECISION_EVENT_STATES:
            decision = next((
                decision
                for decision in reversed(self.session.decisions)
                if decision.action == "persist"
                and decision.item_id == item.item_id
                and decision.revision == item.revision
            ), None)
            if decision is not None:
                event["decision_id"] = decision.decision_id
        if manifest_path is not None:
            event["manifest"] = str(manifest_path)
        self._emit(event)
        self._emit_session()

    def _checkpoint_session(self) -> None:
        write_session(self.session_dir, self.session)
        self._emit_session()

    def _emit_session(self) -> None:
        current = self._current_items()
        self._emit({
            "type": "queue_session",
            "session_id": self.session.session_id,
            "revision": self.session.revision,
            "state": self.session.state,
            "queued": sum(item.state in {"discovered", "queued", "preparing"} for item in current),
            "analyzing": sum(item.state in {"analyzing", "validating"} for item in current),
            "ready": sum(item.state in {"ready", "edited"} for item in current),
            "save_queued": sum(item.state == "save_queued" for item in current),
            "saving": sum(item.state == "saving" for item in current),
            "saved": sum(item.state == "verified" for item in current),
            "attention": sum(item.state in {"failed", "uncertain"} for item in current),
        })
