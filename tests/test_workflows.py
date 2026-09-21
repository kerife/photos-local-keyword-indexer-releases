from __future__ import annotations

import csv
import hashlib
import os
import signal
import shutil
import stat
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from photos_indexer.adapters import PhotoScriptPermissionError, PhotoSelection, ScriptPhotoRecord, SelectedPhoto
from photos_indexer.manifest import (
    PhotoRecord,
    build_scan_manifest,
    compute_mutation_digest,
    compute_rollback_digest,
    load_manifest,
    write_preview_csv,
    write_manifest,
)
from photos_indexer.models import VisionResult


class FakeWorkspace:
    recovered: list[Path] = []

    def __init__(self, parent: Path, run_id: str) -> None:
        self.root = parent / f".fake-exports-{run_id}"
        self.exported: list[Path] = []

    @classmethod
    def recover(cls, parent: Path) -> list[Path]:
        cls.recovered.append(parent)
        return []

    def __enter__(self) -> "FakeWorkspace":
        self.root.mkdir(mode=0o700)
        return self

    def destination_for(self, photo_uuid: str) -> Path:
        uuid.UUID(photo_uuid)
        destination = self.root / photo_uuid
        destination.mkdir(mode=0o700)
        return destination

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if any(path.exists() for path in self.exported):
            raise AssertionError("an exported raster survived until workspace cleanup")
        for child in self.root.iterdir():
            child.rmdir()
        self.root.rmdir()


class FakeBridge:
    def __init__(self, records: dict[str, ScriptPhotoRecord | Exception]) -> None:
        self.records = records
        self.replace_calls: list[tuple[str, list[str]]] = []
        self.workspace: FakeWorkspace | None = None
        self.export_failures: set[str] = set()

    def read(self, local_id: str) -> ScriptPhotoRecord:
        value = self.records[local_id]
        if isinstance(value, Exception):
            raise value
        return value

    def export(self, local_id: str, destination: Path) -> Path:
        failed_uuids = {
            value.uuid for key, value in self.records.items()
            if key in self.export_failures and isinstance(value, ScriptPhotoRecord)
        }
        if local_id in self.export_failures or local_id in failed_uuids:
            raise RuntimeError("private export failure")
        exported = destination / "photo.png"
        exported.write_bytes(b"\x89PNG\r\n\x1a\nlocal")
        assert self.workspace is not None
        self.workspace.exported.append(exported)
        return exported

    def replace_keywords(self, local_id: str, keywords: list[str]) -> tuple[str, ...]:
        self.replace_calls.append((local_id, list(keywords)))
        return tuple(keywords)


class FakeVision:
    def __init__(self, results: dict[str, VisionResult | Exception], events: list[str], runs_root: Path) -> None:
        self.results = results
        self.events = events
        self.runs_root = runs_root
        self.checked: list[str] = []

    def check_model(self, model: str) -> str:
        self.events.append("ollama-check")
        self.checked.append(model)
        if self.runs_root.exists():
            raise AssertionError("run storage was created before Ollama preflight")
        return "0.12.7"

    def analyze(self, model: str, image: Path) -> VisionResult:
        self.events.append(f"analyze:{image.parent.name}")
        value = self.results[image.parent.name]
        if isinstance(value, Exception):
            raise value
        return value


def make_manifest(run_dir: Path, photos: list[PhotoRecord], *, scan_status: str = "ready") -> Path:
    manifest = build_scan_manifest(
        run_id=str(uuid.uuid4()),
        app_name="photos-local-keyword-indexer",
        app_version="0.1.0",
        model_name="qwen3-vl:4b",
        ollama_version="0.12.7",
        endpoint="http://127.0.0.1:11434",
        selection={"requested": len(photos), "eligible": len(photos), "screenshots_excluded": 0, "access": "authorized"},
        photos=photos,
        summary={
            "ready": sum(photo.scan_state == "ready" for photo in photos),
            "noop": sum(photo.scan_state == "noop" for photo in photos),
            "analysis_failed": sum(photo.scan_state == "analysis_failed" for photo in photos),
        },
        scan_status=scan_status,
        run_errors=[{"stage": "workspace", "code": "WORKSPACE_FAILED"}] if scan_status == "failed" else [],
        confidence_threshold=0.60,
    )
    return write_manifest(run_dir, manifest)


def make_reviewed_manifest(
    run_dir: Path,
    photos: list[PhotoRecord],
    *,
    scan_status: str = "ready",
) -> Path:
    """Create a schema3 review while preserving caller-supplied mutation state."""
    from photos_indexer.service import review_manifest

    source_photos = [
        PhotoRecord(
            uuid=photo.uuid,
            photos_local_identifier=photo.photos_local_identifier,
            title=photo.title,
            date=photo.date,
            existing_keywords=list(photo.existing_keywords),
            proposed_keywords=list(photo.proposed_keywords),
            contains_people=photo.contains_people,
            contains_text=photo.contains_text,
            confidence=photo.confidence,
            scan_state=photo.scan_state,
            # Mutation-stage errors belong to the post-apply fixture state,
            # not to the source dry-run that is being reviewed.
            errors=[error for error in photo.errors if error["stage"] not in {"apply", "rollback"}],
            proposed_caption=photo.proposed_caption,
            caption_state="proposed" if photo.proposed_caption else "not_requested",
        )
        for photo in photos
    ]
    review_selections = {
        photo.uuid: list(photo.proposed_keywords)
        for photo in source_photos
        if photo.uuid is not None and not photo.errors
    }
    source_path = make_manifest(
        run_dir.parent / f"{run_dir.name}-source", source_photos, scan_status=scan_status
    )
    source_path.parent.parent.chmod(0o700)
    reviewed_path = review_manifest(
        source_path,
        review_selections,
        {
            photo.uuid: True
            for photo in source_photos
            if photo.uuid is not None and photo.proposed_caption and not photo.errors
        },
    )
    reviewed = load_manifest(reviewed_path.parent)
    for target, original in zip(reviewed.photos, photos):
        if original.errors and original.scan_state == "ready":
            # Preserve intentionally malformed rows for status/apply
            # hardening fixtures without using the public review API to
            # approve an errored photo.
            target.scan_state = original.scan_state
            target.proposed_keywords = list(original.proposed_keywords)
            target.proposed_caption = original.proposed_caption
            target.caption_state = original.caption_state
        target.apply_state = original.apply_state
        target.applied_keywords = list(original.applied_keywords)
        target.rollback_state = original.rollback_state
        target.rolled_back_keywords = list(original.rolled_back_keywords)
        target.errors = list(original.errors)
        target.applied_caption = original.applied_caption
        target.caption_state = original.caption_state
        if target.apply_state in {"verified", "uncertain"} and (target.applied_keywords or target.applied_caption):
            target.mutation_digest = compute_mutation_digest(reviewed, target)
        if target.rollback_state != "not_run":
            target.rollback_digest = compute_rollback_digest(reviewed, target)
    reviewed.summary = {
        "ready": sum(photo.scan_state == "ready" for photo in reviewed.photos),
        "noop": sum(photo.scan_state == "noop" for photo in reviewed.photos),
        "analysis_failed": sum(photo.scan_state == "analysis_failed" for photo in reviewed.photos),
    }
    reviewed.scan_digest = reviewed.compute_scan_digest()
    for target in reviewed.photos:
        if target.apply_state in {"verified", "uncertain"} and (target.applied_keywords or target.applied_caption):
            target.mutation_digest = compute_mutation_digest(reviewed, target)
        if target.rollback_state != "not_run":
            target.rollback_digest = compute_rollback_digest(reviewed, target)
    return write_manifest(run_dir, reviewed)


def make_photo(number: int, *, proposed: list[str] | None = None) -> PhotoRecord:
    return PhotoRecord(
        uuid=f"00000000-0000-4000-8000-{number:012d}",
        photos_local_identifier=f"local-{number}",
        title=f"Photo {number}",
        date=datetime(2026, 8, number, 10, 0),
        existing_keywords=["PERRO"],
        proposed_keywords=proposed if proposed is not None else ["playa"],
        contains_people=False,
        contains_text=False,
        confidence=0.90,
    )


class ScanWorkflowTests(unittest.TestCase):
    def test_technical_trace_explains_gps_when_apple_maps_is_disabled(self) -> None:
        from photos_indexer.workflows import _PreparedPhotoAnalysis, _technical_trace

        trace = _technical_trace(
            _PreparedPhotoAnalysis(
                local_id="local-1",
                selected_date=datetime(2026, 8, 24, 10, 0),
                used_gps=True,
                used_apple_maps=False,
            ),
            inference_ms=0,
            postprocess_ms=0,
        )

        self.assertIn("geolocalización local", trace.prompt_effective.casefold())
        self.assertIn("Apple Maps no", trace.prompt_effective)
        self.assertNotIn("latitud", trace.prompt_effective.casefold())

    def test_technical_trace_keeps_advanced_prompt_context_without_coordinates(self) -> None:
        from photos_indexer.workflows import _PreparedPhotoAnalysis, _technical_trace

        prepared = _PreparedPhotoAnalysis(
            local_id="asset/L0/trace-advanced",
            selected_date=datetime(2025, 1, 1),
            used_gps=True,
            used_apple_maps=True,
        )
        trace = _technical_trace(
            prepared,
            inference_ms=1,
            postprocess_ms=1,
            analysis_profile="free_local",
            analysis_layers={
                "places": True,
                "documents_text": False,
                "people_accessories": False,
                "semantic_normalization": False,
            },
            additional_information="La escena es una plaza.",
            analysis_prompt="Busca el monumento visible.",
        )

        self.assertIn("La escena es una plaza", trace.prompt_effective)
        self.assertIn("Busca el monumento visible", trace.prompt_effective)
        self.assertIn("geolocalización local", trace.prompt_effective.casefold())
        self.assertNotIn("latitud", trace.prompt_effective.casefold())
        self.assertNotIn("longitud", trace.prompt_effective.casefold())

    def test_technical_trace_distinguishes_apple_maps_lookup_outcomes(self) -> None:
        from photos_indexer.workflows import _PreparedPhotoAnalysis, _technical_trace

        expectations = {
            "no_results": "no devolvió lugares sanitizables",
            "timeout": "agotó el tiempo de espera",
            "error": "devolvió un error",
            "results_filtered": "todos se descartaron por sanitización",
        }
        for state, copy in expectations.items():
            with self.subTest(state=state):
                trace = _technical_trace(
                    _PreparedPhotoAnalysis(
                        local_id="asset/L0/trace-maps",
                        selected_date=datetime(2026, 8, 24, 10, 0),
                        used_gps=True,
                        used_apple_maps=True,
                        place_lookup_state=state,
                    ),
                    inference_ms=0,
                    postprocess_ms=0,
                )

                self.assertEqual(trace.place_lookup_state, state)
                self.assertIn(copy, trace.prompt_effective.casefold())
                self.assertNotIn("latitud", trace.prompt_effective.casefold())
                self.assertNotIn("longitud", trace.prompt_effective.casefold())

    def test_trace_records_maps_candidate_discarded_by_visual_evidence(self) -> None:
        from photos_indexer.workflows import _PreparedPhotoAnalysis, _analyze_prepared_photo

        with tempfile.TemporaryDirectory() as tmp:
            exported = Path(tmp) / "photo.png"
            exported.write_bytes(b"\x89PNG\r\n\x1a\nlocal")
            prepared = _PreparedPhotoAnalysis(
                local_id="local-venue",
                selected_date=datetime(2026, 8, 24, 10, 0),
                metadata=ScriptPhotoRecord(
                    "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                    "local-venue",
                    "Venue",
                    datetime(2026, 8, 24, 10, 0),
                    (),
                    (19.485, -99.117),
                ),
                active_model="qwen3-vl:4b",
                model_reason="location_context",
                exported=exported,
                place_context=("Basílica de Guadalupe",),
                used_gps=True,
                used_apple_maps=True,
                place_lookup_state="results",
            )

            class Vision:
                def analyze(self, model: str, image: Path, **kwargs: object) -> VisionResult:
                    return VisionResult(
                        ("Basílica de Guadalupe",),
                        "Un edificio cerca de una plaza.",
                        False,
                        False,
                        0.91,
                    )

            photo = _analyze_prepared_photo(prepared, vision=Vision(), include_caption=False)

        self.assertEqual(photo.scan_state, "noop")
        trace = photo.technical_trace
        self.assertIsNotNone(trace)
        assert trace is not None
        self.assertEqual(trace.place_context, ("Basílica de Guadalupe",))
        self.assertEqual(trace.place_lookup_state, "results")
        self.assertEqual(trace.place_evidence_state, "discarded_by_visual_evidence")
        self.assertIn("no se usaron como nombres específicos", trace.prompt_effective)
        self.assertNotIn("19.485", trace.prompt_effective)

    def test_prepare_records_place_lookup_status_without_failing_analysis(self) -> None:
        from photos_indexer.workflows import (
            ModelPlan,
            _analyze_prepared_photo,
            _prepare_photo_analysis,
        )

        with tempfile.TemporaryDirectory() as tmp:
            selected = SelectedPhoto("local-map-empty", datetime(2026, 8, 24, 10, 0))
            photo_uuid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"

            class Bridge:
                def read(self, identifier: str) -> ScriptPhotoRecord:
                    return ScriptPhotoRecord(
                        photo_uuid,
                        identifier,
                        "Maps Empty",
                        selected.creation_date,
                        (),
                        (45.4313917, 12.3348283),
                    )

                def export(self, identifier: str, destination: Path) -> Path:
                    exported = destination / "photo.png"
                    exported.write_bytes(b"\x89PNG\r\n\x1a\nlocal")
                    return exported

            class Resolver:
                def resolve(self, location):
                    return None

            class Places:
                def nearby_with_status(self, location, *, cancel_requested=None):
                    return type("Lookup", (), {"names": (), "state": "no_results"})()

            class Vision:
                def analyze(self, model: str, image: Path, **kwargs: object) -> VisionResult:
                    return VisionResult(("canal",), "Una foto de un canal.", False, False, 0.92)

            workspace = FakeWorkspace(Path(tmp), "maps-status")
            with workspace:
                prepared = _prepare_photo_analysis(
                    selected,
                    bridge=Bridge(),
                    workspace=workspace,
                    model_plan=ModelPlan("single", "qwen3-vl:4b", "qwen3-vl:4b"),
                    landmark_resolver=Resolver(),
                    places_client=Places(),
                    cancel_requested=lambda: False,
                )
                photo = _analyze_prepared_photo(prepared, vision=Vision(), include_caption=True)

            trace = photo.technical_trace
            self.assertIsNotNone(trace)
            assert trace is not None
            self.assertTrue(trace.used_gps)
            self.assertTrue(trace.used_apple_maps)
            self.assertEqual(trace.place_lookup_state, "no_results")
            self.assertEqual(trace.place_evidence_state, "not_applicable")
            self.assertIn("no devolvió lugares", trace.prompt_effective.casefold())

    def test_single_photo_pipeline_keeps_photos_work_serial_and_cleans_before_return(self) -> None:
        """Moving vision into a worker must not move PhotoScript or Maps with it."""
        from photos_indexer.workflows import (
            ModelPlan,
            _analyze_prepared_photo,
            _prepare_photo_analysis,
        )

        with tempfile.TemporaryDirectory() as tmp:
            selected = SelectedPhoto("local-1", datetime(2026, 8, 24, 10, 0))
            photo_uuid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
            events: list[str] = []

            class Bridge:
                def read(self, identifier: str) -> ScriptPhotoRecord:
                    events.append("photos:read")
                    return ScriptPhotoRecord(
                        photo_uuid,
                        identifier,
                        "Venecia",
                        selected.creation_date,
                        ("viaje",),
                        (45.4313917, 12.3348283),
                    )

                def export(self, identifier: str, destination: Path) -> Path:
                    events.append("photos:export")
                    exported = destination / "photo.png"
                    exported.write_bytes(b"\x89PNG\r\n\x1a\nlocal")
                    return exported

            class Resolver:
                def resolve(self, location):
                    events.append("photos:landmark")
                    return None

            class Places:
                def nearby(self, location, *, cancel_requested=None):
                    events.append("photos:maps")
                    return ("Canal Grande",)

            class Vision:
                def analyze(self, model: str, image: Path, **kwargs: object) -> VisionResult:
                    events.append("ollama:analyze")
                    self.image = image
                    self.kwargs = kwargs
                    return VisionResult(
                        ("canal", "góndola", "Venecia"),
                        "Una góndola navega por un canal.",
                        False,
                        False,
                        0.93,
                    )

            workspace = FakeWorkspace(Path(tmp), "pipeline")
            with workspace:
                prepared = _prepare_photo_analysis(
                    selected,
                    bridge=Bridge(),
                    workspace=workspace,
                    model_plan=ModelPlan("single", "qwen3-vl:4b", "qwen3-vl:4b"),
                    landmark_resolver=Resolver(),
                    places_client=Places(),
                    cancel_requested=lambda: False,
                    ollama_versions={"qwen3-vl:4b": "0.12.7"},
                )
                self.assertEqual(
                    events,
                    ["photos:read", "photos:export", "photos:landmark", "photos:maps"],
                )
                assert prepared.exported is not None
                self.assertTrue(prepared.exported.exists())

                vision = Vision()
                photo = _analyze_prepared_photo(prepared, vision=vision, include_caption=True)

                self.assertEqual(events[-1], "ollama:analyze")
                self.assertFalse(vision.image.exists())
                self.assertEqual(vision.kwargs["location"], (45.4313917, 12.3348283))
                self.assertEqual(vision.kwargs["place_context"], ("Canal Grande",))
                self.assertEqual(photo.scan_state, "ready")
                self.assertEqual(photo.proposed_keywords, ["canal", "góndola", "Venecia"])
                self.assertEqual(photo.proposed_caption, "Una góndola navega por un canal.")
                trace = photo.technical_trace
                self.assertIsNotNone(trace)
                assert trace is not None
                self.assertTrue(trace.used_gps)
                self.assertTrue(trace.used_apple_maps)
                self.assertFalse(trace.used_landmark)
                self.assertEqual(trace.ollama_version, "0.12.7")
                self.assertEqual(trace.place_context, ("Canal Grande",))
                self.assertEqual(trace.place_lookup_state, "results")
                self.assertEqual(trace.place_evidence_state, "context_available")
                self.assertNotIn("45.4314", trace.prompt_effective)
                self.assertNotIn("12.3348", trace.prompt_effective)
                self.assertIn("geolocalización local", trace.prompt_effective.casefold())
                self.assertIn("apple maps", trace.prompt_effective.casefold())
                self.assertIn("no transcribas", trace.prompt_effective.casefold())
                self.assertEqual(
                    trace.prompt_sha256,
                    hashlib.sha256(trace.prompt_effective.encode("utf-8")).hexdigest(),
                )
                self.assertEqual(
                    set(trace.durations_ms),
                    {"metadata", "export", "context", "inference", "postprocess", "total"},
                )
                self.assertEqual(
                    trace.durations_ms["total"],
                    sum(value for key, value in trace.durations_ms.items() if key != "total"),
                )

    def test_specific_keywords_replace_an_underdescribed_generic_caption(self) -> None:
        from photos_indexer.workflows import _PreparedPhotoAnalysis, _analyze_prepared_photo

        with tempfile.TemporaryDirectory() as tmp:
            exported = Path(tmp) / "photo.png"
            exported.write_bytes(b"\x89PNG\r\n\x1a\nlocal")
            prepared = _PreparedPhotoAnalysis(
                local_id="local-specific-caption",
                selected_date=datetime(2025, 4, 16, 6, 40, 30),
                metadata=ScriptPhotoRecord(
                    "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                    "local-specific-caption",
                    "",
                    datetime(2025, 4, 16, 6, 40, 30),
                    (),
                    (43.7696, 11.2558),
                ),
                active_model="qwen3-vl:4b",
                model_reason="location_context",
                exported=exported,
                place_context=("Piazza della Signoria", "Palazzo Vecchio"),
                used_gps=True,
                used_apple_maps=True,
                place_lookup_state="results",
            )

            class Vision:
                def analyze(self, model: str, image: Path, **kwargs: object) -> VisionResult:
                    return VisionResult(
                        (
                            "persona",
                            "fontana di neptuno",
                            "estatua de neptuno",
                            "plaza con monumentos",
                            "turistas en grupo",
                            "arquitectura renacentista",
                        ),
                        "Una foto de persona.",
                        True,
                        False,
                        0.95,
                    )

            photo = _analyze_prepared_photo(prepared, vision=Vision(), include_caption=True)

        self.assertEqual(
            photo.proposed_caption,
            "Una foto de persona, fontana di neptuno y estatua de neptuno.",
        )

    def test_empty_selection_is_a_valid_warning_only_scan(self) -> None:
        from photos_indexer.service import support_snapshot
        from photos_indexer.workflows import ScanDependencies, run_scan

        class Selector:
            def select(self, *, limit: int) -> PhotoSelection:
                return PhotoSelection((), limit, 0, 3, "authorized")

        class Vision:
            def check_model(self, model: str) -> str:
                return "0.12.7"

        with tempfile.TemporaryDirectory() as tmp:
            result = run_scan(
                Path(tmp) / "runs",
                limit=1,
                dependencies=ScanDependencies(
                    platform_name=lambda: "Darwin",
                    selector_factory=Selector,
                    bridge_factory=lambda: object(),
                    vision_factory=Vision,
                    workspace_factory=FakeWorkspace,
                    recover_workspaces=lambda _: [],
                ),
            )

            self.assertEqual(result.exit_code, 0)
            self.assertEqual(result.error_codes, ())
            self.assertEqual(result.warning_codes, ("FEWER_PHOTOS_AVAILABLE",))
            self.assertEqual(result.next_action, "none")
            self.assertIsNotNone(result.manifest_path)
            snapshot = support_snapshot(result)
            self.assertEqual(snapshot["exit_code"], 0)
            self.assertEqual(snapshot["error_codes"], [])
            self.assertEqual(snapshot["warning_codes"], ["FEWER_PHOTOS_AVAILABLE"])

    def test_model_policy_must_be_a_string_before_membership_validation(self) -> None:
        from photos_indexer.workflows import resolve_model_plan

        with self.assertRaises(ValueError):
            resolve_model_plan(policy=[])  # type: ignore[arg-type]

    def test_scan_adds_local_landmark_when_model_confirms_name_and_visible_architecture(self) -> None:
        from photos_indexer.landmarks import Landmark
        from photos_indexer.workflows import ScanDependencies, run_scan

        with tempfile.TemporaryDirectory() as tmp:
            selected = SelectedPhoto("local-1", datetime(2026, 8, 24, 10, 0))
            landmark = Landmark("Basílica de Santa María de la Salud", 45.4314, 12.3348)
            seen_hints: list[str | None] = []
            seen_places: list[tuple[str, ...]] = []

            class Selector:
                def select(self, *, limit: int) -> PhotoSelection:
                    return PhotoSelection((selected,), 1, 1, 0, "authorized")

            class Bridge:
                def read(self, identifier: str) -> ScriptPhotoRecord:
                    return ScriptPhotoRecord(
                        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", identifier, "", selected.creation_date, (),
                        (45.4313917, 12.3348283),
                    )

                def export(self, identifier: str, destination: Path) -> Path:
                    exported = destination / "photo.png"
                    exported.write_bytes(b"\x89PNG\r\n\x1a\nlocal")
                    return exported

            class Vision:
                def check_model(self, model: str) -> str:
                    return "0.12.7"

                def analyze(self, model: str, image: Path, *, location=None, landmark_hint=None, place_context=()) -> VisionResult:
                    seen_hints.append(landmark_hint)
                    seen_places.append(tuple(place_context))
                    return VisionResult(
                        ("iglesia", "cúpula", "Basílica de Santa María de la Salud"),
                        "", False, False, 0.95,
                    )

            class Resolver:
                def resolve(self, location):
                    return landmark

            class Places:
                def nearby(self, location, *, cancel_requested=None):
                    return ("Basilica della Salute", "Canal Grande")

            result = run_scan(
                Path(tmp) / "runs",
                limit=1,
                dependencies=ScanDependencies(
                    platform_name=lambda: "Darwin", selector_factory=Selector, bridge_factory=Bridge,
                    vision_factory=Vision, landmark_resolver_factory=Resolver, places_factory=Places,
                    recover_workspaces=lambda parent: [],
                ),
                apple_maps=True,
            )

            assert result.manifest is not None
            self.assertIn("Basílica de Santa María de la Salud", result.manifest.photos[0].proposed_keywords)
            self.assertEqual(seen_hints, [landmark.name])
            self.assertEqual(seen_places, [("Basilica della Salute", "Canal Grande")])

    def test_scan_does_not_add_local_landmark_from_generic_architecture_only(self) -> None:
        from photos_indexer.landmarks import Landmark
        from photos_indexer.workflows import ScanDependencies, run_scan

        with tempfile.TemporaryDirectory() as tmp:
            selected = SelectedPhoto("local-1", datetime(2026, 8, 24, 10, 0))
            landmark = Landmark("Basílica de Santa María de la Salud", 45.4314, 12.3348)

            class Selector:
                def select(self, *, limit: int) -> PhotoSelection:
                    return PhotoSelection((selected,), 1, 1, 0, "authorized")

            class Bridge:
                workspace: FakeWorkspace | None = None

                def read(self, identifier: str) -> ScriptPhotoRecord:
                    return ScriptPhotoRecord(
                        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", identifier, "", selected.creation_date, (),
                        (45.4313917, 12.3348283),
                    )

                def export(self, identifier: str, destination: Path) -> Path:
                    exported = destination / "photo.png"
                    exported.write_bytes(b"\x89PNG\r\n\x1a\nlocal")
                    assert self.workspace is not None
                    self.workspace.exported.append(exported)
                    return exported

            class Vision:
                def check_model(self, model: str) -> str:
                    return "0.12.7"

                def analyze(self, model: str, image: Path, *, location=None, landmark_hint=None, place_context=()) -> VisionResult:
                    return VisionResult(
                        ("iglesia", "cúpula", "edificio"),
                        "Una vista de la Basílica de Santa María de la Salud.",
                        False, False, 0.95,
                    )

            class Resolver:
                def resolve(self, location):
                    return landmark

            bridge = Bridge()

            class Workspace:
                def __init__(self, parent: Path, run_id: str) -> None:
                    self.root = parent / f".caption-test-{run_id}"
                    self.exported: list[Path] = []

                def __enter__(self) -> "Workspace":
                    self.root.mkdir(mode=0o700)
                    return self

                def destination_for(self, photo_uuid: str) -> Path:
                    destination = self.root / photo_uuid
                    destination.mkdir(mode=0o700)
                    return destination

                def cleanup(self) -> None:
                    shutil.rmtree(self.root)

                def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
                    self.cleanup()

            def workspace_factory(parent: Path, run_id: str) -> Workspace:
                workspace = Workspace(parent, run_id)
                bridge.workspace = workspace
                return workspace

            result = run_scan(
                Path(tmp) / "runs",
                limit=1,
                dependencies=ScanDependencies(
                    platform_name=lambda: "Darwin", selector_factory=Selector, bridge_factory=lambda: bridge,
                    vision_factory=Vision, landmark_resolver_factory=Resolver,
                    workspace_factory=workspace_factory,
                    recover_workspaces=lambda parent: [],
                ),
                include_caption=True,
            )

            assert result.manifest is not None
            self.assertNotIn(landmark.name, result.manifest.photos[0].proposed_keywords)
            self.assertEqual(result.manifest.photos[0].proposed_caption, "Una foto de edificio.")

    def test_scan_does_not_persist_nearby_apple_maps_poi_name_without_landmark_verification(self) -> None:
        from photos_indexer.workflows import ScanDependencies, run_scan

        with tempfile.TemporaryDirectory() as tmp:
            selected = SelectedPhoto("local-1", datetime(2026, 8, 24, 10, 0))

            class Selector:
                def select(self, *, limit: int) -> PhotoSelection:
                    return PhotoSelection((selected,), 1, 1, 0, "authorized")

            class Bridge:
                def read(self, identifier: str) -> ScriptPhotoRecord:
                    return ScriptPhotoRecord(
                        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", identifier, "", selected.creation_date, (),
                        (45.4313917, 12.3348283),
                    )

                def export(self, identifier: str, destination: Path) -> Path:
                    exported = destination / "photo.png"
                    exported.write_bytes(b"\x89PNG\r\n\x1a\nlocal")
                    return exported

            class Vision:
                def check_model(self, model: str) -> str:
                    return "0.12.7"

                def analyze(self, model: str, image: Path, *, location=None, landmark_hint=None, place_context=()) -> VisionResult:
                    return VisionResult(
                        ("Hotel Flora", "canal"),
                        "",
                        False,
                        False,
                        0.95,
                    )

            class Resolver:
                def resolve(self, location):
                    return None

            class Places:
                def nearby(self, location, *, cancel_requested=None):
                    return ("Hotel Flora", "Canal Grande")

            result = run_scan(
                Path(tmp) / "runs",
                limit=1,
                dependencies=ScanDependencies(
                    platform_name=lambda: "Darwin",
                    selector_factory=Selector,
                    bridge_factory=Bridge,
                    vision_factory=Vision,
                    landmark_resolver_factory=Resolver,
                    places_factory=Places,
                    recover_workspaces=lambda parent: [],
                ),
                apple_maps=True,
            )

            assert result.manifest is not None
            self.assertEqual(result.manifest.photos[0].proposed_keywords, ["canal"])

    def test_scan_keeps_apple_maps_landmark_with_independent_visible_architecture(self) -> None:
        from photos_indexer.workflows import ScanDependencies, run_scan

        with tempfile.TemporaryDirectory() as tmp:
            selected = SelectedPhoto("local-1", datetime(2026, 8, 24, 10, 0))

            class Selector:
                def select(self, *, limit: int) -> PhotoSelection:
                    return PhotoSelection((selected,), 1, 1, 0, "authorized")

            class Bridge:
                def read(self, identifier: str) -> ScriptPhotoRecord:
                    return ScriptPhotoRecord(
                        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", identifier, "", selected.creation_date, (),
                        (45.4313917, 12.3348283),
                    )

                def export(self, identifier: str, destination: Path) -> Path:
                    exported = destination / "photo.png"
                    exported.write_bytes(b"\x89PNG\r\n\x1a\nlocal")
                    return exported

            class Vision:
                def check_model(self, model: str) -> str:
                    return "0.12.7"

                def analyze(self, model: str, image: Path, *, location=None, landmark_hint=None, place_context=()) -> VisionResult:
                    return VisionResult(
                        ("Basilica della Salute", "cúpula"),
                        "",
                        False,
                        False,
                        0.95,
                    )

            class Resolver:
                def resolve(self, location):
                    return None

            class Places:
                def nearby(self, location, *, cancel_requested=None):
                    return ("Basilica della Salute", "Canal Grande")

            result = run_scan(
                Path(tmp) / "runs",
                limit=1,
                dependencies=ScanDependencies(
                    platform_name=lambda: "Darwin",
                    selector_factory=Selector,
                    bridge_factory=Bridge,
                    vision_factory=Vision,
                    landmark_resolver_factory=Resolver,
                    places_factory=Places,
                    recover_workspaces=lambda parent: [],
                ),
                apple_maps=True,
            )

            assert result.manifest is not None
            self.assertEqual(
                result.manifest.photos[0].proposed_keywords,
                ["Basílica de Santa María de la Salud", "cúpula"],
            )

    def test_scan_confirms_diablos_venue_and_rocco_from_maps_and_visual_evidence(self) -> None:
        from photos_indexer.workflows import ScanDependencies, run_scan

        with tempfile.TemporaryDirectory() as tmp:
            selected = SelectedPhoto("local-1", datetime(2026, 8, 24, 10, 0))

            class Selector:
                def select(self, *, limit: int) -> PhotoSelection:
                    return PhotoSelection((selected,), 1, 1, 0, "authorized")

            class Bridge:
                def read(self, identifier: str) -> ScriptPhotoRecord:
                    return ScriptPhotoRecord(
                        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", identifier, "", selected.creation_date, (),
                        (19.4040, -99.0860),
                    )

                def export(self, identifier: str, destination: Path) -> Path:
                    exported = destination / "photo.png"
                    exported.write_bytes(b"\x89PNG\r\n\x1a\nlocal")
                    return exported

            class Vision:
                def check_model(self, model: str) -> str:
                    return "0.12.7"

                def analyze(self, model: str, image: Path, **kwargs: object) -> VisionResult:
                    return VisionResult(
                        (
                            "Estadio Alfredo Harp Helú",
                            "estadio",
                            "mascota",
                            "Diablos Rojos del México",
                            "Rocco",
                        ),
                        "Rocco de los Diablos Rojos en el Estadio Alfredo Harp Helú.",
                        True,
                        False,
                        0.95,
                    )

            class Resolver:
                def resolve(self, location):
                    return None

            class Places:
                def nearby(self, location, *, cancel_requested=None):
                    return ("Estadio Alfredo Harp Helu", "Palacio de los Deportes")

            result = run_scan(
                Path(tmp) / "runs",
                limit=1,
                include_caption=True,
                apple_maps=True,
                dependencies=ScanDependencies(
                    platform_name=lambda: "Darwin",
                    selector_factory=Selector,
                    bridge_factory=Bridge,
                    vision_factory=Vision,
                    landmark_resolver_factory=Resolver,
                    places_factory=Places,
                    workspace_factory=FakeWorkspace,
                    recover_workspaces=lambda parent: [],
                ),
            )

            assert result.manifest is not None
            self.assertTrue(result.manifest.photos, result)
            photo = result.manifest.photos[0]
            self.assertEqual(
                photo.proposed_keywords[:3],
                ["Estadio Alfredo Harp Helú", "Diablos Rojos del México", "Rocco"],
            )
            self.assertEqual(
                photo.proposed_caption,
                "Rocco de los Diablos Rojos en el Estadio Alfredo Harp Helú.",
            )

    def test_landmark_visual_match_accepts_curated_alias_only_with_independent_architecture_cue(self) -> None:
        from photos_indexer.workflows import _landmark_visual_match

        name = "Basílica de Santa María de la Salud"
        aliases = ("Basilica della Salute",)
        self.assertTrue(_landmark_visual_match(("Basilica della Salute", "cúpula"), name, aliases=aliases))
        self.assertFalse(_landmark_visual_match(("Basilica della Salute",), name, aliases=aliases))

    def test_landmark_visual_match_does_not_treat_a_gondola_as_building_confirmation(self) -> None:
        from photos_indexer.workflows import _landmark_visual_match

        name = "Basílica de Santa María de la Salud"
        aliases = ("Basilica della Salute",)

        # A nearby building name can be echoed from Apple Maps while the
        # camera is pointed at a canal. A gondola is useful scene evidence,
        # but it does not identify which building is in the frame.
        self.assertFalse(_landmark_visual_match(("Basilica della Salute", "góndola"), name, aliases=aliases))

    def test_landmark_visual_match_accepts_specific_visible_architecture_cues(self) -> None:
        from photos_indexer.workflows import _landmark_visual_match

        name = "Basílica de Santa María de la Salud"

        self.assertTrue(_landmark_visual_match((name, "fachada de piedra"), name))

    def test_contextual_city_alias_is_kept_with_its_independent_visible_cue(self) -> None:
        from photos_indexer.workflows import _filter_unverified_place_echoes

        # Apple Maps and the model may use the English city alias even when
        # the Spanish taxonomy stores the place as Venecia.  The alias should
        # survive only when the image also supplies a visible city cue.
        self.assertEqual(
            _filter_unverified_place_echoes(("Venice", "canal"), ("Venice",)),
            ("Venice", "canal"),
        )

    def test_landmark_visual_match_accepts_plural_accentless_architecture_cue(self) -> None:
        from photos_indexer.workflows import _landmark_visual_match

        name = "Basílica de Santa María de la Salud"

        self.assertTrue(_landmark_visual_match((name, "edificios historicos"), name))

    def test_scan_rejects_metadata_that_does_not_match_selected_local_identifier(self) -> None:
        from photos_indexer.workflows import ScanDependencies, run_scan

        with tempfile.TemporaryDirectory() as tmp:
            selected = SelectedPhoto("local-1", datetime(2026, 8, 24, 10, 0))
            calls: list[str] = []

            class Selector:
                def select(self, *, limit: int) -> PhotoSelection:
                    return PhotoSelection((selected,), 1, 1, 0, "authorized")

            class Bridge:
                def read(self, identifier: str) -> ScriptPhotoRecord:
                    return ScriptPhotoRecord(
                        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "local-other", "Wrong",
                        selected.creation_date, (),
                    )

                def export(self, identifier: str, destination: Path) -> Path:
                    calls.append("export")
                    raise AssertionError("identity mismatch reached export")

            class Vision:
                def check_model(self, model: str) -> str:
                    return "0.12.7"

                def analyze(self, model: str, image: Path) -> VisionResult:
                    calls.append("analyze")
                    raise AssertionError("identity mismatch reached analysis")

            result = run_scan(
                Path(tmp) / "runs",
                limit=1,
                dependencies=ScanDependencies(
                    platform_name=lambda: "Darwin", selector_factory=Selector, bridge_factory=Bridge,
                    vision_factory=Vision, recover_workspaces=lambda parent: [],
                ),
            )

            assert result.manifest is not None
            self.assertEqual(result.manifest.photos[0].uuid, None)
            self.assertEqual(result.manifest.photos[0].errors, [{"stage": "metadata", "code": "IDENTITY_MISMATCH"}])
            self.assertEqual(calls, [])

    def test_metadata_failure_is_non_actionable_and_never_exports_or_analyzes(self) -> None:
        from photos_indexer.workflows import ScanDependencies, run_scan

        with tempfile.TemporaryDirectory() as tmp:
            runs_root = Path(tmp) / "runs"
            selected = SelectedPhoto("local-opaque-id", datetime(2026, 8, 24, 10, 0))
            calls: list[tuple[str, str]] = []

            class Vision:
                def check_model(self, model: str) -> str:
                    return "0.12.7"

                def analyze(self, model: str, image: Path) -> VisionResult:
                    calls.append(("analyze", str(image)))
                    return VisionResult(("gato",), "", False, False, 0.99)

            class Selector:
                def select(self, *, limit: int) -> PhotoSelection:
                    return PhotoSelection((selected,), 1, 1, 0, "authorized")

            class Bridge:
                def read(self, identifier: str) -> ScriptPhotoRecord:
                    calls.append(("read", identifier))
                    raise RuntimeError("private lookup failure")

                def export(self, identifier: str, destination: Path) -> Path:
                    calls.append(("export", identifier))
                    raise AssertionError("metadata failure reached export")

                def replace_keywords(self, identifier: str, keywords: list[str]) -> tuple[str, ...]:
                    calls.append(("replace", identifier))
                    raise AssertionError("scan reached keyword setter")

            result = run_scan(
                runs_root,
                limit=1,
                dependencies=ScanDependencies(
                    platform_name=lambda: "Darwin", selector_factory=Selector, bridge_factory=Bridge,
                    vision_factory=Vision, recover_workspaces=lambda parent: [],
                ),
            )

            assert result.manifest is not None
            photo = result.manifest.photos[0]
            self.assertIsNone(photo.uuid)
            self.assertEqual(photo.scan_state, "analysis_failed")
            self.assertEqual(photo.proposed_keywords, [])
            self.assertEqual(photo.errors, [{"stage": "metadata", "code": "READ_FAILED"}])
            self.assertEqual(calls, [("read", "local-opaque-id")])

    def test_scan_exports_by_true_photoscript_uuid_after_local_identifier_lookup(self) -> None:
        from photos_indexer.workflows import ScanDependencies, run_scan

        with tempfile.TemporaryDirectory() as tmp:
            runs_root = Path(tmp) / "runs"
            true_uuid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
            selected = SelectedPhoto("photos-local-id", datetime(2026, 8, 24, 10, 0))
            calls: list[tuple[str, str]] = []
            workspace_parents: list[Path] = []

            class Vision:
                def check_model(self, model: str) -> str:
                    return "0.12.7"

                def analyze(self, model: str, image: Path) -> VisionResult:
                    return VisionResult((), "", False, False, 0.99)

            class Selector:
                def select(self, *, limit: int) -> PhotoSelection:
                    return PhotoSelection((selected,), 1, 1, 0, "authorized")

            class Bridge:
                def read(self, identifier: str) -> ScriptPhotoRecord:
                    calls.append(("read", identifier))
                    return ScriptPhotoRecord(true_uuid, "photos-local-id", "Title", selected.creation_date, ())

                def export(self, identifier: str, destination: Path) -> Path:
                    calls.append(("export", identifier))
                    exported = destination / "photo.png"
                    exported.write_bytes(b"\x89PNG\r\n\x1a\nlocal")
                    return exported

            result = run_scan(
                runs_root,
                limit=1,
                dependencies=ScanDependencies(
                    platform_name=lambda: "Darwin", selector_factory=Selector, bridge_factory=Bridge,
                    vision_factory=Vision,
                    workspace_factory=lambda parent, run_id: workspace_parents.append(parent) or FakeWorkspace(parent, run_id),
                    recover_workspaces=lambda parent: [],
                ),
            )

            self.assertEqual(result.exit_code, 0)
            self.assertEqual(calls, [("read", "photos-local-id"), ("export", true_uuid)])
            assert result.manifest_path is not None
            self.assertEqual(workspace_parents, [result.manifest_path.parent])

    def test_signal_cleanup_scope_cleans_and_restores_sigint_and_sigterm_handlers(self) -> None:
        from photos_indexer.workflows import _scoped_signal_cleanup

        prior_calls: list[int] = []

        def prior_handler(signum: int, frame: object) -> None:
            prior_calls.append(signum)

        class Signals:
            SIGINT = signal.SIGINT
            SIGTERM = signal.SIGTERM
            SIG_DFL = signal.SIG_DFL
            SIG_IGN = signal.SIG_IGN

            def __init__(self) -> None:
                self.handlers = {self.SIGINT: prior_handler, self.SIGTERM: prior_handler}

            def signal(self, signum: int, handler: object) -> object:
                previous = self.handlers[signum]
                self.handlers[signum] = handler
                return previous

        class Workspace:
            def __init__(self) -> None:
                self.cleanup_calls = 0

            def cleanup(self) -> None:
                self.cleanup_calls += 1

        signals = Signals()
        workspace = Workspace()
        with _scoped_signal_cleanup(workspace, signal_module=signals):
            installed_int = signals.handlers[signal.SIGINT]
            installed_term = signals.handlers[signal.SIGTERM]
            installed_int(signal.SIGINT, None)  # type: ignore[operator]
            installed_term(signal.SIGTERM, None)  # type: ignore[operator]

        self.assertEqual(workspace.cleanup_calls, 2)
        self.assertEqual(prior_calls, [signal.SIGINT, signal.SIGTERM])
        self.assertIs(signals.handlers[signal.SIGINT], prior_handler)
        self.assertIs(signals.handlers[signal.SIGTERM], prior_handler)

    def test_signal_cleanup_scope_is_safe_in_the_ipc_worker_thread(self) -> None:
        from threading import Thread

        from photos_indexer.workflows import _scoped_signal_cleanup

        entered: list[bool] = []
        errors: list[BaseException] = []

        def run() -> None:
            try:
                with _scoped_signal_cleanup(object()):
                    entered.append(True)
            except BaseException as error:
                errors.append(error)

        thread = Thread(target=run)
        thread.start()
        thread.join()

        self.assertEqual(errors, [])
        self.assertEqual(entered, [True])

    def test_scan_persists_a_cancelled_manifest_when_signal_interrupts_the_workspace(self) -> None:
        """A real signal must leave an auditable run, not an empty directory."""
        from photos_indexer.workflows import ScanDependencies, run_scan

        with tempfile.TemporaryDirectory() as tmp:
            runs_root = Path(tmp) / "runs"
            selected = SelectedPhoto("local-1", datetime(2026, 8, 24, 10, 0))

            class Selector:
                def select(self, *, limit: int) -> PhotoSelection:
                    return PhotoSelection((selected,), 1, 1, 0, "authorized")

            class Vision:
                def check_model(self, model: str) -> str:
                    return "0.12.7"

            class Bridge:
                pass

            class InterruptingScope:
                def __init__(self, workspace: object) -> None:
                    del workspace

                def __enter__(self) -> "InterruptingScope":
                    raise KeyboardInterrupt

                def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
                    return False

            result = run_scan(
                runs_root,
                limit=1,
                dependencies=ScanDependencies(
                    platform_name=lambda: "Darwin",
                    selector_factory=Selector,
                    bridge_factory=Bridge,
                    vision_factory=Vision,
                    workspace_factory=FakeWorkspace,
                    recover_workspaces=lambda _: [],
                    signal_scope=InterruptingScope,
                ),
            )

            run_dirs = list(runs_root.glob("*-*"))
            self.assertEqual(len(run_dirs), 1)
            self.assertEqual(result.exit_code, 1)
            self.assertIsNotNone(result.manifest_path)
            assert result.manifest_path is not None
            self.assertEqual(result.error_codes, ("CANCELLED",))
            self.assertEqual(result.next_action, "fix_failed_scan")
            self.assertEqual(load_manifest(result.manifest_path.parent).run_errors, [
                {"stage": "workspace", "code": "CANCELLED"},
            ])

    def test_stale_recovery_only_enters_private_recognized_run_directories(self) -> None:
        from photos_indexer.workflows import _recover_stale_workspaces

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            valid = root / "20260824T183000Z-12345678"
            valid.mkdir(mode=0o700)
            os.chmod(valid, 0o700)
            relaxed = root / "20260824T183001Z-abcdef12"
            relaxed.mkdir(mode=0o755)
            os.chmod(relaxed, 0o755)
            (root / "not-a-run").mkdir(mode=0o700)
            linked = root / "20260824T183002Z-deadbeef"
            linked.symlink_to(valid, target_is_directory=True)
            visited: list[Path] = []

            recovered = _recover_stale_workspaces(root, recover=lambda parent: visited.append(parent) or [parent / ".stale"])

            self.assertEqual(visited, [valid])
            self.assertEqual(recovered, [valid / ".stale"])

    def test_selector_failure_creates_no_run_and_workspace_failures_are_auditable(self) -> None:
        from photos_indexer.workflows import ScanDependencies, run_scan

        class Vision:
            def check_model(self, model: str) -> str:
                return "0.12.7"

        class BrokenSelector:
            def select(self, *, limit: int) -> PhotoSelection:
                raise RuntimeError("private selection failure")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "runs"
            selection_failure = run_scan(
                root,
                dependencies=ScanDependencies(
                    platform_name=lambda: "Darwin", vision_factory=Vision, selector_factory=BrokenSelector,
                    recover_workspaces=lambda parent: [],
                ),
            )
            self.assertEqual(selection_failure.exit_code, 2)
            # Selection happens before run-root creation so a PhotoKit/TCC or
            # selector failure leaves no empty durable workspace behind.
            self.assertFalse(root.exists())

        class Selector:
            def select(self, *, limit: int) -> PhotoSelection:
                return PhotoSelection((), 1, 0, 0, "authorized")

        class BrokenWorkspace:
            def __enter__(self) -> object:
                raise RuntimeError("private workspace enter failure")

            def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
                return None

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "runs"
            workspace_failure = run_scan(
                root,
                limit=1,
                dependencies=ScanDependencies(
                    platform_name=lambda: "Darwin", vision_factory=Vision, selector_factory=Selector,
                    bridge_factory=object, workspace_factory=lambda parent, run_id: BrokenWorkspace(),
                    recover_workspaces=lambda parent: [],
                ),
            )
            self.assertEqual(workspace_failure.exit_code, 2)
            self.assertIsNotNone(workspace_failure.manifest_path)
            assert workspace_failure.manifest_path is not None
            self.assertEqual(load_manifest(workspace_failure.manifest_path.parent).scan_status, "failed")

    def test_workspace_exit_failure_persists_completed_rows_as_failed_scan(self) -> None:
        from photos_indexer.workflows import ScanDependencies, run_scan

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "runs"
            true_uuid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
            selected = SelectedPhoto("local-1", datetime(2026, 8, 24, 10, 0))

            class Vision:
                def check_model(self, model: str) -> str:
                    return "0.12.7"

                def analyze(self, model: str, image: Path) -> VisionResult:
                    return VisionResult((), "", False, False, 0.99)

            class Selector:
                def select(self, *, limit: int) -> PhotoSelection:
                    return PhotoSelection((selected,), 1, 1, 0, "authorized")

            class Bridge:
                def read(self, identifier: str) -> ScriptPhotoRecord:
                    return ScriptPhotoRecord(true_uuid, "local-1", "One", selected.creation_date, ())

                def export(self, identifier: str, destination: Path) -> Path:
                    exported = destination / "photo.png"
                    exported.write_bytes(b"\x89PNG\r\n\x1a\nlocal")
                    return exported

            class ExitFailureWorkspace(FakeWorkspace):
                def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
                    super().__exit__(exc_type, exc, traceback)
                    raise RuntimeError("private workspace exit failure")

            result = run_scan(
                root,
                limit=1,
                dependencies=ScanDependencies(
                    platform_name=lambda: "Darwin", vision_factory=Vision, selector_factory=Selector,
                    bridge_factory=Bridge, workspace_factory=ExitFailureWorkspace,
                    recover_workspaces=lambda parent: [],
                ),
            )

            self.assertEqual(result.exit_code, 2)
            assert result.manifest_path is not None
            manifest = load_manifest(result.manifest_path.parent)
            self.assertEqual(manifest.scan_status, "failed")
            self.assertEqual(len(manifest.photos), 1)
    def test_scan_only_exposes_exact_pull_instruction_for_a_missing_model(self) -> None:
        from photos_indexer.adapters import OllamaModelMissingError, OllamaUnavailableError
        from photos_indexer.workflows import ScanDependencies, run_scan

        class Vision:
            def __init__(self, error: Exception) -> None:
                self.error = error

            def check_model(self, model: str) -> str:
                raise self.error

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "runs"
            created: list[str] = []
            common = {
                "platform_name": lambda: "Darwin",
                "selector_factory": lambda: created.append("selector"),
                "bridge_factory": lambda: created.append("bridge"),
            }
            missing = run_scan(
                root,
                model="vision:missing",
                dependencies=ScanDependencies(
                    **common,
                    vision_factory=lambda: Vision(OllamaModelMissingError("requested local model is not installed", model="vision:missing")),
                ),
            )
            unavailable = run_scan(
                root,
                model="vision:broken",
                dependencies=ScanDependencies(
                    **common,
                    vision_factory=lambda: Vision(OllamaUnavailableError("daemon unavailable")),
                ),
            )

            self.assertEqual(missing.exit_code, 2)
            self.assertEqual(missing.safe_instruction, "ollama pull vision:missing")
            self.assertEqual(unavailable.exit_code, 2)
            self.assertIsNone(unavailable.safe_instruction)
            self.assertEqual(created, [])
            self.assertFalse(root.exists())

    def test_scan_exposes_actionable_ollama_preflight_codes_before_opening_photos(self) -> None:
        from photos_indexer.adapters import (
            OllamaEndpointUnavailableError,
            OllamaNoVisionError,
            OllamaModelPolicyError,
            OllamaUnavailableError,
            OllamaVersionTooOldError,
        )
        from photos_indexer.workflows import ScanDependencies, run_scan

        class Vision:
            def __init__(self, error: Exception) -> None:
                self.error = error

            def check_model(self, model: str) -> str:
                raise self.error

        errors = (
            (OllamaEndpointUnavailableError("transport detail"), "OLLAMA_UNAVAILABLE"),
            (OllamaVersionTooOldError("version detail"), "OLLAMA_VERSION_OLD"),
            (OllamaNoVisionError("capability detail"), "OLLAMA_NO_VISION"),
            (OllamaModelPolicyError("cloud models are not allowed"), "MODEL_INVALID"),
            (OllamaUnavailableError("other local detail"), "OLLAMA_PREFLIGHT_FAILED"),
        )
        for error, code in errors:
            with self.subTest(code=code), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "runs"
                opened_photos: list[str] = []
                result = run_scan(
                    root,
                    model="vision:test",
                    dependencies=ScanDependencies(
                        platform_name=lambda: "Darwin",
                        vision_factory=lambda error=error: Vision(error),
                        selector_factory=lambda: opened_photos.append("selector"),
                    ),
                )

                self.assertEqual(result.error_codes, (code,))
                expected_action = "fix_fatal_error" if code == "MODEL_INVALID" else "retry_preflight"
                self.assertEqual(result.next_action, expected_action)
                if code == "MODEL_INVALID":
                    self.assertIsNone(result.safe_instruction)
                self.assertEqual(opened_photos, [])
                self.assertFalse(root.exists())

    def test_scan_denied_photos_access_is_actionable_and_does_not_create_empty_run_root(self) -> None:
        """TCC denial must stop before any durable run artifact is created."""
        from photos_indexer.adapters import PhotosAccessError
        from photos_indexer.workflows import ScanDependencies, run_scan

        class Vision:
            def check_model(self, model: str) -> str:
                return "0.12.7"

        class DeniedSelector:
            def select(self, *, limit: int) -> PhotoSelection:
                raise PhotosAccessError()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "runs"
            result = run_scan(
                root,
                limit=1,
                dependencies=ScanDependencies(
                    platform_name=lambda: "Darwin",
                    vision_factory=Vision,
                    selector_factory=DeniedSelector,
                ),
            )

            self.assertEqual(result.exit_code, 2)
            self.assertEqual(result.error_codes, ("PHOTOS_ACCESS_DENIED",))
            self.assertEqual(result.next_action, "grant_photos_access")
            self.assertIsNone(result.manifest_path)
            self.assertFalse(root.exists())

    def test_scan_rejects_a_denied_selection_before_creating_run_storage(self) -> None:
        """A malformed selector result must not bypass the PhotoKit TCC gate."""
        from photos_indexer.workflows import ScanDependencies, run_scan

        class Vision:
            def check_model(self, model: str) -> str:
                return "0.12.7"

        class Selector:
            def select(self, *, limit: int) -> PhotoSelection:
                return PhotoSelection((), limit, 0, 0, "denied")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "runs"
            bridge_created: list[bool] = []
            result = run_scan(
                root,
                limit=1,
                dependencies=ScanDependencies(
                    platform_name=lambda: "Darwin",
                    vision_factory=Vision,
                    selector_factory=Selector,
                    bridge_factory=lambda: bridge_created.append(True),
                ),
            )

            self.assertEqual(result.exit_code, 2)
            self.assertEqual(result.error_codes, ("PHOTOS_ACCESS_DENIED",))
            self.assertEqual(result.next_action, "grant_photos_access")
            self.assertIsNone(result.manifest_path)
            self.assertEqual(bridge_created, [])
            self.assertFalse(root.exists())

    def test_scan_rejects_non_string_selection_access_as_photo_access_denied(self) -> None:
        """Malformed selector access must not escape as an unhandled TypeError."""
        from photos_indexer.workflows import ScanDependencies, run_scan

        class Vision:
            def check_model(self, model: str) -> str:
                return "0.12.7"

        class Selector:
            def select(self, *, limit: int) -> PhotoSelection:
                return PhotoSelection((), limit, 0, 0, [])  # type: ignore[arg-type]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "runs"
            result = run_scan(
                root,
                limit=1,
                dependencies=ScanDependencies(
                    platform_name=lambda: "Darwin",
                    vision_factory=Vision,
                    selector_factory=Selector,
                ),
            )

            self.assertEqual(result.exit_code, 2)
            self.assertEqual(result.error_codes, ("PHOTOS_ACCESS_DENIED",))
            self.assertEqual(result.next_action, "grant_photos_access")
            self.assertIsNone(result.manifest_path)
            self.assertFalse(root.exists())

    def test_scan_persists_automation_denial_for_audit_but_never_suggests_apply(self) -> None:
        """A PhotoScript TCC failure remains auditable without an apply CTA."""
        from photos_indexer.adapters import PhotoScriptPermissionError
        from photos_indexer.workflows import ScanDependencies, run_scan

        selected = SelectedPhoto("local-1", datetime(2026, 8, 24, 10, 0))

        class Vision:
            def check_model(self, model: str) -> str:
                return "0.12.7"

        class Selector:
            def select(self, *, limit: int) -> PhotoSelection:
                return PhotoSelection((selected,), 1, 1, 0, "authorized")

        class DeniedBridge:
            def read(self, local_id: str) -> ScriptPhotoRecord:
                raise PhotoScriptPermissionError()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "runs"
            result = run_scan(
                root,
                limit=1,
                dependencies=ScanDependencies(
                    platform_name=lambda: "Darwin",
                    vision_factory=Vision,
                    selector_factory=Selector,
                    bridge_factory=DeniedBridge,
                    recover_workspaces=lambda _: [],
                ),
            )

            self.assertEqual(result.exit_code, 1)
            self.assertEqual(result.error_codes, ("PHOTOS_AUTOMATION_DENIED",))
            self.assertEqual(result.next_action, "grant_photos_automation")
            self.assertNotEqual(result.next_action, "review_then_apply")
            assert result.manifest is not None
            self.assertEqual(result.manifest.scan_status, "ready_with_errors")
            self.assertEqual(
                result.manifest.photos[0].errors,
                [{"stage": "metadata", "code": "PHOTOS_AUTOMATION_DENIED"}],
            )
            self.assertTrue(result.manifest_path is not None and result.manifest_path.exists())

    def test_scan_surfaces_automation_denial_when_bridge_initialization_is_blocked(self) -> None:
        """A setup-time Apple Events denial must be actionable and leave no run root."""
        from photos_indexer.adapters import PhotoScriptPermissionError
        from photos_indexer.workflows import ScanDependencies, run_scan

        selected = SelectedPhoto("local-1", datetime(2026, 8, 24, 10, 0))

        class Vision:
            def check_model(self, model: str) -> str:
                return "0.12.7"

        class Selector:
            def select(self, *, limit: int) -> PhotoSelection:
                return PhotoSelection((selected,), 1, 1, 0, "authorized")

        class DeniedBridge:
            def __init__(self) -> None:
                raise PhotoScriptPermissionError()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "runs"
            result = run_scan(
                root,
                limit=1,
                dependencies=ScanDependencies(
                    platform_name=lambda: "Darwin",
                    vision_factory=Vision,
                    selector_factory=Selector,
                    bridge_factory=DeniedBridge,
                ),
            )

            self.assertEqual(result.exit_code, 2)
            self.assertEqual(result.error_codes, ("PHOTOS_AUTOMATION_DENIED",))
            self.assertEqual(result.next_action, "grant_photos_automation")
            self.assertIsNone(result.manifest_path)
            self.assertFalse(root.exists())

    def test_scan_recovers_existing_exports_before_bridge_setup_failure(self) -> None:
        from photos_indexer.adapters import PhotoScriptUnavailableError
        from photos_indexer.workflows import ScanDependencies, run_scan

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "runs"
            root.mkdir(mode=0o700)
            os.chmod(root, 0o700)
            recovered: list[Path] = []

            class Vision:
                def check_model(self, model: str) -> str:
                    return "0.12.7"

            class Selector:
                def select(self, *, limit: int) -> PhotoSelection:
                    return PhotoSelection((), limit, 0, 0, "authorized")

            def bridge_factory() -> object:
                raise PhotoScriptUnavailableError()

            result = run_scan(
                root,
                limit=1,
                dependencies=ScanDependencies(
                    platform_name=lambda: "Darwin",
                    vision_factory=Vision,
                    selector_factory=Selector,
                    bridge_factory=bridge_factory,
                    recover_workspaces=lambda parent: recovered.append(parent) or [],
                ),
            )

            self.assertEqual(result.error_codes, ("PHOTOSCRIPT_UNAVAILABLE",))
            self.assertEqual(recovered, [root])
            self.assertTrue(root.exists())

    def test_scan_surfaces_photoscript_compile_error_as_actionable_setup_failure(self) -> None:
        from photos_indexer.adapters import PhotoScriptUnavailableError
        from photos_indexer.workflows import ScanDependencies, run_scan

        class Vision:
            def check_model(self, model: str) -> str:
                return "0.12.7"

        class Selector:
            def select(self, *, limit: int) -> PhotoSelection:
                return PhotoSelection((SelectedPhoto("local-1", datetime(2026, 8, 24, 10, 0)),), limit, 1, 0, "authorized")

        class BrokenBridge:
            def __init__(self) -> None:
                raise PhotoScriptUnavailableError()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "runs"
            result = run_scan(
                root,
                limit=1,
                dependencies=ScanDependencies(
                    platform_name=lambda: "Darwin",
                    vision_factory=Vision,
                    selector_factory=Selector,
                    bridge_factory=BrokenBridge,
                ),
            )

        self.assertEqual(result.error_codes, ("PHOTOSCRIPT_UNAVAILABLE",))
        self.assertEqual(result.next_action, "fix_fatal_error")
        self.assertIsNone(result.manifest_path)

    def test_scan_keeps_mid_run_photoscript_incompatibility_actionable_in_status(self) -> None:
        """A bridge failure after setup still requires compatibility repair, not a blind rescan."""
        from photos_indexer.adapters import PhotoScriptUnavailableError
        from photos_indexer.workflows import ScanDependencies, run_scan, run_status

        selected = SelectedPhoto("local-1", datetime(2026, 8, 24, 10, 0))

        class Vision:
            def check_model(self, model: str) -> str:
                return "0.12.7"

        class Selector:
            def select(self, *, limit: int) -> PhotoSelection:
                return PhotoSelection((selected,), limit, 1, 0, "authorized")

        class BrokenBridge:
            def read(self, local_id: str) -> ScriptPhotoRecord:
                raise PhotoScriptUnavailableError()

        with tempfile.TemporaryDirectory() as tmp:
            result = run_scan(
                Path(tmp) / "runs",
                limit=1,
                dependencies=ScanDependencies(
                    platform_name=lambda: "Darwin",
                    vision_factory=Vision,
                    selector_factory=Selector,
                    bridge_factory=BrokenBridge,
                    recover_workspaces=lambda _: [],
                ),
            )

            self.assertEqual(result.exit_code, 1)
            self.assertEqual(result.error_codes, ("PHOTOSCRIPT_UNAVAILABLE",))
            self.assertEqual(result.next_action, "fix_fatal_error")
            assert result.manifest_path is not None
            persisted = run_status(result.manifest_path)
            self.assertEqual(persisted.exit_code, 1)
            self.assertEqual(persisted.error_codes, ("PHOTOSCRIPT_UNAVAILABLE",))
            self.assertEqual(persisted.next_action, "fix_fatal_error")

    def test_ipc_preflight_keeps_successful_model_diagnostics_when_another_model_is_missing(self) -> None:
        from photos_indexer.adapters import OllamaModelMissingError
        from photos_indexer.ipc import _default_preflight
        from photos_indexer.service import CancellationToken

        class Vision:
            def check_model(self, model: str) -> str:
                if model == "qwen3-vl:8b":
                    raise OllamaModelMissingError("missing", model=model)
                return "0.12.7"

        with patch("photos_indexer.ipc.OllamaVisionClient", return_value=Vision()):
            exit_code, details = _default_preflight(
                {"models": ["qwen3-vl:4b", "qwen3-vl:8b"]},
                CancellationToken(),
            )

        self.assertEqual(exit_code, 2)
        self.assertEqual(details["models"], {"qwen3-vl:4b": "0.12.7"})
        self.assertEqual(details["error_codes"], ["OLLAMA_MODEL_MISSING"])
        self.assertEqual(details["safe_instruction"], "ollama pull qwen3-vl:8b")
    def test_scan_preflights_then_writes_one_sanitized_row_per_selected_photo(self) -> None:
        from photos_indexer.workflows import DEFAULT_MODEL, ScanDependencies, run_scan

        with tempfile.TemporaryDirectory() as tmp:
            runs_root = Path(tmp) / "runs"
            events: list[str] = []
            now = datetime(2026, 8, 24, 18, 30, tzinfo=timezone.utc)
            run_uuid = uuid.UUID("12345678-1234-5678-9234-567812345678")
            first_uuid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
            selection = PhotoSelection(
                photos=(
                    SelectedPhoto("local-1", datetime(2026, 8, 24, 10, 0)),
                    SelectedPhoto("local-2", datetime(2026, 8, 23, 10, 0)),
                ),
                requested=3,
                eligible=2,
                screenshots_excluded=1,
                access="limited",
            )

            class Selector:
                def select(self, *, limit: int) -> PhotoSelection:
                    events.append("photos-select")
                    self.assert_limit = limit
                    return selection

            bridge = FakeBridge({
                "local-1": ScriptPhotoRecord(
                    first_uuid, "local-1", "Vacaciones familiares", datetime(2026, 8, 24, 10, 0), ("PERRO",)
                ),
                "local-2": RuntimeError("private raw metadata failure"),
            })
            vision = FakeVision(
                {
                    first_uuid: VisionResult(("perro", "playa"), "Una foto de un perro en una playa.", False, False, 0.91),
                    str(uuid.uuid5(uuid.NAMESPACE_URL, "photos-local-keyword-indexer:local-2")): VisionResult(
                        ("gato",), "another private caption", False, False, 0.59
                    ),
                },
                events,
                runs_root,
            )
            workspace_holder: list[FakeWorkspace] = []

            def workspace_factory(parent: Path, run_id: str) -> FakeWorkspace:
                workspace = FakeWorkspace(parent, run_id)
                workspace_holder.append(workspace)
                bridge.workspace = workspace
                return workspace

            dependencies = ScanDependencies(
                platform_name=lambda: "Darwin",
                now_utc=lambda: now,
                uuid4=lambda: run_uuid,
                selector_factory=Selector,
                bridge_factory=lambda: bridge,
                vision_factory=lambda: vision,
                workspace_factory=workspace_factory,
                recover_workspaces=FakeWorkspace.recover,
            )
            progress: list[tuple[str | None, str]] = []

            result = run_scan(
                runs_root,
                limit=3,
                include_caption=True,
                dependencies=dependencies,
                progress_callback=lambda photo: progress.append((photo.uuid, photo.scan_state)),
            )

            self.assertEqual(events[:2], ["ollama-check", "photos-select"])
            self.assertEqual(vision.checked, [DEFAULT_MODEL])
            self.assertEqual(result.exit_code, 1)
            self.assertEqual(result.warning_codes, ("FEWER_PHOTOS_AVAILABLE", "PHOTOS_ACCESS_LIMITED"))
            self.assertEqual(result.manifest_path.parent.name, "20260824T183000Z-12345678")
            self.assertEqual(stat.S_IMODE(result.manifest_path.parent.stat().st_mode), 0o700)
            manifest = load_manifest(result.manifest_path.parent)
            self.assertEqual(manifest.scan_status, "ready_with_errors")
            self.assertEqual(manifest.policy["confidence_threshold"], 0.60)
            self.assertTrue(manifest.selection["captions_requested"])
            self.assertEqual(manifest.photos[0].proposed_caption, "Una foto de un perro en una playa.")
            self.assertEqual(manifest.summary, {"ready": 1, "noop": 0, "analysis_failed": 1})
            self.assertEqual(progress, [(first_uuid, "ready"), (None, "analysis_failed")])
            self.assertEqual(manifest.photos[0].proposed_keywords, ["playa"])
            self.assertEqual(manifest.photos[1].title, "")
            self.assertEqual(manifest.photos[1].existing_keywords, [])
            self.assertIsNone(manifest.photos[1].uuid)
            self.assertEqual(manifest.photos[1].proposed_keywords, [])
            self.assertEqual(manifest.photos[1].scan_state, "analysis_failed")
            self.assertEqual(
                manifest.photos[1].errors,
                [{"stage": "metadata", "code": "READ_FAILED"}],
            )
            self.assertEqual(bridge.replace_calls, [])
            self.assertEqual(FakeWorkspace.recovered[-1], runs_root)
            self.assertEqual(len(workspace_holder), 1)
            with (result.manifest_path.parent / "preview.csv").open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 2)
            self.assertIn("caption_status", rows[0])
            self.assertNotIn("Una foto de un perro en una playa.", str(rows))
            self.assertNotIn("private raw", str(rows).casefold())

    def test_scan_rejects_platform_limit_and_empty_model_before_creating_dependencies(self) -> None:
        from photos_indexer.workflows import ScanDependencies, run_scan

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "runs"
            created: list[str] = []
            dependencies = ScanDependencies(
                platform_name=lambda: "Linux",
                selector_factory=lambda: created.append("selector"),
                bridge_factory=lambda: created.append("bridge"),
                vision_factory=lambda: created.append("vision"),
            )
            for kwargs in ({}, {"limit": 0}, {"limit": 501}, {"model": ""}, {"model": "vision;unsafe"}):
                result = run_scan(root, dependencies=dependencies, **kwargs)
                self.assertEqual(result.exit_code, 2)
            self.assertEqual(created, [])
            self.assertFalse(root.exists())

            invalid_local = run_scan(
                root,
                model="vision;unsafe",
                dependencies=ScanDependencies(
                    platform_name=lambda: "Darwin",
                    vision_factory=lambda: created.append("vision"),
                ),
            )
            self.assertEqual(invalid_local.error_codes, ("MODEL_INVALID",))
            self.assertEqual(created, [])

    def test_scan_rejects_non_boolean_random_selection_before_dependencies(self) -> None:
        from photos_indexer.workflows import ScanDependencies, run_scan

        created: list[str] = []
        result = run_scan(
            Path("runs"),
            random_selection=1,  # type: ignore[arg-type]
            dependencies=ScanDependencies(
                platform_name=lambda: "Darwin",
                vision_factory=lambda: created.append("vision"),
            ),
        )

        self.assertEqual(result.exit_code, 2)
        self.assertEqual(result.error_codes, ("SCAN_OPTIONS_INVALID",))
        self.assertEqual(created, [])

    def test_scan_continues_after_analysis_failure_and_empty_keywords_are_noop(self) -> None:
        from photos_indexer.workflows import ScanDependencies, run_scan

        with tempfile.TemporaryDirectory() as tmp:
            runs_root = Path(tmp) / "runs"
            first_uuid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
            second_uuid = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
            selection = PhotoSelection(
                photos=(
                    SelectedPhoto("local-1", datetime(2026, 8, 24, 10, 0)),
                    SelectedPhoto("local-2", datetime(2026, 8, 23, 10, 0)),
                ),
                requested=2,
                eligible=2,
                screenshots_excluded=0,
                access="authorized",
            )

            class Selector:
                def select(self, *, limit: int) -> PhotoSelection:
                    return selection

            bridge = FakeBridge({
                "local-1": ScriptPhotoRecord(first_uuid, "local-1", "One", datetime(2026, 8, 24, 10), ()),
                "local-2": ScriptPhotoRecord(second_uuid, "local-2", "Two", datetime(2026, 8, 23, 10), ()),
            })
            vision = FakeVision(
                {
                    first_uuid: RuntimeError("raw model failure must not persist"),
                    second_uuid: VisionResult((), "private caption", False, False, 0.99),
                },
                [],
                runs_root,
            )

            def workspace_factory(parent: Path, run_id: str) -> FakeWorkspace:
                workspace = FakeWorkspace(parent, run_id)
                bridge.workspace = workspace
                return workspace

            result = run_scan(
                runs_root,
                dependencies=ScanDependencies(
                    platform_name=lambda: "Darwin",
                    selector_factory=Selector,
                    bridge_factory=lambda: bridge,
                    vision_factory=lambda: vision,
                    workspace_factory=workspace_factory,
                    recover_workspaces=lambda parent: [],
                ),
            )

            self.assertEqual(result.exit_code, 1)
            self.assertEqual(result.error_codes, ("ANALYSIS_FAILED",))
            assert result.manifest is not None
            self.assertEqual([photo.scan_state for photo in result.manifest.photos], ["analysis_failed", "noop"])
            self.assertEqual(result.manifest.photos[0].errors, [{"stage": "analysis", "code": "ANALYSIS_FAILED"}])
            self.assertNotIn("raw model failure", str(result.manifest.to_dict()))

    def test_analysis_error_codes_classify_bounded_ollama_failures(self) -> None:
        from photos_indexer.adapters import (
            OllamaImagePreparationError,
            OllamaRequestError,
            OllamaResponseError,
        )
        from photos_indexer.workflows import _adapter_error_code

        self.assertEqual(
            _adapter_error_code(OllamaResponseError("raw response"), "ANALYSIS_FAILED"),
            "OLLAMA_RESPONSE_INVALID",
        )
        self.assertEqual(
            _adapter_error_code(OllamaRequestError("raw request"), "ANALYSIS_FAILED"),
            "OLLAMA_REQUEST_FAILED",
        )
        self.assertEqual(
            _adapter_error_code(OllamaImagePreparationError("raw image"), "ANALYSIS_FAILED"),
            "VISION_IMAGE_INVALID",
        )

    def test_scan_continues_after_malformed_metadata_record(self) -> None:
        """A malformed PhotoScript record must fail only its photo."""
        from photos_indexer.workflows import ScanDependencies, run_scan

        with tempfile.TemporaryDirectory() as tmp:
            runs_root = Path(tmp) / "runs"
            first_uuid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
            second_uuid = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
            selection = PhotoSelection(
                photos=(
                    SelectedPhoto("local-1", datetime(2026, 8, 24, 10, 0)),
                    SelectedPhoto("local-2", datetime(2026, 8, 23, 10, 0)),
                ),
                requested=2,
                eligible=2,
                screenshots_excluded=0,
                access="authorized",
            )

            class Selector:
                def select(self, *, limit: int) -> PhotoSelection:
                    return selection

            class Bridge(FakeBridge):
                def __init__(self) -> None:
                    super().__init__({
                        "local-1": ScriptPhotoRecord(
                            first_uuid, "local-1", 123, datetime(2026, 8, 24, 10, 0), ()  # type: ignore[arg-type]
                        ),
                        "local-2": ScriptPhotoRecord(
                            second_uuid, "local-2", "Two", datetime(2026, 8, 23, 10, 0), ()
                        ),
                    })
                    self.exported_ids: list[str] = []

                def export(self, local_id: str, destination: Path) -> Path:
                    self.exported_ids.append(local_id)
                    return super().export(local_id, destination)

            bridge = Bridge()
            vision = FakeVision(
                {second_uuid: VisionResult(("playa",), "", False, False, 0.99)}, [], runs_root
            )

            def workspace_factory(parent: Path, run_id: str) -> FakeWorkspace:
                workspace = FakeWorkspace(parent, run_id)
                bridge.workspace = workspace
                return workspace

            result = run_scan(
                runs_root,
                dependencies=ScanDependencies(
                    platform_name=lambda: "Darwin",
                    selector_factory=Selector,
                    bridge_factory=lambda: bridge,
                    vision_factory=lambda: vision,
                    workspace_factory=workspace_factory,
                    recover_workspaces=lambda parent: [],
                ),
            )

            self.assertEqual(result.exit_code, 1)
            assert result.manifest is not None
            self.assertEqual([photo.scan_state for photo in result.manifest.photos], ["analysis_failed", "ready"])
            self.assertEqual(result.manifest.photos[0].errors, [{"stage": "metadata", "code": "IDENTITY_MISMATCH"}])
            self.assertEqual(result.manifest.photos[1].proposed_keywords, ["playa"])
            self.assertEqual(bridge.exported_ids, ["bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"])

            from photos_indexer.workflows import _safe_photo_metadata

            self.assertIsNone(_safe_photo_metadata(
                ScriptPhotoRecord(
                    first_uuid, "local-1", "Valid", datetime(2026, 8, 24, 10, 0), (),
                    [10**10000, 0],  # type: ignore[list-item]
                ),
                "local-1",
            ))

    def test_scan_exposes_ollama_unavailable_when_analysis_loses_the_endpoint(self) -> None:
        from photos_indexer.adapters import OllamaEndpointUnavailableError
        from photos_indexer.workflows import ScanDependencies, run_scan

        with tempfile.TemporaryDirectory() as tmp:
            runs_root = Path(tmp) / "runs"
            photo_uuid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
            selection = PhotoSelection(
                photos=(SelectedPhoto("local-1", datetime(2026, 8, 24, 10, 0)),),
                requested=1,
                eligible=1,
                screenshots_excluded=0,
                access="authorized",
            )

            class Selector:
                def select(self, *, limit: int) -> PhotoSelection:
                    return selection

            bridge = FakeBridge({
                "local-1": ScriptPhotoRecord(photo_uuid, "local-1", "One", selection.photos[0].creation_date, ()),
            })
            vision = FakeVision(
                {photo_uuid: OllamaEndpointUnavailableError("daemon unavailable")},
                [],
                runs_root,
            )

            def workspace_factory(parent: Path, run_id: str) -> FakeWorkspace:
                workspace = FakeWorkspace(parent, run_id)
                bridge.workspace = workspace
                return workspace

            result = run_scan(
                runs_root,
                dependencies=ScanDependencies(
                    platform_name=lambda: "Darwin",
                    selector_factory=Selector,
                    bridge_factory=lambda: bridge,
                    vision_factory=lambda: vision,
                    workspace_factory=workspace_factory,
                    recover_workspaces=lambda parent: [],
                ),
            )

            self.assertEqual(result.error_codes, ("OLLAMA_UNAVAILABLE",))
            assert result.manifest is not None
            self.assertEqual(result.manifest.photos[0].errors, [{"stage": "analysis", "code": "OLLAMA_UNAVAILABLE"}])

    def test_scan_with_caption_only_proposal_requests_review_and_apply(self) -> None:
        """A valid caption is actionable even when the model proposes no keyword."""
        from photos_indexer.workflows import ScanDependencies, run_scan

        with tempfile.TemporaryDirectory() as tmp:
            selected = SelectedPhoto("local-1", datetime(2026, 8, 24, 10, 0))
            photo_uuid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"

            class Selector:
                def select(self, *, limit: int) -> PhotoSelection:
                    return PhotoSelection((selected,), 1, 1, 0, "authorized")

            class Bridge:
                def __init__(self) -> None:
                    self.workspace: FakeWorkspace | None = None

                def read(self, local_id: str) -> ScriptPhotoRecord:
                    return ScriptPhotoRecord(photo_uuid, local_id, "", selected.creation_date, ())

                def export(self, local_id: str, destination: Path) -> Path:
                    exported = destination / "photo.png"
                    exported.write_bytes(b"\x89PNG\r\n\x1a\nlocal")
                    assert self.workspace is not None
                    self.workspace.exported.append(exported)
                    return exported

            bridge = Bridge()

            class Vision:
                def check_model(self, model: str) -> str:
                    return "0.12.7"

                def analyze(self, model: str, image: Path) -> VisionResult:
                    return VisionResult((), "Una playa visible.", False, False, 0.95)

            def workspace_factory(parent: Path, run_id: str) -> FakeWorkspace:
                workspace = FakeWorkspace(parent, run_id)
                bridge.workspace = workspace
                return workspace

            result = run_scan(
                Path(tmp) / "runs",
                limit=1,
                include_caption=True,
                dependencies=ScanDependencies(
                    platform_name=lambda: "Darwin",
                    selector_factory=Selector,
                    bridge_factory=lambda: bridge,
                    vision_factory=Vision,
                    workspace_factory=workspace_factory,
                    recover_workspaces=lambda parent: [],
                ),
            )

            assert result.manifest is not None
            self.assertEqual(result.exit_code, 0)
            self.assertEqual(result.next_action, "review_then_apply")
            self.assertEqual(result.manifest.photos[0].scan_state, "noop")
            self.assertEqual(result.manifest.photos[0].proposed_keywords, [])
            self.assertEqual(result.manifest.photos[0].proposed_caption, "Una playa visible.")

    def test_scan_keeps_open_semantic_document_inference_without_writing(self) -> None:
        from photos_indexer.workflows import ScanDependencies, run_scan

        with tempfile.TemporaryDirectory() as tmp:
            selected = SelectedPhoto("local-1", datetime(2026, 8, 24, 10, 0))
            photo_uuid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
            mutation_calls: list[str] = []

            class Selector:
                def select(self, *, limit: int) -> PhotoSelection:
                    return PhotoSelection((selected,), 1, 1, 0, "authorized")

            class Bridge:
                workspace: FakeWorkspace | None = None

                def read(self, local_id: str) -> ScriptPhotoRecord:
                    return ScriptPhotoRecord(photo_uuid, local_id, "", selected.creation_date, ())

                def export(self, local_id: str, destination: Path) -> Path:
                    exported = destination / "photo.png"
                    exported.write_bytes(b"\x89PNG\r\n\x1a\nlocal")
                    assert self.workspace is not None
                    self.workspace.exported.append(exported)
                    return exported

                def replace_keywords(self, local_id: str, keywords: list[str]) -> tuple[str, ...]:
                    mutation_calls.append("keywords")
                    return tuple(keywords)

                def replace_description(self, local_id: str, caption: str) -> str:
                    mutation_calls.append("caption")
                    return caption

            bridge = Bridge()

            class Vision:
                def check_model(self, model: str) -> str:
                    return "0.32.1"

                def analyze(self, model: str, image: Path) -> VisionResult:
                    return VisionResult(
                        (
                            "prescripción óptica",
                            "receta de lentes",
                            "graduación de lentes",
                            "texto manuscrito",
                        ),
                        "Prescripción óptica con graduación manuscrita para lentes.",
                        False,
                        True,
                        0.94,
                    )

            def workspace_factory(parent: Path, run_id: str) -> FakeWorkspace:
                workspace = FakeWorkspace(parent, run_id)
                bridge.workspace = workspace
                return workspace

            result = run_scan(
                Path(tmp) / "runs",
                limit=1,
                include_caption=True,
                dependencies=ScanDependencies(
                    platform_name=lambda: "Darwin",
                    selector_factory=Selector,
                    bridge_factory=lambda: bridge,
                    vision_factory=Vision,
                    workspace_factory=workspace_factory,
                    recover_workspaces=lambda parent: [],
                ),
            )

            assert result.manifest is not None
            photo = result.manifest.photos[0]
            self.assertEqual(result.exit_code, 0)
            self.assertEqual(photo.proposed_keywords, [
                "texto",
                "prescripción óptica",
                "receta de lentes",
                "graduación de lentes",
                "texto manuscrito",
            ])
            self.assertEqual(
                photo.proposed_caption,
                "Prescripción óptica con graduación manuscrita para lentes.",
            )
            self.assertEqual(mutation_calls, [])

    def test_scan_uses_a_safe_keyword_caption_when_model_caption_is_rejected(self) -> None:
        from photos_indexer.workflows import ScanDependencies, run_scan

        with tempfile.TemporaryDirectory() as tmp:
            selected = SelectedPhoto("local-1", datetime(2026, 8, 24, 10, 0))
            photo_uuid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"

            class Selector:
                def select(self, *, limit: int) -> PhotoSelection:
                    return PhotoSelection((selected,), 1, 1, 0, "authorized")

            class Bridge:
                workspace: FakeWorkspace | None = None

                def read(self, local_id: str) -> ScriptPhotoRecord:
                    return ScriptPhotoRecord(photo_uuid, local_id, "", selected.creation_date, ())

                def export(self, local_id: str, destination: Path) -> Path:
                    exported = destination / "photo.png"
                    exported.write_bytes(b"\x89PNG\r\n\x1a\nlocal")
                    assert self.workspace is not None
                    self.workspace.exported.append(exported)
                    return exported

            bridge = Bridge()

            class Vision:
                def check_model(self, model: str) -> str:
                    return "0.12.7"

                def analyze(self, model: str, image: Path) -> VisionResult:
                    return VisionResult(("texto", "mesa", "silla"), "Una foto de texto, mesa y silla.", False, True, 0.95)

            def workspace_factory(parent: Path, run_id: str) -> FakeWorkspace:
                workspace = FakeWorkspace(parent, run_id)
                bridge.workspace = workspace
                return workspace

            result = run_scan(
                Path(tmp) / "runs",
                limit=1,
                include_caption=True,
                dependencies=ScanDependencies(
                    platform_name=lambda: "Darwin",
                    selector_factory=Selector,
                    bridge_factory=lambda: bridge,
                    vision_factory=Vision,
                    workspace_factory=workspace_factory,
                    recover_workspaces=lambda parent: [],
                ),
            )

            assert result.manifest is not None
            self.assertEqual(result.manifest.photos[0].proposed_caption, "Una foto de mesa y silla.")

    def test_scan_wires_apple_maps_places_into_contextual_caption_validation(self) -> None:
        from photos_indexer.workflows import ScanDependencies, run_scan

        with tempfile.TemporaryDirectory() as tmp:
            selected = SelectedPhoto("local-1", datetime(2026, 8, 24, 10, 0))
            photo_uuid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"

            class Selector:
                def select(self, *, limit: int) -> PhotoSelection:
                    return PhotoSelection((selected,), 1, 1, 0, "authorized")

            class Bridge:
                workspace: FakeWorkspace | None = None

                def read(self, local_id: str) -> ScriptPhotoRecord:
                    return ScriptPhotoRecord(photo_uuid, local_id, "", selected.creation_date, (), (45.44, 12.33))

                def export(self, local_id: str, destination: Path) -> Path:
                    exported = destination / "photo.png"
                    exported.write_bytes(b"\x89PNG\r\n\x1a\nlocal")
                    assert self.workspace is not None
                    self.workspace.exported.append(exported)
                    return exported

            bridge = Bridge()

            class Vision:
                def check_model(self, model: str) -> str:
                    return "0.12.7"

                def analyze(self, model: str, image: Path, **kwargs: object) -> VisionResult:
                    return VisionResult((), "Una vista del Canal Grande en Venecia.", False, False, 0.95)

            class Places:
                def nearby(self, location, *, cancel_requested=None):
                    return ("Canal Grande", "Venecia")

            def workspace_factory(parent: Path, run_id: str) -> FakeWorkspace:
                workspace = FakeWorkspace(parent, run_id)
                bridge.workspace = workspace
                return workspace

            result = run_scan(
                Path(tmp) / "runs",
                limit=1,
                apple_maps=True,
                include_caption=True,
                dependencies=ScanDependencies(
                    platform_name=lambda: "Darwin",
                    selector_factory=Selector,
                    bridge_factory=lambda: bridge,
                    vision_factory=Vision,
                    places_factory=Places,
                    workspace_factory=workspace_factory,
                    recover_workspaces=lambda parent: [],
                ),
            )

            assert result.manifest is not None
            self.assertEqual(result.exit_code, 0)
            self.assertEqual(result.manifest.photos[0].proposed_caption, "Una vista del Canal Grande en Venecia.")

    def test_scan_records_export_failure_separately_and_continues(self) -> None:
        from photos_indexer.workflows import ScanDependencies, run_scan

        with tempfile.TemporaryDirectory() as tmp:
            runs_root = Path(tmp) / "runs"
            first_uuid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
            second_uuid = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
            selection = PhotoSelection(
                photos=(
                    SelectedPhoto("local-1", datetime(2026, 8, 24, 10, 0)),
                    SelectedPhoto("local-2", datetime(2026, 8, 23, 10, 0)),
                ),
                requested=2, eligible=2, screenshots_excluded=0, access="authorized",
            )

            class Selector:
                def select(self, *, limit: int) -> PhotoSelection:
                    return selection

            bridge = FakeBridge({
                "local-1": ScriptPhotoRecord(first_uuid, "local-1", "One", datetime(2026, 8, 24, 10), ()),
                "local-2": ScriptPhotoRecord(second_uuid, "local-2", "Two", datetime(2026, 8, 23, 10), ()),
            })
            bridge.export_failures.add("local-1")
            vision = FakeVision({second_uuid: VisionResult(("gato",), "", False, False, 0.99)}, [], runs_root)

            def workspace_factory(parent: Path, run_id: str) -> FakeWorkspace:
                workspace = FakeWorkspace(parent, run_id)
                bridge.workspace = workspace
                return workspace

            result = run_scan(
                runs_root,
                dependencies=ScanDependencies(
                    platform_name=lambda: "Darwin", selector_factory=Selector, bridge_factory=lambda: bridge,
                    vision_factory=lambda: vision, workspace_factory=workspace_factory,
                    recover_workspaces=lambda parent: [],
                ),
            )

            assert result.manifest is not None
            self.assertEqual(result.manifest.photos[0].errors, [{"stage": "export", "code": "EXPORT_FAILED"}])
            self.assertEqual(result.manifest.photos[1].proposed_keywords, ["gato"])

    def test_scan_rejects_a_symlink_runs_root_without_changing_its_target(self) -> None:
        from photos_indexer.workflows import ScanDependencies, run_scan

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target"
            target.mkdir(mode=0o755)
            os.chmod(target, 0o755)
            linked = root / "runs"
            linked.symlink_to(target, target_is_directory=True)
            opened: list[str] = []

            class Vision:
                def check_model(self, model: str) -> str:
                    return "0.12.7"

            result = run_scan(
                linked,
                dependencies=ScanDependencies(
                    platform_name=lambda: "Darwin",
                    vision_factory=Vision,
                    selector_factory=lambda: opened.append("photos"),
                    recover_workspaces=lambda parent: opened.append("recover"),  # type: ignore[arg-type]
                ),
            )

            self.assertEqual(result.exit_code, 2)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o755)
            self.assertEqual(opened, [])
            self.assertEqual(list(target.iterdir()), [])

    def test_scan_removes_empty_run_when_manifest_persistence_fails(self) -> None:
        from photos_indexer.workflows import ScanDependencies, run_scan

        with tempfile.TemporaryDirectory() as tmp:
            runs_root = Path(tmp) / "runs"
            selected = SelectedPhoto("local-1", datetime(2026, 8, 24, 10, 0))

            class Selector:
                def select(self, *, limit: int) -> PhotoSelection:
                    return PhotoSelection((selected,), 1, 1, 0, "authorized")

            class Bridge:
                def read(self, identifier: str) -> ScriptPhotoRecord:
                    return ScriptPhotoRecord(
                        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", identifier, "", selected.creation_date, (),
                    )

            class Vision:
                def check_model(self, model: str) -> str:
                    return "0.12.7"

            dependencies = ScanDependencies(
                platform_name=lambda: "Darwin",
                selector_factory=Selector,
                bridge_factory=Bridge,
                vision_factory=Vision,
                uuid4=lambda: uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
                now_utc=lambda: datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc),
            )
            with patch(
                "photos_indexer.workflows._persist_scan",
                side_effect=OSError("simulated manifest storage failure"),
            ):
                result = run_scan(runs_root, limit=1, dependencies=dependencies)

            self.assertEqual(result.error_codes, ("MANIFEST_WRITE_FAILED",))
            self.assertTrue(runs_root.is_dir())
            self.assertEqual(list(runs_root.iterdir()), [])

    def test_scan_cleanup_failure_with_no_reviewable_rows_requires_a_new_dry_run(self) -> None:
        """A cleanup error must not suggest reviewing a blocked row."""
        from photos_indexer.workflows import ScanDependencies, run_scan

        selected = SelectedPhoto("local-1", datetime(2026, 8, 24, 10, 0))
        photo_uuid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"

        class Selector:
            def select(self, *, limit: int) -> PhotoSelection:
                return PhotoSelection((selected,), limit, 1, 0, "authorized")

        class Bridge:
            workspace: "CleanupWorkspace | None" = None

            def read(self, identifier: str) -> ScriptPhotoRecord:
                return ScriptPhotoRecord(photo_uuid, identifier, "Photo", selected.creation_date, ())

            def export(self, identifier: str, destination: Path) -> Path:
                exported = destination / "photo.png"
                exported.write_bytes(b"\x89PNG\r\n\x1a\nlocal")
                assert self.workspace is not None
                self.workspace.exported = exported
                return exported

        class Vision:
            def check_model(self, model: str) -> str:
                return "0.12.7"

            def analyze(self, model: str, image: Path) -> VisionResult:
                return VisionResult(("playa",), "", False, False, 0.95)

        class CleanupWorkspace:
            exported: Path | None = None

            def __init__(self, parent: Path, run_id: str) -> None:
                self.root = parent / f".cleanup-failure-{run_id}"

            def __enter__(self) -> "CleanupWorkspace":
                self.root.mkdir(mode=0o700)
                return self

            def destination_for(self, photo_uuid: str) -> Path:
                destination = self.root / photo_uuid
                destination.mkdir(mode=0o700)
                return destination

            def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
                shutil.rmtree(self.root, ignore_errors=True)

        bridge = Bridge()

        def workspace_factory(parent: Path, run_id: str) -> CleanupWorkspace:
            workspace = CleanupWorkspace(parent, run_id)
            bridge.workspace = workspace
            return workspace

        def fail_unlink(path: Path, *args: object, **kwargs: object) -> None:
            if path.name == "photo.png":
                raise OSError("simulated cleanup failure")
            original_unlink(path, *args, **kwargs)

        with tempfile.TemporaryDirectory() as temporary_directory:
            original_unlink = Path.unlink
            with patch.object(Path, "unlink", fail_unlink):
                result = run_scan(
                    Path(temporary_directory) / "runs",
                    limit=1,
                    dependencies=ScanDependencies(
                        platform_name=lambda: "Darwin",
                        selector_factory=Selector,
                        bridge_factory=lambda: bridge,
                        vision_factory=Vision,
                        workspace_factory=workspace_factory,
                        recover_workspaces=lambda _: [],
                    ),
                )

        self.assertEqual(result.error_codes, ("EXPORT_DELETE_FAILED",))
        self.assertEqual(result.next_action, "fix_failed_scan")

    def test_scan_persist_failure_cleanup_preserves_an_unsafe_run_directory(self) -> None:
        from photos_indexer.workflows import ScanDependencies, run_scan

        with tempfile.TemporaryDirectory() as tmp:
            runs_root = Path(tmp) / "runs"
            selected = SelectedPhoto("local-1", datetime(2026, 8, 24, 10, 0))

            class Selector:
                def select(self, *, limit: int) -> PhotoSelection:
                    return PhotoSelection((selected,), 1, 1, 0, "authorized")

            class Bridge:
                def read(self, identifier: str) -> ScriptPhotoRecord:
                    return ScriptPhotoRecord(
                        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", identifier, "", selected.creation_date, (),
                    )

            class Vision:
                def check_model(self, model: str) -> str:
                    return "0.12.7"

            def failed_persist(run_dir: Path, **kwargs: object) -> None:
                run_dir.chmod(0o755)
                raise OSError("simulated manifest storage failure")

            dependencies = ScanDependencies(
                platform_name=lambda: "Darwin",
                selector_factory=Selector,
                bridge_factory=Bridge,
                vision_factory=Vision,
                uuid4=lambda: uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
                now_utc=lambda: datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc),
            )
            with patch("photos_indexer.workflows._persist_scan", side_effect=failed_persist):
                result = run_scan(runs_root, limit=1, dependencies=dependencies)

            self.assertEqual(result.error_codes, ("MANIFEST_WRITE_FAILED",))
            run_dirs = list(runs_root.iterdir())
            self.assertEqual(len(run_dirs), 1)
            self.assertEqual(stat.S_IMODE(run_dirs[0].stat().st_mode), 0o755)

    def test_failed_run_cleanup_is_fail_closed_when_lstat_fails(self) -> None:
        from photos_indexer.workflows import _discard_failed_run

        with patch("photos_indexer.workflows.Path.lstat", side_effect=PermissionError):
            _discard_failed_run(Path("/private/invalid-run"))


class StatefulBridge:
    def __init__(self, states: dict[str, list[str] | Exception], manifest_path: Path | None = None) -> None:
        self.states = states
        self.manifest_path = manifest_path
        self.replace_calls: list[tuple[str, list[str]]] = []
        self.description_calls: list[tuple[str, str]] = []
        self.fail_after_writing: set[str] = set()
        self.descriptions: dict[str, str] = {}

    def read(self, local_id: str) -> ScriptPhotoRecord:
        state_key = local_id if local_id.startswith("local-") else f"local-{int(local_id[-12:])}"
        value = self.states[state_key]
        if isinstance(value, Exception):
            raise value
        number = int(state_key.removeprefix("local-"))
        return ScriptPhotoRecord(
            f"00000000-0000-4000-8000-{number:012d}", state_key, f"Fresh {number}",
            datetime(2026, 8, number, 10, 0), tuple(value), None, self.descriptions.get(state_key, ""),
        )

    def replace_keywords(self, local_id: str, keywords: list[str]) -> tuple[str, ...]:
        state_key = local_id if local_id.startswith("local-") else f"local-{int(local_id[-12:])}"
        if self.manifest_path is not None:
            manifest = load_manifest(self.manifest_path.parent)
            photo = next(item for item in manifest.photos if item.photos_local_identifier == state_key)
            expected_state = "removing" if len(keywords) < len(self.states[state_key]) else "writing"  # type: ignore[arg-type]
            observed = photo.rollback_state if expected_state == "removing" else photo.apply_state
            if observed != expected_state:
                raise AssertionError(f"manifest state was {observed}, expected {expected_state}")
        self.replace_calls.append((local_id, list(keywords)))
        if state_key in self.fail_after_writing:
            raise RuntimeError("private setter failure")
        self.states[state_key] = list(keywords)
        return tuple(reversed(keywords))

    def replace_description(self, local_id: str, description: str) -> str:
        state_key = local_id if local_id.startswith("local-") else f"local-{int(local_id[-12:])}"
        self.description_calls.append((local_id, description))
        self.descriptions[state_key] = description
        return description


class RevalidatingSelector:
    def __init__(self, unavailable: set[str] | None = None) -> None:
        self.unavailable = unavailable or set()
        self.calls: list[str] = []

    def revalidate(self, local_id: str) -> SelectedPhoto:
        self.calls.append(local_id)
        if local_id in self.unavailable:
            raise RuntimeError("private PhotoKit failure")
        number = int(local_id.removeprefix("local-"))
        return SelectedPhoto(local_id, datetime(2026, 8, number, 10, 0))


class ApplyWorkflowTests(unittest.TestCase):
    def test_apply_and_rollback_refresh_preview_csv_states_without_caption_text(self) -> None:
        """The review table must reflect durable mutation checkpoints."""
        from photos_indexer.workflows import ApplyDependencies, run_apply, run_rollback

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "runs" / "run"
            photo = make_photo(1)
            photo.proposed_caption = "Una playa visible."
            photo.caption_state = "proposed"
            manifest_path = make_reviewed_manifest(run_dir, [photo])
            write_preview_csv(manifest_path.parent, load_manifest(manifest_path.parent))
            bridge = StatefulBridge({"local-1": ["PERRO"]}, manifest_path)
            dependencies = ApplyDependencies(
                selector_factory=RevalidatingSelector,
                bridge_factory=lambda: bridge,
                global_lock_path=Path(tmp) / "global" / "mutation.lock",
            )

            applied = run_apply(manifest_path, dependencies=dependencies)
            self.assertEqual(applied.exit_code, 0)
            with (manifest_path.parent / "preview.csv").open(encoding="utf-8", newline="") as handle:
                applied_row = next(csv.DictReader(handle))
            self.assertEqual(applied_row["status"], "verified")
            self.assertEqual(applied_row["caption_status"], "verificado")
            self.assertNotIn(photo.proposed_caption, applied_row.values())

            rolled_back = run_rollback(manifest_path, dependencies=dependencies)
            self.assertEqual(rolled_back.exit_code, 0)
            with (manifest_path.parent / "preview.csv").open(encoding="utf-8", newline="") as handle:
                rollback_row = next(csv.DictReader(handle))
            self.assertEqual(rollback_row["status"], "verified_removed")
            self.assertEqual(rollback_row["caption_status"], "retirado")
            self.assertNotIn(photo.proposed_caption, rollback_row.values())

    def test_apply_rejects_an_unreviewed_schema1_scan_before_opening_adapters(self) -> None:
        from photos_indexer.workflows import ApplyDependencies, run_apply

        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = make_manifest(Path(tmp) / "run", [make_photo(1)])
            calls: list[str] = []
            dependencies = ApplyDependencies(
                selector_factory=lambda: calls.append("selector"),
                bridge_factory=lambda: calls.append("bridge"),
                global_lock_path=Path(tmp) / "global" / "mutation.lock",
            )

            result = run_apply(manifest_path, dependencies=dependencies)

            self.assertEqual(result.exit_code, 2)
            self.assertEqual(result.error_codes, ("MANIFEST_NOT_REVIEWED",))
            self.assertEqual(calls, [])

    def test_apply_surfaces_automation_permission_when_bridge_factory_is_denied(self) -> None:
        from photos_indexer.workflows import ApplyDependencies, run_apply, run_status

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "runs" / "run"
            manifest_path = make_reviewed_manifest(run_dir, [make_photo(1)])

            def denied_bridge() -> object:
                raise PhotoScriptPermissionError()

            result = run_apply(
                manifest_path,
                dependencies=ApplyDependencies(
                    selector_factory=RevalidatingSelector,
                    bridge_factory=denied_bridge,
                    global_lock_path=Path(tmp) / "global" / "mutation.lock",
                ),
            )

            self.assertEqual(result.error_codes, ("PHOTOS_AUTOMATION_DENIED",))
            self.assertEqual(result.next_action, "grant_photos_automation")
            persisted = load_manifest(manifest_path.parent)
            self.assertEqual(persisted.photos[0].apply_state, "failed")
            self.assertEqual(
                persisted.photos[0].errors,
                [{"stage": "apply", "code": "PHOTOS_AUTOMATION_DENIED"}],
            )
            status = run_status(manifest_path)
            self.assertEqual(status.error_codes, ("PHOTOS_AUTOMATION_DENIED",))
            self.assertEqual(status.next_action, "grant_photos_automation")

    def test_apply_surfaces_automation_permission_lost_during_photo_read(self) -> None:
        """A mid-run TCC denial must remain a persisted, actionable result."""
        from photos_indexer.workflows import ApplyDependencies, run_apply, run_status

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "runs" / "run"
            manifest_path = make_reviewed_manifest(run_dir, [make_photo(1)])
            bridge = StatefulBridge({"local-1": PhotoScriptPermissionError()})

            result = run_apply(
                manifest_path,
                dependencies=ApplyDependencies(
                    selector_factory=RevalidatingSelector,
                    bridge_factory=lambda: bridge,
                    global_lock_path=Path(tmp) / "global" / "mutation.lock",
                ),
            )

            self.assertEqual(result.exit_code, 1)
            self.assertEqual(result.error_codes, ("PHOTOS_AUTOMATION_DENIED",))
            self.assertEqual(result.next_action, "grant_photos_automation")
            persisted = load_manifest(manifest_path.parent)
            self.assertEqual(persisted.photos[0].apply_state, "failed")
            self.assertEqual(
                persisted.photos[0].errors,
                [{"stage": "apply", "code": "PHOTOS_AUTOMATION_DENIED"}],
            )
            self.assertEqual(run_status(manifest_path).next_action, "grant_photos_automation")

    def test_apply_surfaces_photoscript_unavailable_when_bridge_factory_fails(self) -> None:
        from photos_indexer.adapters import PhotoScriptUnavailableError
        from photos_indexer.workflows import ApplyDependencies, run_apply

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "runs" / "run"
            manifest_path = make_reviewed_manifest(run_dir, [make_photo(1)])

            def unavailable_bridge() -> object:
                raise PhotoScriptUnavailableError()

            result = run_apply(
                manifest_path,
                dependencies=ApplyDependencies(
                    selector_factory=RevalidatingSelector,
                    bridge_factory=unavailable_bridge,
                    global_lock_path=Path(tmp) / "global" / "mutation.lock",
                ),
            )

            self.assertEqual(result.error_codes, ("PHOTOSCRIPT_UNAVAILABLE",))
            self.assertEqual(result.next_action, "fix_fatal_error")

    def test_apply_does_not_retry_photoscript_unavailable_during_photo_read(self) -> None:
        """A persisted compatibility failure needs repair, not a blind retry."""
        from photos_indexer.adapters import PhotoScriptUnavailableError
        from photos_indexer.workflows import ApplyDependencies, run_apply, run_status

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "runs" / "run"
            manifest_path = make_reviewed_manifest(run_dir, [make_photo(1)])
            bridge = StatefulBridge({"local-1": PhotoScriptUnavailableError()})

            result = run_apply(
                manifest_path,
                dependencies=ApplyDependencies(
                    selector_factory=RevalidatingSelector,
                    bridge_factory=lambda: bridge,
                    global_lock_path=Path(tmp) / "global" / "mutation.lock",
                ),
            )

            self.assertEqual(result.exit_code, 1)
            self.assertEqual(result.error_codes, ("PHOTOSCRIPT_UNAVAILABLE",))
            self.assertEqual(result.next_action, "fix_fatal_error")
            self.assertEqual(run_status(manifest_path).next_action, "fix_fatal_error")

    def test_rollback_surfaces_automation_permission_when_bridge_factory_is_denied(self) -> None:
        from photos_indexer.workflows import ApplyDependencies, run_rollback, run_status

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "runs" / "run"
            photo = make_photo(1)
            photo.apply_state = "verified"
            photo.applied_keywords = ["playa"]
            manifest_path = make_reviewed_manifest(run_dir, [photo])

            def denied_bridge() -> object:
                raise PhotoScriptPermissionError()

            result = run_rollback(
                manifest_path,
                dependencies=ApplyDependencies(
                    selector_factory=RevalidatingSelector,
                    bridge_factory=denied_bridge,
                    global_lock_path=Path(tmp) / "global" / "mutation.lock",
                ),
            )

            self.assertEqual(result.error_codes, ("PHOTOS_AUTOMATION_DENIED",))
            self.assertEqual(result.next_action, "grant_photos_automation")
            persisted = load_manifest(manifest_path.parent)
            self.assertEqual(persisted.photos[0].rollback_state, "failed")
            self.assertEqual(
                persisted.photos[0].errors,
                [{"stage": "rollback", "code": "PHOTOS_AUTOMATION_DENIED"}],
            )
            status = run_status(manifest_path)
            self.assertEqual(status.error_codes, ("PHOTOS_AUTOMATION_DENIED",))
            self.assertEqual(status.next_action, "grant_photos_automation")

    def test_rollback_surfaces_automation_permission_lost_during_photo_read(self) -> None:
        """Rollback must persist a mid-run TCC denial without losing its receipt."""
        from photos_indexer.workflows import ApplyDependencies, run_rollback, run_status

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "runs" / "run"
            photo = make_photo(1)
            photo.apply_state = "verified"
            photo.applied_keywords = ["playa"]
            manifest_path = make_reviewed_manifest(run_dir, [photo])
            bridge = StatefulBridge({"local-1": PhotoScriptPermissionError()})

            result = run_rollback(
                manifest_path,
                dependencies=ApplyDependencies(
                    selector_factory=RevalidatingSelector,
                    bridge_factory=lambda: bridge,
                    global_lock_path=Path(tmp) / "global" / "mutation.lock",
                ),
            )

            self.assertEqual(result.exit_code, 1)
            self.assertEqual(result.error_codes, ("PHOTOS_AUTOMATION_DENIED",))
            self.assertEqual(result.next_action, "grant_photos_automation")
            persisted = load_manifest(manifest_path.parent)
            self.assertEqual(persisted.photos[0].rollback_state, "failed")
            self.assertEqual(
                persisted.photos[0].errors,
                [{"stage": "rollback", "code": "PHOTOS_AUTOMATION_DENIED"}],
            )
            self.assertEqual(run_status(manifest_path).next_action, "grant_photos_automation")

    def test_rollback_surfaces_photoscript_unavailable_when_bridge_factory_fails(self) -> None:
        from photos_indexer.adapters import PhotoScriptUnavailableError
        from photos_indexer.workflows import ApplyDependencies, run_rollback

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "runs" / "run"
            photo = make_photo(1)
            photo.apply_state = "verified"
            photo.applied_keywords = ["playa"]
            manifest_path = make_reviewed_manifest(run_dir, [photo])

            def unavailable_bridge() -> object:
                raise PhotoScriptUnavailableError()

            result = run_rollback(
                manifest_path,
                dependencies=ApplyDependencies(
                    selector_factory=RevalidatingSelector,
                    bridge_factory=unavailable_bridge,
                    global_lock_path=Path(tmp) / "global" / "mutation.lock",
                ),
            )

            self.assertEqual(result.error_codes, ("PHOTOSCRIPT_UNAVAILABLE",))
            self.assertEqual(result.next_action, "fix_fatal_error")

    def test_reviewed_manifest_can_retry_failed_apply_rows_without_replaying_verified_rows(self) -> None:
        from photos_indexer.service import review_manifest
        from photos_indexer.workflows import ApplyDependencies, run_apply

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "runs"
            photos = [make_photo(1), make_photo(2)]
            source_path = make_manifest(root / "source", photos)
            root.chmod(0o700)
            reviewed_path = review_manifest(source_path, {photo.uuid: ["playa"] for photo in photos})

            class ReadFailingBridge(StatefulBridge):
                def __init__(self) -> None:
                    super().__init__({"local-1": ["PERRO"], "local-2": ["PERRO"]}, reviewed_path)
                    self.fail_reads = 1

                def read(self, local_id: str) -> ScriptPhotoRecord:
                    if self.fail_reads:
                        self.fail_reads -= 1
                        raise RuntimeError("temporary read failure")
                    return super().read(local_id)

            bridge = ReadFailingBridge()
            dependencies = ApplyDependencies(
                selector_factory=RevalidatingSelector,
                bridge_factory=lambda: bridge,
                global_lock_path=Path(tmp) / "global" / "mutation.lock",
            )

            first = run_apply(reviewed_path, dependencies=dependencies)
            self.assertEqual(first.exit_code, 1)
            first_manifest = load_manifest(reviewed_path.parent)
            self.assertEqual([photo.apply_state for photo in first_manifest.photos], ["failed", "verified"])

            second = run_apply(reviewed_path, dependencies=dependencies)
            self.assertEqual(second.exit_code, 0)
            self.assertEqual([photo.apply_state for photo in load_manifest(reviewed_path.parent).photos], ["verified", "verified"])
            self.assertEqual(bridge.replace_calls, [
                (str(photos[1].uuid), ["PERRO", "playa"]),
                (str(photos[0].uuid), ["PERRO", "playa"]),
            ])

    def test_apply_cancels_before_first_photo_without_writing_and_is_retryable(self) -> None:
        from photos_indexer.workflows import ApplyDependencies, run_apply

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            photo = make_photo(1)
            manifest_path = make_reviewed_manifest(run_dir, [photo])
            bridge = StatefulBridge({"local-1": ["PERRO"]}, manifest_path)
            dependencies = ApplyDependencies(
                selector_factory=RevalidatingSelector,
                bridge_factory=lambda: bridge,
                global_lock_path=Path(tmp) / "global" / "mutation.lock",
            )

            cancelled = run_apply(manifest_path, dependencies=dependencies, cancel_requested=lambda: True)

            self.assertEqual(cancelled.exit_code, 1)
            self.assertEqual(cancelled.error_codes, ("CANCELLED",))
            self.assertEqual(cancelled.next_action, "retry_failed_operation")
            loaded = load_manifest(run_dir).photos[0]
            self.assertEqual(loaded.apply_state, "cancelled")
            self.assertEqual(loaded.errors[-1], {"stage": "apply", "code": "CANCELLED"})
            self.assertEqual(bridge.replace_calls, [])

            retried = run_apply(manifest_path, dependencies=dependencies)
            self.assertEqual(retried.exit_code, 0)
            self.assertEqual(load_manifest(run_dir).photos[0].apply_state, "verified")

    def test_apply_stops_between_photos_after_a_completed_write_boundary(self) -> None:
        from photos_indexer.workflows import ApplyDependencies, run_apply

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            photos = [make_photo(1), make_photo(2)]
            manifest_path = make_reviewed_manifest(run_dir, photos)
            cancel_requested = [False]

            class CancellingBridge(StatefulBridge):
                def replace_keywords(self, local_id: str, keywords: list[str]) -> tuple[str, ...]:
                    result = super().replace_keywords(local_id, keywords)
                    cancel_requested[0] = True
                    return result

            bridge = CancellingBridge({"local-1": ["PERRO"], "local-2": ["PERRO"]}, manifest_path)
            result = run_apply(
                manifest_path,
                dependencies=ApplyDependencies(
                    selector_factory=RevalidatingSelector,
                    bridge_factory=lambda: bridge,
                    global_lock_path=Path(tmp) / "global" / "mutation.lock",
                ),
                cancel_requested=lambda: cancel_requested[0],
            )

            self.assertEqual(result.exit_code, 1)
            self.assertEqual(result.error_codes, ("CANCELLED",))
            loaded = load_manifest(run_dir).photos
            self.assertEqual([photo.apply_state for photo in loaded], ["verified", "cancelled"])
            self.assertEqual(len(bridge.replace_calls), 1)

    def test_apply_ignores_a_late_cancel_after_the_last_verified_write(self) -> None:
        from photos_indexer.workflows import ApplyDependencies, run_apply

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            photo = make_photo(1)
            manifest_path = make_reviewed_manifest(run_dir, [photo])
            cancel_requested = [False]

            class CancellingBridge(StatefulBridge):
                def replace_keywords(self, local_id: str, keywords: list[str]) -> tuple[str, ...]:
                    result = super().replace_keywords(local_id, keywords)
                    cancel_requested[0] = True
                    return result

            bridge = CancellingBridge({"local-1": ["PERRO"]}, manifest_path)
            result = run_apply(
                manifest_path,
                dependencies=ApplyDependencies(
                    selector_factory=RevalidatingSelector,
                    bridge_factory=lambda: bridge,
                    global_lock_path=Path(tmp) / "global" / "mutation.lock",
                ),
                cancel_requested=lambda: cancel_requested[0],
            )

            self.assertEqual(result.exit_code, 0)
            self.assertEqual(result.error_codes, ())
            self.assertEqual(result.next_action, "rollback_available")
            self.assertEqual(load_manifest(run_dir).photos[0].apply_state, "verified")

    def test_apply_finishes_current_setter_when_cancel_is_requested_inside_critical_section(self) -> None:
        from photos_indexer.workflows import ApplyDependencies, run_apply

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            manifest_path = make_reviewed_manifest(run_dir, [make_photo(1), make_photo(2)])
            cancel_requested = [False]
            critical = [False]

            class CriticalBridge(StatefulBridge):
                def replace_keywords(self, local_id: str, keywords: list[str]) -> tuple[str, ...]:
                    critical[0] = True
                    cancel_requested[0] = True
                    result = super().replace_keywords(local_id, keywords)
                    critical[0] = False
                    return result

            bridge = CriticalBridge({"local-1": ["PERRO"], "local-2": ["PERRO"]}, manifest_path)
            def cancellation_probe() -> bool:
                if critical[0]:
                    raise AssertionError("cancel checked inside setter")
                return cancel_requested[0]

            result = run_apply(
                manifest_path,
                dependencies=ApplyDependencies(
                    selector_factory=RevalidatingSelector,
                    bridge_factory=lambda: bridge,
                    global_lock_path=Path(tmp) / "global" / "mutation.lock",
                ),
                cancel_requested=cancellation_probe,
            )

            self.assertEqual(result.error_codes, ("CANCELLED",))
            self.assertEqual([photo.apply_state for photo in load_manifest(run_dir).photos], ["verified", "cancelled"])
            self.assertEqual(len(bridge.replace_calls), 1)

    def test_apply_writes_a_caption_only_when_description_is_empty_and_rollback_removes_it(self) -> None:
        from photos_indexer.workflows import ApplyDependencies, run_apply, run_rollback

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            photo = make_photo(1)
            photo.scan_state = "noop"
            photo.proposed_keywords = []
            photo.proposed_caption = "Una góndola en un canal visible."
            photo.caption_state = "proposed"
            manifest_path = make_reviewed_manifest(run_dir, [photo])
            bridge = StatefulBridge({"local-1": ["PERRO"]}, manifest_path)
            dependencies = ApplyDependencies(
                selector_factory=RevalidatingSelector,
                bridge_factory=lambda: bridge,
                global_lock_path=Path(tmp) / "global" / "mutation.lock",
            )

            applied = run_apply(manifest_path, dependencies=dependencies)
            self.assertEqual(applied.exit_code, 0, applied.error_codes)
            loaded = load_manifest(run_dir).photos[0]
            self.assertEqual(loaded.apply_state, "verified")
            self.assertEqual(loaded.applied_caption, "Una góndola en un canal visible.")
            self.assertEqual(len(loaded.mutation_digest or ""), 64)
            self.assertEqual(bridge.descriptions["local-1"], "Una góndola en un canal visible.")

            repeat = run_apply(manifest_path, dependencies=dependencies)
            self.assertEqual(repeat.exit_code, 2, repeat.error_codes)
            self.assertEqual(repeat.error_codes, ("REVIEW_NOT_PRISTINE",))
            self.assertEqual(bridge.description_calls, [(photo.uuid, "Una góndola en un canal visible.")])
            self.assertEqual(bridge.replace_calls, [])

            rolled_back = run_rollback(manifest_path, dependencies=dependencies)
            self.assertEqual(rolled_back.exit_code, 0)
            loaded = load_manifest(run_dir).photos[0]
            self.assertEqual(loaded.caption_state, "removed")
            self.assertEqual(bridge.descriptions["local-1"], "")

            preserved = make_photo(2)
            preserved.scan_state = "noop"
            preserved.proposed_keywords = []
            preserved.proposed_caption = "Una playa visible."
            preserved.caption_state = "proposed"
            preserved_dir = Path(tmp) / "preserved"
            preserved_manifest = make_reviewed_manifest(preserved_dir, [preserved])
            preserved_bridge = StatefulBridge({"local-2": ["PERRO"]}, preserved_manifest)
            preserved_bridge.descriptions["local-2"] = "Caption existente."
            preserved_result = run_apply(
                preserved_manifest,
                dependencies=ApplyDependencies(
                    selector_factory=RevalidatingSelector,
                    bridge_factory=lambda: preserved_bridge,
                    global_lock_path=Path(tmp) / "global-preserved" / "mutation.lock",
                ),
            )
            self.assertEqual(preserved_result.exit_code, 0)
            self.assertEqual(load_manifest(preserved_dir).photos[0].caption_state, "preserved")

            noop = make_photo(3)
            noop.scan_state = "noop"
            noop.proposed_keywords = []
            noop_dir = Path(tmp) / "noop"
            noop_manifest = make_reviewed_manifest(noop_dir, [noop])
            noop_result = run_apply(noop_manifest)
            self.assertEqual(noop_result.exit_code, 0)
            self.assertEqual(load_manifest(noop_dir).photos[0].apply_state, "noop")

    def test_apply_preserves_a_caption_added_concurrently_after_initial_read(self) -> None:
        from photos_indexer.workflows import ApplyDependencies, run_apply

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            photo = make_photo(1)
            photo.proposed_caption = "Una playa visible."
            photo.caption_state = "proposed"
            manifest_path = make_reviewed_manifest(run_dir, [photo])

            class ConcurrentCaptionBridge(StatefulBridge):
                def replace_keywords(self, local_id: str, keywords: list[str]) -> tuple[str, ...]:
                    result = super().replace_keywords(local_id, keywords)
                    self.descriptions["local-1"] = "Caption externa."
                    return result

            bridge = ConcurrentCaptionBridge({"local-1": ["PERRO"]}, manifest_path)
            result = run_apply(
                manifest_path,
                dependencies=ApplyDependencies(
                    selector_factory=RevalidatingSelector,
                    bridge_factory=lambda: bridge,
                    global_lock_path=Path(tmp) / "global" / "mutation.lock",
                ),
            )

            self.assertEqual(result.exit_code, 0, result.error_codes)
            loaded = load_manifest(run_dir).photos[0]
            self.assertEqual(loaded.apply_state, "verified")
            self.assertEqual(loaded.caption_state, "preserved")
            self.assertIsNone(loaded.applied_caption)
            self.assertEqual(bridge.descriptions["local-1"], "Caption externa.")
            self.assertEqual(bridge.description_calls, [])

    def test_apply_reloads_keywords_before_writing_to_preserve_a_concurrent_addition(self) -> None:
        from photos_indexer.workflows import ApplyDependencies, run_apply

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            photo = make_photo(1)
            manifest_path = make_reviewed_manifest(run_dir, [photo])

            class ConcurrentKeywordBridge(StatefulBridge):
                reads = 0

                def read(self, local_id: str) -> ScriptPhotoRecord:
                    self.reads += 1
                    if self.reads == 2:
                        self.states["local-1"] = ["PERRO", "externa"]
                    return super().read(local_id)

                def replace_keywords(self, local_id: str, keywords: list[str]) -> tuple[str, ...]:
                    if "externa" not in keywords:
                        raise RuntimeError("concurrent keyword would be overwritten")
                    return super().replace_keywords(local_id, keywords)

            bridge = ConcurrentKeywordBridge({"local-1": ["PERRO"]}, manifest_path)
            result = run_apply(
                manifest_path,
                dependencies=ApplyDependencies(
                    selector_factory=RevalidatingSelector,
                    bridge_factory=lambda: bridge,
                    global_lock_path=Path(tmp) / "global" / "mutation.lock",
                ),
            )

            self.assertEqual(result.exit_code, 0, result.error_codes)
            loaded = load_manifest(run_dir).photos[0]
            self.assertEqual(loaded.apply_state, "verified")
            self.assertEqual(loaded.applied_keywords, ["playa"])
            self.assertEqual(loaded.mutation_digest, compute_mutation_digest(load_manifest(run_dir), loaded))
            self.assertEqual(bridge.states["local-1"], ["PERRO", "externa", "playa"])

    def test_caption_rollback_is_terminal_and_blocks_a_second_apply(self) -> None:
        """A completed caption rollback cannot be replayed or silently reapplied."""
        from photos_indexer.workflows import ApplyDependencies, run_apply, run_rollback, run_status

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "caption-terminal"
            photo = make_photo(1)
            photo.scan_state = "noop"
            photo.proposed_keywords = []
            photo.proposed_caption = "Una playa visible."
            photo.caption_state = "proposed"
            manifest_path = make_reviewed_manifest(run_dir, [photo])
            bridge = StatefulBridge({"local-1": ["PERRO"]}, manifest_path)
            dependencies = ApplyDependencies(
                selector_factory=RevalidatingSelector,
                bridge_factory=lambda: bridge,
                global_lock_path=Path(tmp) / "global" / "mutation.lock",
            )

            self.assertEqual(run_apply(manifest_path, dependencies=dependencies).exit_code, 0)
            self.assertEqual(run_rollback(manifest_path, dependencies=dependencies).exit_code, 0)
            writes_before_retry = list(bridge.description_calls)

            status = run_status(manifest_path)
            self.assertEqual(status.status_summary["rollback_status"], "complete")
            self.assertEqual(status.next_action, "none")

            retry = run_apply(manifest_path, dependencies=dependencies)
            self.assertEqual(retry.error_codes, ("ROLLBACK_ALREADY_STARTED",))
            self.assertEqual(bridge.description_calls, writes_before_retry)

    def test_apply_requires_fresh_uuid_and_local_identifier_and_exact_case_readback(self) -> None:
        from photos_indexer.workflows import ApplyDependencies, run_apply

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            wrong_uuid = make_photo(1)
            wrong_local = make_photo(2)
            casing = make_photo(3)
            manifest_path = make_reviewed_manifest(run_dir, [wrong_uuid, wrong_local, casing])
            replace_calls: list[str] = []

            class Selector:
                def revalidate(self, local_id: str) -> SelectedPhoto:
                    return SelectedPhoto(local_id, datetime(2026, 8, 24, 10, 0))

            class Bridge:
                def read(self, identifier: str) -> ScriptPhotoRecord:
                    number = int(identifier[-12:])
                    if number == 1:
                        return ScriptPhotoRecord(str(wrong_local.uuid), "local-1", "Wrong", wrong_uuid.date, ("PERRO",))
                    if number == 2:
                        return ScriptPhotoRecord(str(wrong_local.uuid), "local-other", "Wrong", wrong_local.date, ("PERRO",))
                    return ScriptPhotoRecord(str(casing.uuid), "local-3", "Fresh", casing.date, ("PERRO",))

                def replace_keywords(self, identifier: str, keywords: list[str]) -> tuple[str, ...]:
                    replace_calls.append(identifier)
                    return ("PERRO", "PLAYA")

            result = run_apply(
                manifest_path,
                dependencies=ApplyDependencies(selector_factory=Selector, bridge_factory=Bridge),
            )

            self.assertEqual(result.exit_code, 1)
            loaded = load_manifest(run_dir)
            self.assertEqual([photo.apply_state for photo in loaded.photos], ["failed", "failed", "uncertain"])
            self.assertEqual(loaded.photos[0].errors[-1], {"stage": "apply", "code": "IDENTITY_MISMATCH"})
            self.assertEqual(loaded.photos[1].errors[-1], {"stage": "apply", "code": "IDENTITY_MISMATCH"})
            self.assertEqual(replace_calls, [str(casing.uuid)])

    def test_apply_and_rollback_revalidate_local_id_but_mutate_by_true_uuid(self) -> None:
        from photos_indexer.workflows import ApplyDependencies, run_apply, run_rollback

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "runs" / "run"
            photo = make_photo(1)
            manifest_path = make_reviewed_manifest(run_dir, [photo])
            true_uuid = photo.uuid
            calls: list[tuple[str, str]] = []
            keywords = ["PERRO"]

            class Selector:
                def revalidate(self, identifier: str) -> SelectedPhoto:
                    calls.append(("revalidate", identifier))
                    return SelectedPhoto(identifier, photo.date)

            class Bridge:
                def read(self, identifier: str) -> ScriptPhotoRecord:
                    calls.append(("read", identifier))
                    return ScriptPhotoRecord(str(true_uuid), photo.photos_local_identifier, photo.title, photo.date, tuple(keywords))

                def replace_keywords(self, identifier: str, desired: list[str]) -> tuple[str, ...]:
                    calls.append(("replace", identifier))
                    keywords[:] = desired
                    return tuple(desired)

            dependencies = ApplyDependencies(selector_factory=Selector, bridge_factory=Bridge)
            applied = run_apply(manifest_path, dependencies=dependencies)
            self.assertEqual(applied.exit_code, 0)
            self.assertEqual(calls, [
                ("revalidate", "local-1"),
                ("read", str(true_uuid)),
                ("read", str(true_uuid)),
                ("replace", str(true_uuid)),
            ])

            calls.clear()
            rolled_back = run_rollback(manifest_path, dependencies=dependencies)
            self.assertEqual(rolled_back.exit_code, 0)
            self.assertEqual(calls, [
                ("revalidate", "local-1"),
                ("read", str(true_uuid)),
                ("read", str(true_uuid)),
                ("replace", str(true_uuid)),
            ])

    def test_apply_uses_fresh_state_persists_writing_and_continues_after_failure(self) -> None:
        from photos_indexer.workflows import ApplyDependencies, run_apply

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "runs" / "run"
            photos = [
                make_photo(1, proposed=["playa", "gato"]),
                make_photo(2, proposed=["playa"]),
                make_photo(3, proposed=["playa"]),
                make_photo(4, proposed=["playa"]),
            ]
            manifest_path = make_reviewed_manifest(run_dir, photos)
            bridge = StatefulBridge({
                "local-1": ["PERRO", "PLAYA"],
                "local-2": ["PERRO", "playa"],
                "local-3": RuntimeError("private read failure"),
                "local-4": ["PERRO"],
            }, manifest_path)
            selector = RevalidatingSelector()
            global_lock = Path(tmp) / "global" / "mutation.lock"
            dependencies = ApplyDependencies(
                selector_factory=lambda: selector,
                bridge_factory=lambda: bridge,
                global_lock_path=global_lock,
            )

            result = run_apply(
                manifest_path,
                dependencies=dependencies,
            )

            self.assertEqual(result.exit_code, 1)
            manifest = load_manifest(run_dir)
            self.assertEqual([photo.apply_state for photo in manifest.photos], ["verified", "noop", "failed", "verified"])
            self.assertEqual(manifest.photos[0].applied_keywords, ["gato"])
            self.assertEqual(manifest.photos[3].applied_keywords, ["playa"])
            self.assertEqual(manifest.photos[2].errors[-1], {"stage": "apply", "code": "APPLY_READ_FAILED"})
            self.assertEqual(bridge.replace_calls, [
                (str(photos[0].uuid), ["PERRO", "PLAYA", "gato"]),
                (str(photos[3].uuid), ["PERRO", "playa"]),
            ])
            self.assertEqual(selector.calls, ["local-1", "local-2", "local-3", "local-4"])
            self.assertEqual(stat.S_IMODE((run_dir / ".run.lock").stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(global_lock.stat().st_mode), 0o600)

            bridge.states["local-3"] = ["PERRO"]
            bridge.replace_calls.clear()
            retry = run_apply(
                manifest_path,
                dependencies=dependencies,
            )
            self.assertEqual(retry.exit_code, 0)
            self.assertEqual(bridge.replace_calls, [(str(photos[2].uuid), ["PERRO", "playa"])])
            self.assertEqual([photo.apply_state for photo in load_manifest(run_dir).photos], ["verified", "noop", "verified", "verified"])

    def test_apply_skips_a_ready_row_with_scan_errors_but_processes_valid_rows(self) -> None:
        from photos_indexer.workflows import ApplyDependencies, run_apply, run_status

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "runs" / "run"
            invalid = make_photo(1)
            invalid.errors = [{"stage": "analysis", "code": "ANALYSIS_FAILED"}]
            valid = make_photo(2)
            manifest_path = make_reviewed_manifest(run_dir, [invalid, valid], scan_status="ready_with_errors")
            bridge = StatefulBridge({"local-1": ["PERRO"], "local-2": ["PERRO"]}, manifest_path)
            selector = RevalidatingSelector()

            before_apply = run_status(manifest_path)
            self.assertEqual(before_apply.next_action, "review_then_apply")
            self.assertEqual(before_apply.status_summary["apply_status"], "pending")

            result = run_apply(
                manifest_path,
                dependencies=ApplyDependencies(
                    selector_factory=lambda: selector,
                    bridge_factory=lambda: bridge,
                ),
            )

            self.assertEqual(result.exit_code, 1)
            self.assertEqual(result.error_codes, ("ANALYSIS_FAILED",))
            self.assertEqual([photo.apply_state for photo in load_manifest(run_dir).photos], ["not_run", "verified"])
            self.assertEqual(selector.calls, ["local-2"])
            self.assertEqual(bridge.replace_calls, [(str(valid.uuid), ["PERRO", "playa"])])

    def test_apply_marks_interrupted_and_post_write_failures_uncertain_without_retrying_them(self) -> None:
        from photos_indexer.workflows import ApplyDependencies, run_apply

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "runs" / "run"
            interrupted = make_photo(1)
            interrupted.apply_state = "writing"
            post_write = make_photo(2)
            manifest_path = make_reviewed_manifest(run_dir, [interrupted, post_write])
            bridge = StatefulBridge({"local-1": ["PERRO"], "local-2": ["PERRO"]}, manifest_path)
            bridge.fail_after_writing.add("local-2")
            selector = RevalidatingSelector()

            first = run_apply(
                manifest_path,
                dependencies=ApplyDependencies(selector_factory=lambda: selector, bridge_factory=lambda: bridge),
            )
            self.assertEqual(first.exit_code, 1)
            self.assertEqual([photo.apply_state for photo in load_manifest(run_dir).photos], ["uncertain", "uncertain"])
            self.assertEqual(bridge.replace_calls, [(str(post_write.uuid), ["PERRO", "playa"])])

            bridge.replace_calls.clear()
            second = run_apply(
                manifest_path,
                dependencies=ApplyDependencies(selector_factory=lambda: selector, bridge_factory=lambda: bridge),
            )
            self.assertEqual(second.exit_code, 2)
            self.assertEqual(second.error_codes, ("REVIEW_NOT_PRISTINE",))
            self.assertEqual(bridge.replace_calls, [])

    def test_apply_marks_recovered_writing_caption_as_uncertain_for_auditing(self) -> None:
        from photos_indexer.workflows import ApplyDependencies, run_apply, run_status

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "runs" / "run"
            photo = make_photo(1)
            photo.apply_state = "writing"
            photo.proposed_caption = "Una playa visible."
            photo.caption_state = "proposed"
            manifest_path = make_reviewed_manifest(run_dir, [photo])
            created: list[str] = []

            result = run_apply(
                manifest_path,
                dependencies=ApplyDependencies(
                    selector_factory=lambda: created.append("selector"),
                    bridge_factory=lambda: created.append("bridge"),
                ),
            )

            self.assertEqual(result.exit_code, 1)
            recovered = load_manifest(run_dir).photos[0]
            self.assertEqual(recovered.apply_state, "uncertain")
            self.assertEqual(recovered.applied_caption, "Una playa visible.")
            self.assertEqual(recovered.caption_state, "uncertain")
            self.assertEqual(recovered.errors[-1], {"stage": "apply", "code": "INTERRUPTED_WRITE"})
            self.assertEqual(created, [])

            status = run_status(manifest_path)
            self.assertEqual(status.exit_code, 1)
            self.assertEqual(status.error_codes, ("INTERRUPTED_WRITE",))
            self.assertEqual(status.status_summary["apply_status"], "uncertain")
            self.assertEqual(status.status_summary["keywords_verified"], 0)
            self.assertEqual(status.status_summary["captions_verified"], 0)
            self.assertEqual(status.next_action, "manual_review")

            repeated = run_apply(
                manifest_path,
                dependencies=ApplyDependencies(
                    selector_factory=lambda: created.append("selector"),
                    bridge_factory=lambda: created.append("bridge"),
                ),
            )
            self.assertEqual(repeated.exit_code, 2)
            self.assertEqual(repeated.error_codes, ("REVIEW_NOT_PRISTINE",))
            self.assertEqual(created, [])

    def test_apply_audits_a_keyword_that_was_verified_before_a_later_caption_failure(self) -> None:
        """A mixed metadata write must not lose evidence of a successful field write."""
        from photos_indexer.workflows import ApplyDependencies, run_apply, run_rollback

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "runs" / "run"
            photo = make_photo(1)
            photo.proposed_caption = "Una playa visible."
            photo.caption_state = "proposed"
            manifest_path = make_reviewed_manifest(run_dir, [photo])
            keywords = ["PERRO"]
            keyword_calls: list[list[str]] = []
            caption_calls: list[str] = []

            class Selector:
                def revalidate(self, local_id: str) -> SelectedPhoto:
                    return SelectedPhoto(local_id, photo.date)

            class Bridge:
                def read(self, local_id: str) -> ScriptPhotoRecord:
                    return ScriptPhotoRecord(
                        str(photo.uuid), photo.photos_local_identifier, photo.title, photo.date, tuple(keywords), None, ""
                    )

                def replace_keywords(self, local_id: str, desired: list[str]) -> tuple[str, ...]:
                    keyword_calls.append(list(desired))
                    keywords[:] = desired
                    return tuple(desired)

                def replace_description(self, local_id: str, description: str) -> str:
                    caption_calls.append(description)
                    raise RuntimeError("caption setter failed after keyword readback")

            dependencies = ApplyDependencies(
                selector_factory=Selector,
                bridge_factory=Bridge,
                global_lock_path=Path(tmp) / "global" / "mutation.lock",
            )
            applied = run_apply(manifest_path, dependencies=dependencies)

            self.assertEqual(applied.exit_code, 1)
            uncertain = load_manifest(run_dir).photos[0]
            self.assertEqual(uncertain.apply_state, "uncertain")
            self.assertEqual(uncertain.applied_keywords, ["playa"])
            self.assertIsNone(uncertain.applied_caption)
            self.assertEqual(uncertain.errors[-1], {"stage": "apply", "code": "CAPTION_WRITE_UNCERTAIN"})
            self.assertEqual(keyword_calls, [["PERRO", "playa"]])
            self.assertEqual(caption_calls, ["Una playa visible."])

            # Uncertain writes remain manual-review-only and are never silently
            # reversed by rollback.
            rolled_back = run_rollback(manifest_path, dependencies=dependencies)
            self.assertEqual(rolled_back.exit_code, 1)
            self.assertEqual(load_manifest(run_dir).photos[0].rollback_state, "not_run")
            self.assertEqual(keywords, ["PERRO", "playa"])

    def test_apply_attempts_caption_even_when_keyword_readback_fails(self) -> None:
        """An uncertain keyword write must not silently skip an independent caption."""
        from photos_indexer.workflows import ApplyDependencies, run_apply

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "runs" / "run"
            photo = make_photo(1)
            photo.proposed_caption = "Una playa visible."
            photo.caption_state = "proposed"
            manifest_path = make_reviewed_manifest(run_dir, [photo])
            caption_calls: list[str] = []

            class Selector:
                def revalidate(self, local_id: str) -> SelectedPhoto:
                    return SelectedPhoto(local_id, photo.date)

            class Bridge:
                def read(self, local_id: str) -> ScriptPhotoRecord:
                    return ScriptPhotoRecord(
                        str(photo.uuid), photo.photos_local_identifier, photo.title,
                        photo.date, ("PERRO",), None, ""
                    )

                def replace_keywords(self, local_id: str, desired: list[str]) -> tuple[str, ...]:
                    del local_id, desired
                    raise RuntimeError("keyword readback failed after setter")

                def replace_description(self, local_id: str, description: str) -> str:
                    del local_id
                    caption_calls.append(description)
                    return description

            result = run_apply(
                manifest_path,
                dependencies=ApplyDependencies(
                    selector_factory=Selector,
                    bridge_factory=Bridge,
                    global_lock_path=Path(tmp) / "global" / "mutation.lock",
                ),
            )

            self.assertEqual(result.exit_code, 1)
            self.assertEqual(caption_calls, ["Una playa visible."])
            uncertain = load_manifest(run_dir).photos[0]
            self.assertEqual(uncertain.apply_state, "uncertain")
            self.assertEqual(uncertain.applied_caption, "Una playa visible.")
            self.assertEqual(uncertain.caption_state, "verified")
            self.assertEqual(uncertain.errors[-1], {"stage": "apply", "code": "KEYWORD_WRITE_UNCERTAIN"})

    def test_apply_rejects_wrong_basename_failed_scan_and_any_prior_rollback_without_adapters(self) -> None:
        from photos_indexer.workflows import ApplyDependencies, run_apply

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "runs"
            created: list[str] = []
            dependencies = ApplyDependencies(
                selector_factory=lambda: created.append("selector"),
                bridge_factory=lambda: created.append("bridge"),
            )
            self.assertEqual(run_apply(root / "other.json", dependencies=dependencies).exit_code, 2)

            failed_dir = root / "failed"
            failed_path = make_manifest(failed_dir, [make_photo(1)], scan_status="failed")
            self.assertEqual(run_apply(failed_path, dependencies=dependencies).exit_code, 2)

            rolled_dir = root / "rolled"
            rolled = make_photo(2)
            rolled.apply_state = "verified"
            rolled.applied_keywords = ["playa"]
            rolled.rollback_state = "verified_removed"
            rolled_path = make_reviewed_manifest(rolled_dir, [rolled])
            self.assertEqual(run_apply(rolled_path, dependencies=dependencies).exit_code, 2)
            self.assertEqual(created, [])

    def test_apply_fails_closed_when_the_global_lock_is_held(self) -> None:
        from photos_indexer.workflows import ApplyDependencies, _operation_locks, run_apply

        with tempfile.TemporaryDirectory() as tmp:
            first_run = Path(tmp) / "first-parent" / "run"
            second_run = Path(tmp) / "second-parent" / "run"
            make_manifest(first_run, [make_photo(1)])
            manifest_path = make_reviewed_manifest(second_run, [make_photo(2)])
            global_lock = Path(tmp) / "per-user-private" / "mutation.lock"
            created: list[str] = []
            dependencies = ApplyDependencies(
                selector_factory=lambda: created.append("selector"),
                bridge_factory=lambda: created.append("bridge"),
                global_lock_path=global_lock,
            )
            with _operation_locks(first_run, global_lock):
                result = run_apply(
                    manifest_path,
                    dependencies=dependencies,
                )

            self.assertEqual(result.exit_code, 2)
            self.assertEqual(result.error_codes, ("LOCK_OR_MANIFEST_FAILED",))
            self.assertEqual(created, [])
            self.assertEqual(stat.S_IMODE(global_lock.parent.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(global_lock.stat().st_mode), 0o600)

    def test_default_global_lock_is_stable_and_independent_of_temp_environment(self) -> None:
        from unittest.mock import patch

        from photos_indexer.workflows import _default_global_lock_path

        expected = Path("/tmp") / f".photos-local-keyword-indexer-{os.getuid()}" / "mutation.lock"
        with patch.dict(os.environ, {"TMPDIR": "/private/untrusted", "TMP": "/other", "TEMP": "/elsewhere"}):
            self.assertEqual(_default_global_lock_path(), expected)


class RollbackWorkflowTests(unittest.TestCase):
    def test_rollback_rejects_mutated_apply_evidence_before_opening_adapters(self) -> None:
        from photos_indexer.workflows import ApplyDependencies, run_rollback

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            photo = make_photo(1)
            photo.proposed_caption = "Una playa visible."
            photo.caption_state = "proposed"
            manifest_path = make_reviewed_manifest(run_dir, [photo])
            manifest = load_manifest(run_dir)
            row = manifest.photos[0]
            # Only mutation evidence is changed; the source sibling and the
            # reviewed immutable scan data remain intact.
            row.apply_state = "verified"
            row.applied_keywords = ["playa"]
            row.applied_caption = "Una playa visible."
            row.caption_state = "verified"
            write_manifest(run_dir, manifest)
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
            self.assertEqual(result.error_codes, ("MUTATION_EVIDENCE_INVALID",))
            self.assertEqual(calls, [])
            self.assertEqual(bridge.replace_calls, [])

    def test_rollback_rejects_a_changed_value_when_the_receipt_is_stale(self) -> None:
        from photos_indexer.workflows import ApplyDependencies, run_rollback

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            photo = make_photo(1, proposed=["playa", "gato"])
            photo.apply_state = "verified"
            photo.applied_keywords = ["playa"]
            manifest_path = make_reviewed_manifest(run_dir, [photo])
            manifest = load_manifest(run_dir)
            manifest.photos[0].applied_keywords = ["gato"]
            write_manifest(run_dir, manifest)
            bridge = StatefulBridge({"local-1": ["PERRO", "gato"]}, manifest_path)

            result = run_rollback(
                manifest_path,
                dependencies=ApplyDependencies(
                    selector_factory=RevalidatingSelector,
                    bridge_factory=lambda: bridge,
                    global_lock_path=Path(tmp) / "global" / "mutation.lock",
                ),
            )

            self.assertEqual(result.exit_code, 2)
            self.assertEqual(result.error_codes, ("MUTATION_EVIDENCE_INVALID",))
            self.assertEqual(bridge.replace_calls, [])

    def test_status_blocks_mutation_when_apply_receipt_is_missing(self) -> None:
        from photos_indexer.workflows import run_status

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            photo = make_photo(1)
            photo.apply_state = "verified"
            photo.applied_keywords = ["playa"]
            manifest_path = make_reviewed_manifest(run_dir, [photo])
            manifest = load_manifest(run_dir)
            manifest.photos[0].mutation_digest = None
            write_manifest(run_dir, manifest)

            result = run_status(manifest_path)

            self.assertEqual(result.exit_code, 2)
            self.assertEqual(result.error_codes, ("MUTATION_EVIDENCE_INVALID",))
            self.assertEqual(result.status_summary["rollback_status"], "blocked")
            self.assertEqual(result.next_action, "rescan_after_mutation_evidence")

    def test_rollback_rejects_an_unreviewed_schema1_manifest_before_opening_adapters(self) -> None:
        from photos_indexer.workflows import ApplyDependencies, run_rollback

        with tempfile.TemporaryDirectory() as tmp:
            photo = make_photo(1)
            photo.apply_state = "verified"
            photo.applied_keywords = ["playa"]
            manifest_path = make_manifest(Path(tmp) / "run", [photo])
            calls: list[str] = []
            dependencies = ApplyDependencies(
                selector_factory=lambda: calls.append("selector"),
                bridge_factory=lambda: calls.append("bridge"),
                global_lock_path=Path(tmp) / "global" / "mutation.lock",
            )

            result = run_rollback(manifest_path, dependencies=dependencies)

            self.assertEqual(result.exit_code, 2)
            self.assertEqual(result.error_codes, ("MANIFEST_NOT_REVIEWED",))
            self.assertEqual(calls, [])

    def test_rollback_rejects_an_unreviewed_routing_manifest_even_with_mutation_state(self) -> None:
        from photos_indexer.workflows import ApplyDependencies, run_rollback

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "runs" / "run"
            photo = make_photo(1)
            manifest = build_scan_manifest(
                run_id=str(uuid.uuid4()),
                app_name="photos-local-keyword-indexer",
                app_version="0.1.0",
                model_name="qwen3-vl:4b",
                ollama_version="0.12.7",
                endpoint="http://127.0.0.1:11434",
                selection={"requested": 1, "eligible": 1, "screenshots_excluded": 0, "access": "authorized"},
                photos=[photo],
                summary={"ready": 1, "noop": 0, "analysis_failed": 0},
                confidence_threshold=0.60,
                model_policy="single",
                fast_model="qwen3-vl:4b",
                detailed_model="qwen3-vl:4b",
                ollama_versions={"qwen3-vl:4b": "0.12.7"},
            )
            photo.apply_state = "verified"
            photo.applied_keywords = ["playa"]
            manifest_path = write_manifest(run_dir, manifest)
            replace_calls: list[str] = []

            class Selector:
                def revalidate(self, local_id: str) -> SelectedPhoto:
                    return SelectedPhoto(local_id, photo.date)

            class Bridge:
                def read(self, identifier: str) -> ScriptPhotoRecord:
                    return ScriptPhotoRecord(
                        str(photo.uuid), photo.photos_local_identifier, photo.title, photo.date, ("PERRO", "playa")
                    )

                def replace_keywords(self, identifier: str, desired: list[str]) -> tuple[str, ...]:
                    replace_calls.append(identifier)
                    return tuple(desired)

            result = run_rollback(
                manifest_path,
                dependencies=ApplyDependencies(
                    selector_factory=Selector,
                    bridge_factory=Bridge,
                    global_lock_path=Path(tmp) / "global" / "mutation.lock",
                ),
            )

            self.assertEqual(result.exit_code, 2)
            self.assertEqual(result.error_codes, ("MANIFEST_NOT_REVIEWED",))
            self.assertEqual(replace_calls, [])

    def test_rollback_cancels_between_photos_without_starting_the_next_removal(self) -> None:
        from photos_indexer.workflows import ApplyDependencies, run_rollback

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            photos = [make_photo(1), make_photo(2)]
            for photo in photos:
                photo.apply_state = "verified"
                photo.applied_keywords = ["playa"]
            manifest_path = make_reviewed_manifest(run_dir, photos)
            cancel_requested = [False]

            class CancellingBridge(StatefulBridge):
                def replace_keywords(self, local_id: str, keywords: list[str]) -> tuple[str, ...]:
                    result = super().replace_keywords(local_id, keywords)
                    cancel_requested[0] = True
                    return result

            bridge = CancellingBridge(
                {"local-1": ["PERRO", "playa"], "local-2": ["PERRO", "playa"]}, manifest_path
            )
            result = run_rollback(
                manifest_path,
                dependencies=ApplyDependencies(
                    selector_factory=RevalidatingSelector,
                    bridge_factory=lambda: bridge,
                    global_lock_path=Path(tmp) / "global" / "mutation.lock",
                ),
                cancel_requested=lambda: cancel_requested[0],
            )

            self.assertEqual(result.exit_code, 1)
            self.assertEqual(result.error_codes, ("CANCELLED",))
            self.assertEqual(result.next_action, "retry_failed_operation")
            loaded = load_manifest(run_dir).photos
            self.assertEqual([photo.rollback_state for photo in loaded], ["verified_removed", "cancelled"])
            self.assertEqual(len(bridge.replace_calls), 1)

    def test_rollback_result_preserves_partial_error_codes_for_support(self) -> None:
        from photos_indexer.service import support_snapshot
        from photos_indexer.workflows import ApplyDependencies, run_rollback

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            photo = make_photo(1)
            photo.apply_state = "verified"
            photo.applied_keywords = ["playa"]
            manifest_path = make_reviewed_manifest(run_dir, [photo])
            bridge = StatefulBridge({"local-1": ["PERRO", "PLAYA"]}, manifest_path)

            result = run_rollback(
                manifest_path,
                dependencies=ApplyDependencies(
                    selector_factory=RevalidatingSelector,
                    bridge_factory=lambda: bridge,
                    global_lock_path=Path(tmp) / "global" / "mutation.lock",
                ),
            )

            self.assertEqual(result.exit_code, 1)
            self.assertEqual(result.error_codes, ("CASING_CONFLICT",))
            self.assertEqual(result.next_action, "manual_review")
            self.assertEqual(support_snapshot(result)["error_codes"], ["CASING_CONFLICT"])

    def test_rollback_ignores_a_late_cancel_after_the_last_verified_removal(self) -> None:
        from photos_indexer.workflows import ApplyDependencies, run_rollback

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            photo = make_photo(1)
            photo.apply_state = "verified"
            photo.applied_keywords = ["playa"]
            manifest_path = make_reviewed_manifest(run_dir, [photo])
            cancel_requested = [False]

            class CancellingBridge(StatefulBridge):
                def replace_keywords(self, local_id: str, keywords: list[str]) -> tuple[str, ...]:
                    result = super().replace_keywords(local_id, keywords)
                    cancel_requested[0] = True
                    return result

            bridge = CancellingBridge({"local-1": ["PERRO", "playa"]}, manifest_path)
            result = run_rollback(
                manifest_path,
                dependencies=ApplyDependencies(
                    selector_factory=RevalidatingSelector,
                    bridge_factory=lambda: bridge,
                    global_lock_path=Path(tmp) / "global" / "mutation.lock",
                ),
                cancel_requested=lambda: cancel_requested[0],
            )

            self.assertEqual(result.exit_code, 0)
            self.assertEqual(result.error_codes, ())
            self.assertEqual(result.next_action, "none")
            self.assertEqual(load_manifest(run_dir).photos[0].rollback_state, "verified_removed")

    def test_rollback_reports_prior_uncertain_apply_for_manual_review(self) -> None:
        from photos_indexer.workflows import ApplyDependencies, run_rollback

        with tempfile.TemporaryDirectory() as tmp:
            for state in ("uncertain", "writing"):
                with self.subTest(state=state):
                    run_dir = Path(tmp) / state
                    photo = make_photo(1)
                    photo.apply_state = state
                    manifest_path = make_reviewed_manifest(run_dir, [photo])
                    created: list[str] = []

                    result = run_rollback(
                        manifest_path,
                        dependencies=ApplyDependencies(
                            selector_factory=lambda: created.append("selector"),
                            bridge_factory=lambda: created.append("bridge"),
                            global_lock_path=Path(tmp) / "global" / "mutation.lock",
                        ),
                    )

                    self.assertEqual(result.exit_code, 1)
                    self.assertEqual(result.next_action, "manual_review")
                    self.assertEqual(created, [])

    def test_rollback_rejects_fresh_identity_mismatch_before_mutation(self) -> None:
        from photos_indexer.workflows import ApplyDependencies, run_rollback

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            photo = make_photo(1)
            photo.apply_state = "verified"
            photo.applied_keywords = ["playa"]
            manifest_path = make_reviewed_manifest(run_dir, [photo])
            replace_calls: list[str] = []

            class Selector:
                def revalidate(self, local_id: str) -> SelectedPhoto:
                    return SelectedPhoto(local_id, photo.date)

            class Bridge:
                def read(self, identifier: str) -> ScriptPhotoRecord:
                    return ScriptPhotoRecord(str(photo.uuid), "local-other", "Wrong", photo.date, ("PERRO", "playa"))

                def replace_keywords(self, identifier: str, keywords: list[str]) -> tuple[str, ...]:
                    replace_calls.append(identifier)
                    return tuple(keywords)

            result = run_rollback(
                manifest_path,
                dependencies=ApplyDependencies(selector_factory=Selector, bridge_factory=Bridge),
            )

            self.assertEqual(result.exit_code, 1)
            loaded = load_manifest(run_dir).photos[0]
            self.assertEqual(loaded.rollback_state, "failed")
            self.assertEqual(loaded.errors[-1], {"stage": "rollback", "code": "IDENTITY_MISMATCH"})
            self.assertEqual(replace_calls, [])

    def test_rollback_removes_safe_exact_values_in_mixed_absent_and_casing_outcomes(self) -> None:
        from photos_indexer.workflows import ApplyDependencies, run_rollback, run_status

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "runs" / "run"
            absent = make_photo(1, proposed=["playa", "gato"])
            casing = make_photo(2, proposed=["playa", "gato"])
            for photo in (absent, casing):
                photo.apply_state = "verified"
                photo.applied_keywords = ["playa", "gato"]
            manifest_path = make_reviewed_manifest(run_dir, [absent, casing])
            bridge = StatefulBridge({
                "local-1": ["PERRO", "playa", "externa"],
                "local-2": ["PERRO", "playa", "GATO", "externa"],
            }, manifest_path)
            selector = RevalidatingSelector()

            result = run_rollback(
                manifest_path,
                dependencies=ApplyDependencies(
                    selector_factory=lambda: selector,
                    bridge_factory=lambda: bridge,
                    global_lock_path=Path(tmp) / "global" / "mutation.lock",
                ),
            )

            self.assertEqual(result.exit_code, 1)
            loaded = load_manifest(run_dir)
            self.assertEqual([photo.rollback_state for photo in loaded.photos], ["failed", "casing_conflict"])
            self.assertEqual([photo.rolled_back_keywords for photo in loaded.photos], [["playa"], ["playa"]])
            self.assertEqual(bridge.replace_calls, [
                (str(absent.uuid), ["PERRO", "externa"]),
                (str(casing.uuid), ["PERRO", "GATO", "externa"]),
            ])
            self.assertEqual(bridge.states["local-1"], ["PERRO", "externa"])
            self.assertEqual(bridge.states["local-2"], ["PERRO", "GATO", "externa"])
            self.assertEqual(run_status(manifest_path).status_summary["keywords_removed"], 2)

            bridge.states["local-1"] = ["PERRO", "playa", "externa"]
            bridge.replace_calls.clear()
            retry = run_rollback(
                manifest_path,
                dependencies=ApplyDependencies(
                    selector_factory=lambda: selector,
                    bridge_factory=lambda: bridge,
                    global_lock_path=Path(tmp) / "global" / "mutation.lock",
                ),
            )
            self.assertEqual(retry.exit_code, 1)
            retried = load_manifest(run_dir)
            self.assertEqual(retried.photos[0].rollback_state, "already_absent")
            self.assertEqual(retried.photos[0].rolled_back_keywords, ["playa"])
            self.assertEqual(bridge.states["local-1"], ["PERRO", "playa", "externa"])
            self.assertEqual(bridge.replace_calls, [])
            self.assertEqual(run_status(manifest_path).status_summary["keywords_removed"], 2)

    def test_rollback_removes_only_one_exact_applied_value_and_continues_safely(self) -> None:
        from photos_indexer.workflows import ApplyDependencies, run_rollback

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "runs" / "run"
            photos = [make_photo(number) for number in range(1, 7)]
            for photo in photos:
                photo.apply_state = "verified"
                photo.applied_keywords = ["playa"]
            photos[3].apply_state = "uncertain"
            photos[3].applied_keywords = []
            manifest_path = make_reviewed_manifest(run_dir, photos)
            bridge = StatefulBridge({
                "local-1": ["PERRO", "playa", "PLAYA", "playa", "externa"],
                "local-2": ["PERRO"],
                "local-3": ["PERRO", "PLAYA"],
                "local-4": ["PERRO", "playa"],
                "local-5": RuntimeError("private read failure"),
                "local-6": ["PERRO", "playa"],
            }, manifest_path)
            bridge.fail_after_writing.add("local-6")
            selector = RevalidatingSelector()

            result = run_rollback(
                manifest_path,
                dependencies=ApplyDependencies(selector_factory=lambda: selector, bridge_factory=lambda: bridge),
            )

            self.assertEqual(result.exit_code, 1)
            manifest = load_manifest(run_dir)
            self.assertEqual(
                [photo.rollback_state for photo in manifest.photos],
                ["verified_removed", "already_absent", "casing_conflict", "not_run", "failed", "uncertain"],
            )
            self.assertEqual(bridge.replace_calls, [
                (str(photos[0].uuid), ["PERRO", "PLAYA", "playa", "externa"]),
                (str(photos[5].uuid), ["PERRO"]),
            ])
            self.assertEqual(selector.calls, ["local-1", "local-2", "local-3", "local-5", "local-6"])
            self.assertEqual(bridge.states["local-1"], ["PERRO", "PLAYA", "playa", "externa"])

            bridge.states["local-5"] = ["PERRO", "playa"]
            bridge.replace_calls.clear()
            retry = run_rollback(
                manifest_path,
                dependencies=ApplyDependencies(selector_factory=lambda: selector, bridge_factory=lambda: bridge),
            )
            self.assertEqual(retry.exit_code, 1)
            self.assertEqual(bridge.replace_calls, [(str(photos[4].uuid), ["PERRO"])])
            self.assertEqual(load_manifest(run_dir).photos[4].rollback_state, "verified_removed")

    def test_rollback_refreshes_keywords_before_writing_to_preserve_a_concurrent_addition(self) -> None:
        """A keyword added after the first read must survive rollback."""
        from photos_indexer.workflows import ApplyDependencies, run_rollback

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            photo = make_photo(1)
            photo.apply_state = "verified"
            photo.applied_keywords = ["playa"]
            manifest_path = make_reviewed_manifest(run_dir, [photo])

            class ConcurrentBridge(StatefulBridge):
                reads = 0

                def read(self, local_id: str) -> ScriptPhotoRecord:
                    self.reads += 1
                    if self.reads == 2:
                        self.states["local-1"].append("externa")
                    return super().read(local_id)

                def replace_keywords(self, local_id: str, keywords: list[str]) -> tuple[str, ...]:
                    if "externa" not in keywords:
                        raise AssertionError("rollback would overwrite a concurrent keyword")
                    return super().replace_keywords(local_id, keywords)

            bridge = ConcurrentBridge({"local-1": ["PERRO", "playa"]}, manifest_path)
            result = run_rollback(
                manifest_path,
                dependencies=ApplyDependencies(
                    selector_factory=RevalidatingSelector,
                    bridge_factory=lambda: bridge,
                    global_lock_path=Path(tmp) / "global" / "mutation.lock",
                ),
            )

            self.assertEqual(result.exit_code, 0, result.error_codes)
            loaded = load_manifest(run_dir).photos[0]
            self.assertEqual(loaded.rollback_state, "verified_removed")
            self.assertEqual(loaded.rolled_back_keywords, ["playa"])
            self.assertEqual(bridge.states["local-1"], ["PERRO", "externa"])

    def test_rollback_preserves_a_caption_changed_before_writing(self) -> None:
        """A caption changed by another editor must not be deleted by rollback."""
        from photos_indexer.workflows import ApplyDependencies, run_rollback

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            photo = make_photo(1)
            photo.scan_state = "noop"
            photo.proposed_keywords = []
            photo.applied_keywords = []
            photo.apply_state = "verified"
            photo.proposed_caption = "Una playa visible."
            photo.applied_caption = "Una playa visible."
            photo.caption_state = "verified"
            manifest_path = make_reviewed_manifest(run_dir, [photo])

            class CaptionChangedBridge(StatefulBridge):
                reads = 0

                def read(self, local_id: str) -> ScriptPhotoRecord:
                    self.reads += 1
                    if self.reads == 2:
                        self.descriptions["local-1"] = "Caption externo."
                    return super().read(local_id)

            bridge = CaptionChangedBridge({"local-1": ["PERRO"]}, manifest_path)
            bridge.descriptions["local-1"] = photo.applied_caption
            result = run_rollback(
                manifest_path,
                dependencies=ApplyDependencies(
                    selector_factory=RevalidatingSelector,
                    bridge_factory=lambda: bridge,
                    global_lock_path=Path(tmp) / "global" / "mutation.lock",
                ),
            )

            self.assertEqual(result.exit_code, 1)
            loaded = load_manifest(run_dir).photos[0]
            self.assertEqual(loaded.rollback_state, "uncertain")
            self.assertEqual(loaded.caption_state, "uncertain")
            self.assertEqual(bridge.descriptions["local-1"], "Caption externo.")
            self.assertEqual(bridge.description_calls, [])

    def test_rollback_marks_previous_removing_uncertain_and_never_retries_it(self) -> None:
        from photos_indexer.workflows import ApplyDependencies, run_rollback

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "runs" / "run"
            photo = make_photo(1)
            photo.apply_state = "verified"
            photo.applied_keywords = ["playa"]
            photo.proposed_caption = "Una playa visible."
            photo.applied_caption = "Una playa visible."
            photo.caption_state = "verified"
            photo.rollback_state = "removing"
            manifest_path = make_reviewed_manifest(run_dir, [photo])
            manifest = load_manifest(run_dir)
            manifest.photos[0].mutation_digest = compute_mutation_digest(manifest, manifest.photos[0])
            manifest.photos[0].rollback_digest = compute_rollback_digest(manifest, manifest.photos[0])
            write_manifest(run_dir, manifest)
            created: list[str] = []

            result = run_rollback(
                manifest_path,
                dependencies=ApplyDependencies(
                    selector_factory=lambda: created.append("selector"),
                    bridge_factory=lambda: created.append("bridge"),
                ),
            )

            self.assertEqual(result.exit_code, 1)
            loaded = load_manifest(run_dir).photos[0]
            self.assertEqual(loaded.rollback_state, "uncertain")
            self.assertEqual(loaded.caption_state, "uncertain")
            self.assertEqual(loaded.errors[-1], {"stage": "rollback", "code": "INTERRUPTED_REMOVAL"})
            self.assertEqual(created, [])

    def test_rollback_persists_keyword_removal_before_caption_failure(self) -> None:
        """A verified keyword removal remains auditable if caption removal then fails."""
        from photos_indexer.workflows import ApplyDependencies, run_rollback, run_status

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            photo = make_photo(1)
            photo.apply_state = "verified"
            photo.applied_keywords = ["playa"]
            photo.proposed_caption = "Una playa visible."
            photo.applied_caption = "Una playa visible."
            photo.caption_state = "verified"
            manifest_path = make_reviewed_manifest(run_dir, [photo])

            class CaptionFailingBridge(StatefulBridge):
                def replace_description(self, local_id: str, description: str) -> str:
                    self.description_calls.append((local_id, description))
                    raise RuntimeError("caption setter failure")

            bridge = CaptionFailingBridge({"local-1": ["PERRO", "playa"]}, manifest_path)
            bridge.descriptions["local-1"] = photo.applied_caption
            result = run_rollback(
                manifest_path,
                dependencies=ApplyDependencies(
                    selector_factory=RevalidatingSelector,
                    bridge_factory=lambda: bridge,
                    global_lock_path=Path(tmp) / "global" / "mutation.lock",
                ),
            )

            self.assertEqual(result.exit_code, 1)
            loaded = load_manifest(run_dir).photos[0]
            self.assertEqual(loaded.rollback_state, "uncertain")
            self.assertEqual(loaded.rolled_back_keywords, ["playa"])
            self.assertEqual(loaded.caption_state, "uncertain")
            self.assertEqual(loaded.errors[-1], {"stage": "rollback", "code": "REMOVAL_UNCERTAIN"})
            self.assertEqual(bridge.states["local-1"], ["PERRO"])
            self.assertEqual(bridge.descriptions["local-1"], photo.applied_caption)
            status = run_status(manifest_path)
            self.assertEqual(status.status_summary["keywords_removed"], 1)
            self.assertEqual(status.next_action, "manual_review")


class StatusWorkflowTests(unittest.TestCase):
    def test_scan_rejects_a_non_path_runs_root_value(self) -> None:
        from photos_indexer.workflows import ScanDependencies, run_scan

        class Vision:
            def check_model(self, model: str) -> str:
                return "0.12.7"

        for runs_root in (123, None, []):
            with self.subTest(runs_root=runs_root):
                result = run_scan(
                    runs_root,  # type: ignore[arg-type]
                    dependencies=ScanDependencies(
                        platform_name=lambda: "Darwin",
                        vision_factory=Vision,
                    ),
                )
                self.assertEqual(result.exit_code, 2)
                self.assertEqual(result.error_codes, ("RUNS_ROOT_INVALID",))
                self.assertEqual(result.next_action, "fix_fatal_error")

    def test_workflow_entrypoints_reject_a_non_path_manifest_value(self) -> None:
        from photos_indexer.workflows import run_apply, run_rollback, run_status

        for workflow in (run_apply, run_rollback, run_status):
            with self.subTest(workflow=workflow.__name__):
                result = workflow(123)  # type: ignore[arg-type]
                self.assertEqual(result.exit_code, 2)
                self.assertEqual(result.error_codes, ("MANIFEST_INVALID",))
                self.assertEqual(result.next_action, "fix_fatal_error")

    def test_status_blocks_a_reviewed_manifest_when_its_source_is_missing(self) -> None:
        """A detached review must not look ready for mutation."""
        from photos_indexer.service import review_manifest
        from photos_indexer.workflows import run_status

        with tempfile.TemporaryDirectory() as tmp:
            runs_root = Path(tmp) / "runs"
            source_path = make_manifest(runs_root / "source", [make_photo(1)])
            runs_root.chmod(0o700)
            source = load_manifest(source_path.parent)
            reviewed_path = review_manifest(source_path, {source.photos[0].uuid: ["playa"]})
            source_path.unlink()
            source_path.parent.rmdir()

            result = run_status(reviewed_path)

            self.assertEqual(result.exit_code, 2)
            self.assertEqual(result.error_codes, ("REVIEW_PROVENANCE_INVALID",))
            self.assertEqual(result.status_summary["apply_status"], "blocked")
            self.assertEqual(result.status_summary["rollback_status"], "blocked")
            self.assertEqual(result.next_action, "rescan_after_provenance")

    def test_status_requires_permission_recovery_before_any_apply_cta(self) -> None:
        """A partial scan caused by TCC must be rerun, never applied blindly."""
        from photos_indexer.workflows import run_status

        with tempfile.TemporaryDirectory() as tmp:
            photo = make_photo(1)
            photo.uuid = None
            photo.existing_keywords = []
            photo.proposed_keywords = []
            photo.scan_state = "analysis_failed"
            photo.errors = [{"stage": "metadata", "code": "PHOTOS_AUTOMATION_DENIED"}]
            manifest_path = make_reviewed_manifest(
                Path(tmp) / "run", [photo], scan_status="ready_with_errors"
            )

            result = run_status(manifest_path)

            self.assertEqual(result.status_summary["apply_status"], "not_started")
            self.assertEqual(result.next_action, "grant_photos_automation")

    def test_status_blocks_a_mutated_legacy_schema1_manifest(self) -> None:
        """Legacy mutation state must not advertise rollback without provenance."""
        from photos_indexer.workflows import run_status

        with tempfile.TemporaryDirectory() as tmp:
            photo = make_photo(1)
            photo.apply_state = "verified"
            photo.applied_keywords = [photo.proposed_keywords[0]]
            manifest_path = make_manifest(Path(tmp) / "run", [photo])

            result = run_status(manifest_path)

            self.assertEqual(result.exit_code, 2)
            self.assertEqual(result.error_codes, ("MANIFEST_NOT_REVIEWED",))
            self.assertEqual(result.status_summary["apply_status"], "blocked")
            self.assertEqual(result.status_summary["rollback_status"], "blocked")
            self.assertEqual(result.next_action, "rescan_after_provenance")

    def test_apply_rejects_permission_only_manifest_until_a_fresh_scan(self) -> None:
        """A reviewed/no-op permission run cannot masquerade as a successful apply."""
        from photos_indexer.workflows import run_apply

        with tempfile.TemporaryDirectory() as tmp:
            photo = make_photo(1)
            photo.uuid = None
            photo.existing_keywords = []
            photo.proposed_keywords = []
            photo.scan_state = "analysis_failed"
            photo.errors = [{"stage": "metadata", "code": "PHOTOS_AUTOMATION_DENIED"}]
            manifest_path = make_reviewed_manifest(
                Path(tmp) / "run", [photo], scan_status="ready_with_errors"
            )

            result = run_apply(manifest_path)

            self.assertEqual(result.exit_code, 2)
            self.assertEqual(result.error_codes, ("PHOTOS_AUTOMATION_DENIED",))
            self.assertEqual(result.next_action, "grant_photos_automation")

    def test_status_preserves_rollback_action_after_partial_apply_with_permission_error(self) -> None:
        """Permission guidance must not hide an already verified rollback."""
        from photos_indexer.workflows import run_status

        with tempfile.TemporaryDirectory() as tmp:
            denied = make_photo(1)
            denied.uuid = None
            denied.existing_keywords = []
            denied.proposed_keywords = []
            denied.scan_state = "analysis_failed"
            denied.errors = [{"stage": "metadata", "code": "PHOTOS_AUTOMATION_DENIED"}]
            applied = make_photo(2)
            applied.apply_state = "verified"
            applied.applied_keywords = ["playa"]
            manifest_path = make_reviewed_manifest(
                Path(tmp) / "run", [denied, applied], scan_status="ready_with_errors"
            )

            result = run_status(manifest_path)

            self.assertEqual(result.next_action, "rollback_available")

    def test_status_reports_not_started_when_scan_has_no_apply_eligible_rows(self) -> None:
        from photos_indexer.workflows import run_status

        with tempfile.TemporaryDirectory() as tmp:
            photo = make_photo(1)
            photo.scan_state = "noop"
            photo.proposed_keywords = []
            manifest_path = make_manifest(Path(tmp) / "run", [photo])

            result = run_status(manifest_path)

            self.assertEqual(result.status_summary["apply_status"], "not_started")
            self.assertEqual(result.next_action, "none")

    def test_status_reports_caption_only_noop_as_pending_apply(self) -> None:
        from photos_indexer.workflows import run_status

        with tempfile.TemporaryDirectory() as tmp:
            photo = make_photo(1)
            photo.scan_state = "noop"
            photo.proposed_keywords = []
            photo.proposed_caption = "Una góndola en un canal visible."
            photo.caption_state = "proposed"
            manifest_path = make_manifest(Path(tmp) / "run", [photo])

            result = run_status(manifest_path)

            self.assertEqual(result.status_summary["apply_status"], "pending")
            self.assertEqual(result.status_summary["no_change"], 0)
            self.assertEqual(result.next_action, "review_then_apply")

    def test_status_blocks_a_reviewed_caption_with_preserved_state_before_apply(self) -> None:
        """A pre-apply preserved caption is edited approval state, not a pending run."""
        from photos_indexer.workflows import run_status

        with tempfile.TemporaryDirectory() as tmp:
            photo = make_photo(1)
            photo.proposed_caption = "Una playa visible."
            photo.caption_state = "proposed"
            manifest_path = make_reviewed_manifest(Path(tmp) / "run", [photo])
            manifest = load_manifest(manifest_path.parent)
            manifest.photos[0].caption_state = "preserved"
            write_manifest(manifest_path.parent, manifest)

            result = run_status(manifest_path)

            self.assertEqual(result.exit_code, 2)
            self.assertEqual(result.error_codes, ("REVIEW_NOT_PRISTINE",))
            self.assertEqual(result.status_summary["apply_status"], "blocked")
            self.assertEqual(result.status_summary["rollback_status"], "blocked")
            self.assertEqual(result.next_action, "rescan_after_provenance")

    def test_status_blocks_a_reviewed_caption_proposal_hidden_by_noop_apply(self) -> None:
        """A noop row cannot silently discard an approved pending caption."""
        from photos_indexer.workflows import run_status

        with tempfile.TemporaryDirectory() as tmp:
            photo = make_photo(1)
            photo.proposed_caption = "Una playa visible."
            photo.caption_state = "proposed"
            manifest_path = make_reviewed_manifest(Path(tmp) / "run", [photo])
            manifest = load_manifest(manifest_path.parent)
            manifest.photos[0].apply_state = "noop"
            write_manifest(manifest_path.parent, manifest)

            result = run_status(manifest_path)

            self.assertEqual(result.exit_code, 2)
            self.assertEqual(result.error_codes, ("REVIEW_NOT_PRISTINE",))
            self.assertEqual(result.status_summary["apply_status"], "blocked")
            self.assertEqual(result.status_summary["rollback_status"], "blocked")
            self.assertEqual(result.next_action, "rescan_after_provenance")

    def test_status_is_manifest_only_and_computes_counts_and_next_action(self) -> None:
        from unittest.mock import patch

        from photos_indexer.workflows import run_status

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "runs" / "run"
            ready = make_photo(1)
            applied = make_photo(2)
            applied.apply_state = "verified"
            applied.applied_keywords = ["playa"]
            uncertain = make_photo(3)
            uncertain.apply_state = "uncertain"
            uncertain.errors = [{"stage": "apply", "code": "WRITE_UNCERTAIN"}]
            removed = make_photo(4)
            removed.apply_state = "verified"
            removed.applied_keywords = ["playa"]
            removed.rollback_state = "verified_removed"
            removed.rolled_back_keywords = ["playa"]
            manifest_path = make_reviewed_manifest(run_dir, [ready, applied, uncertain, removed])

            with patch("photos_indexer.workflows.PhotoKitSelector", side_effect=AssertionError("adapter instantiated")), patch(
                "photos_indexer.workflows.PhotoScriptBridge", side_effect=AssertionError("adapter instantiated")
            ):
                result = run_status(manifest_path)

            self.assertEqual(result.exit_code, 1)
            self.assertEqual(result.counts["scan"], {"ready": 4})
            self.assertEqual(result.counts["apply"], {"not_run": 1, "verified": 2, "uncertain": 1})
            self.assertEqual(result.counts["rollback"], {"not_run": 3, "verified_removed": 1})
            self.assertEqual(result.status_summary, {
                "scan_status": "ready",
                "photos_access": "authorized",
                "selection_strategy": "recent",
                "apply_status": "uncertain",
                "rollback_status": "partial",
                "selected": 4,
                "processed": 4,
                "no_change": 0,
                "failed": 0,
                "uncertain": 1,
                "screenshots_excluded": 0,
                "non_image_excluded": 0,
                "keywords_proposed": 4,
                "keywords_verified": 2,
                "keywords_removed": 1,
                "keywords_preserved": 0,
                "keywords_pending": 1,
                "captions_proposed": 0,
                "captions_verified": 0,
                "captions_removed": 0,
                "captions_preserved": 0,
                "captions_pending": 0,
                "approved_photos": 4,
                "approved_keywords": 4,
                "approved_captions": 0,
                "errors_by_code": {"WRITE_UNCERTAIN": 1},
            })
            self.assertEqual(result.next_action, "manual_review")

    def test_status_counts_no_change_once_and_only_eligible_mutation_rows(self) -> None:
        from photos_indexer.workflows import run_status

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            noop = make_photo(1)
            noop.scan_state = "noop"
            noop.proposed_keywords = []
            noop.apply_state = "noop"
            ready = make_photo(2)
            manifest_path = make_manifest(run_dir, [noop, ready])

            result = run_status(manifest_path)

            self.assertEqual(result.status_summary["no_change"], 1)
            self.assertEqual(result.status_summary["apply_status"], "pending")
            self.assertEqual(result.status_summary["rollback_status"], "not_started")

    def test_status_keeps_rollback_available_when_partial_rollback_still_has_pending_rows(self) -> None:
        from photos_indexer.workflows import run_status

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            removed = make_photo(1)
            removed.apply_state = "verified"
            removed.applied_keywords = ["playa"]
            removed.rollback_state = "verified_removed"
            removed.rolled_back_keywords = ["playa"]
            pending = make_photo(2)
            pending.apply_state = "verified"
            pending.proposed_keywords = ["canal"]
            pending.applied_keywords = ["canal"]
            manifest_path = make_reviewed_manifest(run_dir, [removed, pending])

            result = run_status(manifest_path)

            self.assertEqual(result.status_summary["rollback_status"], "partial")
            self.assertEqual(result.next_action, "rollback_available")

    def test_status_counts_cancelled_apply_and_rollback_rows_as_failed_summary(self) -> None:
        from photos_indexer.workflows import run_status

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            cancelled_apply = make_photo(1)
            cancelled_apply.apply_state = "cancelled"
            cancelled_apply.errors = [{"stage": "apply", "code": "CANCELLED"}]
            cancelled_rollback = make_photo(2)
            cancelled_rollback.apply_state = "verified"
            cancelled_rollback.applied_keywords = [cancelled_rollback.proposed_keywords[0]]
            cancelled_rollback.rollback_state = "cancelled"
            cancelled_rollback.errors = [{"stage": "rollback", "code": "CANCELLED"}]
            manifest_path = make_reviewed_manifest(run_dir, [cancelled_apply, cancelled_rollback])

            result = run_status(manifest_path)

            self.assertEqual(result.status_summary["apply_status"], "partial")
            self.assertEqual(result.status_summary["rollback_status"], "partial")
            self.assertEqual(result.status_summary["failed"], 2)
            self.assertEqual(result.next_action, "retry_failed_operation")

    def test_status_counts_casing_conflict_as_uncertain_manual_review(self) -> None:
        from photos_indexer.workflows import run_status

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            conflict = make_photo(1)
            conflict.apply_state = "verified"
            conflict.applied_keywords = [conflict.proposed_keywords[0]]
            conflict.rollback_state = "casing_conflict"
            conflict.rolled_back_keywords = [conflict.proposed_keywords[0]]
            conflict.errors = [{"stage": "rollback", "code": "CASING_CONFLICT"}]
            manifest_path = make_reviewed_manifest(run_dir, [conflict])

            result = run_status(manifest_path)

            self.assertEqual(result.status_summary["rollback_status"], "partial")
            self.assertEqual(result.status_summary["uncertain"], 1)
            self.assertEqual(result.status_summary["failed"], 0)
            self.assertEqual(result.next_action, "manual_review")

    def test_status_does_not_suggest_reapply_after_caption_was_preserved_as_noop(self) -> None:
        from photos_indexer.workflows import run_status

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            photo = make_photo(1)
            photo.scan_state = "noop"
            photo.proposed_keywords = []
            photo.proposed_caption = "Una playa visible."
            photo.caption_state = "preserved"
            photo.apply_state = "noop"
            manifest_path = make_manifest(run_dir, [photo])

            result = run_status(manifest_path)

            self.assertEqual(result.status_summary["apply_status"], "complete")
            self.assertEqual(result.status_summary["no_change"], 1)
            self.assertEqual(result.next_action, "none")

    def test_status_counts_keywords_verified_before_later_caption_failure(self) -> None:
        from photos_indexer.workflows import run_status

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            photo = make_photo(1)
            photo.proposed_caption = "Una playa visible."
            photo.caption_state = "proposed"
            photo.apply_state = "uncertain"
            photo.applied_keywords = ["playa"]
            photo.errors = [{"stage": "apply", "code": "WRITE_UNCERTAIN"}]
            manifest_path = make_reviewed_manifest(run_dir, [photo])

            result = run_status(manifest_path)

            self.assertEqual(result.status_summary["apply_status"], "uncertain")
            self.assertEqual(result.status_summary["keywords_verified"], 1)
            self.assertEqual(result.next_action, "manual_review")

    def test_status_reports_interrupted_write_checkpoint_before_recovery(self) -> None:
        """A persisted writing checkpoint must expose its recovery cause."""
        from photos_indexer.workflows import run_status

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            photo = make_photo(1)
            photo.apply_state = "writing"
            manifest_path = make_reviewed_manifest(run_dir, [photo])

            result = run_status(manifest_path)

            self.assertEqual(result.exit_code, 1)
            self.assertEqual(result.error_codes, ("INTERRUPTED_WRITE",))
            self.assertEqual(result.status_summary["errors_by_code"], {"INTERRUPTED_WRITE": 1})
            self.assertEqual(result.next_action, "manual_review")

    def test_status_reports_interrupted_removal_checkpoint_before_recovery(self) -> None:
        """A persisted removing checkpoint must expose its recovery cause."""
        from photos_indexer.workflows import run_status

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            photo = make_photo(1)
            photo.apply_state = "verified"
            photo.applied_keywords = ["playa"]
            photo.rollback_state = "removing"
            manifest_path = make_reviewed_manifest(run_dir, [photo])

            result = run_status(manifest_path)

            self.assertEqual(result.exit_code, 1)
            self.assertEqual(result.error_codes, ("INTERRUPTED_REMOVAL",))
            self.assertEqual(result.status_summary["errors_by_code"], {"INTERRUPTED_REMOVAL": 1})
            self.assertEqual(result.next_action, "manual_review")

    def test_failed_scan_dominates_status_and_includes_run_error(self) -> None:
        from photos_indexer.workflows import run_status

        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = make_manifest(Path(tmp) / "run", [], scan_status="failed")

            result = run_status(manifest_path)

            self.assertEqual(result.exit_code, 1)
            self.assertEqual(result.next_action, "fix_failed_scan")
            self.assertEqual(result.status_summary["apply_status"], "blocked")
            self.assertEqual(result.status_summary["rollback_status"], "blocked")
            self.assertEqual(result.status_summary["errors_by_code"], {"WORKSPACE_FAILED": 1})

    def test_limited_photos_access_is_visible_in_scan_and_status(self) -> None:
        from photos_indexer.workflows import run_status

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            manifest_path = make_manifest(run_dir, [make_photo(1)])
            manifest = load_manifest(run_dir)
            manifest.selection["access"] = "limited"
            write_manifest(run_dir, manifest)

            result = run_status(manifest_path)

            self.assertEqual(result.status_summary["photos_access"], "limited")
            self.assertIn("PHOTOS_ACCESS_LIMITED", result.warning_codes)

    def test_status_preserves_fewer_photos_warning_from_scan_selection(self) -> None:
        from photos_indexer.workflows import run_status

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            manifest_path = make_manifest(run_dir, [make_photo(1)])
            manifest = load_manifest(run_dir)
            manifest.selection["requested"] = 20
            write_manifest(run_dir, manifest)

            result = run_status(manifest_path)

            self.assertIn("FEWER_PHOTOS_AVAILABLE", result.warning_codes)

    def test_status_rejects_non_manifest_paths(self) -> None:
        from photos_indexer.workflows import run_status

        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(run_status(Path(tmp) / "other.json").exit_code, 2)


class ConsoleProjectionTests(unittest.TestCase):
    def test_sanitized_rows_expose_only_bounded_display_fields_and_error_codes(self) -> None:
        from photos_indexer.workflows import sanitized_rows

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "runs" / "private-path"
            photo = make_photo(1)
            photo.title = "Título visible que debe truncarse SHOULD_NOT_PRINT"
            photo.errors = [{"stage": "analysis", "code": "LOW_CONFIDENCE"}]
            manifest_path = make_manifest(run_dir, [photo], scan_status="ready_with_errors")
            manifest = load_manifest(manifest_path.parent)

            rows = sanitized_rows(manifest)

            self.assertEqual(rows, [{
                "uuid": "00000000",
                "title": "Título visible que debe…",
                "date": "2026-08-01T10:00",
                "existing": "PERRO",
                "proposed": "playa",
                "caption_status": "—",
                "confidence": "0.90",
                "status": "ready",
                "error_codes": "LOW_CONFIDENCE",
            }])
            rendered = str(rows)
            self.assertNotIn("00000000-0000-4000-8000-000000000001", rendered)
            self.assertNotIn("SHOULD_NOT_PRINT", rendered)
            self.assertNotIn("local-1", rendered)
            self.assertNotIn("private-path", rendered)

    def test_sanitized_rows_mark_caption_only_proposals_without_exposing_text(self) -> None:
        from photos_indexer.workflows import sanitized_rows

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            photo = make_photo(1)
            photo.scan_state = "noop"
            photo.proposed_keywords = []
            photo.proposed_caption = "Una escena visible."
            photo.caption_state = "proposed"
            manifest_path = make_manifest(run_dir, [photo])
            manifest = load_manifest(manifest_path.parent)

            row = sanitized_rows(manifest)[0]

            self.assertEqual(row["proposed"], "")
            self.assertEqual(row["caption_status"], "pendiente")
            self.assertNotIn(photo.proposed_caption, str(row))

    def test_sanitized_rows_neutralize_terminal_controls_without_mutating_manifest(self) -> None:
        from photos_indexer.workflows import sanitized_rows

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            photo = make_photo(1)
            photo.title = "\x1b[31mTítulo\x1b[0m"
            manifest_path = make_manifest(run_dir, [photo])
            manifest = load_manifest(manifest_path.parent)

            row = sanitized_rows(manifest)[0]

            self.assertNotIn("\x1b", row["title"])
            self.assertIn("Título", row["title"])
            self.assertIn("\x1b", manifest.photos[0].title)

    def test_sanitized_rows_replace_unpaired_surrogates_in_keywords(self) -> None:
        from photos_indexer.workflows import sanitized_rows

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            photo = make_photo(1)
            manifest_path = make_manifest(run_dir, [photo])
            manifest = load_manifest(manifest_path.parent)
            manifest.photos[0].existing_keywords = ["Viaje\ud800"]

            row = sanitized_rows(manifest)[0]

            self.assertEqual(row["existing"], "Viaje�")
            self.assertIn("\ud800", manifest.photos[0].existing_keywords[0])

    def test_sanitized_rows_show_uncertain_caption_without_exposing_caption(self) -> None:
        from photos_indexer.workflows import sanitized_rows

        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = make_manifest(Path(tmp) / "run", [make_photo(1)])
            manifest = load_manifest(manifest_path.parent)
            photo = manifest.photos[0]
            photo.proposed_caption = None
            photo.applied_caption = "Texto privado"
            photo.caption_state = "uncertain"

            row = sanitized_rows(manifest)[0]

            self.assertEqual(row["caption_status"], "incierto")
            self.assertNotIn("Texto privado", str(row))

            csv_path = write_preview_csv(Path(tmp) / "csv", manifest)
            with csv_path.open(newline="", encoding="utf-8") as handle:
                csv_row = next(csv.DictReader(handle))
            self.assertEqual(csv_row["caption_status"], "incierto")
            self.assertNotIn("Texto privado", str(csv_row))

class WorkflowGuardBranchTests(unittest.TestCase):
    """Exercise fail-closed workflow guards without opening macOS adapters."""

    def test_metadata_identity_progress_and_architecture_guards_fail_closed(self) -> None:
        from photos_indexer.workflows import (
            _IdentityMismatchError,
            _landmark_visual_match,
            _notify_scan_progress,
            _require_fresh_identity,
            _safe_photo_metadata,
            _visual_architecture_cue_matches,
        )

        record = ScriptPhotoRecord(
            "not-a-uuid", "local-1", "", datetime(2026, 8, 1, 10), (),
        )
        self.assertIsNone(_safe_photo_metadata(record, "local-1"))
        self.assertIsNone(_safe_photo_metadata(object(), "local-1"))
        valid = ScriptPhotoRecord(
            str(make_photo(1).uuid), "local-1", "", datetime(2026, 8, 1, 10), (),
        )
        self.assertIs(_safe_photo_metadata(valid, "local-1"), valid)
        self.assertIsNone(_safe_photo_metadata(valid, "local-other"))

        photo = make_photo(1)
        with self.assertRaises(_IdentityMismatchError):
            _require_fresh_identity(object(), photo)
        with self.assertRaises(_IdentityMismatchError):
            _require_fresh_identity(
                ScriptPhotoRecord("bad", photo.photos_local_identifier, "", photo.date, ()), photo,
            )
        with self.assertRaises(_IdentityMismatchError):
            _require_fresh_identity(
                ScriptPhotoRecord(str(photo.uuid), "other-local", "", photo.date, ()), photo,
            )

        self.assertFalse(_landmark_visual_match((), ""))
        self.assertFalse(_landmark_visual_match(("cúpula",), "Basílica"))
        self.assertFalse(_visual_architecture_cue_matches("", {"iglesia"}))
        self.assertFalse(_visual_architecture_cue_matches("paisaje", {"edificio histórico"}))

        calls: list[str] = []

        def broken_observer(_: PhotoRecord) -> None:
            calls.append("called")
            raise RuntimeError("observer failure")

        _notify_scan_progress(broken_observer, photo)
        _notify_scan_progress(None, photo)
        self.assertEqual(calls, ["called"])

    def test_review_row_comparison_allows_only_reductions(self) -> None:
        from photos_indexer.workflows import _reviewed_rows_match_source

        with tempfile.TemporaryDirectory() as tmp:
            source = load_manifest(make_manifest(Path(tmp) / "source", [make_photo(1)]).parent)
            reviewed = load_manifest(source_path := make_manifest(Path(tmp) / "reviewed", [make_photo(1)]).parent)
            reviewed.photos[0].model_used = source.model["name"]
            reviewed.photos[0].model_reason = "single_policy"
            self.assertTrue(_reviewed_rows_match_source(source, reviewed))

            def fresh_reviewed() -> object:
                value = load_manifest(source_path)
                value.photos[0].model_used = source.model["name"]
                value.photos[0].model_reason = "single_policy"
                return value

            reviewed.selection["access"] = "limited"
            self.assertFalse(_reviewed_rows_match_source(source, reviewed))
            reviewed.selection = dict(source.selection)
            reviewed.photos = []
            self.assertFalse(_reviewed_rows_match_source(source, reviewed))

            reviewed = fresh_reviewed()
            reviewed.photos[0].photos_local_identifier = "unknown"
            self.assertFalse(_reviewed_rows_match_source(source, reviewed))

            reviewed = fresh_reviewed()
            reviewed.photos[0].title = "changed"
            self.assertFalse(_reviewed_rows_match_source(source, reviewed))

            reviewed = fresh_reviewed()
            reviewed.photos[0].proposed_keywords = ["playa", "playa"]
            self.assertFalse(_reviewed_rows_match_source(source, reviewed))

            reviewed = fresh_reviewed()
            reviewed.photos[0].proposed_caption = "Una playa visible."
            self.assertFalse(_reviewed_rows_match_source(source, reviewed))

            reviewed = fresh_reviewed()
            reviewed.photos[0].scan_state = "analysis_failed"
            self.assertFalse(_reviewed_rows_match_source(source, reviewed))

            noop = make_photo(2)
            noop.scan_state = "noop"
            noop.proposed_keywords = []
            noop_source = load_manifest(make_manifest(Path(tmp) / "noop-source", [noop]).parent)
            noop_copy = make_photo(2)
            noop_copy.scan_state = "noop"
            noop_copy.proposed_keywords = []
            noop_review = load_manifest(make_manifest(Path(tmp) / "noop-review", [noop_copy]).parent)
            noop_review.photos[0].scan_state = "ready"
            self.assertFalse(_reviewed_rows_match_source(noop_source, noop_review))

    def test_review_provenance_rejects_a_mutated_source_scan(self) -> None:
        from photos_indexer.service import review_manifest
        from photos_indexer.workflows import _review_provenance_is_valid

        with tempfile.TemporaryDirectory() as tmp:
            runs_root = Path(tmp) / "runs"
            source_path = make_manifest(runs_root / "source", [make_photo(1)])
            runs_root.chmod(0o700)
            reviewed_path = review_manifest(source_path, {make_photo(1).uuid: ["playa"]})
            source = load_manifest(source_path.parent)
            source_photo = source.photos[0]
            source_photo.apply_state = "verified"
            source_photo.applied_keywords = ["playa"]
            source_photo.mutation_digest = compute_mutation_digest(source, source_photo)
            write_manifest(source_path.parent, source)

            self.assertFalse(_review_provenance_is_valid(reviewed_path, load_manifest(reviewed_path.parent)))

    def test_review_provenance_scans_only_private_sibling_runs(self) -> None:
        from photos_indexer.service import review_manifest
        from photos_indexer.workflows import _review_provenance_is_valid

        with tempfile.TemporaryDirectory() as tmp:
            runs_root = Path(tmp) / "runs"
            source_dir = runs_root / "source"
            source_path = make_manifest(source_dir, [make_photo(1)])
            runs_root.chmod(0o700)
            (runs_root / "invalid").mkdir(mode=0o700)
            (runs_root / "invalid" / "manifest.json").write_text("not json", encoding="utf-8")

            reviewed_path = review_manifest(source_path, {make_photo(1).uuid: ["playa"]})
            reviewed = load_manifest(reviewed_path.parent)
            self.assertTrue(_review_provenance_is_valid(reviewed_path, reviewed))

            reviewed.source_scan_digest = "0" * 64
            self.assertFalse(_review_provenance_is_valid(reviewed_path, reviewed))

            class MissingProvenance:
                schema_version = 3
                reviewed_from_run_id = None
                source_scan_digest = None

            self.assertFalse(_review_provenance_is_valid(reviewed_path, MissingProvenance()))

    def test_review_provenance_rejects_a_changed_top_level_model_plan(self) -> None:
        from photos_indexer.service import review_manifest
        from photos_indexer.workflows import _review_provenance_is_valid

        with tempfile.TemporaryDirectory() as tmp:
            source_path = make_manifest(Path(tmp) / "source", [make_photo(1)])
            reviewed_path = review_manifest(source_path, {make_photo(1).uuid: ["playa"]})
            reviewed = load_manifest(reviewed_path.parent)
            reviewed.model["detailed_name"] = "qwen3-vl:8b"
            write_manifest(reviewed_path.parent, reviewed)

            self.assertFalse(_review_provenance_is_valid(reviewed_path, load_manifest(reviewed_path.parent)))

    def test_review_provenance_rejects_a_changed_top_level_app_identity(self) -> None:
        from photos_indexer.service import review_manifest
        from photos_indexer.workflows import _review_provenance_is_valid

        with tempfile.TemporaryDirectory() as tmp:
            source_path = make_manifest(Path(tmp) / "source", [make_photo(1)])
            reviewed_path = review_manifest(source_path, {make_photo(1).uuid: ["playa"]})
            reviewed = load_manifest(reviewed_path.parent)
            reviewed.app["version"] = "9.9.9"
            write_manifest(reviewed_path.parent, reviewed)

            self.assertFalse(_review_provenance_is_valid(reviewed_path, load_manifest(reviewed_path.parent)))

    def test_scan_cancellation_persists_a_failed_run_without_processing_photos(self) -> None:
        from photos_indexer.workflows import ScanDependencies, run_scan

        with tempfile.TemporaryDirectory() as tmp:
            selected = SelectedPhoto("local-1", datetime(2026, 8, 1, 10))

            class Selector:
                def select(self, *, limit: int) -> PhotoSelection:
                    return PhotoSelection((selected,), limit, 1, 0, "authorized")

            class Vision:
                def check_model(self, model: str) -> str:
                    return "0.12.7"

            result = run_scan(
                Path(tmp) / "runs",
                limit=1,
                dependencies=ScanDependencies(
                    platform_name=lambda: "Darwin",
                    selector_factory=Selector,
                    bridge_factory=lambda: object(),
                    vision_factory=Vision,
                    workspace_factory=FakeWorkspace,
                    recover_workspaces=lambda _: [],
                ),
                cancel_requested=lambda: True,
            )

            self.assertEqual(result.exit_code, 1)
            self.assertEqual(result.error_codes, ("CANCELLED",))
            self.assertEqual(result.next_action, "fix_failed_scan")
            assert result.manifest is not None
            self.assertEqual(result.manifest.scan_status, "failed")
            self.assertEqual(result.manifest.run_errors, [{"stage": "workspace", "code": "CANCELLED"}])
            self.assertEqual(result.manifest.photos, [])

    def test_runs_and_operation_lock_guards_reject_non_directories_and_unsafe_files(self) -> None:
        from photos_indexer.workflows import _ensure_private_lock_parent, _ensure_runs_root, _open_operation_lock

        with tempfile.TemporaryDirectory() as tmp:
            root_file = Path(tmp) / "runs"
            root_file.write_text("not a directory", encoding="utf-8")
            with self.assertRaises(OSError):
                _ensure_runs_root(root_file)

            lock_parent_file = Path(tmp) / "lock-parent"
            lock_parent_file.write_text("not a directory", encoding="utf-8")
            with self.assertRaises(NotADirectoryError):
                _ensure_private_lock_parent(lock_parent_file)

            lock_file = Path(tmp) / "lock"
            lock_file.write_text("existing", encoding="utf-8")
            lock_file.chmod(0o644)
            with self.assertRaises(ValueError):
                _open_operation_lock(lock_file)

    def test_private_lock_parent_rejects_symlinked_ancestor(self) -> None:
        from photos_indexer.workflows import _ensure_private_lock_parent

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root / "outside"
            outside.mkdir()
            alias = root / "alias"
            alias.symlink_to(outside, target_is_directory=True)

            with self.assertRaises(ValueError):
                _ensure_private_lock_parent(alias / "nested")

            self.assertFalse((outside / "nested").exists())


if __name__ == "__main__":
    unittest.main()
