"""Explicitly opt-in macOS mutation smoke harness; never collected as a pytest test."""

from __future__ import annotations

import os
import platform
import tempfile
import uuid
from collections.abc import Mapping
from pathlib import Path

from photos_indexer.adapters import PhotoKitSelector, PhotoScriptBridge, PhotoSelection
from photos_indexer.manifest import load_manifest
from photos_indexer.models import VisionResult
from photos_indexer.service import review_manifest
from photos_indexer.taxonomy import TAXONOMY, canonical_keyword_key
from photos_indexer.workflows import ApplyDependencies, ScanDependencies, run_apply, run_rollback, run_scan


CONFIRMATION = "I_CONFIRM_TEST_LIBRARY_KEYWORD_MUTATION"
CAPTION_CONFIRMATION = "I_CONFIRM_TEST_LIBRARY_CAPTION_MUTATION"
SMOKE_CAPTION = "Escena de prueba local."


class SmokeRefused(RuntimeError):
    def __init__(self, code: str = "OPT_IN_REQUIRED") -> None:
        self.code = code
        super().__init__(code)


def require_opt_in(environment: Mapping[str, str], platform_name: str) -> uuid.UUID:
    if platform_name != "Darwin" or environment.get("PHOTOS_INDEXER_SMOKE_CONFIRM") != CONFIRMATION:
        raise SmokeRefused("KEYWORD_OPT_IN_REQUIRED")
    try:
        return uuid.UUID(environment["PHOTOS_INDEXER_SMOKE_UUID"])
    except (KeyError, TypeError, ValueError) as error:
        raise SmokeRefused("UUID_REQUIRED") from error


def require_caption_opt_in(environment: Mapping[str, str], platform_name: str) -> None:
    """Require a second, explicit consent for mutating Photos descriptions."""
    if platform_name != "Darwin" or environment.get("PHOTOS_INDEXER_SMOKE_CAPTION_CONFIRM") != CAPTION_CONFIRMATION:
        raise SmokeRefused("CAPTION_OPT_IN_REQUIRED")


class _SinglePhotoSelector:
    def __init__(self, selector: PhotoKitSelector, local_id: str) -> None:
        self._selector = selector
        self._local_id = local_id

    def select(self, *, limit: int) -> PhotoSelection:
        if limit != 1:
            raise SmokeRefused
        selected = self._selector.revalidate(self._local_id)
        return PhotoSelection((selected,), 1, 1, 0, "limited")

    def revalidate(self, local_id: str):  # type: ignore[no-untyped-def]
        if local_id != self._local_id:
            raise SmokeRefused
        return self._selector.revalidate(local_id)


class _DeterministicVision:
    def __init__(self, keyword: str, caption: str = SMOKE_CAPTION) -> None:
        self._keyword = keyword
        self._caption = caption

    def check_model(self, model: str) -> str:
        return "smoke-fake"

    def analyze(self, model: str, image: Path) -> VisionResult:
        return VisionResult((self._keyword,), self._caption, False, False, 0.99)


def _remove_one(values: list[str], target: str) -> None:
    try:
        values.remove(target)
    except ValueError:
        pass


def main() -> int:
    try:
        target_uuid = require_opt_in(os.environ, platform.system())
        require_caption_opt_in(os.environ, platform.system())
    except SmokeRefused as error:
        print(f"smoke:disabled:{error.code}")
        return 2

    smoke_root = tempfile.TemporaryDirectory(prefix="photos-indexer-smoke-")
    bridge: PhotoScriptBridge | None = None
    applied: list[str] = []
    original_description = ""
    marker = f"photos-indexer-smoke-external-{uuid.uuid4().hex[:8]}"
    local_id = ""
    try:
        bridge = PhotoScriptBridge()
        original = bridge.read(str(target_uuid))
        if uuid.UUID(original.uuid) != target_uuid:
            raise SmokeRefused
        local_id = original.local_id
        original_description = original.description
        if original_description:
            raise SmokeRefused("TARGET_DESCRIPTION_NOT_EMPTY")
        existing_keys = {canonical_keyword_key(value) for value in original.existing_keywords}
        keyword = next((value for value in TAXONOMY if canonical_keyword_key(value) not in existing_keys), None)
        if keyword is None:
            raise SmokeRefused
        real_selector = PhotoKitSelector()
        selector = _SinglePhotoSelector(real_selector, local_id)
        scan = run_scan(
            Path(smoke_root.name),
            limit=1,
            model="smoke-local:fake",
            dependencies=ScanDependencies(
                selector_factory=lambda: selector,
                bridge_factory=lambda: bridge,
                vision_factory=lambda: _DeterministicVision(keyword),
            ),
            # This harness deliberately exercises the PhotoScript description
            # setter as well as keyword mutation.
            include_caption=True,
        )
        if (
            scan.manifest_path is None
            or scan.manifest is None
            or scan.manifest.summary["ready"] != 1
            or scan.manifest.photos[0].proposed_caption != SMOKE_CAPTION
        ):
            raise SmokeRefused

        # Mutation is intentionally gated through the same reviewed-manifest
        # boundary as the distributed app.  Applying the schema-2 scan output
        # directly is rejected by run_apply and would leave this harness
        # testing a path the product cannot use.
        keyword_selections = {
            photo.uuid: list(photo.proposed_keywords)
            for photo in scan.manifest.photos
            if photo.uuid is not None
        }
        caption_selections = {
            photo.uuid: bool(photo.proposed_caption)
            for photo in scan.manifest.photos
            if photo.uuid is not None
        }
        reviewed_manifest_path = review_manifest(
            scan.manifest_path,
            keyword_selections,
            caption_selections,
        )

        mutation_dependencies = ApplyDependencies(selector_factory=lambda: selector, bridge_factory=lambda: bridge)
        applied_result = run_apply(reviewed_manifest_path, dependencies=mutation_dependencies)
        if applied_result.exit_code != 0:
            raise RuntimeError("apply did not complete")
        applied = list(load_manifest(reviewed_manifest_path.parent).photos[0].applied_keywords)
        applied_caption = load_manifest(reviewed_manifest_path.parent).photos[0].applied_caption
        after_apply = bridge.read(str(target_uuid))
        if (
            not applied
            or any(value not in after_apply.existing_keywords for value in applied)
            or applied_caption != SMOKE_CAPTION
            or after_apply.description != SMOKE_CAPTION
        ):
            raise SmokeRefused

        external_readback = bridge.replace_keywords(
            str(target_uuid), [*after_apply.existing_keywords, marker]
        )
        if marker not in external_readback:
            raise SmokeRefused

        rolled_back = run_rollback(reviewed_manifest_path, dependencies=mutation_dependencies)
        after_rollback = bridge.read(str(target_uuid))
        if (
            rolled_back.exit_code != 0
            or marker not in after_rollback.existing_keywords
            or after_rollback.description != original_description
        ):
            raise SmokeRefused
        if any(value in after_rollback.existing_keywords for value in applied):
            raise SmokeRefused
        print("smoke:passed")
        return 0
    except SmokeRefused as error:
        print(f"smoke:refused:{error.code}")
        return 2
    except Exception:
        print("smoke:failed")
        return 1
    finally:
        if local_id and bridge is not None:
            try:
                current = bridge.read(str(target_uuid))
                desired = list(current.existing_keywords)
                _remove_one(desired, marker)
                # Only remove values that the manifest confirmed after a
                # successful read-back.  A failed/uncertain apply may have
                # written before reporting an error, but the value could
                # equally have been added by another editor.  Leaving that
                # ambiguity for manual review is safer than deleting data we
                # cannot attribute to this smoke run.
                for value in applied:
                    _remove_one(desired, value)
                if desired != list(current.existing_keywords):
                    bridge.replace_keywords(str(target_uuid), desired)
                if current.description != original_description:
                    bridge.replace_description(str(target_uuid), original_description)
            except Exception:
                print("smoke:cleanup_required")
        smoke_root.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
