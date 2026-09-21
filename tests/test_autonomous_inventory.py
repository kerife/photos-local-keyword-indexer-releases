from __future__ import annotations

from datetime import datetime, timedelta
import importlib
import json
import os
import stat
from types import SimpleNamespace
import uuid

import pytest

from photos_indexer import adapters


def inventory_module():
    assert importlib.util.find_spec("photos_indexer.autonomous_inventory") is not None, "snapshot module missing"
    return importlib.import_module("photos_indexer.autonomous_inventory")


class Bindings:
    image_type = 1
    screenshot_subtype = 8

    def __init__(self, assets, access="authorized"):
        self.assets = assets
        self.access = access
        self.fetch_count = 0

    def request_authorization(self):
        return self.access

    def authorization_status(self):
        return self.access

    def fetch_images_descending(self):
        self.fetch_count += 1
        return iter(tuple(self.assets))

    def asset_for_local_id(self, local_id):
        return next((asset for asset in self.assets if asset.localIdentifier == local_id), None)


def asset(local_id, date, media_type=1, subtype=0):
    return SimpleNamespace(localIdentifier=local_id, creationDate=date, mediaType=media_type,
                           mediaSubtypes=subtype, keywords=["private"], location=(1, 2))


def test_full_catalogue_single_snapshot_includes_screenshots_and_preexisting_keywords(tmp_path):
    # A selection-limit loop, subtype filtering, or live refetch loses these records.
    module = inventory_module()
    now = datetime(2026, 9, 11)
    bindings = Bindings([asset(f"photo-{i:04}", now - timedelta(seconds=i // 3), subtype=8 if i == 0 else 0)
                         for i in reversed(range(1203))])
    selector = adapters.PhotoKitSelector(bindings)
    assert hasattr(selector, "inventory"), "full inventory missing"
    snapshot = module.InventorySnapshot.create(tmp_path / "inventory", selector.inventory())
    bindings.assets.append(asset("later", now + timedelta(days=1)))
    assert snapshot.total_count == 1203
    assert snapshot.chunk_count == 7
    assert [p.local_id for p in snapshot.page(0)][:4] == ["photo-0000", "photo-0001", "photo-0002", "photo-0003"]
    assert [p.local_id for p in snapshot.page(6)] == ["photo-1200", "photo-1201", "photo-1202"]
    reopened = module.InventorySnapshot.load(snapshot.path)
    assert [p.local_id for p in reopened.iter_from(1199)] == ["photo-1199", "photo-1200", "photo-1201", "photo-1202"]
    assert len(list(reopened.iter_from())) == 1203
    assert bindings.fetch_count == 1
    assert list(reopened.iter_from(1203)) == []
    assert all(len(reopened.page(i)) <= 200 for i in range(reopened.chunk_count))


def test_invalid_metadata_is_counted_and_videos_are_excluded(tmp_path):
    module = inventory_module()
    now = datetime(2026, 9, 11)
    bindings = Bindings([asset("video", now, 2), asset("valid", now), asset("missing-date", None),
                         asset("", now), asset("bad-date", object()), asset("bad-media", now, True),
                         asset("control\n", now)])
    snapshot = module.InventorySnapshot.create(tmp_path / "inventory", adapters.PhotoKitSelector(bindings).inventory())
    assert snapshot.total_count == 1
    assert snapshot.invalid_count == 5
    assert snapshot.page(0)[0].local_id == "valid"
    persisted = "".join(path.read_text() for path in snapshot.path.iterdir())
    assert "keywords" not in persisted and "location" not in persisted and "private" not in persisted
    assert "missing-date" not in persisted and "bad-date" not in persisted


def test_private_immutable_snapshot_and_safe_pages(tmp_path):
    module = inventory_module()
    snapshot = module.InventorySnapshot.create(tmp_path / "inventory", [adapters.SelectedPhoto("safe", datetime(2026, 1, 1))])
    assert stat.S_IMODE(snapshot.path.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in snapshot.path.iterdir())
    for value in (-1, True, 1, "0"):
        with pytest.raises(module.InventoryError):
            snapshot.page(value)
    for value in (-1, True, 2):
        with pytest.raises(module.InventoryError):
            list(snapshot.iter_from(value))
    with pytest.raises(module.InventoryError):
        module.InventorySnapshot.create(snapshot.path, [])
    alias = tmp_path / "alias"
    alias.symlink_to(snapshot.path, target_is_directory=True)
    with pytest.raises(module.InventoryError):
        module.InventorySnapshot.load(alias)
    linked = tmp_path / "hardlink"
    os.link(snapshot.path / "chunk-00000000.json", linked)
    with pytest.raises(module.InventoryError):
        snapshot.page(0)


def test_failed_snapshot_is_never_published_and_empty_reopens(tmp_path):
    module = inventory_module()

    def broken():
        yield adapters.SelectedPhoto("safe", datetime(2026, 1, 1))
        raise RuntimeError("source failed")

    with pytest.raises(RuntimeError):
        module.InventorySnapshot.create(tmp_path / "broken", broken())
    assert not (tmp_path / "broken").exists()
    assert list(tmp_path.iterdir()) == []
    snapshot = module.InventorySnapshot.create(tmp_path / "empty", [])
    assert snapshot.total_count == snapshot.chunk_count == snapshot.invalid_count == 0
    assert list(module.InventorySnapshot.load(snapshot.path).iter_from()) == []


def test_corrupt_chunk_and_insecure_manifest_fail_closed(tmp_path):
    module = inventory_module()
    snapshot = module.InventorySnapshot.create(tmp_path / "inventory", [adapters.SelectedPhoto("safe", datetime(2026, 1, 1))])
    chunk = snapshot.path / "chunk-00000000.json"
    chunk.write_text(json.dumps({"records": [{"local_id": "safe", "creation_date": "2026-01-01", "location": [1, 2]}]}))
    with pytest.raises(module.InventoryError):
        snapshot.page(0)
    (snapshot.path / "snapshot.json").chmod(0o644)
    with pytest.raises(module.InventoryError):
        module.InventorySnapshot.load(snapshot.path)


def test_bounded_chunk_accepts_valid_identifiers_at_serialization_size_boundary(tmp_path):
    module = inventory_module()
    records = [adapters.SelectedPhoto("\U0001f600" * 500 + f"{i:03}", datetime(2026, 1, 1)) for i in range(200)]
    snapshot = module.InventorySnapshot.create(tmp_path / "inventory", records)
    assert len(module.InventorySnapshot.load(snapshot.path).page(0)) == 200


def test_inventory_permission_denied_does_not_fetch():
    selector = adapters.PhotoKitSelector(Bindings([], access="denied"))
    assert hasattr(selector, "inventory"), "full inventory missing"
    with pytest.raises(adapters.PhotosAccessError):
        list(selector.inventory())
    assert selector._bindings.fetch_count == 0


def test_local_export_never_falls_back_and_writes_private_bounded_raster(tmp_path):
    assert hasattr(adapters.PhotoKitSelector, "export_local"), "local-only export missing"
    now = datetime(2026, 1, 1)
    bindings = Bindings([asset("shot", now, subtype=8)])
    bindings.local_image_jpeg = lambda current: b"\xff\xd8\xff\xe0test\xff\xd9"
    selector = adapters.PhotoKitSelector(bindings)
    destination = tmp_path / str(uuid.uuid4())
    destination.mkdir(mode=0o700)
    exported = selector.export_local("shot", destination)
    assert exported.parent == destination
    assert exported.read_bytes().startswith(b"\xff\xd8\xff")
    assert stat.S_IMODE(exported.stat().st_mode) == 0o600
    empty_destination = tmp_path / str(uuid.uuid4())
    empty_destination.mkdir(mode=0o700)
    bindings.local_image_jpeg = lambda current: None
    with pytest.raises(adapters.LocalPhotoUnavailableError):
        selector.export_local("shot", empty_destination)
    assert list(empty_destination.iterdir()) == []
    with pytest.raises(adapters.LocalPhotoUnavailableError):
        selector.export_local("deleted", empty_destination)


def test_local_export_rejects_oversized_output_before_writing(tmp_path):
    assert hasattr(adapters.PhotoKitSelector, "export_local"), "local-only export missing"
    bindings = Bindings([asset("photo", datetime(2026, 1, 1))])
    bindings.local_image_jpeg = lambda current: b"\xff\xd8\xff" + b"x" * (8 * 1024 * 1024)
    destination = tmp_path / str(uuid.uuid4())
    destination.mkdir(mode=0o700)
    with pytest.raises(adapters.LocalPhotoUnavailableError):
        adapters.PhotoKitSelector(bindings).export_local("photo", destination)
    assert list(destination.iterdir()) == []


def test_local_export_retains_storage_failure_classification(tmp_path, monkeypatch):
    import errno
    bindings = Bindings([asset("photo", datetime(2026, 1, 1))])
    bindings.local_image_jpeg = lambda current: b"\xff\xd8\xff\xe0test\xff\xd9"
    destination = tmp_path / str(uuid.uuid4())
    destination.mkdir(mode=0o700)
    original_open = adapters.os.open
    def failing_open(path, *args, **kwargs):
        if path == "local.jpg":
            raise OSError(errno.ENOSPC, "export destination is full")
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(adapters.os, "open", failing_open)
    with pytest.raises(adapters.AdapterError) as caught:
        adapters.PhotoKitSelector(bindings).export_local("photo", destination)
    assert type(caught.value).__name__ == "LocalExportStorageError"
    assert isinstance(caught.value.__cause__, OSError)
    assert caught.value.__cause__.errno == errno.ENOSPC
    assert list(destination.iterdir()) == []


def test_local_export_permission_loss_is_fatal_and_destination_must_be_private(tmp_path):
    bindings = Bindings([asset("photo", datetime(2026, 1, 1))], access="denied")
    bindings.local_image_jpeg = lambda current: b"\xff\xd8\xff\xe0test\xff\xd9"
    destination = tmp_path / str(uuid.uuid4())
    destination.mkdir(mode=0o700)
    with pytest.raises(adapters.PhotosAccessError):
        adapters.PhotoKitSelector(bindings).export_local("photo", destination)
    bindings.access = "authorized"
    destination.chmod(0o755)
    with pytest.raises(adapters.AdapterError):
        adapters.PhotoKitSelector(bindings).export_local("photo", destination)
    assert list(destination.iterdir()) == []


@pytest.mark.parametrize("replace_ancestor", [False, True])
@pytest.mark.parametrize("symlink", [False, True])
def test_local_export_rejects_directory_replacement_during_native_request(tmp_path, replace_ancestor, symlink):
    bindings = Bindings([asset("photo", datetime(2026, 1, 1))])
    parent = tmp_path / "exports"
    parent.mkdir(mode=0o700)
    destination = parent / str(uuid.uuid4())
    destination.mkdir(mode=0o700)
    moved = tmp_path / "original"
    redirected = tmp_path / "redirected"
    redirected.mkdir(mode=0o700)

    def replace_during_request(current):
        replaced = parent if replace_ancestor else destination
        replaced.rename(moved)
        if symlink:
            replaced.symlink_to(redirected, target_is_directory=True)
        else:
            replaced.mkdir(mode=0o700)
        if replace_ancestor:
            destination.mkdir(mode=0o700)
        return b"\xff\xd8\xff\xe0test\xff\xd9"

    bindings.local_image_jpeg = replace_during_request
    with pytest.raises(adapters.AdapterError):
        adapters.PhotoKitSelector(bindings).export_local("photo", destination)
    assert list(tmp_path.rglob("local.jpg")) == []


def test_local_export_pins_output_and_cleans_it_if_directory_changes_during_creation(tmp_path, monkeypatch):
    bindings = Bindings([asset("photo", datetime(2026, 1, 1))])
    bindings.local_image_jpeg = lambda current: b"\xff\xd8\xff\xe0test\xff\xd9"
    destination = tmp_path / str(uuid.uuid4())
    destination.mkdir(mode=0o700)
    moved = tmp_path / "original"
    redirected = tmp_path / "redirected"
    redirected.mkdir(mode=0o700)
    real_open = os.open
    pinned = []

    def replace_before_open(path, flags, mode=0o777, *, dir_fd=None):
        if str(path).endswith("local.jpg"):
            destination.rename(moved)
            destination.symlink_to(redirected, target_is_directory=True)
            pinned.append(dir_fd is not None)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", replace_before_open)
    with pytest.raises(adapters.AdapterError):
        adapters.PhotoKitSelector(bindings).export_local("photo", destination)
    assert pinned == [True]
    assert list(moved.iterdir()) == []
    assert list(redirected.iterdir()) == []


def test_snapshot_digest_detects_valid_json_modified_records_after_reopen(tmp_path):
    module = inventory_module()
    snapshot = module.InventorySnapshot.create(tmp_path / "inventory", [adapters.SelectedPhoto("safe", datetime(2026, 1, 1))])
    assert hasattr(snapshot, "digest"), "immutable snapshot digest missing"
    assert len(snapshot.digest) == 64
    assert module.InventorySnapshot.load(snapshot.path).digest == snapshot.digest
    chunk = snapshot.path / "chunk-00000000.json"
    value = json.loads(chunk.read_bytes())
    value["records"][0]["local_id"] = "changed"
    chunk.write_text(json.dumps(value))
    with pytest.raises(module.InventoryError):
        module.InventorySnapshot.load(snapshot.path).page(0)


def test_duplicate_identifiers_with_different_dates_fail_before_publication(tmp_path):
    module = inventory_module()
    with pytest.raises(module.InventoryError):
        module.InventorySnapshot.create(tmp_path / "inventory", [
            adapters.SelectedPhoto("duplicate", datetime(2026, 1, 1)),
            adapters.SelectedPhoto("other", datetime(2026, 1, 2)),
            adapters.SelectedPhoto("duplicate", datetime(2026, 1, 3)),
        ])
    assert list(tmp_path.iterdir()) == []


class ImageOptions:
    def __init__(self):
        self.values = {}

    def __getattr__(self, name):
        if name.startswith("set") and name.endswith("_"):
            return lambda value: self.values.__setitem__(name, value)
        raise AttributeError(name)


@pytest.mark.parametrize("info,image,accepted", [
    ({}, object(), True), ({"cloud": True}, object(), False),
    ({"degraded": True}, object(), False), ({"cancelled": True}, object(), False),
    ({"error": "failed"}, object(), False), ({}, None, False),
])
def test_native_local_request_forbids_network_and_rejects_partial_results(monkeypatch, info, image, accepted):
    assert hasattr(adapters._PhotoKitRuntime, "local_image_jpeg"), "native local image request missing"
    options = ImageOptions()
    calls = []

    def request(current, size, mode, selected_options, callback):
        calls.append((current, size, mode, selected_options))
        callback(image, info)
        return 1

    runtime = adapters._PhotoKitRuntime.__new__(adapters._PhotoKitRuntime)
    runtime._photos = SimpleNamespace(
        PHImageRequestOptions=SimpleNamespace(alloc=lambda: SimpleNamespace(init=lambda: options)),
        PHImageManager=SimpleNamespace(defaultManager=lambda: SimpleNamespace(
            requestImageForAsset_targetSize_contentMode_options_resultHandler_=request)),
        PHImageRequestOptionsDeliveryModeHighQualityFormat=1,
        PHImageRequestOptionsResizeModeExact=2, PHImageRequestOptionsVersionCurrent=0,
        PHImageContentModeAspectFit=0, PHImageResultIsDegradedKey="degraded",
        PHImageCancelledKey="cancelled", PHImageErrorKey="error", PHImageResultIsInCloudKey="cloud")
    encoded_images = []

    def encode(value):
        encoded_images.append(value)
        return b"\xff\xd8\xff\xe0oriented\xff\xd9"

    monkeypatch.setattr(adapters, "_jpeg_for_local_image", encode)
    current = object()
    outcome = runtime.local_image_jpeg(current)
    assert (outcome is not None) is accepted
    assert encoded_images == ([image] if accepted else [])
    assert calls == [(current, (2048, 2048), 0, options)]
    assert options.values == {
        "setNetworkAccessAllowed_": False, "setSynchronous_": True,
        "setDeliveryMode_": 1, "setResizeMode_": 2, "setVersion_": 0,
    }


def test_local_jpeg_retains_supplied_raster_geometry_and_rejects_oversized_raster():
    import Quartz as q

    def rendered(width, height):
        context = q.CGBitmapContextCreate(None, width, height, 8, width * 4,
                                          q.CGColorSpaceCreateWithName(q.kCGColorSpaceSRGB),
                                          q.kCGImageAlphaNoneSkipLast)
        q.CGContextSetRGBFillColor(context, 1, 0, 0, 1)
        q.CGContextFillRect(context, ((0, 0), (width, height)))
        cg_image = q.CGBitmapContextCreateImage(context)
        # NSImage construction needs a WindowServer connection in this sandbox;
        # fake only the Photos-supplied image proxy, keep native JPEG encoding real.
        return SimpleNamespace(CGImageForProposedRect_context_hints_=lambda *args: (cg_image, None))

    encoded = adapters._jpeg_for_local_image(rendered(16, 8))
    assert type(encoded) is bytes
    source = q.CGImageSourceCreateWithData(encoded, None)
    properties = q.CGImageSourceCopyPropertiesAtIndex(source, 0, None)
    assert properties[q.kCGImagePropertyPixelWidth] == 16
    assert properties[q.kCGImagePropertyPixelHeight] == 8
    assert properties.get(q.kCGImagePropertyOrientation, 1) == 1
    assert adapters._jpeg_for_local_image(rendered(2049, 1)) is None
