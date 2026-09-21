from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

from photos_indexer.manifest import (
    ManifestError,
    PhotoRecord,
    ScanManifest,
    build_scan_manifest,
    compute_mutation_digest,
    load_manifest,
    write_manifest,
)
from photos_indexer.taxonomy import TAXONOMY_ID, TAXONOMY_SHA256
from photos_indexer.workflows import run_status


class ManifestMigrationHardeningTests(unittest.TestCase):
    def test_direct_photo_record_rejects_non_list_keyword_collections(self) -> None:
        valid = {
            "uuid": "00000000-0000-4000-8000-000000000001",
            "photos_local_identifier": "local-1",
            "title": "Prueba",
            "date": datetime(2026, 8, 25, 12, 0),
            "existing_keywords": [],
            "proposed_keywords": ["perro"],
            "contains_people": False,
            "contains_text": False,
            "confidence": 0.9,
        }
        for field in ("existing_keywords", "proposed_keywords", "applied_keywords", "rolled_back_keywords"):
            with self.subTest(field=field), self.assertRaises(ManifestError):
                PhotoRecord(**{**valid, field: None})  # type: ignore[arg-type]

    def test_photo_rejects_mutation_errors_without_a_started_mutation(self) -> None:
        """A stale stage error must not make a fresh row look partially mutated."""
        valid = {
            "uuid": "00000000-0000-4000-8000-000000000001",
            "photos_local_identifier": "local-1",
            "title": "Prueba",
            "date": datetime(2026, 8, 25, 12, 0),
            "existing_keywords": [],
            "proposed_keywords": ["perro"],
            "contains_people": False,
            "contains_text": False,
            "confidence": 0.9,
        }
        for error in (
            {"stage": "apply", "code": "APPLY_FAILED"},
            {"stage": "rollback", "code": "ROLLBACK_FAILED"},
        ):
            with self.subTest(error=error), self.assertRaises(ManifestError):
                PhotoRecord(**{**valid, "errors": [error]})

    def test_direct_scan_manifest_rejects_non_mapping_metadata(self) -> None:
        base = {
            "run_id": str(uuid.uuid4()),
            "created_at": datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc),
            "app": {"name": "photos-local-keyword-indexer", "version": "0.1.0"},
            "model": {"name": "qwen3-vl:4b", "ollama_version": "0.12.7", "endpoint": "http://127.0.0.1:11434"},
            "selection": {"requested": 0, "eligible": 0, "screenshots_excluded": 0, "access": "authorized"},
            "photos": [],
            "summary": {"ready": 0, "noop": 0, "analysis_failed": 0},
        }
        for field in ("app", "model", "selection", "policy"):
            for value in ([], None, 1):
                with self.subTest(field=field, value=value), self.assertRaises(ManifestError):
                    ScanManifest(**{**base, field: value})  # type: ignore[arg-type]

    def test_scan_manifest_rejects_a_requested_selection_over_the_operational_limit(self) -> None:
        base = {
            "run_id": str(uuid.uuid4()),
            "created_at": datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc),
            "app": {"name": "photos-local-keyword-indexer", "version": "0.1.0"},
            "model": {"name": "qwen3-vl:4b", "ollama_version": "0.12.7", "endpoint": "http://127.0.0.1:11434"},
            "selection": {"requested": 501, "eligible": 0, "screenshots_excluded": 0, "access": "authorized"},
            "photos": [],
            "summary": {"ready": 0, "noop": 0, "analysis_failed": 0},
        }
        with self.assertRaises(ManifestError):
            ScanManifest(**base)  # type: ignore[arg-type]

    def test_direct_scan_manifest_rejects_a_malformed_creation_date(self) -> None:
        base = {
            "run_id": str(uuid.uuid4()),
            "created_at": datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc),
            "app": {"name": "photos-local-keyword-indexer", "version": "0.1.0"},
            "model": {"name": "qwen3-vl:4b", "ollama_version": "0.12.7", "endpoint": "http://127.0.0.1:11434"},
            "selection": {"requested": 0, "eligible": 0, "screenshots_excluded": 0, "access": "authorized"},
            "photos": [],
            "summary": {"ready": 0, "noop": 0, "analysis_failed": 0},
        }
        for value in (None, [], 1, "not-a-date"):
            with self.subTest(value=value), self.assertRaises(ManifestError):
                ScanManifest(**{**base, "created_at": value})  # type: ignore[arg-type]

    def test_load_manifest_maps_malformed_policy_id_to_manifest_error(self) -> None:
        photo = PhotoRecord(
            uuid="00000000-0000-4000-8000-000000000001",
            photos_local_identifier="local-1",
            title="Prueba",
            date=datetime(2026, 8, 25, 12, 0),
            existing_keywords=[],
            proposed_keywords=["perro"],
            contains_people=False,
            contains_text=False,
            confidence=0.9,
        )
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
            created_at=datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc),
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            manifest_path = write_manifest(Path(temporary_directory) / "run", manifest)
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            payload["policy"]["id"] = []
            manifest_path.write_text(json.dumps(payload), encoding="utf-8")
            manifest_path.chmod(0o600)

            with self.assertRaises(ManifestError):
                load_manifest(manifest_path.parent)

    def test_scan_manifest_rejects_a_non_integer_max_keywords_policy(self) -> None:
        """JSON numeric equality must not weaken the versioned policy schema."""
        photo = PhotoRecord(
            uuid="00000000-0000-4000-8000-000000000001",
            photos_local_identifier="local-1",
            title="Prueba",
            date=datetime(2026, 8, 25, 12, 0),
            existing_keywords=[],
            proposed_keywords=["perro"],
            contains_people=False,
            contains_text=False,
            confidence=0.9,
        )
        with self.assertRaises(ManifestError):
            ScanManifest(
                run_id=str(uuid.uuid4()),
                created_at=datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc),
                app={"name": "photos-local-keyword-indexer", "version": "0.1.0"},
                model={
                    "name": "qwen3-vl:4b",
                    "ollama_version": "0.12.7",
                    "endpoint": "http://127.0.0.1:11434",
                },
                selection={"requested": 1, "eligible": 1, "screenshots_excluded": 0, "access": "authorized"},
                photos=[photo],
                summary={"ready": 1, "noop": 0, "analysis_failed": 0},
                policy={
                    "id": TAXONOMY_ID,
                    "taxonomy_sha256": TAXONOMY_SHA256,
                    "max_keywords": 8.0,
                    "confidence_threshold": 0.6,
                },
            )

    def test_scan_manifest_rejects_a_non_loopback_model_endpoint(self) -> None:
        """A durable run must not claim that image analysis used a remote host."""
        with self.assertRaises(ManifestError):
            ScanManifest(
                run_id=str(uuid.uuid4()),
                created_at=datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc),
                app={"name": "photos-local-keyword-indexer", "version": "0.1.0"},
                model={
                    "name": "qwen3-vl:4b",
                    "ollama_version": "0.12.7",
                    "endpoint": "https://models.example.invalid",
                },
                selection={"requested": 0, "eligible": 0, "screenshots_excluded": 0, "access": "authorized"},
                photos=[],
                summary={"ready": 0, "noop": 0, "analysis_failed": 0},
            )

    def test_scan_manifest_rejects_a_model_name_outside_ollama_syntax(self) -> None:
        """Durable model metadata must use the same syntax as local preflight."""
        with self.assertRaises(ManifestError):
            ScanManifest(
                run_id=str(uuid.uuid4()),
                created_at=datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc),
                app={"name": "photos-local-keyword-indexer", "version": "0.1.0"},
                model={
                    "name": "../../private",
                    "ollama_version": "0.12.7",
                    "endpoint": "http://127.0.0.1:11434",
                },
                selection={"requested": 0, "eligible": 0, "screenshots_excluded": 0, "access": "authorized"},
                photos=[],
                summary={"ready": 0, "noop": 0, "analysis_failed": 0},
            )

    def test_scan_manifest_rejects_a_malformed_ollama_version(self) -> None:
        """A loopback response must not persist arbitrary text as a version."""
        with self.assertRaises(ManifestError):
            ScanManifest(
                run_id=str(uuid.uuid4()),
                created_at=datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc),
                app={"name": "photos-local-keyword-indexer", "version": "0.1.0"},
                model={
                    "name": "qwen3-vl:4b",
                    "ollama_version": "0.12.7/private",
                    "endpoint": "http://127.0.0.1:11434",
                },
                selection={"requested": 0, "eligible": 0, "screenshots_excluded": 0, "access": "authorized"},
                photos=[],
                summary={"ready": 0, "noop": 0, "analysis_failed": 0},
            )

    def test_load_manifest_maps_malformed_photo_state_to_manifest_error(self) -> None:
        photo = PhotoRecord(
            uuid="00000000-0000-4000-8000-000000000001",
            photos_local_identifier="local-1",
            title="Prueba",
            date=datetime(2026, 8, 25, 12, 0),
            existing_keywords=[],
            proposed_keywords=["perro"],
            contains_people=False,
            contains_text=False,
            confidence=0.9,
        )
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
            created_at=datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc),
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            manifest_path = write_manifest(Path(temporary_directory) / "run", manifest)
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            payload["photos"][0]["scan_state"] = []
            manifest_path.write_text(json.dumps(payload), encoding="utf-8")
            manifest_path.chmod(0o600)

            with self.assertRaises(ManifestError):
                load_manifest(manifest_path.parent)

    def test_load_manifest_maps_malformed_error_type_to_manifest_error(self) -> None:
        photo = PhotoRecord(
            uuid="00000000-0000-4000-8000-000000000001",
            photos_local_identifier="local-1",
            title="Prueba",
            date=datetime(2026, 8, 25, 12, 0),
            existing_keywords=[],
            proposed_keywords=["perro"],
            contains_people=False,
            contains_text=False,
            confidence=0.9,
        )
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
            created_at=datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc),
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            manifest_path = write_manifest(Path(temporary_directory) / "run", manifest)
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            payload["photos"][0]["errors"] = [{"stage": [], "code": "READ_FAILED"}]
            manifest_path.write_text(json.dumps(payload), encoding="utf-8")
            manifest_path.chmod(0o600)

            with self.assertRaises(ManifestError):
                load_manifest(manifest_path.parent)

    def test_load_manifest_maps_malformed_enum_type_to_manifest_error(self) -> None:
        photo = PhotoRecord(
            uuid="00000000-0000-4000-8000-000000000001",
            photos_local_identifier="local-1",
            title="Prueba",
            date=datetime(2026, 8, 25, 12, 0),
            existing_keywords=[],
            proposed_keywords=["perro"],
            contains_people=False,
            contains_text=False,
            confidence=0.9,
        )
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
            created_at=datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc),
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            manifest_path = write_manifest(Path(temporary_directory) / "run", manifest)
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            payload["scan_status"] = []
            manifest_path.write_text(json.dumps(payload), encoding="utf-8")
            manifest_path.chmod(0o600)

            with self.assertRaises(ManifestError):
                load_manifest(manifest_path.parent)

    def test_load_manifest_maps_deeply_nested_json_to_manifest_error(self) -> None:
        """A deeply nested payload must not leak RecursionError to callers."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            run_dir = Path(temporary_directory) / "run"
            run_dir.mkdir(mode=0o700)
            manifest_path = run_dir / "manifest.json"
            manifest_path.write_bytes(b"[" * 2000 + b"]" * 2000)
            manifest_path.chmod(0o600)

            with self.assertRaises(ManifestError):
                load_manifest(run_dir)

    def test_load_manifest_maps_integer_conversion_limit_to_manifest_error(self) -> None:
        """A bounded malformed JSON integer must not leak ValueError."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            run_dir = Path(temporary_directory) / "run"
            run_dir.mkdir(mode=0o700)
            manifest_path = run_dir / "manifest.json"
            manifest_path.write_bytes(b'{"value":' + b"9" * 5000 + b"}")
            manifest_path.chmod(0o600)

            with self.assertRaises(ManifestError):
                load_manifest(run_dir)

    def test_load_manifest_rejects_duplicate_json_keys(self) -> None:
        """Ambiguous JSON must not let different readers see different state."""
        manifest = build_scan_manifest(
            run_id=str(uuid.uuid4()),
            app_name="photos-local-keyword-indexer",
            app_version="0.1.0",
            model_name="qwen3-vl:4b",
            ollama_version="0.12.7",
            endpoint="http://127.0.0.1:11434",
            selection={"requested": 0, "eligible": 0, "screenshots_excluded": 0, "access": "authorized"},
            photos=[],
            summary={"ready": 0, "noop": 0, "analysis_failed": 0},
            created_at=datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc),
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            manifest_path = write_manifest(Path(temporary_directory) / "run", manifest)
            payload = manifest_path.read_text(encoding="utf-8")
            payload = payload.replace(
                '"scan_status": "ready"',
                '"scan_status": "failed",\n  "scan_status": "ready"',
                1,
            )
            manifest_path.write_text(payload, encoding="utf-8")
            manifest_path.chmod(0o600)

            with self.assertRaises(ManifestError):
                load_manifest(manifest_path.parent)

    def test_load_rejects_missing_or_invalid_run_directory_with_manifest_error(self) -> None:
        """The public loader must not leak filesystem/type exceptions."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            missing = Path(temporary_directory) / "missing"
            for value in (missing, None, 123):
                with self.subTest(value=value), self.assertRaises(ManifestError):
                    load_manifest(value)  # type: ignore[arg-type]

    def test_status_blocks_mutated_schema2_scan_without_review_provenance(self) -> None:
        photo = PhotoRecord(
            uuid="00000000-0000-4000-8000-000000000001",
            photos_local_identifier="local-1",
            title="Prueba",
            date=datetime(2026, 8, 25, 12, 0),
            existing_keywords=[],
            proposed_keywords=["perro"],
            contains_people=False,
            contains_text=False,
            confidence=0.9,
            model_used="qwen3-vl:4b",
            model_reason="single_policy",
        )
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
            model_policy="single",
            fast_model="qwen3-vl:4b",
            detailed_model="qwen3-vl:4b",
            ollama_versions={"qwen3-vl:4b": "0.12.7"},
            created_at=datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc),
        )
        photo.apply_state = "verified"
        photo.applied_keywords = ["perro"]
        photo.mutation_digest = compute_mutation_digest(manifest, photo)

        with tempfile.TemporaryDirectory() as temporary_directory:
            manifest_path = write_manifest(Path(temporary_directory) / "run", manifest)

            result = run_status(manifest_path)

        self.assertEqual(result.exit_code, 2)
        self.assertEqual(result.error_codes, ("MANIFEST_NOT_REVIEWED",))
        self.assertEqual(result.status_summary["apply_status"], "blocked")
        self.assertEqual(result.status_summary["rollback_status"], "blocked")
        self.assertEqual(result.next_action, "rescan_after_provenance")

    def test_manifest_schema_version_must_be_a_strict_integer(self) -> None:
        photo = PhotoRecord(
            uuid="00000000-0000-4000-8000-000000000001",
            photos_local_identifier="local-1",
            title="Prueba",
            date=datetime(2026, 8, 25, 12, 0),
            existing_keywords=[],
            proposed_keywords=["perro"],
            contains_people=False,
            contains_text=False,
            confidence=0.9,
        )
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
            created_at=datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc),
        )
        for malformed_version in (True, 1.0):
            with self.subTest(schema_version=malformed_version):
                manifest.schema_version = malformed_version  # type: ignore[assignment]
                payload = manifest.to_dict()
                payload["scan_digest"] = manifest.compute_scan_digest()
                with self.assertRaises(ManifestError):
                    ScanManifest.from_dict(payload)
