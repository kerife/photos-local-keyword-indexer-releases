from __future__ import annotations

import csv
import hashlib
import json
import os
import stat
import tempfile
import unittest
import uuid
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import photos_indexer.manifest as manifest_module

try:
    from photos_indexer.manifest import (
        MAX_MANIFEST_BYTES,
        ManifestError,
        PhotoRecord,
        ScanManifest,
        TechnicalTrace,
        build_scan_manifest,
        compute_mutation_digest,
        compute_rollback_digest,
        load_manifest,
        write_manifest,
        write_preview_csv,
    )
    from photos_indexer.models import VisionResult, VisionResultError
    from photos_indexer.taxonomy import (
        CONTEXTUAL_TAXONOMY_ID,
        CONTEXTUAL_TAXONOMY_SHA256,
        TAXONOMY_ID,
        TAXONOMY_SHA256,
        LEGACY_TAXONOMY_ID,
        LEGACY_TAXONOMY_SHA256,
        TAXONOMY,
        canonical_keyword_key,
        normalize_existing_keywords,
        proposed_keywords,
    )
except ModuleNotFoundError:
    PACKAGE_AVAILABLE = False
else:
    PACKAGE_AVAILABLE = True


class PackageAvailabilityTest(unittest.TestCase):
    def test_photos_indexer_package_is_available(self) -> None:
        self.assertTrue(PACKAGE_AVAILABLE)


def vision_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "keywords": ["Perro", "playa"],
        "caption": "Un perro en la playa.",
        "contains_people": False,
        "contains_text": False,
        "confidence": 0.93,
    }
    payload.update(overrides)
    return payload


def digest_for_manifest_payload(payload: dict[str, object]) -> str:
    immutable = {key: value for key, value in payload.items() if key != "scan_digest"}
    photos: list[dict[str, object]] = []
    for original in immutable["photos"]:  # type: ignore[union-attr]
        photo = dict(original)
        for key in ("apply_state", "applied_keywords", "rollback_state", "rolled_back_keywords"):
            photo.pop(key, None)
        photo["errors"] = [
            error for error in photo["errors"]  # type: ignore[assignment]
            if error["stage"] not in {"apply", "rollback"}
        ]
        photos.append(photo)
    immutable["photos"] = photos
    encoded = json.dumps(immutable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@unittest.skipUnless(PACKAGE_AVAILABLE, "production package is not available")
class VisionResultTests(unittest.TestCase):
    def test_rejects_a_non_object_response(self) -> None:
        with self.assertRaises(VisionResultError):
            VisionResult.from_mapping(["not", "an", "object"])  # type: ignore[arg-type]

    def test_accepts_exactly_the_expected_schema(self) -> None:
        result = VisionResult.from_mapping(vision_payload())

        self.assertEqual(result.keywords, ("Perro", "playa"))
        self.assertEqual(result.caption, "Un perro en la playa.")

    def test_rejects_missing_or_extra_keys(self) -> None:
        for payload in (
            {key: value for key, value in vision_payload().items() if key != "caption"},
            vision_payload(extra="forbidden"),
        ):
            with self.assertRaises(VisionResultError):
                VisionResult.from_mapping(payload)

    def test_rejects_non_strict_booleans_and_out_of_range_confidence(self) -> None:
        for payload in (
            vision_payload(contains_people=1),
            vision_payload(contains_text="false"),
            vision_payload(confidence=True),
            vision_payload(confidence=1.01),
        ):
            with self.assertRaises(VisionResultError):
                VisionResult.from_mapping(payload)

    def test_rejects_more_than_eight_keywords(self) -> None:
        with self.assertRaises(VisionResultError):
            VisionResult.from_mapping(vision_payload(keywords=["perro"] * 9))

    def test_accepts_an_empty_keyword_list_as_a_noop(self) -> None:
        result = VisionResult.from_mapping(vision_payload(keywords=[]))

        self.assertEqual(result.keywords, ())

    def test_rejects_empty_keyword_values_and_captions_over_300_characters(self) -> None:
        for payload in (
            vision_payload(keywords=[""]),
            vision_payload(keywords=[" \t "]),
            vision_payload(caption="x" * 301),
        ):
            with self.assertRaises(VisionResultError):
                VisionResult.from_mapping(payload)

    def test_rejects_an_unbounded_keyword_value(self) -> None:
        with self.assertRaises(VisionResultError):
            VisionResult.from_mapping(vision_payload(keywords=["x" * 129]))

    def test_rejects_string_subclasses(self) -> None:
        class StringSubclass(str):
            pass

        for payload in (
            vision_payload(caption=StringSubclass("caption")),
            vision_payload(keywords=[StringSubclass("perro")]),
        ):
            with self.assertRaises(VisionResultError):
                VisionResult.from_mapping(payload)


@unittest.skipUnless(PACKAGE_AVAILABLE, "production package is not available")
class TaxonomyTests(unittest.TestCase):
    def test_rejects_non_string_keywords_and_negative_limits(self) -> None:
        with self.assertRaises(TypeError):
            canonical_keyword_key(1)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            proposed_keywords([], [1])  # type: ignore[list-item]
        with self.assertRaises(ValueError):
            proposed_keywords([], ["perro"], maximum=-1)

    def test_canonical_key_is_nfkc_casefold_and_collapses_whitespace(self) -> None:
        self.assertEqual(canonical_keyword_key("  BLANCO\u00a0 Y\tNEGRO  "), "blanco y negro")

    def test_proposals_preserve_existing_values_but_emit_canonical_allowlisted_terms(self) -> None:
        existing = ["PERRO", "PERRO", "Cafe\u0301"]
        candidates = [" perro ", "  Playa", "BLANCO\u00a0Y\tNEGRO", "inexistente", "gato"]

        self.assertEqual(normalize_existing_keywords(existing), ["PERRO", "PERRO", "Café"])
        self.assertEqual(proposed_keywords(existing, candidates), ["playa", "blanco y negro", "inexistente", "gato"])

    def test_public_proposals_never_exceed_eight_even_when_caller_requests_more(self) -> None:
        candidates = ["persona", "grupo", "retrato", "animal", "mascota", "perro", "gato", "ave", "pez"]
        self.assertEqual(proposed_keywords([], candidates, maximum=9), candidates[:8])

    def test_public_proposals_respect_a_zero_limit(self) -> None:
        self.assertEqual(proposed_keywords([], ["perro"], maximum=0), [])

    def test_taxonomy_has_stable_identity_and_digest(self) -> None:
        self.assertEqual(TAXONOMY_ID, "es-semantic-open-v3")
        self.assertEqual(len(TAXONOMY), 122)
        self.assertEqual(TAXONOMY_SHA256, "a0fe5883ddd2d6e1ccf39f3a289709b9626ef46e1538a4c44463494fe776e41a")


@unittest.skipUnless(PACKAGE_AVAILABLE, "production package is not available")
class ManifestTests(unittest.TestCase):
    def test_technical_trace_is_optional_but_immutable_when_present(self) -> None:
        manifest = self.make_manifest()
        self.assertIsNone(manifest.photos[0].technical_trace)
        manifest.photos[0].technical_trace = TechnicalTrace(
            prompt_effective="Analiza evidencia visual; no transcribas texto literal.",
            prompt_version="vision-prompt-v1",
            prompt_sha256=hashlib.sha256(
                "Analiza evidencia visual; no transcribas texto literal.".encode("utf-8")
            ).hexdigest(),
            used_gps=True,
            used_apple_maps=True,
            used_landmark=False,
            place_context=("Canal Grande",),
            durations_ms={
                "metadata": 2,
                "export": 3,
                "context": 5,
                "inference": 11,
                "postprocess": 7,
                "total": 28,
            },
        )
        manifest.scan_digest = manifest.compute_scan_digest()

        restored = ScanManifest.from_dict(manifest.to_dict())
        original_digest = restored.scan_digest

        self.assertEqual(restored.photos[0].technical_trace, manifest.photos[0].technical_trace)
        assert restored.photos[0].technical_trace is not None
        restored.photos[0].technical_trace.durations_ms["postprocess"] = 8
        restored.photos[0].technical_trace.durations_ms["total"] = 29
        self.assertNotEqual(restored.compute_scan_digest(), original_digest)

    def test_legacy_technical_trace_without_place_states_keeps_scan_digest_valid(self) -> None:
        prompt = "Analiza evidencia visual; usa el contexto de lugar sanitizado: Zocalo."
        photo = PhotoRecord(
            uuid="00000000-0000-4000-8000-000000000001",
            photos_local_identifier="local-id-1",
            title="Centro",
            date=datetime(2026, 8, 24, 10, 30),
            existing_keywords=[],
            proposed_keywords=["plaza"],
            contains_people=False,
            contains_text=False,
            confidence=0.85,
            model_used="qwen3-vl:4b",
            model_reason="location_context",
            technical_trace=TechnicalTrace(
                prompt_effective=prompt,
                prompt_version="vision-prompt-v1",
                prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                used_gps=True,
                used_apple_maps=True,
                used_landmark=False,
                place_context=(),
                durations_ms={
                    "metadata": 1,
                    "export": 1,
                    "context": 1,
                    "inference": 1,
                    "postprocess": 1,
                    "total": 5,
                },
            ),
        )
        manifest = build_scan_manifest(
            run_id=str(uuid.uuid4()),
            app_name="photos-indexer",
            app_version="0.1.0",
            model_name="qwen3-vl:4b",
            ollama_version="0.12.0",
            endpoint="http://127.0.0.1:11434",
            selection={"requested": 1, "eligible": 1, "screenshots_excluded": 0, "access": "authorized"},
            photos=[photo],
            summary={"ready": 1, "noop": 0, "analysis_failed": 0},
            model_policy="single",
            fast_model="qwen3-vl:4b",
            detailed_model="qwen3-vl:4b",
            ollama_versions={"qwen3-vl:4b": "0.12.0"},
        )
        payload = manifest.to_dict()
        trace_payload = payload["photos"][0]["technical_trace"]
        trace_payload.pop("place_lookup_state")
        trace_payload.pop("place_evidence_state")
        payload["scan_digest"] = digest_for_manifest_payload(payload)

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir(mode=0o700)
            path = run_dir / "manifest.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            path.chmod(0o600)

            restored = load_manifest(run_dir)

        restored_trace = restored.photos[0].technical_trace
        assert restored_trace is not None
        self.assertEqual(restored.scan_digest, payload["scan_digest"])
        self.assertEqual(restored_trace.place_lookup_state, "no_results")
        self.assertEqual(restored_trace.place_evidence_state, "not_applicable")
        self.assertNotIn("place_lookup_state", restored_trace.to_dict())
        self.assertNotIn("place_evidence_state", restored_trace.to_dict())

    def test_technical_trace_rejects_coordinates_and_unsanitized_place_context(self) -> None:
        prompt = "Ubicación aproximada: latitud 19.4326, longitud -99.1332."
        for override in (
            {"prompt_effective": prompt},
            {"place_context": ("19.4326, -99.1332",)},
        ):
            values = {
                "prompt_effective": "Analiza evidencia visual.",
                "prompt_version": "vision-prompt-v1",
                "used_gps": True,
                "used_apple_maps": True,
                "used_landmark": False,
                "place_context": ("Canal Grande",),
                "durations_ms": {
                    "metadata": 0, "export": 0, "context": 0,
                    "inference": 0, "postprocess": 0, "total": 0,
                },
                **override,
            }
            values["prompt_sha256"] = hashlib.sha256(
                str(values["prompt_effective"]).encode("utf-8")
            ).hexdigest()
            with self.subTest(override=override), self.assertRaises(ManifestError):
                TechnicalTrace(**values)

    def make_manifest(self) -> ScanManifest:
        photo = PhotoRecord(
            uuid="00000000-0000-4000-8000-000000000001",
            photos_local_identifier="local-id-1",
            title="Vacaciones",
            date=datetime(2026, 8, 24, 10, 30),
            existing_keywords=["PERRO"],
            proposed_keywords=["playa"],
            contains_people=False,
            contains_text=False,
            confidence=0.95,
        )
        return build_scan_manifest(
            run_id=str(uuid.uuid4()),
            app_name="photos-indexer",
            app_version="0.1.0",
            model_name="llava",
            ollama_version="0.12.0",
            endpoint="http://127.0.0.1:11434",
            selection={"requested": 1, "eligible": 1, "screenshots_excluded": 0, "access": "authorized"},
            photos=[photo],
            summary={"ready": 1, "noop": 0, "analysis_failed": 0},
        )

    def test_manifest_serialization_never_includes_caption(self) -> None:
        payload = self.make_manifest().to_dict()
        self.assertNotIn("caption", json.dumps(payload))
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["dry_run"], True)
        self.assertEqual(payload["policy"]["id"], TAXONOMY_ID)

    def test_manifest_loader_keeps_previous_v1_runs_usable(self) -> None:
        payload = self.make_manifest().to_dict()
        payload["policy"] = {
            **payload["policy"],
            "id": LEGACY_TAXONOMY_ID,
            "taxonomy_sha256": LEGACY_TAXONOMY_SHA256,
        }
        payload["scan_digest"] = digest_for_manifest_payload(payload)

        loaded = ScanManifest.from_dict(payload)

        self.assertEqual(loaded.policy["id"], LEGACY_TAXONOMY_ID)

    def test_manifest_loader_keeps_previous_contextual_v2_runs_usable(self) -> None:
        payload = self.make_manifest().to_dict()
        payload["policy"] = {
            **payload["policy"],
            "id": CONTEXTUAL_TAXONOMY_ID,
            "taxonomy_sha256": CONTEXTUAL_TAXONOMY_SHA256,
        }
        payload["scan_digest"] = digest_for_manifest_payload(payload)

        loaded = ScanManifest.from_dict(payload)

        self.assertEqual(loaded.policy["id"], CONTEXTUAL_TAXONOMY_ID)
        self.assertEqual(loaded.policy["taxonomy_sha256"], CONTEXTUAL_TAXONOMY_SHA256)

    def test_manifest_loader_rejects_cloud_model_plans(self) -> None:
        payload = self.make_manifest().to_dict()
        payload["model"]["name"] = "qwen3-vl:4b-cloud"
        payload["scan_digest"] = digest_for_manifest_payload(payload)

        with self.assertRaises(ManifestError):
            ScanManifest.from_dict(payload)

    def test_manifest_records_random_selection_strategy(self) -> None:
        manifest = build_scan_manifest(
            run_id=str(uuid.uuid4()), app_name="photos-indexer", app_version="0.1.0", model_name="llava",
            ollama_version="0.12.0", endpoint="http://127.0.0.1:11434",
            selection={"requested": 1, "eligible": 1, "screenshots_excluded": 0, "access": "authorized", "strategy": "random"},
            photos=[self.make_manifest().photos[0]], summary={"ready": 1, "noop": 0, "analysis_failed": 0},
        )

        self.assertEqual(manifest.to_dict()["selection"]["strategy"], "random")

    def test_manifest_records_targeted_selection_strategy(self) -> None:
        manifest = build_scan_manifest(
            run_id=str(uuid.uuid4()), app_name="photos-indexer", app_version="0.1.0", model_name="llava",
            ollama_version="0.12.0", endpoint="http://127.0.0.1:11434",
            selection={"requested": 1, "eligible": 1, "screenshots_excluded": 0, "access": "authorized", "strategy": "targeted"},
            photos=[self.make_manifest().photos[0]], summary={"ready": 1, "noop": 0, "analysis_failed": 0},
        )

        self.assertEqual(manifest.to_dict()["selection"]["strategy"], "targeted")

    def test_manifest_records_whether_captions_were_requested(self) -> None:
        manifest = build_scan_manifest(
            run_id=str(uuid.uuid4()), app_name="photos-indexer", app_version="0.1.0", model_name="llava",
            ollama_version="0.12.0", endpoint="http://127.0.0.1:11434",
            selection={
                "requested": 0,
                "eligible": 0,
                "screenshots_excluded": 0,
                "access": "authorized",
                "captions_requested": True,
            },
            photos=[], summary={"ready": 0, "noop": 0, "analysis_failed": 0},
        )

        payload = manifest.to_dict()
        self.assertTrue(payload["selection"]["captions_requested"])
        self.assertTrue(ScanManifest.from_dict(payload).selection["captions_requested"])

    def test_manifest_rejects_non_boolean_caption_request(self) -> None:
        with self.assertRaises(ManifestError):
            build_scan_manifest(
                run_id=str(uuid.uuid4()), app_name="photos-indexer", app_version="0.1.0", model_name="llava",
                ollama_version="0.12.0", endpoint="http://127.0.0.1:11434",
                selection={
                    "requested": 0,
                    "eligible": 0,
                    "screenshots_excluded": 0,
                    "access": "authorized",
                    "captions_requested": "yes",
                },
                photos=[], summary={"ready": 0, "noop": 0, "analysis_failed": 0},
            )

    def test_manifest_rejects_non_schema_selection_and_summary_objects(self) -> None:
        for selection, summary in (
            ({"requested": 1, "eligible": 1, "screenshots_excluded": 0, "access": "authorized", "caption": "leak"}, {"ready": 1, "noop": 0, "analysis_failed": 0}),
            ({"requested": -1, "eligible": 0, "screenshots_excluded": 0, "access": "authorized"}, {"ready": 0, "noop": 0, "analysis_failed": 0}),
            ({"requested": 0, "eligible": 0, "screenshots_excluded": 0, "access": "authorized"}, {"ready": 0, "noop": 0, "analysis_failed": -1}),
        ):
            with self.assertRaises(ManifestError):
                build_scan_manifest(
                    run_id=str(uuid.uuid4()), app_name="photos-indexer", app_version="0.1.0", model_name="llava",
                    ollama_version="0.12.0", endpoint="http://127.0.0.1:11434", selection=selection,
                    photos=[], summary=summary,
                )

    def test_manifest_preserves_the_configured_confidence_threshold(self) -> None:
        manifest = build_scan_manifest(
            run_id=str(uuid.uuid4()), app_name="photos-indexer", app_version="0.1.0", model_name="llava",
            ollama_version="0.12.0", endpoint="http://127.0.0.1:11434",
            selection={"requested": 0, "eligible": 0, "screenshots_excluded": 0, "access": "authorized"},
            photos=[], summary={"ready": 0, "noop": 0, "analysis_failed": 0}, confidence_threshold=0.75,
        )
        self.assertEqual(manifest.to_dict()["policy"]["confidence_threshold"], 0.75)

    def test_manifest_rejects_duplicate_nonnull_uuid_and_local_identifier(self) -> None:
        first = self.make_manifest().photos[0]
        duplicate_uuid = PhotoRecord.from_dict(first.to_dict())
        duplicate_uuid.photos_local_identifier = "other-local-id"
        duplicate_local = PhotoRecord.from_dict(first.to_dict())
        duplicate_local.uuid = "other-photo-uuid"

        for photos in ([first, duplicate_uuid], [first, duplicate_local]):
            with self.assertRaises(ManifestError):
                build_scan_manifest(
                    run_id=str(uuid.uuid4()), app_name="photos-indexer", app_version="0.1.0", model_name="llava",
                    ollama_version="0.12.0", endpoint="http://127.0.0.1:11434",
                    selection={"requested": 2, "eligible": 2, "screenshots_excluded": 0, "access": "authorized"},
                    photos=photos, summary={"ready": 2, "noop": 0, "analysis_failed": 0}, confidence_threshold=0.60,
                )

    def test_photo_rejects_illegal_apply_and_rollback_state_transitions(self) -> None:
        base = self.make_manifest().photos[0].to_dict()
        invalid_states = [
            {"apply_state": "verified", "applied_keywords": []},
            {"apply_state": "verified", "applied_keywords": ["PLAYA"]},
            {"apply_state": "not_run", "applied_keywords": ["playa"]},
            {"apply_state": "not_run", "rollback_state": "removing"},
            {"uuid": None, "scan_state": "ready"},
            {"uuid": None, "scan_state": "analysis_failed", "proposed_keywords": [], "apply_state": "writing"},
            {"mutation_digest": "not-a-sha256"},
        ]
        for changes in invalid_states:
            payload = {**base, **changes}
            with self.subTest(changes=changes), self.assertRaises(ManifestError):
                PhotoRecord.from_dict(payload)

    def test_photo_rejects_a_caption_on_an_analysis_failed_row(self) -> None:
        base = self.make_manifest().photos[0].to_dict()
        with self.assertRaises(ManifestError):
            PhotoRecord.from_dict({
                **base,
                "scan_state": "analysis_failed",
                "proposed_keywords": [],
                "proposed_caption": "Una playa visible.",
                "caption_state": "proposed",
            })

    def test_photo_rejects_analysis_failed_without_a_scan_error(self) -> None:
        """A failed scan row must retain the stable cause shown by status/support."""
        base = self.make_manifest().photos[0].to_dict()

        with self.assertRaises(ManifestError):
            PhotoRecord.from_dict({
                **base,
                "scan_state": "analysis_failed",
                "proposed_keywords": [],
                "errors": [],
            })

    def test_manifest_rejects_scan_status_that_disagrees_with_row_errors(self) -> None:
        """The top-level scan state must truthfully describe persisted row errors."""
        clean = self.make_manifest().photos[0]
        with self.assertRaises(ManifestError):
            build_scan_manifest(
                run_id=str(uuid.uuid4()), app_name="photos-indexer", app_version="0.1.0",
                model_name="llava", ollama_version="0.12.0", endpoint="http://127.0.0.1:11434",
                selection={"requested": 1, "eligible": 1, "screenshots_excluded": 0, "access": "authorized"},
                photos=[clean], summary={"ready": 1, "noop": 0, "analysis_failed": 0},
                scan_status="ready_with_errors",
            )

        errored = self.make_manifest().photos[0]
        errored.errors = [{"stage": "cleanup", "code": "EXPORT_DELETE_FAILED"}]
        with self.assertRaises(ManifestError):
            build_scan_manifest(
                run_id=str(uuid.uuid4()), app_name="photos-indexer", app_version="0.1.0",
                model_name="llava", ollama_version="0.12.0", endpoint="http://127.0.0.1:11434",
                selection={"requested": 1, "eligible": 1, "screenshots_excluded": 0, "access": "authorized"},
                photos=[errored], summary={"ready": 1, "noop": 0, "analysis_failed": 0},
                scan_status="ready",
            )

    def test_photo_requires_caption_state_and_matching_applied_caption(self) -> None:
        base = self.make_manifest().photos[0].to_dict()
        with self.subTest("proposal without state"):
            with self.assertRaises(ManifestError):
                PhotoRecord.from_dict({**base, "proposed_caption": "Una playa visible."})
        with self.subTest("applied caption without a verified apply"):
            with self.assertRaises(ManifestError):
                PhotoRecord.from_dict({
                    **base,
                    "proposed_caption": "Una playa visible.",
                    "applied_caption": "Una playa visible.",
                    "caption_state": "verified",
                })
        with self.subTest("applied value differs from reviewed proposal"):
            with self.assertRaises(ManifestError):
                PhotoRecord.from_dict({
                    **base,
                    "proposed_caption": "Una playa visible.",
                    "applied_caption": "Un perro visible.",
                    "caption_state": "verified",
                    "apply_state": "verified",
                    "applied_keywords": ["playa"],
                })
        with self.subTest("removed caption without rollback"):
            with self.assertRaises(ManifestError):
                PhotoRecord.from_dict({
                    **base,
                    "proposed_caption": "Una playa visible.",
                    "applied_caption": "Una playa visible.",
                    "caption_state": "removed",
                    "apply_state": "verified",
                })
        with self.subTest("removed caption without audited applied caption"):
            with self.assertRaises(ManifestError):
                PhotoRecord.from_dict({
                    **base,
                    "proposed_caption": "Una playa visible.",
                    "caption_state": "removed",
                    "apply_state": "verified",
                    "applied_keywords": ["playa"],
                    "rollback_state": "failed",
                })
        with self.subTest("preserved caption without reviewed proposal"):
            with self.assertRaises(ManifestError):
                PhotoRecord.from_dict({
                    **base,
                    "caption_state": "preserved",
                    "apply_state": "verified",
                    "applied_keywords": ["playa"],
                })
        with self.subTest("preserved caption cannot claim an applied value"):
            with self.assertRaises(ManifestError):
                PhotoRecord.from_dict({
                    **base,
                    "proposed_caption": "Una playa visible.",
                    "applied_caption": "Una playa visible.",
                    "caption_state": "preserved",
                    "apply_state": "verified",
                })
        with self.subTest("verified apply cannot leave a caption proposal pending"):
            with self.assertRaises(ManifestError):
                PhotoRecord.from_dict({
                    **base,
                    "proposed_caption": "Una playa visible.",
                    "caption_state": "proposed",
                    "apply_state": "verified",
                    "applied_keywords": ["playa"],
                })
        with self.subTest("rollback uncertainty may preserve caption uncertainty"):
            PhotoRecord.from_dict({
                **base,
                "proposed_caption": "Una playa visible.",
                "applied_caption": "Una playa visible.",
                "caption_state": "uncertain",
                "apply_state": "verified",
                "applied_keywords": ["playa"],
                "rollback_state": "uncertain",
                "rollback_digest": "0" * 64,
            })

    def test_photo_validates_rollback_audit_as_exact_applied_subset(self) -> None:
        base = self.make_manifest().photos[0].to_dict()
        base.update({"apply_state": "verified", "applied_keywords": ["playa"], "rollback_state": "failed"})

        valid = PhotoRecord.from_dict({**base, "rolled_back_keywords": ["playa"]})
        self.assertEqual(valid.rolled_back_keywords, ["playa"])
        for invalid in (["PLAYA"], ["playa", "playa"]):
            with self.subTest(invalid=invalid), self.assertRaises(ManifestError):
                PhotoRecord.from_dict({**base, "rolled_back_keywords": invalid})

    def test_error_catalog_rejects_unknown_raw_control_pathlike_and_excessive_errors(self) -> None:
        base = self.make_manifest().photos[0].to_dict()
        invalid_lists = [
            [{"stage": "unknown", "code": "READ_FAILED"}],
            [{"stage": "analysis", "code": "UNKNOWN"}],
            [{"stage": "analysis", "code": "/private/tmp/raw-error"}],
            [{"stage": "analysis", "code": "RAW\nERROR"}],
            [{"stage": "analysis", "code": "ANALYSIS_FAILED"}] * 17,
        ]
        for errors in invalid_lists:
            with self.subTest(errors=errors[:1]), self.assertRaises(ManifestError):
                PhotoRecord.from_dict({**base, "errors": errors})

    def test_manifest_validates_uuid_access_selection_summary_and_success_row_count_on_load(self) -> None:
        payload = self.make_manifest().to_dict()
        invalid_payloads: list[dict[str, object]] = []
        for mutation in ("uuid", "access", "eligible", "summary", "row_count"):
            candidate = json.loads(json.dumps(payload))
            if mutation == "uuid":
                candidate["photos"][0]["uuid"] = "not-a-uuid"  # type: ignore[index]
            elif mutation == "access":
                candidate["selection"]["access"] = "selected"  # type: ignore[index]
            elif mutation == "eligible":
                candidate["selection"]["eligible"] = 2  # type: ignore[index]
            elif mutation == "summary":
                candidate["summary"] = {"ready": 0, "noop": 1, "analysis_failed": 0}
            else:
                candidate["selection"]["eligible"] = 0  # type: ignore[index]
            candidate["scan_digest"] = digest_for_manifest_payload(candidate)
            invalid_payloads.append(candidate)
        for candidate in invalid_payloads:
            with self.assertRaises(ManifestError):
                ScanManifest.from_dict(candidate)

    def test_run_errors_are_immutable_auditable_and_closed(self) -> None:
        manifest = self.make_manifest()
        manifest.scan_status = "failed"
        manifest.run_errors = [{"stage": "workspace", "code": "WORKSPACE_FAILED"}]
        manifest.summary = {"ready": 1, "noop": 0, "analysis_failed": 0}
        original_without_error = manifest.compute_scan_digest()
        manifest.scan_digest = original_without_error
        manifest.run_errors = []
        self.assertNotEqual(manifest.compute_scan_digest(), original_without_error)
        manifest.run_errors = [{"stage": "workspace", "code": "WORKSPACE_FAILED"}]
        manifest.scan_digest = manifest.compute_scan_digest()
        loaded = ScanManifest.from_dict(manifest.to_dict())
        self.assertEqual(loaded.run_errors, [{"stage": "workspace", "code": "WORKSPACE_FAILED"}])
        with self.assertRaises(ManifestError):
            ScanManifest.from_dict({**loaded.to_dict(), "run_errors": [{"stage": "workspace", "code": "raw/path"}]})

    def test_photo_record_rejects_invalid_identity_date_confidence_and_state(self) -> None:
        valid = {
            "uuid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "photos_local_identifier": "local-1",
            "title": "",
            "date": datetime(2026, 8, 24, 10, 30),
            "existing_keywords": [],
            "proposed_keywords": ["playa"],
            "contains_people": False,
            "contains_text": False,
            "confidence": 0.9,
        }
        invalid = (
            {"uuid": ""},
            {"title": 1},
            {"date": "2026-08-24"},
            {"contains_people": 1},
            {"confidence": "high"},
            {"confidence": True},
            {"confidence": 1.1},
            {"scan_state": "unknown"},
            {"errors": "none"},
            {"errors": [{"stage": 1, "code": "BAD"}]},
        )

        for override in invalid:
            with self.subTest(override=override), self.assertRaises(ManifestError):
                PhotoRecord(**{**valid, **override})  # type: ignore[arg-type]

    def test_manifest_rejects_invalid_top_level_policy_and_collections(self) -> None:
        base = self.make_manifest()
        valid = {
            "run_id": base.run_id,
            "created_at": base.created_at,
            "app": base.app,
            "model": base.model,
            "selection": base.selection,
            "photos": base.photos,
            "summary": base.summary,
            "policy": base.policy,
            "scan_status": base.scan_status,
        }
        invalid = (
            {"run_id": "not-a-uuid"},
            {"created_at": datetime(2026, 8, 24)},
            {"scan_status": "unknown"},
            {"app": {"name": "missing-version"}},
            {"model": {"name": "missing-fields"}},
            {"selection": {**base.selection, "access": 1}},
            {"summary": {**base.summary, "ready": True}},
            {"policy": {"id": "missing-fields"}},
            {"policy": {**base.policy, "id": "wrong-policy"}},
            {"policy": {**base.policy, "confidence_threshold": 2}},
            {"photos": ["not-a-photo"]},
        )

        for override in invalid:
            with self.subTest(override=override), self.assertRaises(ManifestError):
                ScanManifest(**{**valid, **override})  # type: ignore[arg-type]

    def test_manifest_deserialization_rejects_invalid_schema_timestamp_and_collections(self) -> None:
        payload = self.make_manifest().to_dict()
        invalid = (
            {"schema_version": 2},
            {"dry_run": False},
            {"created_at": 1},
            {"created_at": "not-a-date"},
            {"photos": "not-a-list"},
        )

        for override in invalid:
            with self.subTest(override=override), self.assertRaises(ManifestError):
                ScanManifest.from_dict({**payload, **override})

    def test_load_rejects_missing_non_utf8_and_invalid_json_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir(mode=0o700)
            with self.assertRaises(ManifestError):
                load_manifest(run_dir)

            path = run_dir / "manifest.json"
            for content in (b"\xff", b"{"):
                path.write_bytes(content)
                os.chmod(path, 0o600)
                with self.assertRaises(ManifestError):
                    load_manifest(run_dir)

    def test_scan_digest_ignores_apply_and_rollback_fields_but_detects_scan_changes(self) -> None:
        manifest = self.make_manifest()
        original = manifest.scan_digest
        manifest.photos[0].apply_state = "verified"
        manifest.photos[0].applied_keywords = ["playa"]
        manifest.photos[0].rollback_state = "not_run"
        self.assertEqual(manifest.compute_scan_digest(), original)
        manifest.photos[0].proposed_keywords = ["gato"]
        self.assertNotEqual(manifest.compute_scan_digest(), original)

    def test_deserialization_rejects_an_empty_scan_digest(self) -> None:
        payload = self.make_manifest().to_dict()
        payload["scan_digest"] = ""
        with self.assertRaises(ManifestError):
            ScanManifest.from_dict(payload)

    def test_reviewed_manifest_requires_source_scan_digest(self) -> None:
        base = self.make_manifest()

        with self.assertRaises(ManifestError):
            ScanManifest(
                run_id=base.run_id,
                created_at=base.created_at,
                app=base.app,
                model={
                    "policy": "single",
                    "fast_name": "qwen3-vl:4b",
                    "detailed_name": "qwen3-vl:4b",
                    "ollama_version": "0.12.7",
                    "endpoint": "http://127.0.0.1:11434",
                },
                selection=base.selection,
                photos=base.photos,
                summary=base.summary,
                policy=base.policy,
                scan_status=base.scan_status,
                schema_version=3,
                reviewed_from_run_id=str(uuid.uuid4()),
                source_scan_digest=None,
            )

    def test_write_and_load_manifest_uses_private_modes_and_detects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            manifest = self.make_manifest()
            path = write_manifest(run_dir, manifest)

            self.assertEqual(stat.S_IMODE(run_dir.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            loaded = load_manifest(run_dir)
            self.assertEqual(loaded.run_id, manifest.run_id)

            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["photos"][0]["proposed_keywords"] = ["gato"]
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ManifestError):
                load_manifest(run_dir)

    def test_load_rejects_a_symlink_and_hardlinked_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = self.make_manifest()
            safe_dir = root / "safe"
            write_manifest(safe_dir, manifest)

            symlink_dir = root / "link"
            symlink_dir.mkdir()
            (symlink_dir / "manifest.json").symlink_to(safe_dir / "manifest.json")
            with self.assertRaises(ManifestError):
                load_manifest(symlink_dir)

            hardlink_dir = root / "hard"
            hardlink_dir.mkdir()
            os.link(safe_dir / "manifest.json", hardlink_dir / "manifest.json")
            with self.assertRaises(ManifestError):
                load_manifest(hardlink_dir)

    def test_load_rejects_a_symlinked_ancestor_of_the_run_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real_root = root / "real-runs"
            run_dir = real_root / "run"
            write_manifest(run_dir, self.make_manifest())

            alias_root = root / "alias-runs"
            alias_root.symlink_to(real_root, target_is_directory=True)

            with self.assertRaises(ManifestError):
                load_manifest(alias_root / "run")

    def test_write_rejects_a_symlinked_ancestor_before_creating_the_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside_root = root / "outside-runs"
            outside_root.mkdir(mode=0o700)
            alias_root = root / "alias-runs"
            alias_root.symlink_to(outside_root, target_is_directory=True)

            with self.assertRaises(ManifestError):
                write_manifest(alias_root / "run", self.make_manifest())

            self.assertFalse((outside_root / "run").exists())

    def test_load_rejects_run_directory_replaced_after_directory_check(self) -> None:
        """A concurrent run-directory swap must not redirect manifest reads."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            safe_dir = root / "safe"
            outside_dir = root / "outside"
            write_manifest(safe_dir, self.make_manifest())
            write_manifest(outside_dir, self.make_manifest())
            original_read = manifest_module._read_safe_manifest

            def replace_directory_then_read(path: Path, *, expected_directory: os.stat_result) -> bytes:
                moved_dir = root / "safe-original"
                safe_dir.rename(moved_dir)
                safe_dir.symlink_to(outside_dir, target_is_directory=True)
                return original_read(path, expected_directory=expected_directory)

            with patch.object(manifest_module, "_read_safe_manifest", side_effect=replace_directory_then_read):
                with self.assertRaises(ManifestError):
                    load_manifest(safe_dir)

    def test_load_rejects_relaxed_permissions_nonregular_files_and_oversized_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            path = write_manifest(run_dir, self.make_manifest())
            os.chmod(path, 0o644)
            with self.assertRaises(ManifestError):
                load_manifest(run_dir)
            os.chmod(path, 0o600)
            os.chmod(run_dir, 0o755)
            with self.assertRaises(ManifestError):
                load_manifest(run_dir)
            os.chmod(run_dir, 0o700)
            path.unlink()
            os.mkfifo(path)
            with self.assertRaises(ManifestError):
                load_manifest(run_dir)
            path.unlink()
            path.write_bytes(b"x" * (4 * 1024 * 1024 + 1))
            os.chmod(path, 0o600)
            with self.assertRaises(ManifestError):
                load_manifest(run_dir)

    def test_writer_rejects_a_manifest_its_reader_would_reject_for_size(self) -> None:
        """A successful write must always produce a reloadable manifest."""
        manifest = self.make_manifest()
        manifest.photos[0].title = "x" * (MAX_MANIFEST_BYTES + 1)
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "oversized-run"

            with self.assertRaises(ManifestError):
                write_manifest(run_dir, manifest)

            self.assertFalse((run_dir / "manifest.json").exists())

    def test_writer_rejects_an_in_memory_manifest_its_reader_would_reject(self) -> None:
        """Persistence must validate the current object, not only mutable rows."""
        manifest = self.make_manifest()
        manifest.model["endpoint"] = "https://example.invalid"

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "invalid-run"

            with self.assertRaises(ManifestError):
                write_manifest(run_dir, manifest)

            self.assertFalse((run_dir / "manifest.json").exists())

    def test_preview_has_one_private_row_per_photo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest = self.make_manifest()
            manifest.photos[0].title = "Título visible que debe truncarse SHOULD_NOT_PRINT"
            full_uuid = manifest.photos[0].uuid
            path = write_preview_csv(Path(tmp), manifest)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            with path.open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                rows = list(reader)
            self.assertEqual(reader.fieldnames, [
                "uuid", "title", "date", "existing_keywords", "proposed_keywords", "caption_status", "contains_people", "contains_text", "confidence", "status", "error_codes",
            ])
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["uuid"], "00000000")
            self.assertNotEqual(rows[0]["uuid"], full_uuid)
            self.assertEqual(rows[0]["title"], "Título visible que debe…")
            self.assertEqual(manifest.photos[0].uuid, full_uuid)
            self.assertIn("SHOULD_NOT_PRINT", manifest.photos[0].title)
            self.assertEqual(rows[0]["existing_keywords"], '["PERRO"]')
            self.assertEqual(rows[0]["proposed_keywords"], '["playa"]')
            self.assertEqual(rows[0]["caption_status"], "—")
            self.assertEqual(rows[0]["contains_people"], "false")
            self.assertEqual(rows[0]["status"], "ready")
            self.assertEqual(rows[0]["error_codes"], "[]")

    def test_preview_marks_caption_only_proposals_without_caption_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manifest = self.make_manifest()
            photo = manifest.photos[0]
            photo.scan_state = "noop"
            photo.proposed_keywords = []
            photo.proposed_caption = "Texto visible que no debe exportarse"
            photo.caption_state = "proposed"
            path = write_preview_csv(Path(tmp), manifest)

            with path.open(newline="", encoding="utf-8") as handle:
                row = next(csv.DictReader(handle))

            self.assertEqual(row["caption_status"], "pendiente")
            self.assertNotIn("Texto visible que no debe exportarse", row.values())

    def test_preview_neutralizes_formula_titles_and_control_characters_without_mutating_manifest(self) -> None:
        manifest = self.make_manifest()
        manifest.photos[0].title = "=HYPERLINK(\"https://example.invalid\")\x1b[31m"
        with tempfile.TemporaryDirectory() as tmp:
            path = write_preview_csv(Path(tmp), manifest)
            with path.open(encoding="utf-8", newline="") as handle:
                row = next(csv.DictReader(handle))
            self.assertTrue(row["title"].startswith("'=HYPERLINK"))
            self.assertNotIn("\x1b", row["title"])
            self.assertIn("HYPERLINK", manifest.photos[0].title)

    def test_preview_replaces_unpaired_surrogates_without_mutating_manifest(self) -> None:
        manifest = self.make_manifest()
        manifest.photos[0].title = "Título\ud800visible"
        with tempfile.TemporaryDirectory() as tmp:
            path = write_preview_csv(Path(tmp), manifest)
            with path.open(encoding="utf-8", newline="") as handle:
                row = next(csv.DictReader(handle))
        self.assertEqual(row["title"], "Título�visible")
        self.assertIn("\ud800", manifest.photos[0].title)

    def test_preview_replaces_unpaired_surrogates_in_keywords_without_mutating_manifest(self) -> None:
        manifest = self.make_manifest()
        manifest.photos[0].existing_keywords = ["Viaje\ud800"]
        with tempfile.TemporaryDirectory() as tmp:
            path = write_preview_csv(Path(tmp), manifest)
            with path.open(encoding="utf-8", newline="") as handle:
                row = next(csv.DictReader(handle))
        self.assertEqual(row["existing_keywords"], '["Viaje�"]')
        self.assertIn("\ud800", manifest.photos[0].existing_keywords[0])

    def test_manifest_round_trips_unpaired_surrogates_without_encoding_failure(self) -> None:
        manifest = self.make_manifest()
        manifest.photos[0].title = "Título\ud800visible"
        with tempfile.TemporaryDirectory() as tmp:
            path = write_manifest(Path(tmp), manifest)
            loaded = load_manifest(path.parent)

        self.assertEqual(loaded.photos[0].title, manifest.photos[0].title)

    def test_mutation_receipts_encode_unpaired_surrogates_without_failure(self) -> None:
        manifest = self.make_manifest()
        photo = manifest.photos[0]
        photo.photos_local_identifier = "local-id-1\ud800"
        photo.apply_state = "verified"
        photo.applied_keywords = ["playa"]

        mutation_digest = compute_mutation_digest(manifest, photo)
        photo.mutation_digest = mutation_digest
        photo.rollback_state = "verified_removed"
        photo.rolled_back_keywords = ["playa"]

        rollback_digest = compute_rollback_digest(manifest, photo)

        self.assertEqual(len(mutation_digest), 64)
        self.assertEqual(len(rollback_digest), 64)


if __name__ == "__main__":
    unittest.main()
