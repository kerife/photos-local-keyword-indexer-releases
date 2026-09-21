"""Production runtime for the continuous per-photo review queue."""

from __future__ import annotations

import json
import os
import stat
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .adapters import (
    OllamaVisionClient,
    PhotoKitSelector,
    PhotoScriptPermissionError,
    PhotoScriptUnavailableError,
    PhotoScriptBridge,
    PhotoSelection,
    PhotosAccessError,
    ScriptPhotoRecord,
    SelectedPhoto,
    TemporaryExportWorkspace,
)
from .ipc import IPCRequest, QueueDecisionInvalidError
from .landmarks import OfflineLandmarkResolver
from .manifest import ManifestError, ScanManifest, load_manifest
from .places import AppleMapsPlacesClient
from .queue_coordinator import AnalysisOutcome, PersistenceOutcome, QueueCoordinator
from .queue_decision import QueueDecisionArtifactError, _read_private_file, load_queue_decision
from .queue_rescan import QueueRescanArtifact, load_queue_rescan
from .queue_session import QueueItem, QueueSession, SessionConfig, StaleRevisionError, load_session
from .service import app_runs_root, review_manifest_v4
from .workflows import (
    WorkflowResult,
    _PreparedPhotoAnalysis,
    _adapter_error_code,
    _analyze_prepared_photo,
    _discard_failed_run,
    _persist_scan,
    _prepare_photo_analysis,
    _recover_stale_workspaces,
    _safe_photo_metadata,
    resolve_model_plan,
    run_apply,
    run_status,
)


class QueueRuntimeError(ValueError):
    """Raised for a stable queue control failure."""


def _approved_caption_is_finalized(row: Any, approved_caption: str | None) -> bool:
    if approved_caption is None:
        return True
    if row.approved_caption != approved_caption or row.proposed_caption != approved_caption:
        return False
    if row.caption_state == "verified":
        return row.applied_caption == approved_caption
    if row.caption_state == "preserved":
        return row.applied_caption is None
    return False


@dataclass(frozen=True, slots=True)
class QueueRuntimeDependencies:
    selector_factory: Callable[[], Any] = PhotoKitSelector
    bridge_factory: Callable[[], Any] = PhotoScriptBridge
    vision_factory: Callable[[], Any] = OllamaVisionClient
    landmark_resolver_factory: Callable[[], Any] = OfflineLandmarkResolver
    places_factory: Callable[[], Any] = AppleMapsPlacesClient
    workspace_factory: Callable[[Path, str], Any] = TemporaryExportWorkspace
    apply_runner: Callable[[Path], WorkflowResult] = run_apply
    review_runner: Callable[..., Path] = review_manifest_v4
    runs_root_factory: Callable[[], Path] = app_runs_root
    now_utc: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    uuid4: Callable[[], uuid.UUID] = uuid.uuid4


@dataclass(frozen=True, slots=True)
class _QueueCandidate:
    selected: SelectedPhoto
    metadata: ScriptPhotoRecord
    access: str
    strategy: str
    screenshots_excluded: int
    config: SessionConfig
    rescan_options: QueueRescanArtifact | None = None
    refresh_metadata: bool = False


@dataclass(slots=True)
class _RecoveredQueueCandidate:
    """Resolve interrupted discovery only after the queue accepts controls."""
    resolved: _QueueCandidate | None = None


@dataclass(slots=True)
class _PreparedQueueItem:
    prepared: _PreparedPhotoAnalysis
    run_id: str
    created_at: datetime
    run_dir: Path
    selection: PhotoSelection
    workspace_context: Any
    config: SessionConfig
    model_plan: Any
    ollama_versions: dict[str, str]
    rescan_options: QueueRescanArtifact | None


@dataclass(slots=True)
class _ActiveQueue:
    coordinator: QueueCoordinator
    runs_root: Path
    settings_path: Path
    decision_root: Path
    model_versions: dict[str, str]


class _CachedReadBridge:
    def __init__(self, bridge: Any, candidate: _QueueCandidate) -> None:
        self._bridge = bridge
        self._candidate = candidate

    def read(self, local_id: str) -> ScriptPhotoRecord:
        if local_id != self._candidate.selected.local_id:
            raise QueueRuntimeError("photo identity changed before preparation")
        return self._candidate.metadata

    def export(self, photo_uuid: str, destination: Path) -> Path:
        return self._bridge.export(photo_uuid, destination)


def _load_config(path: Path, *, runs_root: Path) -> SessionConfig:
    expected = runs_root.parent / "settings.json"
    if path != expected:
        raise QueueRuntimeError("queue settings path is outside application support")
    try:
        payload = _read_private_file(path)
        value = json.loads(payload.decode("utf-8"))
    except (QueueDecisionArtifactError, UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise QueueRuntimeError("queue settings are invalid") from error
    expected_keys = {
        "limit", "modelPolicy", "singleModel", "fastModel", "detailedModel",
        "appleMaps", "includeCaption", "randomSelection", "autoAnalyze",
        "analysisConcurrency", "version",
    }
    if type(value) is not dict or set(value) != expected_keys or value.get("version") != 2:
        raise QueueRuntimeError("queue settings are invalid")
    policy = value["modelPolicy"]
    model = value["singleModel"] if policy == "single" else None
    try:
        return SessionConfig(
            photo_count=value["limit"],
            inference_concurrency=value["analysisConcurrency"],
            auto_analyze=value["autoAnalyze"],
            include_caption=value["includeCaption"],
            apple_maps=value["appleMaps"],
            random_selection=value["randomSelection"],
            model_policy=policy,
            model=model,
            fast_model=value["fastModel"],
            detailed_model=value["detailedModel"],
        )
    except Exception as error:
        raise QueueRuntimeError("queue settings are invalid") from error


def _ensure_private_root(path: Path) -> None:
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        details = os.lstat(path)
    except OSError as error:
        raise QueueRuntimeError("queue storage is unavailable") from error
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode) or details.st_uid != os.getuid():
        raise QueueRuntimeError("queue storage is unsafe")
    os.chmod(path, 0o700)


def _source_manifest_for_run(runs_root: Path, run_id: str) -> tuple[ScanManifest, Path]:
    matches: list[tuple[ScanManifest, Path]] = []
    try:
        candidates = sorted(runs_root.iterdir())
    except OSError as error:
        raise QueueRuntimeError("queue run storage is unavailable") from error
    for candidate in candidates:
        try:
            manifest = load_manifest(candidate)
        except (ManifestError, OSError):
            continue
        if manifest.run_id != run_id:
            continue
        if manifest.schema_version not in {1, 2} or len(manifest.photos) != 1:
            raise QueueRuntimeError("queue source manifest is invalid")
        matches.append((manifest, candidate / "manifest.json"))
    if len(matches) != 1:
        raise QueueRuntimeError("queue source manifest is unavailable")
    return matches[0]


def _emit_persisted_recovery(
    session: QueueSession,
    runs_root: Path,
    emit: Callable[[dict[str, object]], None],
) -> None:
    """Publish an existing review table before reconnecting to Photos."""
    latest: dict[str, QueueItem] = {}
    for item in session.items:
        current = latest.get(item.item_id)
        if current is None or current.revision < item.revision:
            latest[item.item_id] = item
    items = list(latest.values())
    emit({
        "type": "queue_session",
        "session_id": session.session_id,
        "revision": session.revision,
        "state": session.state,
        "queued": sum(item.state in {"discovered", "queued", "preparing"} for item in items),
        "analyzing": sum(item.state in {"analyzing", "validating"} for item in items),
        "ready": sum(item.state in {"ready", "edited"} for item in items),
        "save_queued": sum(item.state == "save_queued" for item in items),
        "saving": sum(item.state == "saving" for item in items),
        "saved": sum(item.state == "verified" for item in items),
        "attention": sum(item.state in {"failed", "uncertain"} for item in items),
    })
    for item in items:
        event: dict[str, object] = {
            "type": "queue_item",
            "session_id": session.session_id,
            "item_id": item.item_id,
            "revision": item.revision,
            "state": item.state,
        }
        if item.source_run_id is not None:
            try:
                _, manifest_path = _source_manifest_for_run(runs_root, item.source_run_id)
            except QueueRuntimeError:
                pass
            else:
                event["manifest"] = str(manifest_path)
        emit(event)


class ContinuousQueueRuntime:
    """Own active queue sessions and bridge queue IPC controls to workflows."""

    def __init__(self, *, dependencies: QueueRuntimeDependencies | None = None) -> None:
        self.dependencies = dependencies or QueueRuntimeDependencies()
        self._lock = threading.RLock()
        self._active: dict[str, _ActiveQueue] = {}

    def handle(self, request: IPCRequest, emit: Callable[[dict[str, object]], None]) -> None:
        command = request.command
        if command == "queue_start":
            self._start(request, emit)
            return
        session_id = str(request.payload["session_id"])
        if command == "queue_resume":
            with self._lock:
                is_active = session_id in self._active
            if not is_active:
                self._resume_inactive(request, emit)
                return
        active = self._require_active(session_id)
        revision = int(request.payload["revision"])
        if command == "queue_pause":
            active.coordinator.pause(expected_revision=revision)
        elif command == "queue_resume":
            active.coordinator.resume(expected_revision=revision)
        elif command == "queue_update":
            settings_path = Path(str(request.payload["settings_path"]))
            config = _load_config(settings_path, runs_root=active.runs_root)
            model_plan = resolve_model_plan(
                policy=config.model_policy,
                model=config.model,
                fast_model=config.fast_model,
                detailed_model=config.detailed_model,
            )
            preflight = self.dependencies.vision_factory()
            checked = {
                model: str(preflight.check_model(model))
                for model in model_plan.required_models
            }
            active.model_versions.update(checked)
            active.coordinator.update_config(expected_revision=revision, config=config)
        elif command == "queue_discard":
            active.coordinator.discard(
                item_id=str(request.payload["item_id"]),
                revision=revision,
                decision_id=str(request.payload["decision_id"]),
            )
        elif command == "queue_rescan":
            item_id = str(request.payload["item_id"])
            decision_id = str(request.payload["decision_id"])
            if any(
                decision.decision_id == decision_id
                for decision in active.coordinator.session.decisions
            ):
                active.coordinator.rescan(
                    item_id=item_id,
                    revision=revision,
                    decision_id=decision_id,
                )
                return
            artifact = load_queue_rescan(
                active.decision_root,
                session_id=session_id,
                item_id=item_id,
                revision=revision,
                decision_id=decision_id,
            )
            version = str(self.dependencies.vision_factory().check_model(artifact.model))
            active.model_versions[artifact.model] = version
            candidate = active.coordinator.candidate(item_id, revision)
            if isinstance(candidate, _RecoveredQueueCandidate):
                if candidate.resolved is None:
                    raise QueueRuntimeError("photo candidate is unavailable")
                candidate = candidate.resolved
            active.coordinator.rescan(
                item_id=item_id,
                revision=revision,
                decision_id=decision_id,
                candidate_override=replace(
                    candidate,
                    config=active.coordinator.session.config,
                    rescan_options=artifact,
                ),
            )
        elif command == "queue_persist":
            self._persist(active, request)
        elif command == "queue_stop":
            active.coordinator.stop(expected_revision=revision)
            active.coordinator.close()
            with self._lock:
                self._active.pop(session_id, None)
        else:
            raise QueueRuntimeError("queue command is unsupported")

    def _resume_inactive(
        self,
        request: IPCRequest,
        emit: Callable[[dict[str, object]], None],
    ) -> None:
        session_id = str(request.payload["session_id"])
        runs_root = Path(self.dependencies.runs_root_factory())
        _ensure_private_root(runs_root)
        decision_root = runs_root.parent / "queue-sessions"
        session = load_session(decision_root / session_id)
        if session.session_id != session_id:
            raise QueueRuntimeError("queue session identity does not match storage")
        revision = int(request.payload["revision"])
        if session.revision != revision:
            raise StaleRevisionError("control targets a stale session revision")
        if session.state == "stopped":
            raise QueueRuntimeError("stopped queue session cannot be resumed")
        session.set_state(expected_revision=session.revision, state="running")
        _emit_persisted_recovery(session, runs_root, emit)
        self._start(
            request,
            emit,
            restored_session=session,
            restored_runs_root=runs_root,
            recovery_published=True,
        )

    def _start(
        self,
        request: IPCRequest,
        emit: Callable[[dict[str, object]], None],
        *,
        restored_session: QueueSession | None = None,
        restored_runs_root: Path | None = None,
        recovery_published: bool = False,
    ) -> None:
        session_id = str(request.payload["session_id"])
        try:
            uuid.UUID(session_id)
        except ValueError as error:
            raise QueueRuntimeError("queue session identity is invalid") from error
        with self._lock:
            if session_id in self._active:
                raise QueueRuntimeError("queue session is already active")
        if restored_session is None:
            runs_root = Path(str(request.payload["runs_root"]))
            settings_path = Path(str(request.payload["settings_path"]))
            config = _load_config(settings_path, runs_root=runs_root)
        else:
            if restored_runs_root is None:
                raise QueueRuntimeError("queue recovery root is unavailable")
            runs_root = Path(restored_runs_root)
            settings_path = runs_root.parent / "settings.json"
            config = restored_session.config
            _recover_stale_workspaces(runs_root)
        model_plan = resolve_model_plan(
            policy=config.model_policy,
            model=config.model,
            fast_model=config.fast_model,
            detailed_model=config.detailed_model,
        )
        preflight = self.dependencies.vision_factory()
        versions = {model: str(preflight.check_model(model)) for model in model_plan.required_models}
        # Recovery must make its durable cards controllable before asking
        # PhotoScript for another Apple Event.  Construct the bridge only at
        # the first metadata/export operation; Photos can otherwise be busy
        # or unavailable while the user still needs to inspect, discard, or
        # retry an already-persisted card.
        bridge: Any | None = None

        def photo_bridge() -> Any:
            nonlocal bridge
            if bridge is None:
                bridge = self.dependencies.bridge_factory()
            return bridge

        selector = self.dependencies.selector_factory()
        authorization_status = getattr(selector, "authorization_status", None)
        if callable(authorization_status):
            status = authorization_status()
            if status in {"denied", "restricted"}:
                raise PhotosAccessError()
        landmark_resolver = self.dependencies.landmark_resolver_factory()
        decision_root = runs_root.parent / "queue-sessions"
        _ensure_private_root(decision_root)
        session_dir = decision_root / session_id
        session = restored_session or QueueSession.new(session_id=session_id, config=config)
        seen_local_ids: set[str] = set()
        coordinator_holder: list[QueueCoordinator] = []

        def current_config() -> SessionConfig:
            return coordinator_holder[0].session.config if coordinator_holder else config

        def discover(count: int) -> list[_QueueCandidate]:
            if count <= 0:
                return []
            candidate_config = current_config()
            selection = selector.select(limit=500, randomize=candidate_config.random_selection)
            # Keep failed preflight/TCC attempts side-effect free, but create the
            # durable runs root once Photos access and selection have succeeded.
            _ensure_private_root(runs_root)
            candidates: list[_QueueCandidate] = []
            for selected in selection.photos:
                if selected.local_id in seen_local_ids:
                    continue
                seen_local_ids.add(selected.local_id)
                try:
                    metadata = _safe_photo_metadata(photo_bridge().read(selected.local_id), selected.local_id)
                except (PhotosAccessError, PhotoScriptPermissionError, PhotoScriptUnavailableError):
                    raise
                except Exception:
                    continue
                if metadata is None or metadata.existing_keywords or metadata.description.strip():
                    continue
                candidates.append(_QueueCandidate(
                    selected=selected,
                    metadata=metadata,
                    access=selection.access,
                    strategy=selection.strategy,
                    screenshots_excluded=selection.screenshots_excluded,
                    config=candidate_config,
                ))
                if len(candidates) == count:
                    break
            return candidates

        def prepare(candidate: _QueueCandidate | _RecoveredQueueCandidate) -> _PreparedQueueItem:
            if isinstance(candidate, _RecoveredQueueCandidate):
                if candidate.resolved is None:
                    replacements = discover(1)
                    if not replacements:
                        raise QueueRuntimeError("photo candidate is unavailable")
                    candidate.resolved = replacements[0]
                candidate = candidate.resolved
            options = candidate.rescan_options
            effective_config = candidate.config if options is not None else current_config()
            candidate_model_plan = (
                resolve_model_plan(
                    policy="single",
                    model=options.model,
                    fast_model=options.model,
                    detailed_model=options.model,
                )
                if options is not None
                else resolve_model_plan(
                    policy=effective_config.model_policy,
                    model=effective_config.model,
                    fast_model=effective_config.fast_model,
                    detailed_model=effective_config.detailed_model,
                )
            )
            candidate_versions = {
                model: versions[model]
                for model in candidate_model_plan.required_models
                if model in versions
            }
            for model in candidate_model_plan.required_models:
                if model not in candidate_versions:
                    candidate_versions[model] = str(
                        self.dependencies.vision_factory().check_model(model)
                    )
                    versions[model] = candidate_versions[model]
            use_places = options is None or options.layers.places
            analysis_candidate = candidate
            places = (
                self.dependencies.places_factory()
                if effective_config.apple_maps and use_places
                else None
            )
            run_uuid = self.dependencies.uuid4()
            run_id = str(run_uuid)
            now = self.dependencies.now_utc()
            if now.tzinfo is None:
                raise QueueRuntimeError("queue clock must be timezone-aware")
            timestamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            run_dir = runs_root / f"{timestamp}-{run_id[:8]}"
            run_dir.mkdir(mode=0o700)
            run_dir.chmod(0o700)
            workspace_context = self.dependencies.workspace_factory(run_dir, run_id)
            workspace = workspace_context.__enter__()
            try:
                rescan_error_code: str | None = None
                if options is not None or candidate.refresh_metadata:
                    try:
                        refreshed = _safe_photo_metadata(
                            photo_bridge().read(candidate.selected.local_id),
                            candidate.selected.local_id,
                        )
                    except Exception as error:
                        rescan_error_code = _adapter_error_code(error, "READ_FAILED")
                    else:
                        if refreshed is None or refreshed.uuid != candidate.metadata.uuid:
                            rescan_error_code = "IDENTITY_MISMATCH"
                        else:
                            analysis_candidate = replace(candidate, metadata=refreshed)
                if rescan_error_code is not None:
                    prepared = _PreparedPhotoAnalysis(
                        local_id=candidate.selected.local_id,
                        selected_date=candidate.selected.creation_date,
                        errors=(("metadata", rescan_error_code),),
                    )
                else:
                    prepared = _prepare_photo_analysis(
                        analysis_candidate.selected,
                        bridge=_CachedReadBridge(photo_bridge(), analysis_candidate),
                        workspace=workspace,
                        model_plan=candidate_model_plan,
                        landmark_resolver=landmark_resolver,
                        places_client=places,
                        cancel_requested=lambda: False,
                        ollama_versions=versions,
                        use_location_context=use_places,
                    )
            except BaseException:
                workspace_context.__exit__(None, None, None)
                _discard_failed_run(run_dir)
                raise
            return _PreparedQueueItem(
                prepared=prepared,
                run_id=run_id,
                created_at=now,
                run_dir=run_dir,
                selection=PhotoSelection(
                    photos=(candidate.selected,),
                    requested=1,
                    eligible=1,
                    screenshots_excluded=candidate.screenshots_excluded,
                    access=candidate.access,
                    strategy=candidate.strategy,
                ),
                workspace_context=workspace_context,
                config=effective_config,
                model_plan=candidate_model_plan,
                ollama_versions=candidate_versions,
                rescan_options=options,
            )

        def analyze(prepared_item: _PreparedQueueItem) -> AnalysisOutcome:
            try:
                options = prepared_item.rescan_options
                photo = _analyze_prepared_photo(
                    prepared_item.prepared,
                    vision=self.dependencies.vision_factory(),
                    include_caption=prepared_item.config.include_caption,
                    analysis_profile=options.profile if options is not None else None,
                    analysis_layers=(
                        {
                            "places": options.layers.places,
                            "documents_text": options.layers.documents_text,
                            "people_accessories": options.layers.people_accessories,
                            "semantic_normalization": options.layers.semantic_normalization,
                        }
                        if options is not None else None
                    ),
                    additional_information=(
                        options.additional_information if options is not None else None
                    ),
                    analysis_prompt=options.analysis_prompt if options is not None else None,
                )
                scan_status = "ready_with_errors" if photo.errors else "ready"
                _, manifest_path = _persist_scan(
                    prepared_item.run_dir,
                    run_id=prepared_item.run_id,
                    created_at=prepared_item.created_at,
                    model_plan=prepared_item.model_plan,
                    ollama_versions=prepared_item.ollama_versions,
                    selection=prepared_item.selection,
                    photos=[photo],
                    scan_status=scan_status,
                    captions_requested=prepared_item.config.include_caption,
                )
                if photo.scan_state not in {"ready", "noop", "analysis_failed"}:
                    raise QueueRuntimeError("queue analysis state is invalid")
                state = "failed" if photo.scan_state == "analysis_failed" else "ready"
                return AnalysisOutcome(
                    source_run_id=prepared_item.run_id,
                    manifest_path=manifest_path,
                    state=state,
                )
            except BaseException:
                _discard_failed_run(prepared_item.run_dir)
                raise
            finally:
                prepared_item.workspace_context.__exit__(None, None, None)

        restored_candidates: dict[tuple[str, int], _QueueCandidate | _RecoveredQueueCandidate] = {}
        restored_manifests: dict[tuple[str, int], Path] = {}
        recovery_changed = False
        if restored_session is not None:
            source_cache: dict[str, tuple[ScanManifest, Path] | None] = {}
            for historical_item in session.items:
                run_id = historical_item.source_run_id
                if run_id is None or run_id in source_cache:
                    continue
                try:
                    source_cache[run_id] = _source_manifest_for_run(runs_root, run_id)
                except QueueRuntimeError:
                    source_cache[run_id] = None
                else:
                    source = source_cache[run_id]
                    if source is not None:
                        seen_local_ids.add(source[0].photos[0].photos_local_identifier)

            def restored_candidate(source: ScanManifest) -> _QueueCandidate | None:
                photo = source.photos[0]
                if type(photo.uuid) is not str or not photo.uuid:
                    return None
                metadata = ScriptPhotoRecord(
                    uuid=photo.uuid,
                    local_id=photo.photos_local_identifier,
                    title=photo.title,
                    date=photo.date,
                    existing_keywords=tuple(photo.existing_keywords),
                    description="",
                )
                return _QueueCandidate(
                    selected=SelectedPhoto(photo.photos_local_identifier, photo.date),
                    metadata=metadata,
                    access=str(source.selection["access"]),
                    strategy=str(source.selection.get("strategy", "recent")),
                    screenshots_excluded=int(source.selection["screenshots_excluded"]),
                    config=session.config,
                )

            latest: dict[str, QueueItem] = {}
            for item in session.items:
                current = latest.get(item.item_id)
                if current is None or current.revision < item.revision:
                    latest[item.item_id] = item
            needs_replacement: list[QueueItem] = []
            for item in latest.values():
                if item.state in {"save_queued", "saving"}:
                    recovery_changed = True
                    session.checkpoint_item(
                        item_id=item.item_id,
                        revision=item.revision,
                        state="uncertain",
                    )
                    continue
                if item.state in {"discovered", "queued", "preparing", "analyzing", "validating"}:
                    recovery_changed = True
                    session.checkpoint_item(
                        item_id=item.item_id,
                        revision=item.revision,
                        state="queued",
                    )
                    source_entry = (
                        source_cache.get(item.source_run_id)
                        if item.source_run_id is not None
                        else None
                    )
                    candidate = (
                        restored_candidate(source_entry[0])
                        if source_entry is not None
                        else None
                    )
                    if candidate is None:
                        needs_replacement.append(item)
                    else:
                        restored_candidates[(item.item_id, item.revision)] = replace(candidate, refresh_metadata=True)
                    continue
                if item.source_run_id is None:
                    if item.state == "failed":
                        previous_attempt = max(
                            (
                                historical
                                for historical in session.items
                                if historical.item_id == item.item_id
                                and historical.revision < item.revision
                                and historical.source_run_id is not None
                            ),
                            key=lambda historical: historical.revision,
                            default=None,
                        )
                        source_entry = (
                            source_cache.get(previous_attempt.source_run_id)
                            if previous_attempt is not None
                            else None
                        )
                        if source_entry is not None:
                            candidate = restored_candidate(source_entry[0])
                            if candidate is not None:
                                restored_candidates[(item.item_id, item.revision)] = candidate
                    continue
                source_entry = source_cache.get(item.source_run_id)
                if item.state in {"failed", "uncertain", "verified"}:
                    if source_entry is not None:
                        restored_manifests[(item.item_id, item.revision)] = source_entry[1]
                        if item.state in {"failed", "uncertain"}:
                            candidate = restored_candidate(source_entry[0])
                            if candidate is not None:
                                restored_candidates[(item.item_id, item.revision)] = candidate
                    continue
                if item.state not in {"ready", "edited"}:
                    continue
                if source_entry is None:
                    recovery_changed = True
                    session.checkpoint_item(
                        item_id=item.item_id,
                        revision=item.revision,
                        state="failed",
                    )
                    continue
                source, manifest_path = source_entry
                restored_manifests[(item.item_id, item.revision)] = manifest_path
                candidate = restored_candidate(source)
                if candidate is not None:
                    restored_candidates[(item.item_id, item.revision)] = candidate
                if source.photos[0].scan_state == "analysis_failed":
                    recovery_changed = True
                    session.checkpoint_item(
                        item_id=item.item_id,
                        revision=item.revision,
                        state="failed",
                    )

            for item in needs_replacement:
                restored_candidates[(item.item_id, item.revision)] = _RecoveredQueueCandidate()

        coordinator = QueueCoordinator(
            session=session,
            session_dir=session_dir,
            discover=discover,
            prepare=prepare,
            analyze=analyze,
            emit=emit,
            restored_candidates=restored_candidates,
            restored_manifests=restored_manifests,
        )
        coordinator_holder.append(coordinator)
        active = _ActiveQueue(
            coordinator=coordinator,
            runs_root=runs_root,
            settings_path=settings_path,
            decision_root=decision_root,
            model_versions=versions,
        )
        with self._lock:
            self._active[session_id] = active
        try:
            # The first recovery snapshot is deliberately immediate.  Publish
            # only a changed reconciliation afterwards, so a legacy or stale
            # card cannot remain presented with an obsolete state without
            # duplicating every stable card.
            if restored_session is not None and (not recovery_published or recovery_changed):
                coordinator.emit_recovered_items()
            coordinator.start()
        except BaseException:
            with self._lock:
                self._active.pop(session_id, None)
            coordinator.close()
            raise

    def _persist(self, active: _ActiveQueue, request: IPCRequest) -> None:
        payload = request.payload
        session_id = str(payload["session_id"])
        item_id = str(payload["item_id"])
        revision = int(payload["revision"])
        decision_id = str(payload["decision_id"])
        try:
            artifact = load_queue_decision(
                active.decision_root,
                session_id=session_id,
                item_id=item_id,
                revision=revision,
                decision_id=decision_id,
            )
        except QueueDecisionArtifactError as error:
            raise QueueDecisionInvalidError("queue decision artifact is invalid") from error

        def operation(source_path: Path) -> PersistenceOutcome:
            source = load_manifest(source_path.parent)
            photo_uuid = source.photos[0].uuid if len(source.photos) == 1 else None
            if photo_uuid is None:
                raise QueueRuntimeError("queue source manifest has no stable photo identity")
            captions = {photo_uuid: artifact.approved_caption} if artifact.approved_caption is not None else {}
            reviewed_path = self.dependencies.review_runner(
                source_path,
                {photo_uuid: list(artifact.approved_keywords)},
                captions,
            )
            result = self.dependencies.apply_runner(reviewed_path)
            reviewed = load_manifest(reviewed_path.parent)
            status = run_status(reviewed_path)
            blocked_evidence = {
                "MANIFEST_INVALID",
                "REVIEW_PROVENANCE_INVALID",
                "MUTATION_EVIDENCE_INVALID",
            }
            row = reviewed.photos[0] if len(reviewed.photos) == 1 else None
            verified = (
                result.exit_code in {0, 1}
                and row is not None
                and row.apply_state in {"verified", "noop"}
                and _approved_caption_is_finalized(row, artifact.approved_caption)
                and not blocked_evidence.intersection(status.error_codes)
            )
            return PersistenceOutcome(
                verified=verified,
                reviewed_run_id=reviewed.run_id,
                manifest_path=reviewed_path,
            )

        active.coordinator.persist(
            item_id=item_id,
            revision=revision,
            decision_id=decision_id,
            operation=operation,
        )

    def _require_active(self, session_id: str) -> _ActiveQueue:
        with self._lock:
            active = self._active.get(session_id)
        if active is None:
            raise QueueRuntimeError("queue session is not active")
        return active

    def wait(self, session_id: str) -> None:
        self._require_active(session_id).coordinator.wait()

    def pause_for_handoff(self) -> None:
        """Keep sessions/executors intact while the helper changes owners."""
        with self._lock:
            coordinators = [active.coordinator for active in self._active.values()]
        for coordinator in coordinators:
            coordinator.pause()

    def drain_for_handoff(self) -> None:
        self.pause_for_handoff()
        with self._lock:
            coordinators = [active.coordinator for active in self._active.values()]
        for coordinator in coordinators:
            coordinator.wait()

    def current_items(self, session_id: str) -> list[QueueItem]:
        return self._require_active(session_id).coordinator.current_items()

    def item(self, session_id: str, item_id: str, revision: int) -> QueueItem:
        return self._require_active(session_id).coordinator.item(item_id, revision)


_DEFAULT_RUNTIME = ContinuousQueueRuntime()


def default_queue_handler(request: IPCRequest, emit: Callable[[dict[str, object]], None]) -> None:
    _DEFAULT_RUNTIME.handle(request, emit)
