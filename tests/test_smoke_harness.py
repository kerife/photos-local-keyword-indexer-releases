from __future__ import annotations

import unittest
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from photos_indexer.adapters import ScriptPhotoRecord, SelectedPhoto
from photos_indexer.taxonomy import TAXONOMY


class MacosMutationSmokeGuardTests(unittest.TestCase):
    def test_smoke_scan_uses_an_ephemeral_run_directory(self) -> None:
        source = Path(__file__).with_name("macos_mutation_smoke.py").read_text(encoding="utf-8")
        self.assertIn("TemporaryDirectory", source)
        self.assertIn("smoke_root.cleanup()", source)
        self.assertNotIn('Path("smoke-runs")', source)

    def test_smoke_requires_darwin_explicit_uuid_and_exact_confirmation(self) -> None:
        from tests.macos_mutation_smoke import CONFIRMATION, SmokeRefused, require_opt_in

        valid_uuid = "00000000-0000-4000-8000-000000000001"
        valid = {"PHOTOS_INDEXER_SMOKE_UUID": valid_uuid, "PHOTOS_INDEXER_SMOKE_CONFIRM": CONFIRMATION}
        self.assertEqual(str(require_opt_in(valid, "Darwin")), valid_uuid)

        for environment, platform_name in (
            ({}, "Darwin"),
            ({"PHOTOS_INDEXER_SMOKE_UUID": valid_uuid}, "Darwin"),
            ({**valid, "PHOTOS_INDEXER_SMOKE_CONFIRM": "yes"}, "Darwin"),
            ({**valid, "PHOTOS_INDEXER_SMOKE_UUID": "not-a-uuid"}, "Darwin"),
            (valid, "Linux"),
        ):
            with self.subTest(environment=environment, platform_name=platform_name), self.assertRaises(SmokeRefused):
                require_opt_in(environment, platform_name)

    def test_caption_smoke_requires_separate_explicit_confirmation(self) -> None:
        from tests.macos_mutation_smoke import CAPTION_CONFIRMATION, SmokeRefused, require_caption_opt_in

        valid = {"PHOTOS_INDEXER_SMOKE_CAPTION_CONFIRM": CAPTION_CONFIRMATION}
        self.assertIsNone(require_caption_opt_in(valid, "Darwin"))
        with self.assertRaises(SmokeRefused):
            require_caption_opt_in({}, "Darwin")
        with self.assertRaises(SmokeRefused):
            require_caption_opt_in(valid, "Linux")

    def test_external_keyword_is_added_after_apply_and_before_rollback(self) -> None:
        from tests import macos_mutation_smoke as smoke

        target = uuid.UUID("00000000-0000-4000-8000-000000000001")
        keyword = TAXONOMY[0]
        events: list[str] = []
        mutation_paths: list[Path] = []
        state = ["PERRO"]
        caption = [""]

        class Bridge:
            def read(self, identifier: str) -> ScriptPhotoRecord:
                return ScriptPhotoRecord(
                    str(target), "local-1", "Test", datetime(2026, 8, 24), tuple(state), description=caption[0]
                )

            def replace_keywords(self, identifier: str, desired: list[str]) -> tuple[str, ...]:
                events.append("external" if any(value.startswith("photos-indexer-smoke-external-") for value in desired) else "cleanup")
                state[:] = desired
                return tuple(desired)

        class Selector:
            def revalidate(self, local_id: str) -> SelectedPhoto:
                return SelectedPhoto(local_id, datetime(2026, 8, 24))

        def apply(*args: object, **kwargs: object) -> SimpleNamespace:
            events.append("apply")
            mutation_paths.append(args[0])
            state.append(keyword)
            caption[0] = smoke.SMOKE_CAPTION
            return SimpleNamespace(exit_code=0)

        def rollback(*args: object, **kwargs: object) -> SimpleNamespace:
            events.append("rollback")
            mutation_paths.append(args[0])
            state.remove(keyword)
            caption[0] = ""
            return SimpleNamespace(exit_code=0)

        reviewed_path = Path("/tmp/reviewed-smoke-manifest.json")

        def review(path: Path, selections: dict[str, list[str]], captions: dict[str, bool]) -> Path:
            self.assertEqual(path, Path("/tmp/smoke-manifest.json"))
            self.assertEqual(selections, {str(target): [keyword]})
            self.assertEqual(captions, {str(target): True})
            return reviewed_path

        scan_result = SimpleNamespace(
            manifest_path=Path("/tmp/smoke-manifest.json"),
            manifest=SimpleNamespace(
                summary={"ready": 1},
                photos=[SimpleNamespace(uuid=str(target), proposed_keywords=[keyword], proposed_caption=smoke.SMOKE_CAPTION)],
            ),
        )
        with (
            patch.object(smoke, "require_opt_in", return_value=target),
            patch.object(smoke, "require_caption_opt_in", return_value=None),
            patch.object(smoke, "PhotoScriptBridge", return_value=Bridge()),
            patch.object(smoke, "PhotoKitSelector", return_value=Selector()),
            patch.object(smoke, "run_scan", return_value=scan_result),
            patch.object(smoke, "review_manifest", side_effect=review),
            patch.object(smoke, "run_apply", side_effect=apply),
            patch.object(smoke, "run_rollback", side_effect=rollback),
            patch.object(smoke, "load_manifest", return_value=SimpleNamespace(
                photos=[SimpleNamespace(applied_keywords=[keyword], applied_caption=smoke.SMOKE_CAPTION)]
            )),
        ):
            self.assertEqual(smoke.main(), 0)

        self.assertLess(events.index("apply"), events.index("external"))
        self.assertLess(events.index("external"), events.index("rollback"))
        self.assertEqual(state, ["PERRO"])
        self.assertEqual(mutation_paths, [reviewed_path, reviewed_path])

    def test_cleanup_preserves_an_uncertain_keyword_if_apply_wrote_before_reporting_failure(self) -> None:
        from tests import macos_mutation_smoke as smoke

        target = uuid.UUID("00000000-0000-4000-8000-000000000001")
        keyword = TAXONOMY[0]
        state = ["PERRO"]

        class Bridge:
            def read(self, identifier: str) -> ScriptPhotoRecord:
                return ScriptPhotoRecord(str(target), "local-1", "Test", datetime(2026, 8, 24), tuple(state))

            def replace_keywords(self, identifier: str, desired: list[str]) -> tuple[str, ...]:
                state[:] = desired
                return tuple(desired)

        class Selector:
            def revalidate(self, local_id: str) -> SelectedPhoto:
                return SelectedPhoto(local_id, datetime(2026, 8, 24))

        def uncertain_apply(*args: object, **kwargs: object) -> SimpleNamespace:
            state.append(keyword)
            return SimpleNamespace(exit_code=1)

        scan_result = SimpleNamespace(
            manifest_path=Path("/tmp/smoke-manifest.json"),
            manifest=SimpleNamespace(
                summary={"ready": 1},
                photos=[SimpleNamespace(uuid=str(target), proposed_keywords=[keyword], proposed_caption=smoke.SMOKE_CAPTION)],
            ),
        )
        reviewed_path = Path("/tmp/reviewed-smoke-manifest.json")
        with (
            patch.object(smoke, "require_opt_in", return_value=target),
            patch.object(smoke, "require_caption_opt_in", return_value=None),
            patch.object(smoke, "PhotoScriptBridge", return_value=Bridge()),
            patch.object(smoke, "PhotoKitSelector", return_value=Selector()),
            patch.object(smoke, "run_scan", return_value=scan_result),
            patch.object(smoke, "review_manifest", return_value=reviewed_path),
            patch.object(smoke, "run_apply", side_effect=uncertain_apply),
        ):
            self.assertEqual(smoke.main(), 1)

        # The setter may have succeeded before the workflow reported failure.
        # Without manifest evidence, cleanup must not claim ownership and
        # remove a value that could have been added externally.
        self.assertEqual(state, ["PERRO", keyword])


if __name__ == "__main__":
    unittest.main()
