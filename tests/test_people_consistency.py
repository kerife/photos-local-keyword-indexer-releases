from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from photos_indexer.adapters import PhotoSelection, ScriptPhotoRecord, SelectedPhoto
from photos_indexer.models import VisionResult
from photos_indexer.workflows import ScanDependencies, run_scan


class _Workspace:
    def __init__(self, parent: Path, run_id: str) -> None:
        self.root = parent / f".exports-{run_id}"

    def __enter__(self) -> "_Workspace":
        self.root.mkdir(mode=0o700)
        return self

    def destination_for(self, photo_uuid: str) -> Path:
        destination = self.root / photo_uuid
        destination.mkdir(mode=0o700)
        return destination

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        for child in self.root.iterdir():
            for exported in child.iterdir():
                exported.unlink()
            child.rmdir()
        self.root.rmdir()


class PeopleConsistencyTests(unittest.TestCase):
    def test_people_flag_adds_generic_person_keyword_when_model_omits_it(self) -> None:
        selected = SelectedPhoto("local-1", datetime(2026, 8, 24, 10, 0))
        record = ScriptPhotoRecord(
            "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "local-1", "", selected.creation_date, ()
        )

        class Selector:
            def select(self, *, limit: int) -> PhotoSelection:
                return PhotoSelection((selected,), 1, 1, 0, "authorized")

        class Bridge:
            def __init__(self) -> None:
                self.record = record

            def read(self, local_id: str) -> ScriptPhotoRecord:
                return self.record

            def export(self, local_id: str, destination: Path) -> Path:
                exported = destination / "photo.png"
                exported.write_bytes(b"\x89PNG\r\n\x1a\nlocal")
                return exported

        class Vision:
            def check_model(self, model: str) -> str:
                return "0.12.7"

            def analyze(self, model: str, image: Path) -> VisionResult:
                return VisionResult(("playa",), "", True, False, 0.95)

        with tempfile.TemporaryDirectory() as tmp:
            result = run_scan(
                Path(tmp) / "runs",
                limit=1,
                dependencies=ScanDependencies(
                    platform_name=lambda: "Darwin",
                    selector_factory=Selector,
                    bridge_factory=Bridge,
                    vision_factory=Vision,
                    workspace_factory=_Workspace,
                    recover_workspaces=lambda parent: [],
                ),
            )

        self.assertEqual(result.exit_code, 0)
        assert result.manifest is not None
        self.assertEqual(result.manifest.photos[0].proposed_keywords, ["persona", "playa"])

    def test_text_flag_adds_generic_text_keyword_when_model_omits_it(self) -> None:
        selected = SelectedPhoto("local-1", datetime(2026, 8, 24, 10, 0))
        record = ScriptPhotoRecord(
            "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "local-1", "", selected.creation_date, ()
        )

        class Selector:
            def select(self, *, limit: int) -> PhotoSelection:
                return PhotoSelection((selected,), 1, 1, 0, "authorized")

        class Bridge:
            def read(self, local_id: str) -> ScriptPhotoRecord:
                return record

            def export(self, local_id: str, destination: Path) -> Path:
                exported = destination / "photo.png"
                exported.write_bytes(b"\x89PNG\r\n\x1alocal")
                return exported

        class Vision:
            def check_model(self, model: str) -> str:
                return "0.12.7"

            def analyze(self, model: str, image: Path) -> VisionResult:
                return VisionResult(("playa",), "", False, True, 0.95)

        with tempfile.TemporaryDirectory() as tmp:
            result = run_scan(
                Path(tmp) / "runs",
                limit=1,
                dependencies=ScanDependencies(
                    platform_name=lambda: "Darwin",
                    selector_factory=Selector,
                    bridge_factory=Bridge,
                    vision_factory=Vision,
                    workspace_factory=_Workspace,
                    recover_workspaces=lambda parent: [],
                ),
            )

        self.assertEqual(result.exit_code, 0)
        assert result.manifest is not None
        self.assertEqual(result.manifest.photos[0].proposed_keywords, ["texto", "playa"])
