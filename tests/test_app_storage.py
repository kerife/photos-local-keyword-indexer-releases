from __future__ import annotations

import json
import stat
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from photos_indexer.app_storage import AppStorage, StorageError
from photos_indexer.manifest import PhotoRecord, build_scan_manifest, compute_mutation_digest, load_manifest, write_manifest
from photos_indexer.service import review_manifest


def _manifest() -> object:
    photo = PhotoRecord(
        uuid="00000000-0000-4000-8000-000000000001",
        photos_local_identifier="test-local-id",
        title="Prueba",
        date=datetime(2026, 8, 25, 12, 0),
        existing_keywords=[],
        proposed_keywords=["perro"],
        contains_people=False,
        contains_text=False,
        confidence=0.9,
        scan_state="ready",
    )
    return build_scan_manifest(
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


class AppStorageTests(unittest.TestCase):
    def test_for_home_rejects_a_non_path_value_with_a_storage_error(self) -> None:
        for home in (123, [], {}):
            with self.subTest(home=home), self.assertRaises(StorageError):
                AppStorage.for_home(home)  # type: ignore[arg-type]

    def test_import_rejects_a_non_path_source_with_a_storage_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            storage = AppStorage.for_home(Path(temporary_directory) / "home")

            with self.assertRaises(StorageError):
                storage.import_manifest(123)  # type: ignore[arg-type]

    def test_import_rejects_numeric_overflow_as_a_storage_error(self) -> None:
        """Malformed numeric JSON must not leak an OverflowError to the UI."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source_manifest = write_manifest(root / "external" / "source", _manifest())  # type: ignore[arg-type]
            payload = json.loads(source_manifest.read_text(encoding="utf-8"))
            payload["photos"][0]["confidence"] = 10**400
            source_manifest.write_text(json.dumps(payload), encoding="utf-8")
            source_manifest.chmod(0o600)
            storage = AppStorage.for_home(root / "home")

            with self.assertRaises(StorageError):
                storage.import_manifest(source_manifest)

            self.assertFalse(storage.runs_root.exists())

    def test_import_maps_malformed_selection_type_to_storage_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source_manifest = write_manifest(root / "external" / "source", _manifest())  # type: ignore[arg-type]
            payload = json.loads(source_manifest.read_text(encoding="utf-8"))
            payload["selection"]["access"] = []
            source_manifest.write_text(json.dumps(payload), encoding="utf-8")
            source_manifest.chmod(0o600)
            storage = AppStorage.for_home(root / "home")

            with self.assertRaises(StorageError):
                storage.import_manifest(source_manifest)

            self.assertFalse(storage.runs_root.exists())

    def test_import_maps_malformed_enum_type_to_storage_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source_manifest = write_manifest(root / "external" / "source", _manifest())  # type: ignore[arg-type]
            payload = json.loads(source_manifest.read_text(encoding="utf-8"))
            payload["scan_status"] = []
            source_manifest.write_text(json.dumps(payload), encoding="utf-8")
            source_manifest.chmod(0o600)
            storage = AppStorage.for_home(root / "home")

            with self.assertRaises(StorageError):
                storage.import_manifest(source_manifest)

            self.assertFalse(storage.runs_root.exists())

    def test_import_maps_deeply_nested_json_to_storage_error(self) -> None:
        """A malformed nested payload must not leak RecursionError to the UI."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            external = root / "external" / "source"
            external.mkdir(parents=True, mode=0o700)
            source_manifest = external / "manifest.json"
            source_manifest.write_bytes(b"[" * 2000 + b"]" * 2000)
            source_manifest.chmod(0o600)
            storage = AppStorage.for_home(root / "home")

            with self.assertRaises(StorageError):
                storage.import_manifest(source_manifest)

            self.assertFalse(storage.runs_root.exists())

    def test_import_maps_integer_conversion_limit_to_storage_error(self) -> None:
        """A bounded malformed JSON integer must not leak ValueError."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            external = root / "external" / "source"
            external.mkdir(parents=True, mode=0o700)
            source_manifest = external / "manifest.json"
            source_manifest.write_bytes(b'{"value":' + b"9" * 5000 + b"}")
            source_manifest.chmod(0o600)
            storage = AppStorage.for_home(root / "home")

            with self.assertRaises(StorageError):
                storage.import_manifest(source_manifest)

            self.assertFalse(storage.runs_root.exists())

    def test_import_removes_partial_run_when_preview_persistence_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source_manifest = write_manifest(root / "external" / "source", _manifest())  # type: ignore[arg-type]
            storage = AppStorage.for_home(root / "home")

            with mock.patch(
                "photos_indexer.app_storage.write_preview_csv",
                side_effect=OSError("simulated storage failure"),
            ), self.assertRaises(OSError):
                storage.import_manifest(source_manifest)

            self.assertTrue(storage.runs_root.is_dir())
            self.assertEqual(list(storage.runs_root.iterdir()), [])

    def test_import_removes_partial_run_when_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source_manifest = write_manifest(root / "external" / "source", _manifest())  # type: ignore[arg-type]
            storage = AppStorage.for_home(root / "home")

            with mock.patch(
                "photos_indexer.app_storage.write_preview_csv",
                side_effect=KeyboardInterrupt,
            ), self.assertRaises(KeyboardInterrupt):
                storage.import_manifest(source_manifest)

            self.assertTrue(storage.runs_root.is_dir())
            self.assertEqual(list(storage.runs_root.iterdir()), [])

    def test_import_removes_both_runs_when_source_copy_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = _manifest()
            source_manifest = write_manifest(root / "external-runs" / "source", source)  # type: ignore[arg-type]
            source_manifest.parent.parent.chmod(0o700)
            reviewed_manifest = review_manifest(
                source_manifest,
                {"00000000-0000-4000-8000-000000000001": ["perro"]},
            )
            storage = AppStorage.for_home(root / "home")

            with mock.patch(
                "photos_indexer.app_storage.write_preview_csv",
                side_effect=[None, OSError("simulated source-storage failure")],
            ), self.assertRaises(OSError):
                storage.import_manifest(reviewed_manifest)

            self.assertTrue(storage.runs_root.is_dir())
            self.assertEqual(list(storage.runs_root.iterdir()), [])

    def test_initializes_private_application_support_cache_and_logs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            storage = AppStorage.for_home(Path(temporary_directory))

            storage.ensure_directories()

            self.assertEqual(
                storage.runs_root,
                Path(temporary_directory) / "Library" / "Application Support" / "Photos Local Keyword Indexer" / "runs",
            )
            self.assertEqual(
                storage.exports_root,
                Path(temporary_directory) / "Library" / "Caches" / "Photos Local Keyword Indexer" / "exports",
            )
            self.assertEqual(
                storage.logs_root,
                Path(temporary_directory) / "Library" / "Logs" / "Photos Local Keyword Indexer",
            )
            self.assertEqual(
                storage.settings_path,
                Path(temporary_directory) / "Library" / "Application Support" / "Photos Local Keyword Indexer" / "settings.json",
            )
            for path in (storage.application_support_root, storage.runs_root, storage.cache_root, storage.exports_root, storage.logs_root):
                self.assertTrue(path.is_dir())
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)

    def test_import_copies_a_valid_private_manifest_without_altering_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source_run = root / "source-run"
            source_manifest = write_manifest(source_run, _manifest())  # type: ignore[arg-type]
            source_payload = source_manifest.read_bytes()
            storage = AppStorage.for_home(root / "home")

            imported_run = storage.import_manifest(source_manifest)

            imported_manifest = imported_run / "manifest.json"
            self.assertNotEqual(imported_run, source_run)
            self.assertEqual(imported_manifest.read_bytes(), source_payload)
            self.assertEqual(source_manifest.read_bytes(), source_payload)
            self.assertEqual(stat.S_IMODE(imported_run.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(imported_manifest.stat().st_mode), 0o600)
            self.assertEqual(json.loads(imported_manifest.read_text(encoding="utf-8"))["run_id"], json.loads(source_payload)["run_id"])

    def test_import_rejects_a_manifest_with_duplicate_json_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source_manifest = write_manifest(root / "source-run", _manifest())  # type: ignore[arg-type]
            payload = source_manifest.read_text(encoding="utf-8").replace(
                '"scan_status": "ready"',
                '"scan_status": "failed",\n  "scan_status": "ready"',
                1,
            )
            source_manifest.write_text(payload, encoding="utf-8")
            source_manifest.chmod(0o600)
            storage = AppStorage.for_home(root / "home")

            with self.assertRaises(StorageError):
                storage.import_manifest(source_manifest)

            self.assertFalse(storage.runs_root.exists())

    def test_import_copies_a_matching_review_source_and_marks_mutation_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source_root = root / "external-runs"
            source = _manifest()
            source_manifest = write_manifest(source_root / "source", source)  # type: ignore[arg-type]
            source_root.chmod(0o700)
            reviewed_manifest = review_manifest(
                source_manifest,
                {"00000000-0000-4000-8000-000000000001": ["perro"]},
            )
            storage = AppStorage.for_home(root / "home")

            imported_run = storage.import_manifest(reviewed_manifest)

            marker = json.loads((imported_run / "import-status.json").read_text(encoding="utf-8"))
            self.assertEqual(marker, {
                "mode": "mutation_ready",
                "reason": "SOURCE_RUN_IMPORTED",
                "source_run_id": source.run_id,  # type: ignore[union-attr]
            })
            copied_sources = [
                candidate for candidate in storage.runs_root.iterdir()
                if candidate.name.startswith("source-")
            ]
            self.assertEqual(len(copied_sources), 1)
            self.assertEqual(
                load_manifest(copied_sources[0]).run_id,
                json.loads(source_manifest.read_text(encoding="utf-8"))["run_id"],
            )

            from photos_indexer.adapters import ScriptPhotoRecord, SelectedPhoto
            from photos_indexer.workflows import ApplyDependencies, run_apply

            class Selector:
                def revalidate(self, local_id: str) -> SelectedPhoto:
                    return SelectedPhoto(local_id, datetime(2026, 8, 25, 12, 0))

            class Bridge:
                def read(self, identifier: str) -> ScriptPhotoRecord:
                    return ScriptPhotoRecord(
                        source.photos[0].uuid, "test-local-id", "Prueba", datetime(2026, 8, 25, 12, 0), (),
                    )  # type: ignore[union-attr]

                def replace_keywords(self, identifier: str, keywords: list[str]) -> tuple[str, ...]:
                    return tuple(keywords)

            applied = run_apply(
                imported_run / "manifest.json",
                dependencies=ApplyDependencies(
                    selector_factory=Selector,
                    bridge_factory=Bridge,
                    global_lock_path=storage.runs_root / "global" / "mutation.lock",
                ),
            )
            self.assertEqual(applied.error_codes, ())
            self.assertEqual(applied.exit_code, 0)

    def test_import_marks_reviewed_manifest_query_only_when_source_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source_root = root / "external-runs"
            source = _manifest()
            source_manifest = write_manifest(source_root / "source", source)  # type: ignore[arg-type]
            source_root.chmod(0o700)
            reviewed_manifest = review_manifest(
                source_manifest,
                {"00000000-0000-4000-8000-000000000001": ["perro"]},
            )
            source_manifest.unlink()
            source_manifest.parent.rmdir()
            storage = AppStorage.for_home(root / "home")

            imported_run = storage.import_manifest(reviewed_manifest)

            marker = json.loads((imported_run / "import-status.json").read_text(encoding="utf-8"))
            self.assertEqual(marker, {
                "mode": "query_only",
                "reason": "SOURCE_RUN_UNAVAILABLE",
                "source_run_id": json.loads(reviewed_manifest.read_text(encoding="utf-8"))["reviewed_from_run_id"],
            })
            self.assertEqual(
                [candidate for candidate in storage.runs_root.iterdir() if candidate.name.startswith("source-")],
                [],
            )

            from photos_indexer.workflows import ApplyDependencies, run_apply

            adapter_calls: list[str] = []
            result = run_apply(
                imported_run / "manifest.json",
                dependencies=ApplyDependencies(
                    selector_factory=lambda: adapter_calls.append("selector"),
                    bridge_factory=lambda: adapter_calls.append("bridge"),
                ),
            )
            self.assertEqual(result.exit_code, 2)
            self.assertEqual(result.error_codes, ("REVIEW_PROVENANCE_INVALID",))
            self.assertEqual(adapter_calls, [])

    def test_import_marks_reviewed_manifest_query_only_when_model_plan_changed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source_root = root / "external-runs"
            source = _manifest()
            source_manifest = write_manifest(source_root / "source", source)  # type: ignore[arg-type]
            source_root.chmod(0o700)
            reviewed_manifest = review_manifest(
                source_manifest,
                {"00000000-0000-4000-8000-000000000001": ["perro"]},
            )
            reviewed = load_manifest(reviewed_manifest.parent)
            reviewed.model["detailed_name"] = "qwen3-vl:8b"
            write_manifest(reviewed_manifest.parent, reviewed)
            storage = AppStorage.for_home(root / "home")

            imported_run = storage.import_manifest(reviewed_manifest)

            marker = json.loads((imported_run / "import-status.json").read_text(encoding="utf-8"))
            self.assertEqual(marker["mode"], "query_only")
            self.assertEqual(marker["reason"], "SOURCE_RUN_UNAVAILABLE")
            self.assertEqual(
                [candidate for candidate in storage.runs_root.iterdir() if candidate.name.startswith("source-")],
                [],
            )

    def test_import_marks_reviewed_manifest_query_only_when_source_scan_failed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source_root = root / "external-runs"
            source = _manifest()
            source_manifest = write_manifest(source_root / "source", source)  # type: ignore[arg-type]
            source_root.chmod(0o700)
            reviewed_manifest = review_manifest(
                source_manifest,
                {"00000000-0000-4000-8000-000000000001": ["perro"]},
            )

            failed_source = load_manifest(source_manifest.parent)
            failed_source.scan_status = "failed"
            failed_source.run_errors = [{"stage": "workspace", "code": "WORKSPACE_FAILED"}]
            write_manifest(source_manifest.parent, failed_source)
            failed_review = load_manifest(reviewed_manifest.parent)
            failed_review.scan_status = "failed"
            failed_review.run_errors = [{"stage": "workspace", "code": "WORKSPACE_FAILED"}]
            failed_review.source_scan_digest = failed_source.scan_digest
            write_manifest(reviewed_manifest.parent, failed_review)

            storage = AppStorage.for_home(root / "home")
            imported_run = storage.import_manifest(reviewed_manifest)

            marker = json.loads((imported_run / "import-status.json").read_text(encoding="utf-8"))
            self.assertEqual(marker["mode"], "query_only")
            self.assertEqual(marker["reason"], "SOURCE_RUN_UNAVAILABLE")
            self.assertEqual(
                [candidate for candidate in storage.runs_root.iterdir() if candidate.name.startswith("source-")],
                [],
            )

    def test_import_marks_an_already_mutated_review_query_only(self) -> None:
        """A completed review must never be advertised as mutation-ready again."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source_root = root / "external-runs"
            source = _manifest()
            source_manifest = write_manifest(source_root / "source", source)  # type: ignore[arg-type]
            source_root.chmod(0o700)
            reviewed_manifest = review_manifest(
                source_manifest,
                {"00000000-0000-4000-8000-000000000001": ["perro"]},
            )
            reviewed = load_manifest(reviewed_manifest.parent)
            photo = reviewed.photos[0]
            photo.apply_state = "verified"
            photo.applied_keywords = ["perro"]
            photo.mutation_digest = compute_mutation_digest(reviewed, photo)
            write_manifest(reviewed_manifest.parent, reviewed)
            storage = AppStorage.for_home(root / "home")

            imported_run = storage.import_manifest(reviewed_manifest)

            marker = json.loads((imported_run / "import-status.json").read_text(encoding="utf-8"))
            self.assertEqual(marker["mode"], "query_only")
            self.assertEqual(marker["reason"], "REVIEW_NOT_PRISTINE")
            self.assertEqual(
                [candidate for candidate in storage.runs_root.iterdir() if candidate.name.startswith("source-")],
                [],
            )

    def test_import_rejects_symlinks_and_does_not_create_a_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source.json"
            source.write_text("{}", encoding="utf-8")
            source.chmod(0o600)
            link = root / "manifest.json"
            link.symlink_to(source)
            storage = AppStorage.for_home(root / "home")

            with self.assertRaises(StorageError):
                storage.import_manifest(link)

            self.assertFalse(storage.runs_root.exists())

    def test_ensure_directories_rejects_a_symlinked_application_support_ancestor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            home = root / "home"
            outside = root / "outside"
            outside.mkdir()
            library = home / "Library"
            library.mkdir(parents=True)
            (library / "Application Support").symlink_to(outside, target_is_directory=True)
            storage = AppStorage.for_home(home)

            with self.assertRaises(StorageError):
                storage.ensure_directories()

            self.assertFalse((outside / "Photos Local Keyword Indexer").exists())

    def test_settings_round_trip_uses_a_private_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            storage = AppStorage.for_home(Path(temporary_directory))

            settings_path = storage.write_settings({"apple_maps": False, "limit": 20})

            self.assertEqual(settings_path, storage.settings_path)
            self.assertEqual(stat.S_IMODE(settings_path.stat().st_mode), 0o600)
            self.assertEqual(storage.load_settings(), {"apple_maps": False, "limit": 20})

    def test_load_settings_rejects_duplicate_json_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            storage = AppStorage.for_home(Path(temporary_directory))
            storage.ensure_directories()
            storage.settings_path.write_text(
                '{"apple_maps":false,"apple_maps":true}\n',
                encoding="utf-8",
            )
            storage.settings_path.chmod(0o600)

            with self.assertRaises(StorageError):
                storage.load_settings()

    def test_load_settings_rejects_nonstandard_json_constants(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            storage = AppStorage.for_home(Path(temporary_directory))
            storage.ensure_directories()
            storage.settings_path.write_text('{"limit":NaN}\n', encoding="utf-8")
            storage.settings_path.chmod(0o600)

            with self.assertRaises(StorageError):
                storage.load_settings()

    def test_load_settings_rejects_escaped_unicode_surrogates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            storage = AppStorage.for_home(Path(temporary_directory))
            storage.ensure_directories()
            storage.settings_path.write_text('{"label":"\\ud800"}\n', encoding="utf-8")
            storage.settings_path.chmod(0o600)

            with self.assertRaises(StorageError):
                storage.load_settings()

    def test_write_settings_rejects_nonfinite_numbers_without_a_partial_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            storage = AppStorage.for_home(Path(temporary_directory))

            with self.assertRaises(StorageError):
                storage.write_settings({"confidence": float("nan")})

            self.assertFalse(storage.settings_path.exists())

    def test_write_settings_rejects_control_characters_without_a_partial_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            storage = AppStorage.for_home(Path(temporary_directory))

            with self.assertRaises(StorageError):
                storage.write_settings({"label": "bad\x01value"})

            self.assertFalse(storage.settings_path.exists())

    def test_load_settings_maps_deeply_nested_json_to_storage_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            storage = AppStorage.for_home(Path(temporary_directory))
            storage.ensure_directories()
            storage.settings_path.write_bytes(b"[" * 2000 + b"]" * 2000)
            storage.settings_path.chmod(0o600)

            with self.assertRaises(StorageError):
                storage.load_settings()

    def test_write_settings_maps_deep_nesting_to_storage_error_without_a_partial_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            storage = AppStorage.for_home(Path(temporary_directory))
            nested: dict[str, object] = {}
            for _ in range(2000):
                nested = {"value": nested}

            with self.assertRaises(StorageError):
                storage.write_settings(nested)

            self.assertFalse(storage.settings_path.exists())

    def test_write_settings_maps_unicode_surrogates_to_storage_error_without_a_partial_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            storage = AppStorage.for_home(Path(temporary_directory))

            with self.assertRaises(StorageError):
                storage.write_settings({"label": "\ud800"})

            self.assertFalse(storage.settings_path.exists())

    def test_load_settings_rejects_a_dangling_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            storage = AppStorage.for_home(Path(temporary_directory))
            storage.ensure_directories()
            storage.settings_path.symlink_to(Path(temporary_directory) / "missing-settings.json")

            with self.assertRaises(StorageError):
                storage.load_settings()

    def test_load_settings_rejects_a_symlinked_application_support_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            storage = AppStorage.for_home(root / "home")
            outside = root / "outside"
            outside.mkdir(mode=0o700)
            storage.application_support_root.parent.mkdir(parents=True)
            storage.application_support_root.symlink_to(outside, target_is_directory=True)
            (outside / "settings.json").write_text('{"apple_maps":true}\n', encoding="utf-8")
            (outside / "settings.json").chmod(0o600)

            with self.assertRaises(StorageError):
                storage.load_settings()

    def test_import_maps_permission_error_while_opening_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source_manifest = write_manifest(root / "external" / "source", _manifest())  # type: ignore[arg-type]
            storage = AppStorage.for_home(root / "home")

            with mock.patch("photos_indexer.app_storage.os.open", side_effect=PermissionError):
                with self.assertRaises(StorageError):
                    storage.import_manifest(source_manifest)

            self.assertFalse(storage.runs_root.exists())

    def test_import_maps_permission_error_while_statting_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source_manifest = write_manifest(root / "external" / "source", _manifest())  # type: ignore[arg-type]
            storage = AppStorage.for_home(root / "home")

            with mock.patch("photos_indexer.app_storage.os.lstat", side_effect=PermissionError):
                with self.assertRaises(StorageError):
                    storage.import_manifest(source_manifest)

            self.assertFalse(storage.runs_root.exists())

    def test_failed_import_cleanup_is_fail_closed_when_lstat_fails(self) -> None:
        from photos_indexer.app_storage import _remove_new_private_run

        with mock.patch("photos_indexer.app_storage.os.lstat", side_effect=PermissionError):
            _remove_new_private_run(Path("/private/invalid-run"))


if __name__ == "__main__":
    unittest.main()
