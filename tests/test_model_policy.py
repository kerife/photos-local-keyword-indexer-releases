from __future__ import annotations

import unittest
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from photos_indexer.adapters import OllamaModelMissingError, PhotoSelection, ScriptPhotoRecord, SelectedPhoto
from photos_indexer.models import VisionResult
from photos_indexer.workflows import (
    DEFAULT_MODEL,
    DEFAULT_DETAILED_MODEL,
    ModelPlan,
    ScanDependencies,
    resolve_model_plan,
    run_scan,
)


class ModelPolicyTests(unittest.TestCase):
    def test_single_policy_preserves_explicit_model(self) -> None:
        plan = resolve_model_plan(policy="single", model="custom:vision", fast_model=DEFAULT_MODEL, detailed_model=DEFAULT_DETAILED_MODEL)

        self.assertEqual(plan, ModelPlan("single", "custom:vision", "custom:vision"))
        self.assertEqual(plan.model_for_location(None), ("custom:vision", "single_policy"))

    def test_adaptive_policy_routes_gps_to_detailed_model(self) -> None:
        plan = resolve_model_plan(policy="adaptive", model=None, fast_model="qwen3-vl:4b", detailed_model="qwen3-vl:8b")

        self.assertEqual(plan.required_models, ("qwen3-vl:4b", "qwen3-vl:8b"))
        self.assertEqual(plan.model_for_location(None), ("qwen3-vl:4b", "no_location"))
        self.assertEqual(plan.model_for_location((19.4, -99.1)), ("qwen3-vl:8b", "location_context"))

    def test_adaptive_policy_rejects_an_explicit_single_model(self) -> None:
        with self.assertRaises(ValueError):
            resolve_model_plan(policy="adaptive", model="qwen3-vl:4b", fast_model="qwen3-vl:4b", detailed_model="qwen3-vl:8b")

    def test_model_plan_rejects_cloud_models_before_preflight(self) -> None:
        with self.assertRaises(ValueError):
            resolve_model_plan(policy="single", model="qwen3-vl:4b-cloud")

    def test_missing_detailed_model_stops_before_opening_photos(self) -> None:
        selected = False

        class Vision:
            def check_model(self, model: str) -> str:
                if model == "qwen3-vl:8b":
                    raise OllamaModelMissingError("missing", model=model)
                return "0.12.7"

        def selector_factory():
            nonlocal selected
            selected = True
            raise AssertionError("Photos must not open before model preflight")

        result = run_scan(
            Path("runs"), model=None, model_policy="adaptive", detailed_model="qwen3-vl:8b",
            dependencies=ScanDependencies(platform_name=lambda: "Darwin", vision_factory=Vision, selector_factory=selector_factory),
        )

        self.assertEqual(result.error_codes, ("OLLAMA_MODEL_MISSING",))
        self.assertEqual(result.safe_instruction, "ollama pull qwen3-vl:8b")
        self.assertFalse(selected)

    def test_run_scan_rejects_non_boolean_privacy_options_before_preflight(self) -> None:
        """The direct workflow API must not truthily opt into GPS or captions."""
        calls: list[str] = []

        class Vision:
            def check_model(self, model: str) -> str:
                calls.append(f"vision:{model}")
                return "0.12.7"

        def selector_factory() -> object:
            calls.append("photos")
            raise AssertionError("Photos must not open for invalid options")

        for option in ("apple_maps", "include_caption"):
            with self.subTest(option=option):
                calls.clear()
                result = run_scan(
                    Path("runs"),
                    **{option: 1},
                    dependencies=ScanDependencies(
                        platform_name=lambda: "Darwin",
                        vision_factory=Vision,
                        selector_factory=selector_factory,
                    ),
                )

                self.assertEqual(result.exit_code, 2)
                self.assertEqual(result.error_codes, ("SCAN_OPTIONS_INVALID",))
                self.assertEqual(calls, [])

    def test_adaptive_scan_records_the_model_used_for_each_location_state(self) -> None:
        first = SelectedPhoto("local-1", datetime(2026, 8, 25, 10, 0))
        second = SelectedPhoto("local-2", datetime(2026, 8, 24, 10, 0))
        records = {
            "local-1": ScriptPhotoRecord(str(uuid.uuid4()), "local-1", "", first.creation_date, (), None),
            "local-2": ScriptPhotoRecord(str(uuid.uuid4()), "local-2", "", second.creation_date, (), (19.4, -99.1)),
        }
        analyzed: list[str] = []

        class Selector:
            def select(self, *, limit: int):
                return PhotoSelection((first, second), limit, 2, 0, "authorized")

        class Bridge:
            def read(self, local_id: str):
                return records[local_id]

            def export(self, local_id: str, destination: Path) -> Path:
                result = destination / "image.png"
                result.write_bytes(b"png")
                return result

        class Vision:
            def check_model(self, model: str) -> str:
                return "0.12.7"

            def analyze(self, model: str, image: Path, **kwargs: object) -> VisionResult:
                analyzed.append(model)
                return VisionResult(("playa",), "Una escena visible.", False, False, 0.9)

        with tempfile.TemporaryDirectory() as tmp:
            result = run_scan(
                Path(tmp) / "runs", model=None, model_policy="adaptive", detailed_model="qwen3-vl:8b",
                include_caption=True,
                dependencies=ScanDependencies(
                    platform_name=lambda: "Darwin",
                    now_utc=lambda: datetime(2026, 8, 25, tzinfo=timezone.utc),
                    selector_factory=Selector,
                    bridge_factory=Bridge,
                    vision_factory=Vision,
                ),
            )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(analyzed, ["qwen3-vl:4b", "qwen3-vl:8b"])
        assert result.manifest is not None
        self.assertEqual(result.manifest.schema_version, 2)
        self.assertEqual(
            [(photo.model_used, photo.model_reason) for photo in result.manifest.photos],
            [("qwen3-vl:4b", "no_location"), ("qwen3-vl:8b", "location_context")],
        )
        self.assertEqual(
            [photo.proposed_caption for photo in result.manifest.photos],
            ["Una escena visible.", "Una escena visible."],
        )


if __name__ == "__main__":
    unittest.main()
